"""Render the real AppKit preview window to a documentation image.

Uses only built-in fictional fixtures. No microphone, model, or screen capture.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sidecue.config import load_config
from sidecue.native_overlay import A, NativeOverlayWindow
from sidecue.overlay import OverlayUpdate
from sidecue.ui_preview import (
    PREVIEW_PROMPT, PREVIEW_REFERENCES, PREVIEW_SUGGESTION, PREVIEW_TITLE, PREVIEW_TRANSCRIPT,
)


def main() -> None:
    destination = ROOT / "docs/images/sidecue-preview.png"
    config = replace(load_config(ROOT / "config.toml").ui,
                     title=PREVIEW_TITLE, always_on_top=False)
    window = NativeOverlayWindow(config, on_close=lambda: None, engine_label="Preview")
    window.set_input_label("Sample input")
    window.publish_document_list(["sample_notes.txt"])
    window.publish(OverlayUpdate(
        PREVIEW_TRANSCRIPT, PREVIEW_SUGGESTION, PREVIEW_REFERENCES,
        PREVIEW_PROMPT.format(transcript=PREVIEW_TRANSCRIPT),
    ))
    errors = []

    def capture() -> None:
        try:
            # Render the app's own frame view, not the desktop or other windows.
            frame = window._window.contentView().superview()
            frame.displayIfNeeded()
            bitmap = frame.bitmapImageRepForCachingDisplayInRect_(frame.bounds())
            if bitmap is None:
                raise RuntimeError("Could not allocate a preview bitmap")
            frame.cacheDisplayInRect_toBitmapImageRep_(frame.bounds(), bitmap)
            png = bitmap.representationUsingType_properties_(A.NSBitmapImageFileTypePNG, {})
            destination.parent.mkdir(parents=True, exist_ok=True)
            if png is None or not png.writeToFile_atomically_(str(destination), True):
                raise RuntimeError("Could not write the preview image")
        except Exception as exc:
            errors.append(exc)
        finally:
            window._close()

    window.call_later(0.5, capture)
    window.run()
    if errors:
        raise errors[0]
    print(destination)


if __name__ == "__main__":
    main()
