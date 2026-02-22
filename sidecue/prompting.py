from __future__ import annotations

from .types import RetrievalHit


def _trim(text: str, limit: int = 240) -> str:
    value = " ".join(text.split())
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "..."


def build_generation_prompt(
    system_prompt: str,
    transcript: str,
    hits: list[RetrievalHit],
) -> str:
    if hits:
        refs = []
        for idx, hit in enumerate(hits, start=1):
            refs.append(f"[{idx}] {hit.chunk.source_name}: {_trim(hit.chunk.text)}")
        references = "\n".join(refs)
    else:
        references = "[No matching sources]"

    return (
        f"{system_prompt}\n\n"
        f"[CURRENT INPUT]\n{transcript}\n\n"
        f"[RELEVANT SOURCES]\n{references}\n\n"
        "Use this output format:\n"
        "1. Prompt: ...\n"
        "   Evidence: ...\n"
        "2. Prompt: ...\n"
        "   Evidence: ...\n"
    )
