"""nebula v1.6 Phase 2.3 — L3ColdMemory: full-novel vector + keyword hybrid search."""

import json
import logging
from pathlib import Path
from typing import Optional

from backend.config import settings
from backend.memory_os.l3_chunker import TextChunker, BgeM3Embedder, TextChunk
from backend.memory_os.l3_bm25 import BM25Index, HybridSearcher, SearchHit

logger = logging.getLogger(__name__)

# Max chars for a formatted context string (~1K tokens for Chinese)
MAX_CONTEXT_CHARS = 2000


class L3ColdMemory:
    """
    L3 Cold Memory — full-novel vector + keyword hybrid search.

    Storage: projects/{project_id}/memory/l3/
      ├── qdrant/              # Qdrant local file-based storage
      ├── bm25_index.pkl       # Serialized BM25 index
      └── chunk_manifest.json   # Chunk metadata manifest

    Key behaviors:
    - Optional: if Qdrant or embedder unavailable, _available=False,
      search() returns "". System must work without L3.
    - Indexed at chapter advancement (not per-scene).
    - Retrieval is tier_0 (deterministic, zero LLM).
    - Search query: constructed from scene parameters.
    """

    VECTOR_DIM = 1024
    COLLECTION_PREFIX = "nebula"
    # Pre-rename prefix. Projects indexed before the StoryForge → nebula
    # rename still hold their vectors under the old collection name; it is
    # adopted instead of creating a fresh empty collection.
    LEGACY_COLLECTION_PREFIX = "storyforge"

    def __init__(
        self,
        project_id: str,
        projects_dir: Optional[Path] = None,
    ):
        self.project_id = project_id
        self.projects_dir = Path(projects_dir) if projects_dir else settings.projects_dir
        self._project_dir = self.projects_dir / project_id
        self._l3_dir = self._project_dir / "memory" / "l3"
        self._qdrant_dir = self._l3_dir / "qdrant"

        self._chunker = TextChunker()
        self._embedder = BgeM3Embedder()
        self._bm25 = BM25Index()
        self._searcher = HybridSearcher(k=60)
        self._available = False
        self._qdrant_client = None
        self._collection_name = f"{self.COLLECTION_PREFIX}_{project_id}"
        self._legacy_collection_name = f"{self.LEGACY_COLLECTION_PREFIX}_{project_id}"

        self._init_qdrant()
        if self._available:
            self._ensure_dirs()
            # Try loading existing BM25 index
            bm25_path = self._l3_dir / "bm25_index.pkl"
            if bm25_path.exists():
                self._bm25.load_from_disk(bm25_path)

    @property
    def available(self) -> bool:
        return self._available

    def _init_qdrant(self) -> None:
        """Initialize local file-based Qdrant. Sets _available=False on any failure."""
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.models import Distance, VectorParams

            self._qdrant_client = QdrantClient(path=str(self._qdrant_dir))

            # Create collection if not exists
            try:
                self._qdrant_client.get_collection(self._collection_name)
            except Exception:
                # Pre-rename collection present? Adopt it so existing
                # projects keep their L3 index instead of starting empty.
                try:
                    self._qdrant_client.get_collection(self._legacy_collection_name)
                    self._collection_name = self._legacy_collection_name
                except Exception:
                    pass
            if not self._qdrant_client.collection_exists(self._collection_name):
                self._qdrant_client.create_collection(
                    collection_name=self._collection_name,
                    vectors_config=VectorParams(
                        size=self.VECTOR_DIM,
                        distance=Distance.COSINE,
                    ),
                )

            self._available = True
            logger.info(
                "L3ColdMemory initialized: project=%s collection=%s",
                self.project_id, self._collection_name,
            )
        except ImportError:
            logger.warning("L3ColdMemory unavailable: qdrant-client not installed")
            self._available = False
        except Exception as e:
            logger.warning("L3ColdMemory unavailable: %s", e)
            self._available = False

    def _ensure_dirs(self) -> None:
        self._l3_dir.mkdir(parents=True, exist_ok=True)

    def search(
        self,
        query: str,
        top_k: int = 10,
        chapter_number: Optional[int] = None,
    ) -> str:
        """
        Hybrid search for relevant historical text chunks.

        Args:
            query: Search query (from scene_goal + scene_conflict + character names)
            top_k: Number of fused results to return
            chapter_number: Optional filter — exclude current chapter from results

        Returns:
            Formatted context string, or "" if unavailable or no results.
        """
        if not self._available:
            return ""

        if not query.strip():
            return ""

        # 1. Generate query embedding
        query_vec = self._embedder.embed_query(query)
        if not query_vec.any():
            return ""

        # 2. Dense search via Qdrant
        dense_hits = self._qdrant_search(query_vec, limit=20, chapter_number=chapter_number)

        # 3. BM25 keyword search
        bm25_hits = self._bm25.search(query, top_k=20)

        # 4. RRF fusion
        fused = self._searcher.fuse(bm25_hits, dense_hits, top_k=top_k)

        # 5. Format context string
        return self._format_context(fused)

    def index_chapter(
        self,
        chapter_number: int,
        scene_drafts: list[str],
    ) -> int:
        """
        Index all scenes in a chapter into L3.

        Steps:
        1. Chunk all scene texts
        2. Generate dense embeddings
        3. Upsert into Qdrant
        4. Add to BM25 index
        5. Save BM25 index + manifest

        Returns number of chunks indexed (0 if unavailable).
        """
        if not self._available:
            return 0

        # 1. Chunk
        chunks = self._chunker.split_chapter(scene_drafts, self.project_id, chapter_number)
        if not chunks:
            logger.info("L3 index: no chunks produced for chapter %d", chapter_number)
            return 0

        # 2. Embed
        texts = [c.text for c in chunks]
        embeddings = self._embedder.embed(texts)
        if embeddings.shape[0] == 0:
            logger.warning("L3 index: embedding failed for chapter %d", chapter_number)
            return 0

        # 3. Upsert into Qdrant
        self._qdrant_upsert(chunks, embeddings)

        # 4. Add to BM25 index
        self._bm25.add_chunks(chunks)

        # 5. Persist
        self._bm25.save_to_disk(self._l3_dir / "bm25_index.pkl")
        self._save_manifest(chunks)

        logger.info(
            "L3 indexed chapter %d: %d chunks from %d scenes",
            chapter_number, len(chunks), len(scene_drafts),
        )
        return len(chunks)

    def _qdrant_search(
        self,
        query_vec,
        limit: int = 20,
        chapter_number: Optional[int] = None,
    ) -> list[SearchHit]:
        """Execute Qdrant vector search and convert to SearchHits."""
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        try:
            qfilter = None
            if chapter_number is not None:
                qfilter = Filter(
                    must_not=[
                        FieldCondition(
                            key="chapter_number",
                            match=MatchValue(value=chapter_number),
                        ),
                    ],
                )

            results = self._qdrant_client.search(
                collection_name=self._collection_name,
                query_vector=query_vec.tolist(),
                limit=limit,
                query_filter=qfilter,
                with_payload=True,
            )

            hits: list[SearchHit] = []
            for r in results:
                payload = r.payload or {}
                hits.append(SearchHit(
                    chunk_id=payload.get("chunk_id", str(r.id)),
                    score=r.score,
                    text=payload.get("text", ""),
                    chapter_number=payload.get("chapter_number", 0),
                    scene_number=payload.get("scene_number", 0),
                    source="dense",
                ))
            return hits

        except Exception as e:
            logger.warning("L3 Qdrant search failed: %s", e)
            return []

    def _qdrant_upsert(
        self,
        chunks: list[TextChunk],
        embeddings,
    ) -> None:
        """Batch upsert chunks + embeddings into Qdrant."""
        from qdrant_client.models import PointStruct

        points = []
        for i, chunk in enumerate(chunks):
            points.append(PointStruct(
                id=i + self._get_next_point_id(len(points)),
                vector=embeddings[i].tolist(),
                payload={
                    "chunk_id": chunk.chunk_id,
                    "project_id": chunk.project_id,
                    "chapter_number": chunk.chapter_number,
                    "scene_number": chunk.scene_number,
                    "chunk_index": chunk.chunk_index,
                    "text": chunk.text,
                },
            ))

        self._qdrant_client.upsert(
            collection_name=self._collection_name,
            points=points,
            wait=True,
        )

    def _get_next_point_id(self, offset: int = 0) -> int:
        """Get the next Qdrant point ID by counting existing points."""
        try:
            info = self._qdrant_client.count(
                collection_name=self._collection_name, exact=True,
            )
            return info.count + offset
        except Exception:
            return offset

    def _format_context(self, hits: list[SearchHit]) -> str:
        """Format search hits into a compact context string for the Writer."""
        if not hits:
            return ""

        lines = ["【相关历史文本片段】"]
        for hit in hits:
            # Truncate text to ~200 chars for context
            snippet = hit.text[:200].replace("\n", " ")
            lines.append(
                f"  - [第{hit.chapter_number}章 第{hit.scene_number}幕] "
                f"{snippet}"
            )

        # Append chapter source summary
        chapters_seen: dict[int, int] = {}
        for hit in hits:
            ch = hit.chapter_number
            chapters_seen[ch] = chapters_seen.get(ch, 0) + 1
        sorted_chapters = sorted(chapters_seen.keys())
        chapter_hits = ", ".join(
            f"第{ch}章({chapters_seen[ch]}条)" for ch in sorted_chapters
        )
        lines.append(f"\n来源章节: {chapter_hits}")

        result = "\n".join(lines)
        # Enforce max context chars
        if len(result) > MAX_CONTEXT_CHARS:
            result = result[:MAX_CONTEXT_CHARS] + "\n  ..."
        return result

    def _save_manifest(self, chunks: list[TextChunk]) -> None:
        """Save chunk metadata manifest to disk."""
        manifest = []
        for c in chunks:
            manifest.append({
                "chunk_id": c.chunk_id,
                "chapter_number": c.chapter_number,
                "scene_number": c.scene_number,
                "chunk_index": c.chunk_index,
                "text_preview": c.text[:100],
            })

        path = self._l3_dir / "chunk_manifest.json"
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        tmp.replace(path)

    def clear_index(self) -> None:
        """Clear all L3 data for this project (useful for testing/reset)."""
        if not self._available:
            return

        try:
            self._qdrant_client.delete_collection(self._collection_name)
            self._qdrant_client.create_collection(
                collection_name=self._collection_name,
                vectors_config=self._qdrant_client.http.vectors_config,
            )
        except Exception:
            pass

        import shutil
        for f in self._l3_dir.glob("*"):
            if f.is_file():
                f.unlink()
        self._bm25 = BM25Index()
