"""nebula v1.6 Phase 2.1 — TextChunker + BgeM3Embedder for L3 Cold Memory."""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# Regex to strip SF_LOG comments: <!-- SF_LOG ... -->
_SF_LOG_STRIP_PATTERN = re.compile(r"<!--\s*SF_LOG\s+.*?-->", re.DOTALL)


@dataclass
class TextChunk:
    """A single embeddable unit of scene text with metadata."""
    chunk_id: str                  # e.g. "ch3_sc5_p2"
    project_id: str
    chapter_number: int
    scene_number: int
    chunk_index: int               # 0-based within scene
    text: str                      # cleaned text (~200-300 chars)
    metadata: dict = field(default_factory=dict)


class TextChunker:
    """
    Splits scene draft text into embeddable chunks.

    Strategy:
    - Split by double-newline (paragraph boundaries)
    - Target: 250 Chinese chars per chunk
    - Paragraphs < 100 chars merged with next
    - Paragraphs > 500 chars split at sentence boundaries (。！？)
    - SF_LOG comments stripped before embedding
    """

    MIN_CHUNK_LENGTH = 100
    TARGET_CHUNK_LENGTH = 250
    MAX_CHUNK_LENGTH = 500

    # Sentence boundary characters for splitting long paragraphs
    _SENTENCE_BOUNDARY = re.compile(r"[。！？；\n]")

    def split_scene(
        self,
        text: str,
        project_id: str,
        chapter_number: int,
        scene_number: int,
    ) -> list[TextChunk]:
        """Split a single scene draft into chunks."""
        cleaned = _SF_LOG_STRIP_PATTERN.sub("", text)

        # Split into paragraphs by double newline
        raw_paragraphs = [p.strip() for p in cleaned.split("\n\n") if p.strip()]
        if not raw_paragraphs:
            return []

        # Merge short paragraphs with neighbors
        paragraphs = self._merge_short_paragraphs(raw_paragraphs)

        # Split long paragraphs into sentences
        chunks: list[TextChunk] = []
        chunk_idx = 0
        for para in paragraphs:
            sub_chunks = self._split_long_paragraph(para)
            for sub in sub_chunks:
                chunk_id = f"ch{chapter_number}_sc{scene_number}_p{chunk_idx}"
                chunks.append(TextChunk(
                    chunk_id=chunk_id,
                    project_id=project_id,
                    chapter_number=chapter_number,
                    scene_number=scene_number,
                    chunk_index=chunk_idx,
                    text=sub,
                    metadata={"source": f"chapter_{chapter_number}_scene_{scene_number}"},
                ))
                chunk_idx += 1

        return chunks

    def split_chapter(
        self,
        scene_texts: list[str],
        project_id: str,
        chapter_number: int,
    ) -> list[TextChunk]:
        """Split all scenes in a chapter. scene_texts index maps to scene_number."""
        all_chunks: list[TextChunk] = []
        for scene_idx, text in enumerate(scene_texts, start=1):
            chunks = self.split_scene(text, project_id, chapter_number, scene_idx)
            all_chunks.extend(chunks)
        return all_chunks

    def _merge_short_paragraphs(self, paragraphs: list[str]) -> list[str]:
        """Merge consecutive paragraphs < MIN_CHUNK_LENGTH with their neighbors."""
        if len(paragraphs) <= 1:
            return paragraphs

        merged: list[str] = []
        buffer = ""

        for para in paragraphs:
            if len(para) < self.MIN_CHUNK_LENGTH and merged:
                # Append to previous paragraph
                merged[-1] = merged[-1] + "\n" + para
            elif buffer:
                buffer = buffer + "\n" + para
                if len(buffer) >= self.MIN_CHUNK_LENGTH:
                    merged.append(buffer)
                    buffer = ""
            elif len(para) < self.MIN_CHUNK_LENGTH:
                buffer = para
            else:
                merged.append(para)

        if buffer:
            if merged:
                merged[-1] = merged[-1] + "\n" + buffer
            else:
                merged.append(buffer)

        return merged

    def _split_long_paragraph(self, paragraph: str) -> list[str]:
        """Split a paragraph > MAX_CHUNK_LENGTH at sentence boundaries."""
        if len(paragraph) <= self.MAX_CHUNK_LENGTH:
            return [paragraph] if paragraph.strip() else []

        parts = self._SENTENCE_BOUNDARY.split(paragraph)
        result: list[str] = []
        buffer = ""

        for part in parts:
            part = part.strip()
            if not part:
                continue

            candidate = buffer + "。" + part if buffer else part

            if len(candidate) > self.MAX_CHUNK_LENGTH and buffer:
                result.append(buffer + "。")
                buffer = part
            else:
                buffer = candidate

        if buffer:
            result.append(buffer)

        return result if result else [paragraph]


class BgeM3Embedder:
    """
    BAAI/bge-m3 embedding model wrapper for Chinese/English text.

    Produces 1024-dimensional dense vectors.
    Lazy loading — model downloaded on first use.
    Graceful degradation: sets _available=False on any failure.
    """

    MODEL_NAME = "BAAI/bge-m3"
    VECTOR_DIM = 1024
    BATCH_SIZE = 32

    # Model cache root. Renamed storyforge → nebula with the project rename;
    # the legacy path is still honoured when it holds the only copy of the
    # bge-m3 weights, so existing machines don't re-download ~3GB.
    _CACHE_ROOT = Path.home() / ".cache"
    _LEGACY_CACHE_DIR = _CACHE_ROOT / "storyforge" / "models"
    _CACHE_DIR = _CACHE_ROOT / "nebula" / "models"

    @classmethod
    def _holds_model(cls, cache_dir: Path) -> bool:
        """True when the dir already contains the bge-m3 snapshot.

        Checking the snapshot rather than the parent dir matters: a dir can
        exist from a partial/failed download while still needing a full
        re-download.
        """
        return (cache_dir / f"models--{cls.MODEL_NAME.replace('/', '--')}").exists()

    def __init__(self, cache_dir: Optional[Path] = None):
        self._model = None  # SentenceTransformer instance
        self._available: Optional[bool] = None  # None = not yet checked
        if cache_dir is not None:
            self._cache_dir = cache_dir
        elif (
            self._holds_model(self._CACHE_DIR)
            or not self._holds_model(self._LEGACY_CACHE_DIR)
        ):
            self._cache_dir = self._CACHE_DIR
        else:
            # Only the legacy dir has the model — keep using it in place.
            self._cache_dir = self._LEGACY_CACHE_DIR

    @property
    def available(self) -> bool:
        if self._available is None:
            self.ensure_loaded()
        return self._available

    def ensure_loaded(self) -> None:
        """Load model if not already loaded. Sets _available=False on failure."""
        if self._model is not None:
            return
        if self._available is False:
            return

        try:
            from sentence_transformers import SentenceTransformer

            logger.info("Loading embedding model %s...", self.MODEL_NAME)
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            self._model = SentenceTransformer(
                self.MODEL_NAME,
                cache_folder=str(self._cache_dir),
            )
            # Warm-up inference to verify model works
            _ = self._model.encode(["测试"], show_progress_bar=False)
            self._available = True
            logger.info("Embedding model %s loaded (dim=%d)", self.MODEL_NAME, self.VECTOR_DIM)
        except Exception as e:
            logger.warning("BgeM3Embedder unavailable: %s", e)
            self._model = None
            self._available = False

    def embed(self, texts: list[str]) -> np.ndarray:
        """
        Batch embed texts. Returns [N, 1024] float32 array.
        Returns empty (0, 1024) array if unavailable.
        """
        if not self.available or self._model is None:
            return np.empty((0, self.VECTOR_DIM), dtype=np.float32)

        if not texts:
            return np.empty((0, self.VECTOR_DIM), dtype=np.float32)

        embeddings = self._model.encode(
            texts,
            batch_size=self.BATCH_SIZE,
            show_progress_bar=False,
            normalize_embeddings=True,
        )
        if embeddings.ndim == 1:
            embeddings = embeddings.reshape(1, -1)
        return embeddings.astype(np.float32)

    def embed_query(self, query: str) -> np.ndarray:
        """
        Embed a single query string. Returns [1024] float32 array.
        Returns zero vector if unavailable.
        """
        result = self.embed([query])
        if result.shape[0] == 0:
            return np.zeros(self.VECTOR_DIM, dtype=np.float32)
        return result[0]
