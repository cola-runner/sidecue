"""UI fixtures only: no speech source, document indexing, or model client."""
from __future__ import annotations

from dataclasses import replace
import time

from .config import UiConfig
from .overlay import OverlayUpdate, create_overlay


PREVIEW_TITLE = "Sidecue - Preview"
PREVIEW_TRANSCRIPT = "What are the constraints for development, design, and testing?"
PREVIEW_SUGGESTION = (
    "1. Prompt: 3 weeks for development\n"
    "   Evidence: Three-week delivery window. [1]\n"
    "2. Prompt: 2 days of design time\n"
    "   Evidence: Design availability is limited to two days. [1]\n"
    "3. Prompt: Test / production mismatch\n"
    "   Evidence: Test and production settings differ. [1]"
)
PREVIEW_REFERENCES = "[1] sample_notes.txt · Fictional sample"
PREVIEW_PROMPT = (
    "[UI PREVIEW / SAMPLE CONTENT]\n\n"
    "Generate short speaking prompts from the current input and source material. "
    "Include brief evidence for each prompt.\n\n"
    "[CURRENT INPUT]\n{transcript}\n\n"
    "[SOURCE 1]\nThe development window is three weeks. "
    "Design is available for two days this week. "
    "Test and production environments have different settings."
)


def run_ui_preview(config: UiConfig, duration_seconds: float | None = None) -> None:
    pending_timer: int | None = None
    paths = ["sample_notes.txt"]

    def result(text: str) -> OverlayUpdate:
        return OverlayUpdate(text, PREVIEW_SUGGESTION, PREVIEW_REFERENCES, PREVIEW_PROMPT.format(transcript=text))

    def submit(text: str) -> None:
        nonlocal pending_timer
        if pending_timer:
            window.cancel_call(pending_timer)
        window.publish(OverlayUpdate(text, "", "", PREVIEW_PROMPT.format(transcript=text), phase="generating"))

        def finish() -> None:
            nonlocal pending_timer
            pending_timer = None
            window.publish(result(text))

        pending_timer = window.call_later(2.5, finish)

    def add(selected: list[str]) -> None:
        paths.extend(path for path in selected if path not in paths)
        window.publish_document_list(paths)

    def remove(selected: list[str]) -> None:
        paths[:] = [path for path in paths if path not in selected]
        window.publish_document_list(paths)

    def close() -> None:
        if pending_timer:
            window.cancel_call(pending_timer)

    window = create_overlay(
        replace(config, title=PREVIEW_TITLE),
        on_close=close,
        on_text_submitted=submit,
        on_documents_added=add,
        on_documents_removed=remove,
        engine_label="Preview",
    )
    window.set_input_label("Sample input")
    window.publish_document_list(paths)
    window.publish(result(PREVIEW_TRANSCRIPT))
    deadline = time.monotonic() + duration_seconds if duration_seconds is not None else None
    window.run(should_close=lambda: deadline is not None and time.monotonic() >= deadline)
