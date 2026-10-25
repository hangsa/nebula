"""nebula v1.6 Phase 2.2 — BM25Index + HybridSearcher for L3 Cold Memory."""

import logging
import pickle
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from backend.memory_os.l3_chunker import TextChunk

logger = logging.getLogger(__name__)


@dataclass
class SearchHit:
    chunk_id: str
    score: float
    text: str
    chapter_number: int
    scene_number: int
    source: str  # "bm25" or "dense"


class BM25Index:
    """
    BM25 keyword search index over text chunks.

    Uses rank-bm25 Okapi BM25 with Chinese tokenization.
    Falls back to character bigrams if jieba unavailable.
    """

    def __init__(self):
        self._chunks: list[TextChunk] = []
        self._corpus: list[list[str]] = []
        self._model = None  # BM25Okapi instance
        self._available = True
        try:
            from rank_bm25 import BM25Okapi
            self._BM25Okapi = BM25Okapi
        except ImportError:
            self._BM25Okapi = None
            self._available = False
            logger.warning("BM25Index unavailable: rank_bm25 not installed")

    @property
    def is_built(self) -> bool:
        return self._model is not None and len(self._corpus) > 0

    def add_chunks(self, chunks: list[TextChunk]) -> None:
        """Add chunks and rebuild the BM25 index."""
        if not self._available:
            return
        tokenized = [self._tokenize(c.text) for c in chunks]
        self._chunks.extend(chunks)
        self._corpus.extend(tokenized)
        if self._corpus:
            self._model = self._BM25Okapi(self._corpus)

    def search(self, query: str, top_k: int = 20) -> list[SearchHit]:
        """Search for chunks matching the query. Returns empty list if index not built."""
        if not self._available or not self.is_built:
            return []

        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        scores = self._model.get_scores(query_tokens)
        # Get top_k indices sorted by score descending
        if len(scores) <= top_k:
            ranked = sorted(enumerate(scores), key=lambda x: -x[1])
        else:
            import numpy as np
            top_indices = np.argpartition(scores, -top_k)[-top_k:]
            ranked = sorted(
                [(i, scores[i]) for i in top_indices],
                key=lambda x: -x[1],
            )

        hits: list[SearchHit] = []
        for idx, score in ranked:
            if score <= 0:
                continue
            chunk = self._chunks[idx]
            hits.append(SearchHit(
                chunk_id=chunk.chunk_id,
                score=float(score),
                text=chunk.text,
                chapter_number=chunk.chapter_number,
                scene_number=chunk.scene_number,
                source="bm25",
            ))

        return hits[:top_k]

    def save_to_disk(self, path: Path) -> None:
        """Save index state to disk via pickle."""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "chunks": [(c.chunk_id, c.project_id, c.chapter_number, c.scene_number,
                        c.chunk_index, c.text, c.metadata) for c in self._chunks],
            "corpus": self._corpus,
        }
        tmp = path.with_suffix(".tmp")
        with open(tmp, "wb") as f:
            pickle.dump(data, f)
        tmp.replace(path)

    def load_from_disk(self, path: Path) -> bool:
        """Load index state from disk. Returns False if file missing or unavailable."""
        if not self._available:
            return False
        if not path.exists():
            return False

        try:
            with open(path, "rb") as f:
                data = pickle.load(f)

            self._chunks = []
            for item in data["chunks"]:
                self._chunks.append(TextChunk(
                    chunk_id=item[0], project_id=item[1],
                    chapter_number=item[2], scene_number=item[3],
                    chunk_index=item[4], text=item[5], metadata=item[6],
                ))
            self._corpus = data["corpus"]
            if self._corpus:
                self._model = self._BM25Okapi(self._corpus)
            return True
        except Exception as e:
            logger.warning("Failed to load BM25 index from %s: %s", path, e)
            return False

    def _tokenize(self, text: str) -> list[str]:
        """
        Tokenize Chinese text for BM25.

        Strategy: try jieba word segmentation, fallback to character bigrams.
        Filters single-character tokens and only-punctuation tokens.
        """
        # Try jieba if available
        try:
            import jieba
            tokens = list(jieba.cut(text))
            tokens = [t.strip() for t in tokens if len(t.strip()) >= 2]
            if tokens:
                return tokens
        except ImportError:
            pass

        # Fallback: character bigrams
        cleaned = re.sub(r"[^一-鿿㐀-䶿\w]", "", text)
        if len(cleaned) < 2:
            return [cleaned] if cleaned else []

        bigrams = [cleaned[i:i+2] for i in range(len(cleaned) - 1)]
        return bigrams


class HybridSearcher:
    """
    RRF (Reciprocal Rank Fusion) for combining BM25 and dense results.

    Algorithm:
    - For each chunk_id in the union of both result sets:
        rrf_score = sum(1.0 / (k + rank_i) for each set where chunk appears)
    - Sort by rrf_score descending, return top_k
    """

    def __init__(self, k: int = 60):
        self.k = k

    def fuse(
        self,
        bm25_results: list[SearchHit],
        dense_results: list[SearchHit],
        top_k: int = 10,
    ) -> list[SearchHit]:
        """Combine BM25 and dense results using RRF."""
        if not bm25_results and not dense_results:
            return []

        if not bm25_results:
            return dense_results[:top_k]
        if not dense_results:
            return bm25_results[:top_k]

        # Build chunk_id → best hit mapping
        hit_map: dict[str, SearchHit] = {}
        for h in bm25_results:
            if h.chunk_id not in hit_map or h.score > hit_map[h.chunk_id].score:
                hit_map[h.chunk_id] = h
        for h in dense_results:
            if h.chunk_id not in hit_map or h.score > hit_map[h.chunk_id].score:
                hit_map[h.chunk_id] = h

        # Compute RRF scores
        rrf_scores: dict[str, float] = {}

        for rank, hit in enumerate(bm25_results, start=1):
            rrf_scores[hit.chunk_id] = rrf_scores.get(hit.chunk_id, 0.0) + self._rrf_score(rank)

        for rank, hit in enumerate(dense_results, start=1):
            rrf_scores[hit.chunk_id] = rrf_scores.get(hit.chunk_id, 0.0) + self._rrf_score(rank)

        # Sort by RRF score
        ranked = sorted(rrf_scores.items(), key=lambda x: -x[1])

        fused: list[SearchHit] = []
        for chunk_id, rrf in ranked[:top_k]:
            hit = hit_map[chunk_id]
            fused.append(SearchHit(
                chunk_id=hit.chunk_id,
                score=rrf,
                text=hit.text,
                chapter_number=hit.chapter_number,
                scene_number=hit.scene_number,
                source="hybrid",
            ))

        return fused

    def _rrf_score(self, rank: int) -> float:
        return 1.0 / (self.k + rank)
