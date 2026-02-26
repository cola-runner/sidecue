from __future__ import annotations

import argparse
import logging
import math
import os
from dataclasses import replace
from pathlib import Path

from .asr import MacOSTranscriptSource
from .app import SidecueApp
from .config import load_config
from .runtime import app_lock


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="sidecue")
    parser.add_argument("--config", default="config.toml", help="Path to a TOML configuration file")
    parser.add_argument("--asr-mode", choices=["stdin", "mic"], help="Override the input mode")
    parser.add_argument(
        "--llm-provider",
        choices=["codex", "apple_fm", "mock"],
        help="Override the generation provider",
    )
    parser.add_argument("--llm-model", help="Override the model name")
    parser.add_argument("--llm-reasoning-effort", help="Override Codex reasoning effort")
    parser.add_argument("--llm-timeout-seconds", type=float, help="Override the generation timeout")
    parser.add_argument("--codex-command", help="Path to the Codex executable")
    parser.add_argument("--show-prompt", action="store_true", help="Show the full generation context")
    parser.add_argument("--check-mic", action="store_true", help="Test real microphone recognition; requires a nonempty transcript to pass")
    parser.add_argument("--check-seconds", type=float, default=15, help="Microphone test duration in seconds (default: 15)")
    parser.add_argument("--run-seconds", type=float, help="Stop and release resources after this many seconds")
    parser.add_argument("--text", help="Submit an initial text input")
    parser.add_argument("--no-ui", action="store_true", help="Use console output instead of the native window")
    parser.add_argument("--preview-ui", action="store_true", help="Preview sample UI without a microphone or model")
    return parser


def main() -> None:
    try:
        with app_lock(os.environ.get("SIDECUE_LOCK")):
            _main()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc


def _main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args()
    for name in ("check_seconds", "run_seconds", "llm_timeout_seconds"):
        value = getattr(args, name)
        if value is not None and (not math.isfinite(value) or value <= 0):
            parser.error(f"--{name.replace('_', '-')} must be a finite positive number")

    config = load_config(Path(args.config))

    if args.preview_ui:
        if args.check_mic or args.no_ui:
            parser.error("--preview-ui cannot be combined with --check-mic or --no-ui")
        from .ui_preview import run_ui_preview
        run_ui_preview(config.ui, duration_seconds=args.run_seconds)
        return

    if args.asr_mode:
        config.asr = replace(config.asr, mode=args.asr_mode)
    if args.llm_provider:
        config.llm = replace(config.llm, provider=args.llm_provider)
    if args.llm_model:
        config.llm = replace(config.llm, model=args.llm_model)
    if args.llm_reasoning_effort:
        config.llm = replace(config.llm, reasoning_effort=args.llm_reasoning_effort)
    if args.llm_timeout_seconds:
        config.llm = replace(config.llm, timeout_seconds=args.llm_timeout_seconds)
    if args.codex_command:
        config.llm = replace(config.llm, codex_command=args.codex_command)

    if args.check_mic:
        try:
            source = MacOSTranscriptSource(language=config.asr.language, on_device=config.asr.on_device)
            print(f"MIC_CHECK listening: speak within {args.check_seconds:g} seconds.", flush=True)
            transcript = source.check_ready(timeout=args.check_seconds)
        except Exception as exc:
            parser.exit(status=1, message=f"MIC_CHECK FAIL: {exc}\n")
        print(f"MIC_CHECK PASS: recognized_chars={len(transcript)}")
        print(f"buffers={source.status.buffers}, peak={source.status.peak_dbfs:.1f} dBFS")
        return

    app = SidecueApp(
        config,
        enable_overlay=not args.no_ui,
        show_prompt=args.show_prompt,
    )
    if args.text:
        app.submit_utterance(args.text)
    app.run(duration_seconds=args.run_seconds)


if __name__ == "__main__":
    main()
