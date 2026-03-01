#!/bin/sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT_DIR"

"$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/scripts/build_macos_app.py" >/dev/null
LOG_PATH="$HOME/Library/Logs/Sidecue/app.log"
LINES=0
if [ -f "$LOG_PATH" ]; then
  LINES=$(wc -l < "$LOG_PATH")
fi
open -W "$ROOT_DIR/build/Sidecue.app" --args --config config.toml --check-mic "$@"
OUTPUT=$(tail -n "+$((LINES + 1))" "$LOG_PATH")
printf '%s\n' "$OUTPUT"
printf '%s\n' "$OUTPUT" | grep -q '^MIC_CHECK PASS:'
