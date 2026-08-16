# Reproduction guide

Everything needed to rebuild this stack from a bare machine — vLLM serving
`Inferact/Qwen3.8-27B-NVFP4`, pi agents with subagents, and the Terminal-Bench
/ trace-capture / replay experiments. Each stage ends with a checkpoint; don't
continue past a failing one.

`setup.sh` in this directory automates stages 2–5 (idempotent, re-run
anytime); the commands are spelled out here so you know what it does and can
run any stage by hand.

## 0 · Hardware & OS assumptions

- NVIDIA GPU with ≥ 48 GB memory for the 27B NVFP4 checkpoint plus KV cache
  (reference machine: GB10, 121 GB unified, arm64/Grace). Blackwell-class
  recommended for NVFP4 kernels.
- Linux, docker with your user in the `docker` group
  (`sudo usermod -aG docker $USER`, then a fresh login).
- ~80 GB disk: model + task images + traces.

## 1 · Layout

```
<root>/                   # e.g. ~/research/magpie
├── substrate/            # this directory
├── terminal-bench-2-1/   # task checkout (needed for `-p` local harbor runs)
└── .venv/                # python venv with vLLM (created in stage 2)
```

## 2 · vLLM

```bash
cd <root>
curl -LsSf https://astral.sh/uv/install.sh | sh        # uv, if missing
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python vllm ninja --torch-backend auto   # vLLM 0.27.x
```

Start the server (weights auto-download from HF on first launch, ~25 GB,
~5 min load after):

```bash
nohup substrate/backends/vllm.sh > ~/vllm-serve.log 2>&1 &
```

`backends/vllm.sh` encodes requirements each learned from a failed launch or
a measurement — read its comments. The short version: venv bin on PATH (torch
inductor shells out to `ninja`), `--gpu-memory-utilization 0.90` (headroom on
shared/unified memory), `--host 0.0.0.0` (docker sandboxes reach the host via
the bridge, not loopback), MTP speculative decoding (~3x decode, lossless),
prefix caching (-25% wall-clock on agent traffic).

**Checkpoint:** `curl -s localhost:8000/v1/models` lists the model;
`grep -m1 "Resolved architecture" ~/vllm-serve.log` shows `Qwen3_5MTP`
(the speculative draft loaded).

## 3 · pi agent

pi is an npm package; install node via nvm first if needed:

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
nvm install 24
npm install -g --ignore-scripts @earendil-works/pi-coding-agent@0.84.2
```

Pin the version — the Harbor adapter installs the same pin inside every
sandbox (`--ak version=0.84.2`), and host/sandbox must match.

Wire in this substrate's config (provider registration + capture extension,
as symlinks so edits under `substrate/pi/` apply immediately):

```bash
substrate/pi/install.sh
```

That gives pi the `local-vllm` provider (`pi/models.json`: base URL
`127.0.0.1:8000/v1`, OpenAI-completions API, and a `compat` block encoding
vLLM's quirks — notably `thinkingFormat: "qwen-chat-template"`, which makes
`--thinking off` work for Qwen3).

**Checkpoint:** `pi --list-models | grep local-vllm` shows the model, and

```bash
pi -p --no-session --provider local-vllm --model Qwen/Qwen3.8-27B --thinking off 'Say OK.'
```

returns a completion.

## 4 · Subagents

Delegation comes from the [pi-subagents](https://github.com/nicobailon/pi-subagents)
package (pinned), installed through pi's own package manager:

```bash
pi install npm:pi-subagents@0.50.0
```

This registers the `subagent` / `subagent_wait` / `subagent_supervisor` tools
and builtin agents (scout, researcher, worker, reviewer, oracle, delegate) in
`~/.pi/agent/settings.json` + `~/.pi/agent/npm/`. It loads in `--print` mode
too, so the headless path Harbor drives gets the same tools.

Two behavioral facts to know: the model **never delegates unprompted** — you
ask (`use scout to ...`, `run parallel reviewers ...`) or bake guidance into
task prompts; and each subagent is a separate pi process sharing the one vLLM
server, so parallel agents interleave rather than adding capacity.

**Checkpoint:**

```bash
pi -p --no-session --provider local-vllm --model Qwen/Qwen3.8-27B --thinking off \
  'Use the scout subagent to report the current working directory, then relay its answer.'
```

dispatches a real child process and relays its report.

## 5 · Harbor (benchmark runner)

```bash
uv tool install harbor
```

No further config: `harbor/pi_local.py` rides on `PYTHONPATH` and builds each
sandbox from scratch per trial — writes `models.json` pointing at the docker
bridge gateway, installs pinned pi (+ pi-subagents with `--ak
subagents=true`), and verifies the endpoint during install so a bad URL fails
once, loudly.

**Checkpoint** — one real task end-to-end:

```bash
cd <root>
PYTHONPATH=$PWD harbor run -p terminal-bench-2-1/tasks -i fix-git \
  -a substrate.harbor.pi_local:PiLocal -m local-vllm/Qwen/Qwen3.8-27B \
  --ak base_url=http://172.17.0.1:8000/v1 --ak thinking=off --ak version=0.84.2 \
  --allow-agent-host 172.17.0.1 --force-build -e docker -n 1 -k 1
```

Expect reward 1.0 in ~3–5 min. Notes: `172.17.0.1` is docker's default bridge
gateway — verify with `ip route | grep docker0`; `--force-build` is required
on arm64 hosts (prebuilt task images are amd64-only, exit 255 instantly) and
droppable on x86-64.

## 6 · Experiments

**Full sweep** (hours): drop `-i fix-git`, add `-n 4`. Reference result at
these settings: mean reward ≈ 0.13–0.14 with ~50/89 trials ending in
`AgentTimeoutError` — decode speed against 900 s task budgets is the binding
constraint, so treat scores as speed-coupled, not pure capability.

**Trace capture**: add `--ak capture=true` (and `--ak subagents=true` for the
tools-available arm). Corpora land in `jobs/<job>/<trial>/agent/pi-capture/`;
harvest with `bench/collect-captures.py jobs/<stamp> --arm <label>`, catalog
with `bench/index-captures.py`. Host-side capture: set `PI_CAPTURE_DIR` on any
pi invocation.

**Skip capture** by using the published dataset
(`hf download shadowpa0327/pi_agents_terminal_bench --repo-type dataset`);
format is documented in its README and in `bench/bench-replay.py`'s docstring.

**Replay benchmarking** — compare server configs on identical real traffic:

```bash
python3 substrate/bench/bench-replay.py <corpus-dir> \
  --base-url http://localhost:8000 --label my-config
```

Protocol: warm the server with 2 throwaway requests first; one replay at a
time; compare mean TTFT + decode tok/s across labels, never raw wall-clock
(regenerated reply lengths differ). Reference numbers on this hardware:
TTFT ≈ 1.6–1.9 s, decode ≈ 19–21 tok/s single-stream.

## 7 · Known variance & measurement traps

- Agent trajectories don't reproduce run-to-run (batching nondeterminism
  compounds over turns); *distributions* reproduce, single trials don't. Use
  repeats (`-k`) for score claims.
- Never judge speculative decoding on random-token synthetic benchmarks —
  acceptance collapses on unpredictable text by construction.
- Never measure a server's first request — warm-up absorbs JIT/graph capture.
- Concurrency (`-n`) trades per-trial speed for throughput; with wall-clock
  task budgets this changes *scores*, not just latency. Hold `-n` fixed
  across compared runs.
- The traces embed benchmark content (canary GUID preserved): never train on
  them if the model may face Terminal-Bench.
