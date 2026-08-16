#!/usr/bin/env bash
# One-shot setup for the magpie substrate: pi agents + subagents on a local
# vLLM backend. Idempotent — every step checks before acting, so re-running is
# safe and the script doubles as a health check.
#
#   substrate/setup.sh              # install + start server + verify
#   substrate/setup.sh --no-server  # pi stack only (server managed by you)
#   substrate/setup.sh --verify     # verification suite only
set -euo pipefail

SUBSTRATE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$SUBSTRATE")"
PI_VERSION="0.84.2"
PI_SUBAGENTS_VERSION="0.50.0"
MODEL="Qwen/Qwen3.8-27B"
BASE_URL="http://127.0.0.1:8000"

START_SERVER=1
VERIFY_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --no-server) START_SERVER=0 ;;
    --verify)    VERIFY_ONLY=1 ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n== %s\n' "$*"; }
ok()   { printf '   ok: %s\n' "$*"; }
fail() { printf '   FAIL: %s\n' "$*" >&2; exit 1; }

check_prereqs() {
  step "prerequisites"
  if docker ps >/dev/null 2>&1; then
    ok "docker reachable"
  elif sg docker -c "docker ps" >/dev/null 2>&1; then
    ok "docker reachable via 'sg docker' (this shell predates the group change)"
  else
    fail "docker unreachable. Run: sudo usermod -aG docker \$USER  — then log in again."
  fi
  command -v npm >/dev/null || fail "npm not found — install node via nvm (nvm install 24)"
  ok "node $(node --version 2>/dev/null)"
  command -v uv >/dev/null || fail "uv not found — https://docs.astral.sh/uv/"
  ok "uv $(uv --version | awk '{print $2}')"
  [ -x "$REPO/.venv/bin/vllm" ] || fail "no vllm in $REPO/.venv — create the venv and: uv pip install vllm --torch-backend auto"
  ok "vllm venv present"
}

setup_server() {
  step "model server (vLLM: MTP + prefix caching, port 8000)"
  if curl -sf --max-time 3 "$BASE_URL/v1/models" >/dev/null 2>&1; then
    ok "already serving"
    return
  fi
  [ "$START_SERVER" = 1 ] || { ok "skipped (--no-server)"; return; }
  nohup "$SUBSTRATE/backends/vllm.sh" > "$HOME/vllm-serve.log" 2>&1 &
  local pid=$!
  echo "   started pid $pid; loading takes ~5 min (log: ~/vllm-serve.log)"
  for _ in $(seq 1 90); do
    curl -sf --max-time 3 "$BASE_URL/v1/models" >/dev/null 2>&1 && { ok "serving"; return; }
    kill -0 "$pid" 2>/dev/null || fail "server died — check ~/vllm-serve.log"
    sleep 10
  done
  fail "server not up after 15 min — check ~/vllm-serve.log"
}

setup_pi() {
  step "pi CLI (pinned $PI_VERSION)"
  if [ "$(pi --version 2>/dev/null | tail -1)" = "$PI_VERSION" ]; then
    ok "already installed"
  else
    npm install -g --ignore-scripts "@earendil-works/pi-coding-agent@$PI_VERSION" >/dev/null
    ok "installed $(pi --version | tail -1)"
  fi

  step "substrate config (provider + capture extension)"
  "$SUBSTRATE/pi/install.sh" >/dev/null
  ok "symlinked into ~/.pi/agent"

  step "subagents (pi-subagents, pinned $PI_SUBAGENTS_VERSION)"
  if grep -q "npm:pi-subagents" "$HOME/.pi/agent/settings.json" 2>/dev/null; then
    ok "already registered"
  else
    pi install "npm:pi-subagents@$PI_SUBAGENTS_VERSION" >/dev/null
    ok "installed"
  fi
}

setup_harbor() {
  step "Harbor"
  if command -v harbor >/dev/null 2>&1; then
    ok "already installed ($(harbor --version 2>/dev/null | head -1 || echo present))"
  else
    uv tool install harbor >/dev/null
    ok "installed"
  fi
}

verify() {
  step "verify: provider registered"
  pi --list-models 2>/dev/null | grep -q "local-vllm" || fail "local-vllm missing from pi --list-models"
  ok "local-vllm visible"

  if ! curl -sf --max-time 3 "$BASE_URL/v1/models" >/dev/null 2>&1; then
    echo "   server down — skipping live checks (start it, then: setup.sh --verify)"
    return
  fi

  step "verify: end-to-end completion"
  local out
  out=$(timeout 120 pi -p --no-session --provider local-vllm --model "$MODEL" \
        --thinking off 'Reply with exactly: SETUP OK' 2>/dev/null | tail -1)
  [ "$out" = "SETUP OK" ] || fail "unexpected reply: $out"
  ok "$out"

  step "verify: subagent dispatch"
  out=$(timeout 300 pi -p --no-session --provider local-vllm --model "$MODEL" \
        --thinking off 'Use the scout subagent to report the current working directory, then relay its one-line answer.' 2>/dev/null | tail -1)
  [ -n "$out" ] || fail "no output from subagent dispatch"
  ok "scout answered: $out"

  echo
  echo "all green. Benchmark smoke test (needs docker):"
  echo "  cd $REPO && PYTHONPATH=\$PWD harbor run -p terminal-bench-2-1/tasks -i fix-git \\"
  echo "    -a substrate.harbor.pi_local:PiLocal -m local-vllm/$MODEL \\"
  echo "    --ak base_url=http://172.17.0.1:8000/v1 --ak thinking=off --ak version=$PI_VERSION \\"
  echo "    --allow-agent-host 172.17.0.1 --force-build -e docker -n 1 -k 1"
}

if [ "$VERIFY_ONLY" = 1 ]; then
  verify
else
  check_prereqs
  setup_server
  setup_pi
  setup_harbor
  verify
fi
