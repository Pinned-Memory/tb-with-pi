# magpie substrate

Setup for **pi agents + subagents** on a local **vLLM** backend serving
`Inferact/Qwen3.8-27B-NVFP4` (as `Qwen/Qwen3.8-27B`), plus the Harbor adapter
that runs the stack against Terminal-Bench 2.1.

```
substrate/
├── REPRODUCE.md         # full from-scratch guide: vLLM, pi, subagents, experiments
├── setup.sh             # one-shot install + verify (idempotent)
├── backends/
│   └── vllm.sh          # port 8000: MTP spec decode + prefix caching
├── pi/                  # provider config, capture extension
├── harbor/pi_local.py   # Harbor agent: pi in a sandbox, backend on the host
└── bench/
    ├── bench-replay.py      # replay a captured pi workload (sequential)
    ├── bench-throughput.sh  # synthetic latency stressor (not for spec-decode comparisons)
    └── metrics-dash.py      # live dashboard for vLLM /metrics, port 8500
```

## Clone

This repo *is* the `substrate/` package of the wider `magpie` workspace, so
clone it under that name — `harbor -a substrate.harbor.pi_local:PiLocal`
resolves the import relative to the parent directory:

```bash
git clone git@github.com:Pinned-Memory/tb-with-pi.git substrate
```

## Setup

(For the from-bare-machine walkthrough — including installing vLLM, pi, and
subagents by hand, with verification checkpoints and expected results — see
[`REPRODUCE.md`](REPRODUCE.md).)

```bash
substrate/setup.sh              # pi + subagents + vLLM backend + verify
substrate/setup.sh --verify     # health check only (re-run anytime)
```

Prereqs it checks (bring these yourself): docker group membership, node (nvm),
uv, and vLLM in `../.venv`. Versions of pi (0.84.2) and pi-subagents (0.50.0)
are pinned at the top of the script. The server takes ~5 min to load.

## Use

```bash
pi --provider local-vllm --model Qwen/Qwen3.8-27B --thinking off
```

`--thinking off`: thinking is a boolean for this model; off is faster.

Subagents (from [pi-subagents](https://github.com/nicobailon/pi-subagents)) by
asking: `use scout to ...`, `ask oracle whether ...`, `run parallel reviewers
...`, `run this in the background`; `/subagents-fleet` shows running work. The
model never delegates unprompted. Each subagent is another pi process sharing
the one backend.

## Backend

`backends/vllm.sh` (port 8000) serves the model with MTP speculative decoding
(~3x decode, 88-93% draft acceptance) and prefix caching (-25% wall-clock on
agent traffic; TTFT flat to 19.5k-token contexts). The script's comments
explain every flag — each was earned by a failed launch or a measurement.
Historical speculative-decoding comparisons (incl. SGLang + DSpark, since
removed from the stack) live in `../traces/bench/`.

## Terminal-Bench

```bash
cd /home/cc2869/research/magpie
PYTHONPATH=$PWD harbor run \
  -p terminal-bench-2-1/tasks \
  -i fix-git \
  -a substrate.harbor.pi_local:PiLocal \
  -m local-vllm/Qwen/Qwen3.8-27B \
  --ak base_url=http://172.17.0.1:8000/v1 \
  --ak thinking=off \
  --ak version=0.84.2 \
  --allow-agent-host 172.17.0.1 \
  --force-build \
  -e docker -n 1 -k 1
```

Full sweep: drop `-i fix-git`, raise `-n` (bounded by the one backend — tasks
have wall-clock timeouts). Optional: `--ak subagents=true` (installs
pi-subagents in-sandbox, pinned), `--ak capture=true` (records requests for
replay).

Load-bearing and easy to miss:

- `PYTHONPATH=$PWD` — harbor won't find the adapter otherwise.
- `--ak version=0.84.2` — else the sandbox installs `@latest` and drifts.
- `--force-build` — arm64 host; prebuilt task images are amd64-only.
- `172.17.0.1` — docker bridge gateway (`ip route | grep docker0`).

The model has never delegated to subagents unprompted in a captured run; to
measure delegation, add guidance to the task instruction (open experiment).

## Benchmarking server configs

Capture real pi traffic once, replay it against any backend:

```bash
PI_CAPTURE_DIR=$PWD/traces/captures/mytask pi -p ... 'task'   # or --ak capture=true
python3 substrate/bench/bench-replay.py traces/captures/mytask \
  --base-url http://127.0.0.1:8000 --label my-config
```

Replay resends frozen request bodies (identical input for every config) and
measures per-request TTFT + decode tok/s; inference only, tool time excluded.
Rules: warm the server first, one replay at a time, compare TTFT + decode
tok/s rather than wall-clock. Reference corpora in `../traces/captures/`.
Measurement traps that produced wrong conclusions before being caught:
random-token prompts wreck spec-decode acceptance; unwarmed first requests
absorb JIT cost.

`bench/metrics-dash.py` (port 8500) charts vLLM's windowed spec-decode
acceptance, prefix-cache hit rate, throughput, and latency live — the panels
the upstream Grafana dashboards don't have.

## Skills

`harbor run --skill <path>` stages skills into pi's global skill dir — no
adapter change. Cheaper than subagents (no extra processes or GPU contention),
and composable: a skill can tell the agent when to dispatch which subagent.
