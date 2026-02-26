from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal


@dataclass(frozen=True)
class OverlayUpdate:
    transcript: str
    suggestion: str
    references: str
    prompt: str = ""
    phase: Literal["generating", "ready", "error", "input_error"] = "ready"
    elapsed_seconds: float | None = None


@dataclass
class SuggestionState:
    phase: str = "idle"
    suggestion: str = ""
    transcript: str = ""
    references: str = ""
    prompt: str = ""
    error: str = ""
    started_at: float | None = None
    elapsed_seconds: float | None = None

    def apply(self, update: OverlayUpdate, now: float) -> None:
        # New requests and input errors cannot erase the last completed cues.
        if update.phase == "input_error":
            return
        self.phase = update.phase
        self.prompt = update.prompt
        self.error = update.suggestion if update.phase == "error" else ""
        self.elapsed_seconds = update.elapsed_seconds
        self.started_at = now if update.phase == "generating" else None
        if update.phase == "ready":
            self.suggestion = update.suggestion
            self.transcript = update.transcript
            self.references = update.references

    def status(self, now: float) -> str:
        if self.phase == "generating":
            elapsed = max(0, int(now - self.started_at)) if self.started_at is not None else 0
            return f"Generating · {elapsed}s"
        if self.phase == "ready":
            return f"Ready · {self.elapsed_seconds:.1f}s" if self.elapsed_seconds is not None else "Ready"
        if self.phase == "error":
            return "Generation failed"
        return "Waiting for input"


def cue_lines(value: str) -> list[tuple[str, str]]:
    """Preserve the model's cue/evidence structure instead of numbering every line."""
    result = []
    has_cue = False
    for raw in value.splitlines():
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", raw.strip())
        if not line:
            continue
        marker = re.match(r"^(?:[-*•]\s+|\d+[.)、]\s*)", line)
        if marker:
            line = line[marker.end():]
        label = re.match(r"^(?:Prompt|Cue|\u63d0\u793a(?:\u8bcd)?)[\uff1a:]\s*", line, re.I)
        if re.match(r"^(?:Evidence|\u4f9d\u636e)[\uff1a:]", line, re.I):
            result.append(("evidence", line))
        elif marker or label or not has_cue:
            result.append(("cue", line[label.end():] if label else line))
            has_cue = True
        else:
            result.append(("continuation", line))
    return result


def create_overlay(config, on_close, on_documents_added=None,
                   on_documents_removed=None, on_text_submitted=None,
                   engine_label="Codex"):
    # Console mode and pure tests do not load AppKit.
    from .native_overlay import NativeOverlayWindow

    return NativeOverlayWindow(
        config, on_close, on_documents_added, on_documents_removed,
        on_text_submitted, engine_label,
    )
