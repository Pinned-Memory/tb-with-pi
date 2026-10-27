# Results

Terminal-Bench 2.1 scores for Qwen3.8-27B, by backend and agent (pi, Terminus-2).
Per-task results are in [`results/`](results/).

## Summary

| | Sharar `fast` + pi, thinking off | Sharar `--exact` + Terminus-2, thinking on | vLLM + pi (reference) |
|---|---|---|---|
| Model | `RadixArk/Qwen3.8-27B-NVFP4` + Sharar's calibrated 3-bit copies | `RadixArk/Qwen3.8-27B-NVFP4` | `Inferact/Qwen3.8-27B-NVFP4` |
| Speculative decoding | DFlash2 | DFlash2, exact acceptance | MTP |
| Decode | ~64 tok/s, single stream | 24 tok/s per stream at 4 streams (all request time, prefill included) | ~19–21 tok/s, single stream |
| **Mean reward, all 89 tasks** | **0.258** (23/89) | **0.315** (28/89) | ≈ 0.13–0.14 |
| Mean reward, tasks that ran | 0.271 (23/85) | 0.326 (28/86) | not reported |
| **Mean reward, 4x agent time** | not run | **0.652** (58/89) | not run |
| Trials ending in `AgentTimeoutError` | 36/89 (40%) | 58/89 (65%; 3 of them passed anyway) | ~50/89 (56%) |
| Wall-clock, `-n 4` | 7 h 57 min | 8 h 41 min | "hours" |
| Attempts per task | 1 | 1 | 1 (apparently) |

**Official score.** Qwen's model card for `Qwen/Qwen3.8-27B` (revision `1d4bf0f2`,
2026-08-14) reports **73.0** on "Terminal Bench 2.1 (Terminus)" (Qwen3.6-27B: 63.4).
The card's evaluation settings for that row are not given; it is presumably the released
(unquantized) model in its default thinking mode (the card: "Thinking mode is on by
default"; recommended sampling temperature 1.0, top_p 0.95, top_k 20), driven by
Terminus, Terminal-Bench's own agent, not pi. Time limits, attempt count and hardware
are not stated either. It is the ceiling these runs aim at, not a like-for-like
row: agent, precision, thinking and budgets all differ.

| | Agent | Precision | Thinking | Score |
|---|---|---|---|---|
| Qwen (model card) | Terminus | unquantized (presumably) | on (presumably) | 73.0 |
| Sharar `--exact`, 4x agent time (below) | Terminus-2 | NVFP4/FP8 | on | **65.2** |
| Sharar `--exact`, default time (below) | Terminus-2 | NVFP4/FP8 | on | 31.5 |
| Sharar `fast` (below) | pi 0.84.2 | NVFP4/FP8 + 3-bit copies | off | 25.8 |
| vLLM (REPRODUCE.md) | pi 0.84.2 | NVFP4 | off | ≈ 13–14 |

The card gives no context, output or time limits for this row. Its other agentic coding
rows use a 256K context window, and its in-house QwenSWEBench row uses `max_tokens`
32,768 and an 8-hour timeout per task. The `--exact` run matches the first two (context
262,144, `max_tokens` 32,768) but not the time: it keeps TB2.1's own per-task limits.
A pi run with thinking on (`fast`) was started and stopped partway, when the runs were
aligned with the card; it is not reported.

The reference row is from [`REPRODUCE.md`](REPRODUCE.md) §6, run on the same class of
hardware (GB10) with the same harness settings; it was not rerun here.

**Same model, different checkpoints.** Both arms serve `Qwen/Qwen3.8-27B` (each
checkpoint's `base_model`), quantized by different publishers with different recipes:

| | `RadixArk/Qwen3.8-27B-NVFP4` (Sharar) | `Inferact/Qwen3.8-27B-NVFP4` (vLLM) |
|---|---|---|
| Weights | 21.9 GB | 26.4 GB |
| Recipe | mixed: MLP projections NVFP4 (193), attention and linear-attention projections FP8 (208); MTP head unquantized | NVFP4 (group 16), 244 modules left unquantized (embeddings, `lm_head`, small projections) |
| KV cache | FP8 | FP8 (`--kv-cache-dtype fp8` in `backends/vllm.sh`) |
| On top, as served | `fast` swaps in calibrated 3-bit copies of some matrices (lossy); `--exact` does not | none |

So a score difference mixes the engine, the checkpoint and (for `fast`) the 3-bit copies.
Sharar `--exact` isolates the 3-bit copies; serving one checkpoint on both engines would
isolate the engine.

**Thinking off.** Both rows run pi with `--thinking off`, as the README and REPRODUCE.md
do. For Qwen3.8 this is a boolean (pi sends `enable_thinking` through the chat template):
across the 23,339 model replies of the Sharar sweep there are no thinking blocks and 0
reasoning tokens. Thinking spends more tokens per turn, so under fixed time budgets a
faster backend may turn it from a loss into a gain; that is measured below.

**Read with care.** Both rows are a single attempt per task, from different machines,
dates and software versions, so this is not yet a controlled comparison: one task is
0.011 of mean reward, and agent runs do not reproduce trial by trial. What the data does
support is the mechanism: with per-task agent budgets of 750–12,000 s (900 s for 15 of
the 36 that timed out), time-outs are the largest
failure class, and a backend that decodes ~3x faster finishes more tasks inside them
(36 time-outs against ~50). Separating speed from capability needs repeats (`-k 3`), the
vLLM arm rerun on this machine, and Sharar `--exact` (the model's own distribution,
without the lossy 3-bit copies).

## Sharar `fast`, 2026-10-25

Job `sweep-sharar-fast-k1-20261025-1406`; per task:
[`results/sharar-fast-k1-20261025.csv`](results/sharar-fast-k1-20261025.csv).

### Setup

| | |
|---|---|
| Hardware | one DGX Spark (GB10, 121 GiB unified, arm64), driver 580.178.04 |
| Backend | Sharar `f4eb50e` (main, 2026-10-24), `sharar serve 27b`, profile `fast`, up to 4 requests at once, context 262,144 |
| Agent | pi 0.84.2, thinking off, `maxTokens` 32,768, no subagents, no capture |
| Runner | Harbor 0.21.0, `-n 4 -k 1`, `--force-build` (arm64), default task timeouts |
| Tasks | `terminal-bench/terminal-bench-2-1`, 89 tasks |

```bash
# backend: listen on the docker bridge; leave the containers 24 GiB of the unified memory
SHARAR_HOST_FLOOR_GIB=24 uv run sharar serve 27b --host 172.17.0.1

# from the directory that holds substrate/ and the task checkout
harbor datasets download terminal-bench/terminal-bench-2-1 -o terminal-bench-2-1
PYTHONPATH=$PWD harbor run -p terminal-bench-2-1/terminal-bench-2-1 \
  -a substrate.harbor.pi_local:PiLocal -m local-vllm/RadixArk/Qwen3.8-27B-NVFP4 \
  --ak base_url=http://172.17.0.1:30001/v1 --ak thinking=off --ak version=0.84.2 \
  --allow-agent-host 172.17.0.1 --force-build -e docker -n 4 -k 1
```

Harbor 0.25 (2026-10-25) handles a `base_url` agent kwarg itself and rejects it for this
adapter ("doesn't support --base-url overrides"), so pin `harbor==0.21.0`.

### Outcomes

| Outcome | Tasks |
|---|---|
| Passed | 23 |
| Failed the tests | 24 |
| `AgentTimeoutError` | 36 (6 of them after a runaway reply) |
| Runaway reply, then exited | 2 |
| Not run (environment or harness) | 4 |

Prefix caching carried the agent traffic: 2.37 B prompt tokens, 99.8% of them served from
the cache; 3.46 M tokens generated. Median agent time per task: 862 s.

**Passed (23):** build-cython-ext, caffe-cifar-10, cancel-async-tasks, cobol-modernization,
configure-git-webserver, financial-document-processor, fix-code-vulnerability,
git-leak-recovery, headless-terminal, hf-model-inference, kv-store-grpc, largest-eigenval,
log-summary-date-ranges, merge-diff-arc-agi-task, modernize-scientific-stack,
multi-source-data-merger, nginx-request-logging, portfolio-optimization, prove-plus-comm,
pypi-server, pytorch-model-cli, sqlite-with-gcov, train-fasttext.

**Failed the tests (24):** bn-fit-modify, constraints-scheduling, count-dataset-tokens,
crack-7z-hash, custom-memory-heap-crash, dna-insert, filter-js-from-html, fix-ocaml-gc,
gcode-to-text, llm-inference-batching-scheduler, mailman, make-mips-interpreter,
mteb-leaderboard, mteb-retrieve, openssl-selfsigned-cert, protein-assembly,
query-optimize, raman-fitting, sanitize-git-repo, sparql-university, sqlite-db-truncate,
torch-pipeline-parallelism, torch-tensor-parallelism, video-processing.

**Timed out (36):** adaptive-rejection-sampler, break-filter-js-from-html, build-pmars,
build-pov-ray\*, chess-best-move, circuit-fibsqrt\*, code-from-image, compile-compcert,
db-wal-recovery, distribution-search\*, dna-assembly, extract-elf,
extract-moves-from-video, feal-differential-cryptanalysis, feal-linear-cryptanalysis,
git-multibranch, gpt2-codegolf, install-windows-3.11, large-scale-text-editing,
make-doom-for-mips, mcmc-sampling-stan, model-extraction-relu-logits, overfull-hbox,
password-recovery, path-tracing\*, path-tracing-reverse, polyglot-c-py, polyglot-rust-c,
regex-chess, reshard-c4-data\*, rstan-to-pystan, sam-cell-seg, tune-mjcf,
vulnerable-secret, winning-avg-corewars\*, write-compressor. (\* after a runaway reply)

**Runaway reply, then exited (2):** regex-log, schemelike-metacircular-eval.

### Runaway replies (open)

8 trials (9%) contain a reply that ran to `maxTokens` (32,768) and reached pi as a few
words of text or nothing (`stopReason: length`). pi ends the run on it (2 trials), or the
minutes spent generating it push the trial past its timeout (6). The likely shape is a
tool call the model opened and never closed, which the server cannot parse and so drops;
whether that is the model looping, an outsized file write, or a parsing fault is not
known yet, because neither the request nor the raw output was captured. Next: rerun a few
with `--ak capture=true`, replay the failing request, and read the raw completion.

### Not run (4)

These failed before the agent started and count as 0 above. They would fail on any
backend.

| Task | Cause |
|---|---|
| fix-git | Image build clones `github.com/TheMikeMerrill/personal-site`, which no longer exists; the prebuilt image that contains it is amd64-only. |
| qemu-startup | Image build: `apt-get` gets 404 for Debian security packages (possibly transient). |
| qemu-alpine-ssh | Same as qemu-startup. |
| pytorch-model-recovery | Harness: the instruction starts with `- `, and Harbor's pi adapter passes it as the first argument, so pi rejects it (`Error: Unknown option: - You are given…`). Passing it after `--` would fix it. |

## Sharar `--exact` + Terminus-2, thinking on, 2026-10-26

Aligned with the model card: the agent its Terminal Bench row names (Terminus), thinking on
with the card's thinking-mode sampling, and Sharar without its lossy 3-bit copies. Job
`sweep-sharar-exact-terminus2-think-k1-20261026-0000`; per task:
[`results/sharar-exact-terminus2-think-k1-20261026.csv`](results/sharar-exact-terminus2-think-k1-20261026.csv).

### Setup

| | |
|---|---|
| Backend | Sharar `f4eb50e`, `sharar serve 27b --exact` (accept top-1 only, no 3-bit copies), context 262,144 |
| Agent | Harbor's built-in `terminus-2` (2.0.0), JSON parser, on the host via LiteLLM |
| Sampling | temperature 1.0, top_p 0.95, top_k 20 (the card's thinking mode); `enable_thinking` true |
| Thinking history | `interleaved_thinking=true`: each turn's `reasoning_content` is sent back, as the card's `preserve_thinking` expects |
| Limits | `max_tokens` 32,768 per reply; default task timeouts; LiteLLM's default 600 s per request |
| Runner | Harbor 0.21.0, `-n 4 -k 1`, `--force-build` |

```bash
SHARAR_HOST_FLOOR_GIB=24 uv run sharar serve 27b --exact --host 172.17.0.1

harbor run -p terminal-bench-2-1/terminal-bench-2-1 -a terminus-2 \
  -m hosted_vllm/Qwen3.8-27B-NVFP4 --ak api_base=http://172.17.0.1:30001/v1 \
  --ak temperature=1.0 --ak interleaved_thinking=true \
  --ak 'llm_call_kwargs={"top_p":0.95,"max_tokens":32768,"extra_body":{"top_k":20,"chat_template_kwargs":{"enable_thinking":true}}}' \
  --ak 'model_info={"max_input_tokens":262144,"max_output_tokens":32768,"input_cost_per_token":0,"output_cost_per_token":0}' \
  --force-build -e docker -n 4 -k 1
```

### Outcomes

| Outcome | Tasks |
|---|---|
| Passed | 28 (3 of them after an `AgentTimeoutError`: the tests passed on the state left behind) |
| Failed the tests | 3 |
| `AgentTimeoutError`, tests failed | 55 |
| Not run | 3 (fix-git; qemu-startup and qemu-alpine-ssh, as before) |

Nearly every task that did not pass ran out of time: only 3 trials finished and failed.
With thinking on and exact decoding, each turn is slow (1.53 M tokens generated in 1,251
replies, about 24 tok/s per stream with 4 at once), so the median trial used its whole
budget (900 s). Four single replies ran past LiteLLM's 600 s request timeout (in
circuit-fibsqrt, regex-chess, reshard-c4-data and schemelike-metacircular-eval); LiteLLM
then retries, which spends the rest of the budget. No recorded reply reached the 32,768
`max_tokens` cap. Prompt caching served 96% of the 40.8 M prompt tokens.

pytorch-model-recovery ran this time (Terminus-2 does not have pi's leading-`- ` problem)
and passed.

**Against the `fast` + pi run:** 18 tasks pass only here (bn-fit-modify,
break-filter-js-from-html, build-pmars, build-pov-ray, code-from-image,
constraints-scheduling, db-wal-recovery, distribution-search, fix-ocaml-gc,
git-multibranch, large-scale-text-editing, mcmc-sampling-stan, openssl-selfsigned-cert,
password-recovery, pytorch-model-recovery, regex-log, sam-cell-seg, vulnerable-secret);
13 pass only there, 11 of which timed out here (build-cython-ext, caffe-cifar-10,
cobol-modernization, financial-document-processor, headless-terminal,
hf-model-inference, largest-eigenval, nginx-request-logging, pypi-server,
pytorch-model-cli, train-fasttext) and 2 failed (cancel-async-tasks,
configure-git-webserver). Agent, thinking and profile changed together, so this does not
attribute the difference to any one of them.

**Passed (28):** bn-fit-modify, break-filter-js-from-html, build-pmars, build-pov-ray,
code-from-image, constraints-scheduling, db-wal-recovery, distribution-search,
fix-code-vulnerability, fix-ocaml-gc, git-leak-recovery, git-multibranch, kv-store-grpc,
large-scale-text-editing, log-summary-date-ranges, mcmc-sampling-stan,
merge-diff-arc-agi-task, modernize-scientific-stack, multi-source-data-merger,
openssl-selfsigned-cert, password-recovery, portfolio-optimization, prove-plus-comm,
pytorch-model-recovery, regex-log, sam-cell-seg, sqlite-with-gcov, vulnerable-secret.

**Failed the tests (3):** cancel-async-tasks, configure-git-webserver, mteb-retrieve.

### Fixes for the next run

| Problem | Fix |
|---|---|
| Time-outs (58 of 89) | `--agent-timeout-multiplier 4`. Terminus-2 does not tell the model its budget, so a trial that finished inside 1x would have run the same under 4x; only the time-outs and not-run tasks need rerunning. |
| 600 s per-request cut-off | `"timeout": 3600` in `llm_call_kwargs` (Harbor passes it to LiteLLM). |
| qemu-startup, qemu-alpine-ssh: 404 at build | Not transient: Debian's `bullseye-security` index lists arm64 qemu and mesa packages its pool no longer serves. Locally, both Dockerfiles drop that source before `apt-get update` (originals kept as `Dockerfile.orig`); packages then come from bullseye main 11.11. Both images build. |
| fix-git | Still not runnable on arm64 (source repo gone); counts 0. |

## Sharar `--exact` + Terminus-2, thinking on, 4x agent time, 2026-10-27

The run above with the fixes applied: the 55 tasks that timed out and the 2 qemu tasks
were rerun with `--agent-timeout-multiplier 4` and a 3,600 s request timeout, everything
else unchanged. The 31 tasks that finished at 1x keep their result: Terminus-2 does not
tell the model its time budget, so a trial that ended inside 1x would have gone the same
way with more time. fix-git still counts 0. Job
`rerun-sharar-exact-terminus2-think-t4-20261026-1930` (16 h 2 min, `-n 4`); per task:
[`results/sharar-exact-terminus2-think-t4-rerun-20261027.csv`](results/sharar-exact-terminus2-think-t4-rerun-20261027.csv)
(the rerun) and
[`results/sharar-exact-terminus2-think-combined-20261027.csv`](results/sharar-exact-terminus2-think-combined-20261027.csv)
(all 89, with the source of each result).

```bash
# as above, plus a 1 h request timeout, 4x agent time, and only the unfinished tasks (-i per task)
--ak 'llm_call_kwargs={"top_p":0.95,"max_tokens":32768,"timeout":3600,"extra_body":{"top_k":20,"chat_template_kwargs":{"enable_thinking":true}}}' \
--agent-timeout-multiplier 4 -i adaptive-rejection-sampler -i build-cython-ext ...
```

**Score: 58/89 = 0.652**, against the card's 0.730, up from 0.315 at default time limits.

| Outcome, all 89 | Tasks |
|---|---|
| Passed | 58 (28 at 1x, 30 in the rerun; 3 of the rerun's after a time-out) |
| Failed the tests | 17 (3 at 1x, 14 in the rerun) |
| `AgentTimeoutError` even at 4x, tests failed | 13 |
| Not run | 1 (fix-git) |

**Passed in the rerun (30):** adaptive-rejection-sampler, build-cython-ext\*,
caffe-cifar-10, circuit-fibsqrt, cobol-modernization\*, count-dataset-tokens,
crack-7z-hash, custom-memory-heap-crash, extract-elf, extract-moves-from-video,
feal-differential-cryptanalysis, feal-linear-cryptanalysis, financial-document-processor,
headless-terminal, hf-model-inference, llm-inference-batching-scheduler,
nginx-request-logging, polyglot-c-py, pypi-server, pytorch-model-cli, qemu-alpine-ssh,
qemu-startup, reshard-c4-data, rstan-to-pystan, sanitize-git-repo,
schemelike-metacircular-eval\*, sparql-university, sqlite-db-truncate, train-fasttext,
video-processing. (\* after a time-out)

**Failed the tests in the rerun (14):** compile-compcert, dna-assembly, dna-insert,
filter-js-from-html, install-windows-3.11, mailman, model-extraction-relu-logits,
mteb-leaderboard, path-tracing-reverse, protein-assembly, query-optimize,
torch-pipeline-parallelism, torch-tensor-parallelism, winning-avg-corewars.

**Timed out even at 4x (13):** chess-best-move, gcode-to-text, gpt2-codegolf,
largest-eigenval, make-doom-for-mips, make-mips-interpreter, overfull-hbox, path-tracing,
polyglot-rust-c, raman-fitting, regex-chess, tune-mjcf, write-compressor.

The patched qemu tasks both passed. No request reached the 3,600 s timeout and no reply
reached the 32,768-token cap; 106 replies took over 5 minutes. The rerun generated
3.18 M tokens in 1,903 replies (about 23 tok/s per stream at 4 at once); the median rerun
trial took 3,030 s.

**Against the card (73.0).** The remaining 7.8 points (about 7 tasks) are within reach of
what is still different: 13 tasks still time out at 4x; the checkpoint is NVFP4/FP8, not
the released weights; fix-git cannot run on arm64; and this is one attempt per task, where
one task is 1.1 points. The card states no time limit, attempt count or output cap for
this row, so a closer match is not possible from the card alone.

## Next

1. Two more attempts per task (`-k 3` in total), at 4x time, for a mean with a spread.
2. The 13 tasks that time out at 4x: more time, or fewer concurrent trials (faster per stream).
3. The vLLM arm on this machine, with the same settings.
4. Diagnose the runaway replies of the pi runs.
