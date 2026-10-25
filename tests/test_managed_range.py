"""Tests for managed mode chapter range config and helpers."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.main import app
from backend.models.autopilot_session import ManagedStartConfig
from backend.conductor.autopilot_runner_async import (
    clear_chapter_drafts,
    clear_checkpoint_for_chapter,
    compute_range_defaults,
    drop_chapter_queue_items,
    enqueue_chapter_scenes,
    find_latest_completed_chapter,
    regenerate_chapter,
    reset_chapter_progress,
    seed_queue,
)
from backend.utils.file_manager import FileManager
from backend.config import settings as _settings


class TestManagedStartConfigRange:
    def test_scope_all_planned_default_is_accepted(self):
        cfg = ManagedStartConfig()
        assert cfg.scope == "all_planned"
        assert cfg.start_chapter is None
        assert cfg.end_chapter is None

    def test_scope_range_requires_start_and_end(self):
        with pytest.raises(ValidationError) as ei:
            ManagedStartConfig(scope="range")
        assert "start_chapter" in str(ei.value) or "end_chapter" in str(ei.value)

    def test_scope_range_accepts_valid_range(self):
        cfg = ManagedStartConfig(scope="range", start_chapter=5, end_chapter=15)
        assert cfg.start_chapter == 5
        assert cfg.end_chapter == 15

    def test_scope_range_rejects_end_less_than_start(self):
        with pytest.raises(ValidationError) as ei:
            ManagedStartConfig(scope="range", start_chapter=10, end_chapter=5)
        assert "end_chapter" in str(ei.value)

    def test_scope_range_rejects_start_less_than_one(self):
        with pytest.raises(ValidationError) as ei:
            ManagedStartConfig(scope="range", start_chapter=0, end_chapter=5)
        assert "start_chapter" in str(ei.value)

    def test_scope_range_allows_equal_start_and_end(self):
        """A single-chapter range is valid (degenerate case)."""
        cfg = ManagedStartConfig(scope="range", start_chapter=7, end_chapter=7)
        assert cfg.start_chapter == cfg.end_chapter == 7

    def test_next_chapter_scope_is_rejected(self):
        """Breaking change: scope='next_chapter' is gone."""
        with pytest.raises(ValidationError):
            ManagedStartConfig(scope="next_chapter", start_chapter=1, end_chapter=1)

    def test_cadence_policy_notify_literals_unchanged(self):
        """Other fields keep their old behavior."""
        cfg = ManagedStartConfig(scope="range", start_chapter=1, end_chapter=10,
                                  cadence="fast", policy="ask", notify="all")
        assert cfg.cadence == "fast"
        assert cfg.policy == "ask"
        assert cfg.notify == "all"


def _chapter_progress(*scene_statuses: tuple[int, str]) -> list[dict]:
    return [
        {"scene_number": n, "status": s} for n, s in scene_statuses
    ]


def _chapter_outline(*scene_numbers: int) -> dict:
    return {
        "chapter_number": 0,  # overwritten by caller
        "scene_plan": [{"scene_number": n, "goal": "", "conflict": ""} for n in scene_numbers],
    }


class TestComputeRangeDefaults:
    def test_no_completed_chapters(self):
        start, end = compute_range_defaults(outline_max=20, latest_completed=None)
        assert (start, end) == (1, 11)  # 1+10

    def test_with_completed_chapters(self):
        start, end = compute_range_defaults(outline_max=20, latest_completed=7)
        assert (start, end) == (8, 18)

    def test_outline_smaller_than_default_span(self):
        start, end = compute_range_defaults(outline_max=5, latest_completed=None)
        assert (start, end) == (1, 5)  # end clamped to outline_max

    def test_completed_at_outline_max(self):
        start, end = compute_range_defaults(outline_max=10, latest_completed=10)
        # start=11 > outline_max=10 → caller will surface "all done" error
        assert (start, end) == (11, 10)  # end clamped to outline_max=10

    def test_latest_completed_zero(self):
        """Defensive: latest_completed=0 is treated as 'nothing done'."""
        start, end = compute_range_defaults(outline_max=15, latest_completed=0)
        assert (start, end) == (1, 11)


class TestFindLatestCompletedChapter:
    def test_returns_max_when_chapters_complete(self):
        progress = {
            "chapters": [
                {"chapter_number": n, "status": "completed",
                 "scenes": _chapter_progress((1, "completed"), (2, "completed"))}
                for n in [1, 2, 3, 4, 5]
            ]
        }
        outline = {
            "chapters": [
                _chapter_outline(1, 2) | {"chapter_number": n}
                for n in [1, 2, 3, 4, 5]
            ]
        }
        assert find_latest_completed_chapter(progress, outline) == 5

    def test_returns_none_when_no_chapter_complete(self):
        progress = {"chapters": []}
        outline = {"chapters": [_chapter_outline(1, 2) | {"chapter_number": 1}]}
        assert find_latest_completed_chapter(progress, outline) is None

    def test_ignores_partial_chapters(self):
        progress = {
            "chapters": [
                {"chapter_number": 1, "status": "completed",
                 "scenes": _chapter_progress((1, "completed"), (2, "completed"))},
                {"chapter_number": 2, "status": "in_progress",
                 "scenes": _chapter_progress((1, "completed"), (2, "in_progress"))},
                {"chapter_number": 3, "status": "completed",
                 "scenes": _chapter_progress((1, "completed"), (2, "completed"))},
            ]
        }
        outline = {
            "chapters": [
                _chapter_outline(1, 2) | {"chapter_number": n}
                for n in [1, 2, 3]
            ]
        }
        assert find_latest_completed_chapter(progress, outline) == 3

    def test_handles_gaps(self):
        """ch2 not in progress at all — should not block ch3 from being the max."""
        progress = {
            "chapters": [
                {"chapter_number": 1, "status": "completed",
                 "scenes": _chapter_progress((1, "completed"))},
                {"chapter_number": 3, "status": "completed",
                 "scenes": _chapter_progress((1, "completed"))},
            ]
        }
        outline = {
            "chapters": [
                _chapter_outline(1) | {"chapter_number": n}
                for n in [1, 2, 3]
            ]
        }
        assert find_latest_completed_chapter(progress, outline) == 3

    def test_force_passed_counts_as_done(self):
        progress = {
            "chapters": [
                {"chapter_number": 1, "status": "completed",
                 "scenes": _chapter_progress((1, "force_passed"))}
            ]
        }
        outline = {
            "chapters": [_chapter_outline(1) | {"chapter_number": 1}]
        }
        assert find_latest_completed_chapter(progress, outline) == 1


@pytest.fixture
def project_layout(tmp_path: Path):
    """Build a minimal project layout with progress.json + chapters/ + session queue."""
    proj = tmp_path / "p_regen"
    proj.mkdir()
    (proj / "project.json").write_text(json.dumps({"id": "p_regen"}))
    # progress.json with ch5 having 3 scenes all completed
    progress = {
        "current_chapter": 8,
        "chapters": [
            {
                "chapter_number": 5,
                "status": "completed",
                "scenes": [
                    {"scene_number": 1, "status": "completed", "retry_count": 2,
                     "coherence_score": 90},
                    {"scene_number": 2, "status": "completed", "retry_count": 0,
                     "coherence_score": 85},
                    {"scene_number": 3, "status": "completed", "retry_count": 0,
                     "coherence_score": 95},
                ],
            },
            {
                "chapter_number": 6,
                "status": "completed",
                "scenes": [
                    {"scene_number": 1, "status": "completed", "retry_count": 0,
                     "coherence_score": 80},
                ],
            },
        ],
    }
    (proj / "progress.json").write_text(json.dumps(progress))
    # chapters/ directory with ch05 + ch06 drafts (and a draft for another chapter
    # that should NOT be touched)
    chapters_dir = proj / "chapters"
    chapters_dir.mkdir()
    (chapters_dir / "ch05_scene_001_draft.md").write_text("draft 1")
    (chapters_dir / "ch05_scene_002_draft.md").write_text("draft 2")
    (chapters_dir / "ch05_scene_003_draft.md").write_text("draft 3")
    (chapters_dir / "ch06_scene_001_draft.md").write_text("ch6 draft")
    (chapters_dir / "ch07_scene_001_draft.md").write_text("ch7 draft (untouched)")
    return proj


class TestResetChapterProgress:
    def test_resets_status_and_clears_metadata(self, project_layout, tmp_path):
        fm = FileManager(tmp_path)
        reset_chapter_progress(fm, "p_regen", 5)
        progress = json.loads((project_layout / "progress.json").read_text())
        ch5 = next(c for c in progress["chapters"] if c["chapter_number"] == 5)
        assert ch5["status"] == "pending"
        for s in ch5["scenes"]:
            assert s["status"] == "pending"
            assert s["retry_count"] == 0
            assert s["coherence_score"] is None

    def test_does_not_touch_other_chapters(self, project_layout, tmp_path):
        fm = FileManager(tmp_path)
        reset_chapter_progress(fm, "p_regen", 5)
        progress = json.loads((project_layout / "progress.json").read_text())
        ch6 = next(c for c in progress["chapters"] if c["chapter_number"] == 6)
        assert ch6["status"] == "completed"
        assert ch6["scenes"][0]["status"] == "completed"


class TestClearChapterDrafts:
    def test_deletes_only_target_chapter_drafts(self, project_layout):
        clear_chapter_drafts("p_regen", 5, project_layout)
        chapters_dir = project_layout / "chapters"
        # ch05 drafts gone
        remaining = sorted(p.name for p in chapters_dir.iterdir())
        assert "ch05_scene_001_draft.md" not in remaining
        assert "ch05_scene_002_draft.md" not in remaining
        assert "ch05_scene_003_draft.md" not in remaining
        # ch06 + ch07 drafts untouched
        assert "ch06_scene_001_draft.md" in remaining
        assert "ch07_scene_001_draft.md" in remaining

    def test_no_op_when_chapter_dir_missing(self, project_layout):
        """Defensive: clear_chapter_drafts on a chapter that has no drafts
        must not raise."""
        clear_chapter_drafts("p_regen", 99, project_layout)
        # No exception; other files still there
        assert (project_layout / "chapters" / "ch07_scene_001_draft.md").exists()


class TestDropChapterQueueItems:
    def test_drops_matching_items_keeps_others(self, project_layout):
        mgr = MagicMock()
        # Snapshot returns queue with items from ch5, ch6, ch7
        snapshot = MagicMock()
        snapshot.queue = [
            MagicMock(id="write-5-1"),
            MagicMock(id="write-5-2"),
            MagicMock(id="write-5-3"),
            MagicMock(id="write-6-1"),
            MagicMock(id="write-7-1"),
        ]
        mgr.load.return_value = snapshot
        # Track added QueueItems
        added: list = []
        def fake_add(item):
            added.append(item)
            return mgr
        mgr.add_queue.side_effect = fake_add
        # We only need to drop; nothing added in this test
        drop_chapter_queue_items(mgr, 5)
        # mgr.add_queue should have been called 3 times — once for each removed item,
        # restoring the queue without ch5 entries.
        # The exact mechanism is implementation detail; what matters: ch5 ids are gone.
        # Verify by checking which ids are present in the final queue snapshot.
        final_queue_ids = {item.id for item in snapshot.queue if item.id not in added}
        # ch5 ids should be absent
        assert "write-5-1" not in final_queue_ids
        assert "write-5-2" not in final_queue_ids
        assert "write-5-3" not in final_queue_ids
        # ch6, ch7 ids preserved
        assert "write-6-1" in final_queue_ids
        assert "write-7-1" in final_queue_ids


class TestEnqueueChapterScenes:
    def test_enqueues_one_item_per_scene_in_plan(self, project_layout):
        mgr = MagicMock()
        added: list = []
        def fake_add(item):
            added.append(item)
            return mgr
        mgr.add_queue.side_effect = fake_add
        scene_plan = [{"scene_number": n} for n in [1, 2, 3]]
        enqueue_chapter_scenes(mgr, 5, scene_plan)
        ids = sorted(item.id for item in added)
        assert ids == ["write-5-1", "write-5-2", "write-5-3"]
        # Priorities follow row-major
        priorities = [item.priority for item in added]
        assert priorities == [5001, 5002, 5003]  # 5*1000+scene


class TestRegenerateChapterOrchestrator:
    def test_full_pipeline_resets_clears_drops_reenqueues(self, project_layout, tmp_path):
        """The orchestrator wires all four steps together."""
        mgr = MagicMock()
        added: list = []
        snapshot = MagicMock()
        # queue has 3 ch5 items + 1 ch6 item (untouched) + 1 ch7 item (untouched)
        snapshot.queue = [
            MagicMock(id="write-5-1"),
            MagicMock(id="write-5-2"),
            MagicMock(id="write-5-3"),
            MagicMock(id="write-6-1"),
            MagicMock(id="write-7-1"),
        ]
        mgr.load.return_value = snapshot
        def fake_add(item):
            added.append(item)
            return mgr
        mgr.add_queue.side_effect = fake_add

        scene_plan = [{"scene_number": n} for n in [1, 2, 3]]
        fm = FileManager(tmp_path)
        regenerate_chapter(fm, "p_regen", mgr, 5, scene_plan, project_layout)

        # 1. progress reset
        progress = json.loads((project_layout / "progress.json").read_text())
        ch5 = next(c for c in progress["chapters"] if c["chapter_number"] == 5)
        assert ch5["status"] == "pending"
        # 2. drafts cleared
        chapters_dir = project_layout / "chapters"
        remaining = {p.name for p in chapters_dir.iterdir()}
        assert "ch05_scene_001_draft.md" not in remaining
        assert "ch05_scene_002_draft.md" not in remaining
        assert "ch05_scene_003_draft.md" not in remaining
        assert "ch07_scene_001_draft.md" in remaining  # untouched
        # 3. queue: ch5 ids removed, then 3 fresh ch5 ids added
        new_ids = [item.id for item in added if item.id.startswith("write-5-")]
        assert sorted(new_ids) == ["write-5-1", "write-5-2", "write-5-3"]


class TestClearCheckpointForChapter:
    def test_removes_checkpoint_when_chapter_matches(self, tmp_path):
        proj = tmp_path / "p_ckpt"
        proj.mkdir()
        (proj / "project.json").write_text(json.dumps({"id": "p_ckpt"}))
        checkpoint = {
            "project_id": "p_ckpt",
            "pipeline_stage": "scene_review",
            "current_chapter": 5,
            "current_scene": 3,
            "l0_snapshot": {},
            "registry_snapshots": {},
            "character_states": [],
            "timestamp": "2026-08-19T00:00:00Z",
        }
        (proj / ".nebula_checkpoint.json").write_text(json.dumps(checkpoint))

        clear_checkpoint_for_chapter("p_ckpt", 5, tmp_path)

        # After sync: file deleted (chapter is being regenerated; no useful state)
        assert not (proj / ".nebula_checkpoint.json").exists()

    def test_deletes_only_matching_chapter_when_different(self, tmp_path):
        proj = tmp_path / "p_ckpt2"
        proj.mkdir()
        (proj / "project.json").write_text(json.dumps({"id": "p_ckpt2"}))
        # Checkpoint for chapter 9, not 5
        checkpoint = {
            "project_id": "p_ckpt2",
            "current_chapter": 9,
            "current_scene": 2,
            "pipeline_stage": "scene_review",
        }
        (proj / ".nebula_checkpoint.json").write_text(json.dumps(checkpoint))

        clear_checkpoint_for_chapter("p_ckpt2", 5, tmp_path)

        # File should still exist (chapter mismatch → no action)
        assert (proj / ".nebula_checkpoint.json").exists()

    def test_no_op_when_checkpoint_missing(self, tmp_path):
        proj = tmp_path / "p_ckpt3"
        proj.mkdir()
        # No checkpoint file
        clear_checkpoint_for_chapter("p_ckpt3", 5, tmp_path)
        # No exception

    def test_no_op_on_corrupt_json(self, tmp_path):
        proj = tmp_path / "p_ckpt_corrupt"
        proj.mkdir()
        # Invalid JSON in checkpoint file
        (proj / ".nebula_checkpoint.json").write_text("{not valid json")
        # Should not raise; should leave the corrupt file alone
        clear_checkpoint_for_chapter("p_ckpt_corrupt", 5, tmp_path)
        assert (proj / ".nebula_checkpoint.json").exists()


@pytest.fixture
def regen_projects_dir(tmp_path, monkeypatch):
    """Project layout with outline + progress for range integration tests."""
    proj = tmp_path / "p_range"
    proj.mkdir()
    (proj / "project.json").write_text(json.dumps({"id": "p_range"}))
    # Outline: 12 chapters, 3 scenes each
    outline = {
        "chapters": [
            {"chapter_number": n, "scene_plan": [{"scene_number": s} for s in (1, 2, 3)]}
            for n in range(1, 13)
        ]
    }
    (proj / "outline.json").write_text(json.dumps(outline))
    # Progress: ch1-3 completed, ch4-6 in_progress, ch7+ nothing
    progress = {
        "current_chapter": 7,
        "chapters": [],
    }
    for n in range(1, 4):
        progress["chapters"].append({
            "chapter_number": n, "status": "completed",
            "scenes": [{"scene_number": s, "status": "completed"} for s in (1, 2, 3)],
        })
    for n in range(4, 7):
        progress["chapters"].append({
            "chapter_number": n, "status": "in_progress",
            "scenes": [{"scene_number": s, "status": "pending"} for s in (1, 2, 3)],
        })
    (proj / "progress.json").write_text(json.dumps(progress))
    monkeypatch.setattr(_settings, "projects_dir", tmp_path)
    return tmp_path


class TestSeedQueueRange:
    def test_scope_range_no_overlap_with_completed(self, regen_projects_dir):
        from backend.conductor.autopilot_session import AutopilotSessionManager
        mgr = AutopilotSessionManager(regen_projects_dir, "p_range")
        mgr.start(ManagedStartConfig(scope="range", start_chapter=7, end_chapter=10))
        outline = json.loads((regen_projects_dir / "p_range" / "outline.json").read_text())
        progress = json.loads((regen_projects_dir / "p_range" / "progress.json").read_text())
        result = seed_queue(mgr, outline, progress, None,
                            ManagedStartConfig(scope="range", start_chapter=7, end_chapter=10),
                            projects_dir=regen_projects_dir)
        # 4 chapters × 3 scenes = 12 items
        assert result.enqueued == 12
        assert result.scope_used == "range"
        assert result.fallback_applied is False

    def test_scope_range_includes_completed_chapters(self, regen_projects_dir):
        """Range [2, 5] overlaps with completed ch1-3 AND in-progress ch4-6.

        New behavior (post proj_1a7d7fcf 2026-08-20): completed chapters in
        scope are NOT regenerated. The destructive path used to delete
        drafts here, which is exactly the bug we removed. With the trust-
        the-disk model, completed chapters stay completed; only the
        unfinished ones get enqueued.

        NB: the fixture sets current_chapter=7, which raises the floor to
        chapter 6. Range [2,5] is entirely below the floor, so nothing in
        scope is enqueued. To get the "completed chapters in scope stay
        completed" assertion below the floor would mask the bug, so we
        instead test it with the floor neutralized by a cfg whose range
        straddles current_chapter.
        """
        from backend.conductor.autopilot_session import AutopilotSessionManager
        mgr = AutopilotSessionManager(regen_projects_dir, "p_range")
        cfg = ManagedStartConfig(scope="range", start_chapter=2, end_chapter=5)
        mgr.start(cfg)
        outline = json.loads((regen_projects_dir / "p_range" / "outline.json").read_text())
        progress = json.loads((regen_projects_dir / "p_range" / "progress.json").read_text())
        result = seed_queue(mgr, outline, progress, None, cfg,
                            projects_dir=regen_projects_dir)
        # Floor=current_chapter-1=6 > end_chapter=5 → empty scope, no enqueue.
        assert result.enqueued == 0
        # progress.json untouched (no regen, no promotion)
        progress_after = json.loads(
            (regen_projects_dir / "p_range" / "progress.json").read_text()
        )
        for ch_num in [1, 2, 3]:
            ch = next(c for c in progress_after["chapters"] if c["chapter_number"] == ch_num)
            assert ch["status"] == "completed", f"ch{ch_num} must remain completed"
        # Critical: NO drafts deleted. chapters/ directory didn't even exist
        # in the fixture, but no exception was raised either.
        assert not (regen_projects_dir / "p_range" / "chapters").exists()

    def test_scope_range_completed_chapters_preserved_when_range_straddles_floor(
        self, regen_projects_dir,
    ):
        """Range [4, 6] straddles the floor (current_chapter=7 → floor=6).
        Floor INCLUDES ch6, so target = {ch6}. ch6 has 3 pending scenes,
        which get enqueued. ch1-3 (completed) and ch4-5 (in_progress, but
        all scenes pending) are below the floor and NOT enqueued.

        Most importantly: completed chapters below the floor stay
        completed, no deletion, no regen.
        """
        from backend.conductor.autopilot_session import AutopilotSessionManager
        mgr = AutopilotSessionManager(regen_projects_dir, "p_range")
        cfg = ManagedStartConfig(scope="range", start_chapter=4, end_chapter=6)
        mgr.start(cfg)
        outline = json.loads((regen_projects_dir / "p_range" / "outline.json").read_text())
        progress = json.loads((regen_projects_dir / "p_range" / "progress.json").read_text())
        result = seed_queue(mgr, outline, progress, None, cfg,
                            projects_dir=regen_projects_dir)
        # Floor=6, target after clamp = {ch6} only. ch6 has 3 pending scenes → 3 enqueued.
        assert result.enqueued == 3
        # ch1-3 status preserved as completed (no destructive regen).
        progress_after = json.loads(
            (regen_projects_dir / "p_range" / "progress.json").read_text()
        )
        for ch_num in [1, 2, 3]:
            ch = next(c for c in progress_after["chapters"] if c["chapter_number"] == ch_num)
            assert ch["status"] == "completed"

    def test_scope_all_planned_preserves_completed_chapters(self, regen_projects_dir):
        """all_planned scope must NOT reset completed chapters anymore.

        Old behavior regenerated ch1-3 (deleted drafts + reset progress)
        because they were all DONE_STATUSES. New behavior keeps them
        completed and only enqueues the unfinished scenes in ch4-6 +
        the never-started ch7-12. The floor is current_chapter-1=6, so
        target is ch7-12 = 6 chapters × 3 scenes = 18 scenes.
        """
        from backend.conductor.autopilot_session import AutopilotSessionManager
        mgr = AutopilotSessionManager(regen_projects_dir, "p_range")
        cfg = ManagedStartConfig(scope="all_planned")
        mgr.start(cfg)
        outline = json.loads((regen_projects_dir / "p_range" / "outline.json").read_text())
        progress = json.loads((regen_projects_dir / "p_range" / "progress.json").read_text())
        result = seed_queue(mgr, outline, progress, None, cfg,
                            projects_dir=regen_projects_dir)
        # Floor=6 INCLUDES ch6, so target = ch6-12 = 7 chapters. ch6 has 3
        # pending scenes; ch7-12 have no progress entry → all scenes unfinished.
        # 7 × 3 = 21 scenes enqueued.
        assert result.enqueued == 21
        # ch1, ch2, ch3 status preserved as completed (NOT reset to pending)
        progress_after = json.loads(
            (regen_projects_dir / "p_range" / "progress.json").read_text()
        )
        for ch_num in [1, 2, 3]:
            ch = next(c for c in progress_after["chapters"] if c["chapter_number"] == ch_num)
            assert ch["status"] == "completed", f"ch{ch_num} must remain completed"
        # ch4-6 untouched (still in_progress per fixture)
        for ch_num in [4, 5, 6]:
            ch = next(c for c in progress_after["chapters"] if c["chapter_number"] == ch_num)
            assert ch["status"] == "in_progress"

    def test_empty_outline_returns_zero(self, regen_projects_dir):
        from backend.conductor.autopilot_session import AutopilotSessionManager
        mgr = AutopilotSessionManager(regen_projects_dir, "p_range")
        cfg = ManagedStartConfig(scope="range", start_chapter=1, end_chapter=10)
        mgr.start(cfg)
        # Empty outline
        result = seed_queue(mgr, {"chapters": []}, None, None, cfg,
                            projects_dir=regen_projects_dir)
        assert result.enqueued == 0
        assert result.scope_used == "range"
        assert result.fallback_applied is False

    def test_next_chapter_branch_removed(self, regen_projects_dir):
        """seed_queue no longer auto-widens when scope has no work. With range
        [100, 110] against a 12-chapter outline, no chapters match and no
        fallback runs."""
        from backend.conductor.autopilot_session import AutopilotSessionManager
        mgr = AutopilotSessionManager(regen_projects_dir, "p_range")
        cfg = ManagedStartConfig(scope="range", start_chapter=100, end_chapter=110)
        mgr.start(cfg)
        outline = json.loads((regen_projects_dir / "p_range" / "outline.json").read_text())
        progress = json.loads((regen_projects_dir / "p_range" / "progress.json").read_text())
        result = seed_queue(mgr, outline, progress, None, cfg,
                            projects_dir=regen_projects_dir)
        # No chapters in [100, 110]; no fallback (we removed next_chapter fallback).
        assert result.enqueued == 0
        assert result.fallback_applied is False

    # ------------------------------------------------------------------
    # New behavior tests — trust-the-disk promotion + current_chapter
    # floor (added 2026-08-20, proj_1a7d7fcf regression fix).
    # ------------------------------------------------------------------

    def test_seed_queue_does_not_delete_drafts_when_chapter_marked_complete(
        self, regen_projects_dir,
    ):
        """The OLD destructive-regen path deleted chapters/ch{N}_*.md
        whenever a chapter was in DONE_STATUSES. The NEW path keeps
        drafts on disk and just trusts them.

        Reproduces proj_1a7d7fcf 2026-08-20 root cause: ch1-3 are marked
        completed in the fixture; with the old code, all_planned
        seed_queue would unlink ch01-*.md etc. The new code leaves
        them alone.
        """
        from backend.conductor.autopilot_session import AutopilotSessionManager
        proj = regen_projects_dir / "p_range"
        # Pre-create drafts for ch1 (would have been deleted by old code).
        (proj / "chapters").mkdir()
        draft_ch1s1 = proj / "chapters" / "ch01_scene_001_draft.md"
        draft_ch1s1.write_text("chapter 1 scene 1 content")
        draft_ch3s3 = proj / "chapters" / "ch03_scene_003_draft.md"
        draft_ch3s3.write_text("chapter 3 scene 3 content")

        mgr = AutopilotSessionManager(regen_projects_dir, "p_range")
        cfg = ManagedStartConfig(scope="range", start_chapter=1, end_chapter=12)
        mgr.start(cfg)
        outline = json.loads((proj / "outline.json").read_text())
        progress = json.loads((proj / "progress.json").read_text())
        seed_queue(mgr, outline, progress, None, cfg,
                   projects_dir=regen_projects_dir)

        # Drafts MUST still be there (the entire point of the fix).
        assert draft_ch1s1.exists(), "ch01 draft deleted by seed_queue — REGRESSION"
        assert draft_ch1s1.read_text() == "chapter 1 scene 1 content"
        assert draft_ch3s3.exists(), "ch03 draft deleted by seed_queue — REGRESSION"
        assert draft_ch3s3.read_text() == "chapter 3 scene 3 content"

    def test_seed_queue_promotes_drafts_on_disk_to_completed(
        self, regen_projects_dir,
    ):
        """Trust-the-disk promotion: if chapters/ch{NN}_scene_{NNN}_draft.md
        exists on disk but progress.json shows the scene as pending,
        flip it to completed (write back to progress.json).
        """
        from backend.conductor.autopilot_session import AutopilotSessionManager
        proj = regen_projects_dir / "p_range"
        (proj / "chapters").mkdir()
        # ch4 scene 1 has a draft on disk but progress says pending.
        draft = proj / "chapters" / "ch04_scene_001_draft.md"
        draft.write_text("ch4 s1 content")

        mgr = AutopilotSessionManager(regen_projects_dir, "p_range")
        cfg = ManagedStartConfig(scope="range", start_chapter=4, end_chapter=4)
        mgr.start(cfg)
        outline = json.loads((proj / "outline.json").read_text())
        progress = json.loads((proj / "progress.json").read_text())
        seed_queue(mgr, outline, progress, None, cfg,
                   projects_dir=regen_projects_dir)

        # progress.json must now show ch4 scene 1 as completed.
        progress_after = json.loads((proj / "progress.json").read_text())
        ch4 = next(c for c in progress_after["chapters"]
                   if c["chapter_number"] == 4)
        ch4s1 = next(s for s in ch4["scenes"] if s["scene_number"] == 1)
        assert ch4s1["status"] == "completed", \
            f"ch4 scene 1 should be promoted; got {ch4s1['status']!r}"
        # Draft must still exist (no deletion).
        assert draft.exists()

    def test_seed_queue_skips_chapters_without_progress_entries(
        self, regen_projects_dir,
    ):
        """If a draft exists but progress.json has NO entry for that
        chapter at all, do NOT auto-create progress entries — let the
        runner do it. Reason: the chapter may have been outline-drifted
        (user regenerated the outline and this draft is orphaned).

        Setting: ch13 is in the outline (we'll add it) but progress.json
        has no entry for ch13. We pre-place a draft for ch13 scene 1.
        seed_queue must NOT add a progress entry for ch13; it must
        enqueue the scene normally (no promotion happens for missing
        entries, but the scene IS unfinished so it gets queued).
        """
        from backend.conductor.autopilot_session import AutopilotSessionManager
        proj = regen_projects_dir / "p_range"
        # Extend the outline to ch13 (the fixture only has 1-12).
        outline = json.loads((proj / "outline.json").read_text())
        outline["chapters"].append({
            "chapter_number": 13,
            "scene_plan": [{"scene_number": 1}, {"scene_number": 2}],
        })
        (proj / "outline.json").write_text(json.dumps(outline))
        # Drop a draft for ch13 scene 1.
        (proj / "chapters").mkdir()
        orphan_draft = proj / "chapters" / "ch13_scene_001_draft.md"
        orphan_draft.write_text("orphan draft from old outline version")

        mgr = AutopilotSessionManager(regen_projects_dir, "p_range")
        cfg = ManagedStartConfig(scope="range", start_chapter=13, end_chapter=13)
        mgr.start(cfg)
        progress = json.loads((proj / "progress.json").read_text())
        seed_queue(mgr, outline, progress, None, cfg,
                   projects_dir=regen_projects_dir)

        progress_after = json.loads((proj / "progress.json").read_text())
        ch13_entries = [c for c in progress_after["chapters"]
                        if c["chapter_number"] == 13]
        # Must NOT have auto-created an entry — the runner will.
        assert ch13_entries == [], \
            "seed_queue must not auto-create progress entries for orphan drafts"

    def test_seed_queue_clamps_to_current_chapter_minus_one_on_resume(
        self, regen_projects_dir,
    ):
        """The original bug: cfg says start=1, current_chapter says 7,
        resume should NOT enqueue ch1-5 (those are below the floor).

        With current_chapter=7 (the fixture), the floor is 6. With
        cfg start_chapter=1 and end=12, target_chapters after clamping
        is ch6-12 = 7 chapters. ch6 has 3 pending scenes (in_progress),
        ch7-12 have no progress entries → all 3 scenes each are
        unfinished. 7 × 3 = 21 scenes enqueued.
        """
        from backend.conductor.autopilot_session import AutopilotSessionManager
        mgr = AutopilotSessionManager(regen_projects_dir, "p_range")
        cfg = ManagedStartConfig(scope="range", start_chapter=1, end_chapter=12)
        mgr.start(cfg)
        outline = json.loads((regen_projects_dir / "p_range" / "outline.json").read_text())
        progress = json.loads((regen_projects_dir / "p_range" / "progress.json").read_text())
        result = seed_queue(mgr, outline, progress, None, cfg,
                            projects_dir=regen_projects_dir)
        # Floor=6 INCLUDES ch6, so target is ch6-12 = 7 × 3 = 21.
        assert result.enqueued == 21
        # ch1-3 status preserved as completed (not reset).
        progress_after = json.loads(
            (regen_projects_dir / "p_range" / "progress.json").read_text()
        )
        for ch_num in [1, 2, 3]:
            ch = next(c for c in progress_after["chapters"]
                      if c["chapter_number"] == ch_num)
            assert ch["status"] == "completed"

    def test_seed_queue_honors_cfg_start_above_floor(
        self, regen_projects_dir,
    ):
        """If cfg.start_chapter is ABOVE the floor (current_chapter-1),
        cfg wins. Reason: the user explicitly chose a future range;
        we shouldn't drag them backward to where the runner is now.

        Fixture: current_chapter=7, floor=6. cfg says start=10.
        Target should be ch10-12 = 3 chapters × 3 scenes = 9.
        """
        from backend.conductor.autopilot_session import AutopilotSessionManager
        mgr = AutopilotSessionManager(regen_projects_dir, "p_range")
        cfg = ManagedStartConfig(scope="range", start_chapter=10, end_chapter=12)
        mgr.start(cfg)
        outline = json.loads((regen_projects_dir / "p_range" / "outline.json").read_text())
        progress = json.loads((regen_projects_dir / "p_range" / "progress.json").read_text())
        result = seed_queue(mgr, outline, progress, None, cfg,
                            projects_dir=regen_projects_dir)
        # Floor=6 < cfg.start=10 → cfg wins. ch10-12 = 9 scenes.
        assert result.enqueued == 9

    def test_seed_queue_no_clamp_when_current_chapter_is_one(
        self, regen_projects_dir,
    ):
        """When current_chapter is 1 (fresh project), floor=0 → no clamp.
        Equivalent to the pre-fix behavior for fresh starts.
        """
        from backend.conductor.autopilot_session import AutopilotSessionManager
        proj = regen_projects_dir / "p_range"
        progress = json.loads((proj / "progress.json").read_text())
        progress["current_chapter"] = 1
        (proj / "progress.json").write_text(json.dumps(progress))

        mgr = AutopilotSessionManager(regen_projects_dir, "p_range")
        cfg = ManagedStartConfig(scope="range", start_chapter=1, end_chapter=12)
        mgr.start(cfg)
        outline = json.loads((proj / "outline.json").read_text())
        result = seed_queue(mgr, outline, progress, None, cfg,
                            projects_dir=regen_projects_dir)
        # No clamp → all 12 chapters in scope, but ch1-3 are completed
        # so only ch4-12 = 9 × 3 = 27 scenes get enqueued.
        assert result.enqueued == 27


class TestRangePreviewEndpoint:
    @pytest.fixture
    def client(self, regen_projects_dir):
        return TestClient(app)

    def test_returns_valid_with_no_regen(self, client):
        resp = client.get(
            "/api/v1/projects/p_range/autopilot/managed/range-preview",
            params={"start": 7, "end": 10},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["outline_max"] == 12
        assert body["valid"] is True
        assert body["error"] is None
        # ch7-10 have no completed chapters
        assert body["regenerate_chapters"] == []
        # defaults based on latest_completed = 3
        assert body["defaults"] == {"start_chapter": 4, "end_chapter": 12}

    def test_returns_regenerate_chapters_when_overlap(self, client):
        resp = client.get(
            "/api/v1/projects/p_range/autopilot/managed/range-preview",
            params={"start": 2, "end": 5},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["outline_max"] == 12
        # ch2, ch3 are completed (in our fixture ch1-3 done)
        assert sorted(body["regenerate_chapters"]) == [2, 3]

    def test_returns_invalid_for_end_above_outline_max(self, client):
        resp = client.get(
            "/api/v1/projects/p_range/autopilot/managed/range-preview",
            params={"start": 5, "end": 100},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False
        assert "结束章节" in body["error"] or "end" in body["error"].lower()

    def test_returns_invalid_for_start_below_one(self, client):
        resp = client.get(
            "/api/v1/projects/p_range/autopilot/managed/range-preview",
            params={"start": 0, "end": 5},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False

    def test_returns_invalid_for_end_less_than_start(self, client):
        resp = client.get(
            "/api/v1/projects/p_range/autopilot/managed/range-preview",
            params={"start": 10, "end": 5},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False

    def test_returns_invalid_when_outline_missing(self, client, tmp_path, monkeypatch):
        # Switch projects_dir to one with no outline
        empty = tmp_path / "p_empty"
        empty.mkdir()
        (empty / "project.json").write_text(json.dumps({"id": "p_empty"}))
        monkeypatch.setattr(_settings, "projects_dir", tmp_path)
        resp = client.get(
            "/api/v1/projects/p_empty/autopilot/managed/range-preview",
            params={"start": 1, "end": 5},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False
        assert "大纲" in body["error"] or "outline" in body["error"].lower()

    def test_scope_all_planned_overrides_start_end(self, client):
        """scope='all_planned' should make start=1, end=outline_max
        regardless of query params."""
        resp = client.get(
            "/api/v1/projects/p_range/autopilot/managed/range-preview",
            params={"start": 5, "end": 15, "scope": "all_planned"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        # all_planned means full range: regenerate ch1-3 (the completed ones)
        assert sorted(body["regenerate_chapters"]) == [1, 2, 3]
        assert body["defaults"]["start_chapter"] == 4
        assert body["defaults"]["end_chapter"] == 12

    def test_returns_valid_for_malformed_progress(self, client, tmp_path,
                                                    monkeypatch):
        """A corrupt progress.json should NOT block the endpoint — fall
        back to empty progress and report defaults based on no completions."""
        proj = tmp_path / "p_corrupt"
        proj.mkdir()
        (proj / "project.json").write_text(json.dumps({"id": "p_corrupt"}))
        # Write outline (12 chapters like the fixture)
        outline = {
            "chapters": [
                {"chapter_number": n, "scene_plan": [{"scene_number": s} for s in (1, 2, 3)]}
                for n in range(1, 13)
            ]
        }
        (proj / "outline.json").write_text(json.dumps(outline))
        # Corrupt progress.json
        (proj / "progress.json").write_text("{not valid json")
        monkeypatch.setattr(_settings, "projects_dir", tmp_path)
        resp = client.get(
            "/api/v1/projects/p_corrupt/autopilot/managed/range-preview",
            params={"start": 1, "end": 5},
        )
        assert resp.status_code == 200
        body = resp.json()
        # With empty progress, defaults start at 1 and end at min(11, 12) = 11
        assert body["valid"] is True
        assert body["regenerate_chapters"] == []
        assert body["defaults"]["start_chapter"] == 1
        assert body["defaults"]["end_chapter"] == 11
