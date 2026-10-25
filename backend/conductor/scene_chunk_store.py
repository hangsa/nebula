"""Per-scene chunk persistence for real-time writing stream (v1.10 Direction B).

Each (project_id, chapter_number, scene_number) tuple owns one .jsonl file at:
  <projects_dir>/<project_id>/autopilot/chunks/ch{NN}_scene_{MM}.jsonl

Every flush of >=50 chars OR 80ms writes one JSON line:
  {"seq": <int>, "chapter_number": <int>, "scene_number": <int>,
   "text": <str>, "created_at": <float>}

Atomicity: chunks are <= 50 chars (UTF-8 <= 150 bytes), well below POSIX PIPE_BUF
(4096 bytes). Opening with mode "a" gives O_APPEND | O_CREAT | O_WRONLY; the
kernel guarantees the offset-then-write pair is atomic so concurrent appends
from a single process cannot tear a line. Multi-process appends would still be
safe on POSIX; on Windows (not supported by nebula) the only mitigation is
a per-chunk threading.Lock — left out because deployment target is Linux/macOS.

Lifecycle:
  Executor._write_scene_stream()  →  store.clear() at start
                                   →  store.append(text) on each flush
                                   →  draft.md written (by _write_scene_chapter_stream)
                                   →  store.clear() after success (or on failure)
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List


@dataclass
class ChunkRecord:
    seq: int
    chapter_number: int
    scene_number: int
    text: str
    created_at: float


def chunk_path(projects_dir: Path, project_id: str, chapter: int, scene: int) -> Path:
    """Resolve the JSONL file path for a given scene's chunks.

    Format: ch03_scene_007.jsonl — zero-padded so a sorted directory listing
    matches numeric ordering (matters for debugging with `cat` / `less`).
    """
    return (
        Path(projects_dir) / project_id / "autopilot" / "chunks"
        / f"ch{chapter:02d}_scene_{scene:03d}.jsonl"
    )


class SceneChunkStore:
    """JSONL-backed per-scene chunk store."""

    _lock = threading.Lock()  # module-level; serialize O_APPEND writes across instances

    def __init__(self, projects_dir: Path, project_id: str,
                 chapter_number: int, scene_number: int) -> None:
        self._path = chunk_path(projects_dir, project_id, chapter_number, scene_number)
        self._chapter_number = chapter_number
        self._scene_number = scene_number
        self._next_seq = self._read_max_seq() + 1

    # --- Public API ----------------------------------------------------

    def append(self, text: str) -> ChunkRecord:
        """Append one chunk; assign next seq. Returns the record."""
        record = ChunkRecord(
            seq=self._next_seq,
            chapter_number=self._chapter_number,
            scene_number=self._scene_number,
            text=text,
            created_at=time.time(),
        )
        line = json.dumps(
            {
                "seq": record.seq,
                "chapter_number": record.chapter_number,
                "scene_number": record.scene_number,
                "text": record.text,
                "created_at": record.created_at,
            },
            ensure_ascii=False,
        )
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # mode "a" = O_APPEND | O_CREAT | O_WRONLY → atomic offset+write
            with self._path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            self._next_seq += 1
        return record

    def read_since(self, since_seq: int) -> List[ChunkRecord]:
        """Return all chunks with seq > since_seq, in append order.

        Used by the SSE endpoint on reconnect to replay missed chunks when the
        client sends Last-Event-ID. Returns [] if the file doesn't exist yet
        (normal at connection time, before any chunk has been written).
        """
        if not self._path.exists():
            return []
        records: List[ChunkRecord] = []
        with self._path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                if d["seq"] > since_seq:
                    records.append(ChunkRecord(
                        seq=d["seq"],
                        chapter_number=d["chapter_number"],
                        scene_number=d["scene_number"],
                        text=d["text"],
                        created_at=d["created_at"],
                    ))
        return records

    def total_text(self) -> str:
        """Concatenate every chunk's text in order."""
        if not self._path.exists():
            return ""
        out: List[str] = []
        with self._path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                out.append(d["text"])
        return "".join(out)

    def clear(self) -> None:
        """Delete the jsonl file. Idempotent. Called once draft.md is written,
        or on scene_failed."""
        if self._path.exists():
            self._path.unlink()
        # Reset internal counter so a *subsequent* append() starts at seq=1.
        self._next_seq = 1

    # --- Internal ------------------------------------------------------

    def _read_max_seq(self) -> int:
        """Scan the existing file (if any) for the largest seq, so seq is
        monotonic across SceneChunkStore instances constructed in the same
        process (e.g. if the executor is re-instantiated mid-flight)."""
        if not self._path.exists():
            return 0
        max_seq = 0
        with self._path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    max_seq = max(max_seq, int(json.loads(line)["seq"]))
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
        return max_seq
