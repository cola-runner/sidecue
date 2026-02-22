from __future__ import annotations

import unittest

from sidecue.retriever import KeywordRetriever, build_chunks
from sidecue.types import SourceDocument


class RetrieverTests(unittest.TestCase):
    def setUp(self) -> None:
        docs = [SourceDocument(path="demo.txt", text="alpha beta gamma")]
        chunks = build_chunks(docs, chunk_chars=200, overlap_chars=20)
        self.retriever = KeywordRetriever(chunks)

    def test_search_top_k_zero_returns_empty(self) -> None:
        self.assertEqual(self.retriever.search("alpha", top_k=0), [])

    def test_search_negative_top_k_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.retriever.search("alpha", top_k=-1)


if __name__ == "__main__":
    unittest.main()
