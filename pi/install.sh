#!/usr/bin/env bash
# Install the magpie pi substrate into ~/.pi/agent.
# Idempotent: re-run after editing anything under substrate/pi.
#
# Subagents come from the pi-subagents package (installed separately, once):
#   pi install npm:pi-subagents
set -euo pipefail

SUBSTRATE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PI_HOME="$HOME/.pi/agent"

mkdir -p "$PI_HOME/extensions/capture-payload"

# Model config: local vLLM provider.
ln -sf "$SUBSTRATE/models.json" "$PI_HOME/models.json"

# Request-capture extension for replay benchmarking (no-op unless PI_CAPTURE_DIR
# is set or running inside a Harbor sandbox).
ln -sf "$SUBSTRATE/extensions/capture-payload/index.ts" "$PI_HOME/extensions/capture-payload/index.ts"

echo "installed into $PI_HOME"
