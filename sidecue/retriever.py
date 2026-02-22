from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from math import log
import re

from .types import RetrievalHit, SourceChunk, SourceDocument


TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    return TOKEN_PATTERN.findall(text.lower())


def build_chunks(
    docs: list[SourceDocument],
    chunk_chars: int,
    overlap_chars: int,
) -> list[SourceChunk]:
    if chunk_chars <= 0:
        raise ValueError("chunk_chars must be positive.")
    if overlap_chars < 0:
        raise ValueError("overlap_chars must not be negative.")
    if overlap_chars >= chunk_chars:
        overlap_chars = chunk_chars // 2

    chunks: list[SourceChunk] = []
    chunk_index = 0
    for doc in docs:
        text = doc.text.strip()
        if not text:
            continue
        start = 0
        while start < len(text):
            end = min(len(text), start + chunk_chars)
            fragment = text[start:end].strip()
            if fragment:
                chunks.append(
                    SourceChunk(
                        chunk_id=f"chunk-{chunk_index}",
                        source_path=doc.path,
                        text=fragment,
                    )
                )
                chunk_index += 1
            if end == len(text):
                break
            start = max(start + 1, end - overlap_chars)
    return chunks


@dataclass
class _ChunkStats:
    chunk: SourceChunk
    tf: Counter[str]
    length: int


class KeywordRetriever:
    def __init__(self, chunks: list[SourceChunk]) -> None:
        self._stats: list[_ChunkStats] = []
        self._idf: dict[str, float] = {}
        self._build(chunks)

    def _build(self, chunks: list[SourceChunk]) -> None:
        df: defaultdict[str, int] = defaultdict(int)
        for chunk in chunks:
            tokens = tokenize(chunk.text)
            tf = Counter(tokens)
            self._stats.append(_ChunkStats(chunk=chunk, tf=tf, length=max(len(tokens), 1)))
            for token in tf:
                df[token] += 1

        total_docs = max(len(self._stats), 1)
        for token, count in df.items():
            self._idf[token] = log((total_docs + 1) / (count + 1)) + 1.0

    def search(self, query: str, top_k: int = 3) -> list[RetrievalHit]:
        if top_k < 0:
            raise ValueError("top_k must not be negative.")
        if top_k == 0:
            return []

        query_tokens = tokenize(query)
        if not query_tokens or not self._stats:
            return []

        query_tf = Counter(query_tokens)
        hits: list[RetrievalHit] = []
        for stat in self._stats:
            score = 0.0
            for token, q_count in query_tf.items():
                if token not in stat.tf:
                    continue
                idf = self._idf.get(token, 1.0)
                score += min(q_count, stat.tf[token]) * idf
            if score > 0:
                normalized = score / (1.0 + 0.05 * stat.length)
                hits.append(RetrievalHit(chunk=stat.chunk, score=normalized))

        hits.sort(key=lambda x: x.score, reverse=True)
        return hits[:top_k]
