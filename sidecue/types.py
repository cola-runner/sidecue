from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceDocument:
    path: str
    text: str


@dataclass(frozen=True)
class SourceChunk:
    chunk_id: str
    source_path: str
    text: str

    @property
    def source_name(self) -> str:
        return self.source_path.rsplit("/", maxsplit=1)[-1]


@dataclass(frozen=True)
class RetrievalHit:
    chunk: SourceChunk
    score: float

