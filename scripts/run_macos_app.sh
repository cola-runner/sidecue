#!/bin/sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT_DIR"

"$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/scripts/build_macos_app.py" >/dev/null
open "$ROOT_DIR/build/Sidecue.app" --args --config config.toml --asr-mode mic "$@"

echo "Sidecue launch requested; this does not confirm speech recognition."
echo "Log: $HOME/Library/Logs/Sidecue/app.log"
