#!/usr/bin/env bash
# Throughput sweep against the live vLLM server: how token/s scales with the
# number of concurrent agent streams (1 parent + N subagents = N+1 streams).
#
# Request shape defaults mirror what real trials produced (see traces/):
# ~4k-token contexts, ~300-token completions per turn. Override via env:
#
#   CONCURRENCY="1 2 4 8" INPUT_LEN=4000 OUTPUT_LEN=300 substrate/bench/bench-throughput.sh
#
# Results: one JSON per point in traces/bench/, plus a summary table on stdout.
# Run against an otherwise idle server or the numbers include foreign load.
set -euo pipefail

VENV_BIN="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.venv/bin" && pwd)"
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
MODEL="${MODEL:-Qwen/Qwen3.8-27B}"
CONCURRENCY="${CONCURRENCY:-1 2 4 8}"
INPUT_LEN="${INPUT_LEN:-4000}"
OUTPUT_LEN="${OUTPUT_LEN:-300}"
PROMPTS_PER_STREAM="${PROMPTS_PER_STREAM:-8}"

OUT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/traces/bench"
mkdir -p "$OUT_DIR"

curl -sf --max-time 5 "$BASE_URL/v1/models" > /dev/null \
  || { echo "server not answering at $BASE_URL" >&2; exit 1; }

for c in $CONCURRENCY; do
  n=$((c * PROMPTS_PER_STREAM))
  echo "=== concurrency $c ($n requests, in=$INPUT_LEN out=$OUTPUT_LEN) ==="
  "$VENV_BIN/vllm" bench serve \
    --base-url "$BASE_URL" \
    --model "$MODEL" \
    --dataset-name random \
    --random-input-len "$INPUT_LEN" \
    --random-output-len "$OUTPUT_LEN" \
    --num-prompts "$n" \
    --max-concurrency "$c" \
    --save-result \
    --result-dir "$OUT_DIR" \
    --result-filename "c${c}_in${INPUT_LEN}_out${OUTPUT_LEN}.json"
done

echo
echo "concurrency | output tok/s | total tok/s | mean TTFT ms | mean TPOT ms"
for c in $CONCURRENCY; do
  python3 - "$OUT_DIR/c${c}_in${INPUT_LEN}_out${OUTPUT_LEN}.json" "$c" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
print(f"{sys.argv[2]:>11} | {d['output_throughput']:>12.1f} | {d['total_token_throughput']:>11.1f} | {d['mean_ttft_ms']:>12.1f} | {d['mean_tpot_ms']:>12.1f}")
EOF
done
