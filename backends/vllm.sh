#!/usr/bin/env bash
# Serve Inferact/Qwen3.8-27B-NVFP4 the way the substrate expects.
#
# The served name is Qwen/Qwen3.8-27B -- models.json and every harbor command
# reference that id, not the checkpoint name. The parsers are the combination
# verified in ../README.md: qwen3_xml for tool calls, qwen3 for reasoning.
#
# Binds 0.0.0.0 so Terminal-Bench sandboxes can reach it via the docker bridge
# gateway (172.17.0.1), not just loopback.
#
# Run it under nohup/tmux/systemd -- a plain login shell takes it down on
# logout, which is how it died the first time.
#
# Prefix caching measured -25% wall-clock on a real captured /solve workload
# (replay_vllm-mtp3-apc): agent turns re-send a growing shared prefix, and the
# cache turns that re-prefill into a lookup. Composes fine with MTP.
#
# MTP speculative decoding measured 2.3x single-stream output throughput
# (7.3 -> 16.6 tok/s at 4k-token contexts) with a 93% draft acceptance rate;
# the checkpoint ships the MTP head (arch resolves to Qwen3_5MTP). Lossless:
# rejection sampling preserves the output distribution. Trailing "$@" lets a
# launch override or extend flags without editing this file.
#
# gpu-memory-utilization is pinned below the 0.92 default because this is
# unified memory shared with the rest of the machine: at 0.92 the server needs
# 111.95 GiB free at startup and fails whenever the desktop holds a bit more
# than usual (observed: 111.52 GiB free -> refused to start).
set -euo pipefail

VENV_BIN="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.venv/bin" && pwd)"

# The venv bin must be on PATH, not just used to find vllm: torch inductor
# shells out to `ninja` during the startup profiling run, and ninja is
# installed in the venv. Without this the engine dies mid-load with
# FileNotFoundError: 'ninja'.
export PATH="$VENV_BIN:$PATH"

exec "$VENV_BIN/vllm" serve Inferact/Qwen3.8-27B-NVFP4 \
  --served-model-name Qwen/Qwen3.8-27B \
  --host 0.0.0.0 --port 8000 \
  --tensor-parallel-size 1 \
  --gpu-memory-utilization 0.90 \
  --max-model-len 262144 \
  --kv-cache-dtype fp8 \
  --reasoning-parser qwen3 \
  --enable-auto-tool-choice --tool-call-parser qwen3_xml \
  --speculative-config '{"method":"mtp","num_speculative_tokens":3}' \
  --enable-prefix-caching \
  "$@"
