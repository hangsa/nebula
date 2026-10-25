"""Async runner + helpers for AutopilotRunner wiring.

Spec: docs/superpowers/specs/2026-07-14-v1.9-autopilot-runner-wiring-design.md
§§3, 5. This file is intentionally thin: pure helpers + a small Protocol.
The production executor lives in `backend/conductor/stage4_async_executor.py`
and the loop-bookkeeping in `backend/conductor/autopilot_loop.py`.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

from backend.conductor.checkpoint import CheckpointManager
from backend.models.autopilot_session import (
    CurrentTask, ManagedStartConfig, QueueItem, SessionState,
)
from backend.utils.file_manager import FileManager
from backend.conductor.chapter_drafts_scanner import discover_drafts


logger = logging.getLogger(__name__)


DONE_STATUSES = frozenset({"completed", "force_passed", "skipped"})

# Status-promotion ladder for trust-the-disk promotion (see
# _promote_discovered_drafts). Mirrors the ladder in
# backend/conductor/stage4_async_executor.py:_write_scene_progress so a
# disk-side draft and a runner-side completion reach the same terminal
# status via the same precedence rules. Without the ladder, a draft
# discovered after a force_passed scene would get demoted to completed
# — wrong if the original breaker verdict was "force_passed" for a
# reason the user wants preserved.
_PROMOTION_LADDER = {"pending": 0, "in_progress": 1, "force_passed": 2, "completed": 3}


# Retry-then-pause: a transient LLM hiccup (peer-closed, 5xx, rate limit)
# shouldn't kill the whole autopilot run. We retry the SAME scene up to
# SCENE_WRITE_MAX_RETRIES additional times with growing backoff; only when
# ALL attempts fail do we pause the session with `pause_reason` set so the
# user can intervene via the cockpit banner.
#
# Why 2 retries (3 total attempts): aligns with the existing Fact Guard
# CircuitBreaker.MAX_RETRIES=3 budget, gives the upstream LLM enough slack
# to recover from a transient blip, but doesn't burn the user's token
# budget on a permanently broken prompt. Bug 2026-07-22 proj_a601cee9:
# drop-on-fail left scenes stuck forever after a single dropped LLM
# connection.
SCENE_WRITE_MAX_RETRIES = 2
SCENE_WRITE_RETRY_BACKOFFS = (30, 60)  # seconds before retry #1, #2


# Row-major queue priority scheme: chapters are processed in chapter_number
# order; within a chapter, scenes run in scene_number order. The 1000x gap
# between chapter buckets leaves 998 scene slots plus one archival slot per
# chapter (well above any realistic outline). The archive slot sits after the
# chapter's scenes and before the next chapter's first scene.
#
# Invariants:
#   scene_priority(N, M) < scene_priority(N, M+1)        (within chapter)
#   scene_priority(N, last) < archive_priority(N)         (archival after last scene)
#   archive_priority(N) < scene_priority(N+1, 1)          (archival before next chapter)
#
# Why row-major (and not column-major as before):
#   - Aligns with nebula's per-chapter MemoryOS cache (L1/L4/L2 survive
#     across scenes in the same chapter). Column-major thrashes the cache
#     every scene → ~60% extra context-assembly overhead per the design doc.
#   - Net-novel authoring works chapter-by-chapter; writing ch31.scene_4
#     "knows" all of ch31 already exists.
#   - Eliminates the "ch32.scene_1 written before ch31.scene_4" forward-leak
#     where MemoryOS L2 was missing the just-prior chapter's summary.
PRIORITY_SCALE_PER_CHAPTER = 1000
ARCHIVE_PRIORITY_OFFSET = 999  # archive runs after all scenes in its chapter


def scene_priority(chapter_number: int, scene_number: int) -> int:
    """Lower = earlier. Row-major: chapter_number then scene_number."""
    return chapter_number * PRIORITY_SCALE_PER_CHAPTER + scene_number


def archive_priority(chapter_number: int) -> int:
    """Just above the chapter's last scene, below the next chapter's first scene."""
    return chapter_number * PRIORITY_SCALE_PER_CHAPTER + ARCHIVE_PRIORITY_OFFSET


@dataclass
class SeedResult:
    """Outcome of seed_queue. Distinguishes "scope was widened automatically
    because the requested scope had nothing left to do" from "everything
    really is done" — without this distinction the API's no_work_to_do
    branch can't tell the user what actually happened (bug 2026-07-17:
    proj_cc4ca4ae saw a misleading "all 33 chapters done" toast when in
    reality the user's stale scope=next_chapter was the blocker).

    `enqueued` is the TOTAL number of NEW queue items added by this call,
    summed across both code paths:
      - `seeded`: scenes added by `_enqueue_for_scope` (the standard
        enqueue path; uses deterministic ids for idempotent dedup).
      - `regenerated_scenes`: scenes re-enqueued for completed chapters
        in scope (the regeneration path; drops old queue items first then
        re-adds via `regenerate_chapter`).
    The two diverge when regeneration kicks in: a fresh start against an
    outline with completed chapters in scope gets `seeded=0` for those
    chapters (regen already added them) but `enqueued>0` once regenerated
    scenes are added.

    `matched` is the total number of unfinished scenes in the requested
    scope (including scenes already in the queue from a previous call).
    The two diverge once seed_queue becomes idempotent: a restart against
    a queue that already holds its target scenes returns enqueued=0 but
    matched>0, which means "nothing new, but there's still work — keep
    running."

    `enqueued` is telemetry-only — the caller's no-work decision uses
    queue length, not enqueued count (see autopilot_loop.py:152).
    """
    enqueued: int
    scope_used: str
    fallback_applied: bool
    matched: int = 0


class AsyncTaskExecutor(Protocol):
    """The runner only knows this Protocol. Production: AsyncStage4Executor.
    Tests: FakeStage4Executor."""

    async def execute(self, item: QueueItem, project_id: str) -> dict:
        ...

    async def execute_stream(self, item: QueueItem, project_id: str) -> dict:
        """Streaming twin. Implementations may delegate to execute() if they
        don't yet support streaming (FakeStage4Executor pre-Plan-3 still does)."""
        ...


CADENCE_DELAYS = {"fast": 0.5, "balanced": 2.0, "careful": 5.0}


def is_chapter_complete(
    progress_scenes: list,
    expected_scene_plan: list,
) -> bool:
    """Spec §5 rule rows 5, 6, 7. Pure — no I/O.

    A chapter is complete when, for every scene in the outline (in order),
    the matching progress entry exists with a status in {completed, force_passed,
    skipped}. Extra progress entries beyond the outline's length are ignored
    (defensive against outline edits mid-run).
    """
    if not expected_scene_plan:
        return True  # nothing planned → trivially done
    plan_nums = [s.get("scene_number") for s in expected_scene_plan]
    progress_by_num = {
        s.get("scene_number"): s.get("status")
        for s in progress_scenes
    }
    for n in plan_nums:
        status = progress_by_num.get(n)
        if status not in DONE_STATUSES:
            return False
    return True


def find_latest_completed_chapter(progress: dict, outline: dict) -> Optional[int]:
    """Return the highest chapter_number whose every scene in the outline's
    scene_plan is in DONE_STATUSES. None if no chapter is fully done.

    Reuses is_chapter_complete so the "done" definition stays in one place.
    """
    outline_by_num = {
        c.get("chapter_number"): c for c in (outline.get("chapters") or [])
    }
    progress_by_num = {
        ch.get("chapter_number"): ch for ch in (progress.get("chapters") or [])
    }
    completed: list[int] = []
    for ch_num, outline_ch in outline_by_num.items():
        ch_progress = progress_by_num.get(ch_num, {})
        planned = outline_ch.get("scene_plan", []) or []
        if not planned:
            continue
        if is_chapter_complete(ch_progress.get("scenes", []) or [], planned):
            completed.append(ch_num)
    return max(completed) if completed else None


def _promote_discovered_drafts(
    fm: FileManager,
    project_id: str,
    progress: dict,
    discovered: set,
) -> int:
    """Promote disk-side drafts to `status='completed'` in the in-memory
    `progress` dict, but ONLY for scenes whose progress entry already
    exists with a status below 'completed' on the ladder.

    Returns the count of scenes promoted. Does NOT persist — the caller
    (seed_queue) writes the updated progress.json once after the loop so
    we don't fsync on every promotion.

    Conservative on missing entries: if `progress` has no entry for the
    chapter at all (chapter never started in progress.json), we don't
    auto-create one. Same for scenes. The reasoning:
      - auto-creating chapter entries would mask bookkeeping bugs
        (e.g. _advance_chapter skipped writing for some reason);
      - the user's outline might have been regenerated AFTER the draft
        was written, making the draft orphaned;
      - the safer default is "the runner still has to run the scene" —
        it'll just no-op on the existing draft via the `_scene_in_outline`
        precheck or skip cleanly via the outline drift short-circuit.
    The caller can later re-run seed_queue after the chapter advances
    and the progress entry exists, at which point promotion kicks in.

    Mirrors the ladder from `_write_scene_progress` in
    stage4_async_executor.py — pending → in_progress → force_passed →
    completed. We only ever promote UP the ladder: a pending scene with a
    draft becomes completed; a force_passed scene with a draft also
    becomes completed (the draft is newer evidence than the breaker
    verdict, and the user would rather have a real scene than a
    placeholder). We never demote (no path sets status back to
    pending/in_progress/force_passed).
    """
    chapters = progress.setdefault("chapters", []) or []
    promoted = 0
    for ch_num, sc_num in discovered:
        ch_p = next(
            (c for c in chapters if c.get("chapter_number") == ch_num),
            None,
        )
        if ch_p is None:
            continue
        sc_p = next(
            (s for s in ch_p.get("scenes", []) or []
             if s.get("scene_number") == sc_num),
            None,
        )
        if sc_p is None:
            continue
        existing = sc_p.get("status")
        if _PROMOTION_LADDER.get(existing, 0) < _PROMOTION_LADDER["completed"]:
            sc_p["status"] = "completed"
            promoted += 1
    return promoted


def compute_range_defaults(
    outline_max: int, latest_completed: Optional[int],
) -> tuple[int, int]:
    """Default range for ManagedStartConfig(scope='range').

    start = (latest_completed or 0) + 1  →  1 if nothing completed yet.
    end   = min(start + 10, outline_max).

    Caller is responsible for surfacing "invalid" when start > outline_max.
    """
    start = (latest_completed or 0) + 1
    end = min(start + 10, outline_max)
    return start, end


def reset_chapter_progress(fm: FileManager, project_id: str,
                            chapter_number: int) -> None:
    """Set chapter + all scenes to status='pending', clear retry/coherence.
    Mutates progress.json in place via FileManager.write_json (atomic .tmp
    + replace) so a process crash mid-regeneration cannot corrupt the very
    file regeneration is meant to repair. Other chapters untouched.
    """
    progress_path = fm.project_path(project_id, "progress.json")
    if not progress_path.exists():
        return
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    for ch in progress.get("chapters", []):
        if ch.get("chapter_number") != chapter_number:
            continue
        ch["status"] = "pending"
        for s in ch.get("scenes", []):
            s["status"] = "pending"
            s["retry_count"] = 0
            s["coherence_score"] = None
    fm.write_json(project_id, "progress.json", progress)


def clear_chapter_drafts(project_id: str, chapter_number: int,
                          project_dir: Path) -> None:
    """Delete drafts for the given chapter: chapters/ch{NN}_*.md.

    Format matches the layout produced by stage4 writers: ch{NN}_scene_{NNN}_draft.md.
    Defensive: missing directories or no matching files are no-ops.

    `project_dir` is the project directory (folder containing chapters/),
    not the projects-root parent.
    """
    import re
    chapters_dir = project_dir / "chapters"
    if not chapters_dir.exists():
        return
    # Match ch01_, ch1_, ch001_ etc. — zero-padded or not. The filename
    # must start with the literal "ch" then the chapter_number digits
    # then "_"; any padding (or none) is allowed.
    pattern = re.compile(rf"^ch0*{chapter_number}_")
    for f in chapters_dir.iterdir():
        if f.is_file() and pattern.match(f.name):
            f.unlink()


def drop_chapter_queue_items(mgr: "AutopilotSessionManager",
                              chapter_number: int) -> None:
    """Remove all queue items with id like 'write-{chapter_number}-{scene}'.

    Persists via mgr.save after the in-memory mutation.
    """
    snapshot = mgr.load()
    if snapshot is None:
        return
    prefix = f"write-{chapter_number}-"
    new_queue = [q for q in snapshot.queue if not q.id.startswith(prefix)]
    if len(new_queue) == len(snapshot.queue):
        return
    snapshot.queue = new_queue
    mgr.save(snapshot)


def enqueue_chapter_scenes(mgr: "AutopilotSessionManager",
                            chapter_number: int,
                            scene_plan: list) -> None:
    """Enqueue one write-{ch}-{scene} item per scene in the plan, with
    row-major priority (matches existing _enqueue_for_scope scheme)."""
    for s in scene_plan:
        n = s.get("scene_number")
        if n is None:
            continue
        item = QueueItem(
            id=f"write-{chapter_number}-{n}",
            kind="write_scene",
            chapter_number=chapter_number,
            scheduled_at=None,
            priority=scene_priority(chapter_number, n),
            payload={"scene_number": n},
        )
        mgr.add_queue(item)


def regenerate_chapter(fm: FileManager,
                        project_id: str,
                        mgr: "AutopilotSessionManager",
                        chapter_number: int,
                        scene_plan: list,
                        project_dir: Path) -> None:
    """Orchestrate regeneration: reset progress, clear drafts, drop queue
    items, re-enqueue scenes. Checkpoint sync is the caller's responsibility
    (separate helper, see clear_checkpoint_for_chapter).

    `fm` is used for the atomic progress.json write; `project_dir` is still
    passed for the unlink loop in clear_chapter_drafts (file deletes are
    inherently atomic, no need for FileManager).

    The helpers are called in this order: reset progress → clear drafts →
    drop queue items → re-enqueue. Reset-then-clear ensures the next write
    doesn't see stale scene status; drop-then-enqueue prevents duplicate
    ids in the queue (matching the existing dedup logic).
    """
    reset_chapter_progress(fm, project_id, chapter_number)
    clear_chapter_drafts(project_id, chapter_number, project_dir)
    drop_chapter_queue_items(mgr, chapter_number)
    enqueue_chapter_scenes(mgr, chapter_number, scene_plan)


def clear_checkpoint_for_chapter(project_id: str, chapter_number: int,
                                  projects_dir: Path) -> None:
    """Clear the .nebula_checkpoint.json when its current_chapter
    matches the chapter being regenerated.

    Rationale: the checkpoint snapshot reflects completed work that the
    regeneration will overwrite. Leaving it in place creates a stale
    snapshot that the runner's recover() path would later read. The
    runner rebuilds the checkpoint naturally on the next checkpoint tick,
    so deleting is preferable to mutating in place.
    """
    project_dir = projects_dir / project_id
    for name in (
        CheckpointManager.CHECKPOINT_FILENAME,
        CheckpointManager.LEGACY_CHECKPOINT_FILENAME,
    ):
        path = project_dir / name
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # Corrupt or unreadable checkpoint — safe to leave; recover() handles it.
            continue
        if data.get("current_chapter") == chapter_number:
            path.unlink()
            logger.info("checkpoint cleared for chapter=%s project=%s",
                        chapter_number, project_id)


def repair_stuck_chapters(progress: dict, outline: dict) -> list:
    """Flip chapters stuck at status='in_progress' despite all outline scenes
    being terminal to status='completed'. Mutates `progress` in place;
    returns the sorted chapter_numbers that were repaired.

    Pure on its inputs (no I/O). The caller is responsible for persisting
    `progress` afterwards if disk durability is wanted.

    Why this exists: the executor finalizes scenes one at a time and only
    flips the chapter status after the last scene lands. If the runner stops
    between those two writes (manual stop, server restart, force-passed
    mid-flight), the chapter is left half-finalized forever. Without an
    explicit repair, the autopilot sees "this chapter is still in progress"
    and refuses to advance. surfacing 2026-07-17 on proj_cc4ca4ae where
    chapters 21-30 had every scene completed but chapter.status was still
    'in_progress'.
    """
    outline_chapters_by_num = {
        c.get("chapter_number"): c for c in (outline.get("chapters") or [])
    }
    repaired: list = []
    for ch in progress.get("chapters", []) or []:
        if ch.get("status") != "in_progress":
            continue
        ch_num = ch.get("chapter_number")
        outline_ch = outline_chapters_by_num.get(ch_num)
        if outline_ch is None:
            continue  # no ground truth → skip defensively
        planned = outline_ch.get("scene_plan", []) or []
        if not planned:
            continue
        if is_chapter_complete(ch.get("scenes", []) or [], planned):
            ch["status"] = "completed"
            repaired.append(ch_num)
    repaired.sort()
    return repaired


def seed_queue(
    mgr: "AutopilotSessionManager",
    outline: dict,
    progress: Optional[dict],
    novel_outline: Optional[dict],
    cfg: ManagedStartConfig,
    projects_dir: Optional[Path] = None,
) -> SeedResult:
    """Translate (outline, progress, novel_outline, cfg) into QueueItems
    appended to mgr.queue. Returns a SeedResult describing what was enqueued.

    Scope handling:
      - "all_planned": every chapter in outline.
      - "range": chapters whose chapter_number is in
        [cfg.start_chapter, cfg.end_chapter] inclusive.

    Trust-the-disk promotion: when `projects_dir` is provided, scan
    `chapters/` for draft.md files in the target scope. For each scene
    whose draft exists but progress.json shows it as pending (or has no
    entry at all), promote to completed via the status ladder. This
    replaces the destructive regeneration path removed 2026-08-20
    (proj_1a7d7fcf: it silently destroyed ch1–ch11 drafts after a
    progress.json drift).

    current_chapter floor: if `progress.current_chapter` is set and > 1,
    raise the effective start to `current_chapter - 1` so a resume
    doesn't restart from `cfg.start_chapter` when the runner had
    actually advanced further. Honors cfg as a lower bound — if the user
    explicitly asked to start at chapter 5 and current_chapter=8, the
    floor wins (start at 7), but if they asked start=12 and current=8,
    start_chapter=12 still wins.

    Idempotent on QueueItem ids: items with deterministic ids already in
    mgr.queue are skipped, so a restart that re-runs seed_queue against a
    non-empty queue grows the queue by zero. The `next_chapter` auto-
    fallback that was removed in v2.1 left no replacement; out-of-scope
    work stays out of scope.

    Pure-ish: the only side effects are `mgr.add_queue(...)` (which
    writes session.json), the optional `_promote_discovered_drafts`
    helper (which writes progress.json), and the target_chapters clamp.
    No file deletion, no checkpoint clearing, no draft removal — the
    regeneration helpers (`regenerate_chapter`, `clear_chapter_drafts`,
    `clear_checkpoint_for_chapter`) remain available for explicit
    user-initiated flows but are NOT called from seed_queue.
    """
    if not outline or not outline.get("chapters"):
        return SeedResult(enqueued=0, scope_used=cfg.scope, fallback_applied=False)
    progress = progress or {}
    chapters = progress.get("chapters", []) or []
    progress_by_chapter = {
        ch.get("chapter_number"): ch for ch in chapters
    }
    all_chapters = outline["chapters"]

    # Determine target chapter list from cfg.scope.
    if cfg.scope == "range":
        target_chapters = [
            ch for ch in all_chapters
            if cfg.start_chapter <= ch.get("chapter_number", 0) <= cfg.end_chapter
        ]
    else:  # "all_planned"
        target_chapters = list(all_chapters)

    # Trust-the-disk promotion: walk chapters/, find drafts that exist
    # but progress.json shows as pending, and flip them to completed.
    # Must run BEFORE _enqueue_for_scope so the freshly-promoted
    # progress state is what the enqueue pass sees (otherwise we'd
    # enqueue scenes whose drafts are already on disk).
    promoted_scenes = 0
    if projects_dir is not None:
        from backend.utils.file_manager import FileManager
        fm = FileManager(projects_dir)
        project_dir = projects_dir / mgr.project_id
        discovered = discover_drafts(project_dir, target_chapters)
        if discovered:
            promoted_scenes = _promote_discovered_drafts(
                fm, mgr.project_id, progress, discovered,
            )
            if promoted_scenes:
                fm.write_json(mgr.project_id, "progress.json", progress)
                # Refresh the lookup dict from the just-promoted progress
                # so _enqueue_for_scope sees the post-promotion state.
                progress_by_chapter = {
                    ch.get("chapter_number"): ch
                    for ch in progress.get("chapters", []) or []
                }
                logger.info(
                    "seed_queue: promoted %d disk-side drafts to completed for %s",
                    promoted_scenes, mgr.project_id,
                )

    # current_chapter floor: don't restart from earlier than where the
    # runner had actually advanced. Honors cfg as a lower bound.
    progress_cc = progress.get("current_chapter")
    floor: Optional[int] = None
    if isinstance(progress_cc, int) and progress_cc > 1:
        floor = progress_cc - 1
    if floor is not None:
        original_count = len(target_chapters)
        target_chapters = [
            ch for ch in target_chapters
            if ch.get("chapter_number", 0) >= floor
        ]
        if len(target_chapters) != original_count:
            logger.info(
                "seed_queue: clamped target to current_chapter-1=%d for %s "
                "(%d → %d chapters)",
                floor, mgr.project_id, original_count, len(target_chapters),
            )

    seeded, matched = _enqueue_for_scope(
        mgr, target_chapters, progress_by_chapter,
    )

    # `enqueued` reports total queue growth: scenes enqueued via
    # _enqueue_for_scope PLUS disk-side promotions (which may add
    # QueueItems for retryable scenes, but mostly they just shrink the
    # "unfinished" set so they subtract from `matched`).
    return SeedResult(
        enqueued=seeded,
        scope_used=cfg.scope, fallback_applied=False,
        matched=matched,
    )


def _enqueue_for_scope(
    mgr: "AutopilotSessionManager",
    target_chapters: list,
    progress_by_chapter: dict,
) -> tuple[int, int]:
    """Enqueue unfinished scenes for the given chapters. Returns
    `(seeded, matched)`:
      - `seeded`: number of NEW queue items added (excludes items already
        in mgr.queue with the same deterministic id).
      - `matched`: total number of unfinished scenes in scope (including
        those already queued).

    Pure-ish (only side effect is via mgr.add_queue).
    """
    # Snapshot existing queue ids once. Reading mgr.queue inside the loop
    # would force a session.json load per scene; this keeps dedup O(n).
    # mgr.load() can return None if the session file doesn't exist yet
    # (e.g. seed_queue called before mgr.start()) — treat that as empty.
    snapshot = mgr.load()
    existing_ids = {q.id for q in (snapshot.queue or [])} if snapshot else set()

    seeded = 0
    matched = 0
    for ch in target_chapters:
        ch_num = ch.get("chapter_number")
        scene_plan = ch.get("scene_plan", []) or []
        if not scene_plan:
            continue
        ch_progress = progress_by_chapter.get(ch_num, {})
        done_nums = {
            s.get("scene_number")
            for s in ch_progress.get("scenes", []) or []
            if s.get("status") in DONE_STATUSES
        }
        for s in scene_plan:
            n = s.get("scene_number")
            if n in done_nums:
                continue
            item_id = f"write-{ch_num}-{n}"
            matched += 1
            if item_id in existing_ids:
                continue
            item = QueueItem(
                id=item_id,
                kind="write_scene",
                chapter_number=ch_num,
                scheduled_at=None,
                priority=scene_priority(ch_num, n),  # row-major; archival uses archive_priority
                payload={"scene_number": n},
            )
            mgr.add_queue(item)
            existing_ids.add(item_id)
            seeded += 1
    return seeded, matched


class AsyncAutopilotRunner:
    """Drives the session queue. Exits when state != running or queue empty."""

    def __init__(
        self,
        mgr: "AutopilotSessionManager",
        executor: AsyncTaskExecutor,
        cadence: str = "balanced",
    ) -> None:
        self._mgr = mgr
        self._executor = executor
        self._cadence_delay = CADENCE_DELAYS.get(cadence, CADENCE_DELAYS["balanced"])

    def _pick_next(self, queue: list) -> Optional[QueueItem]:
        if not queue:
            return None
        return min(queue, key=lambda q: q.priority)

    async def run(self) -> None:
        """Main loop. Exits on state != running, or when queue is exhausted
        (auto-stop in that case)."""
        while True:
            # Layer 1 fix: keep last_heartbeat_at fresh so recover_running_sessions
            # can detect a dead runner. Without this the field is always null and
            # the stale-session downgrade never fires. Called once per iteration
            # (each iteration already has asyncio.sleep(self._cadence_delay)
            # = 0.5/2/5s, well under the 30s staleness threshold).
            self._mgr.heartbeat()
            s = self._mgr.load()
            if s is None or s.state != SessionState.RUNNING:
                return
            item = self._pick_next(s.queue)
            if item is None:
                self._mgr.stop()
                return
            await self._step_one(item, project_id=s.project_id)
            await asyncio.sleep(self._cadence_delay)

    async def _step_one(self, item: QueueItem, project_id: str) -> dict:
        """One iteration. Mirrors sync runner.step() but async + retry-then-pause.

        Retry budget (write_scene only — archival / plan_chapter / fact_guard
        / review keep the pre-fix drop-on-fail behavior):
          - Up to SCENE_WRITE_MAX_RETRIES additional attempts on the SAME
            queue item with growing backoff (SCENE_WRITE_RETRY_BACKOFFS).
          - On every retry the queue item is intentionally NOT dropped so
            that `seed_queue()` (idempotent on item ids) cannot re-add a
            duplicate. If the user resumes, the runner picks the same item.
          - Between backoff sleeps we re-check session state — if the user
            paused/stopped during the wait, we abort the retry and return.
        Failure mode (write_scene only):
          - When all attempts fail, the runner calls fail_current_task()
            AND mgr.pause(reason=...). The queue item stays so resume()
            can re-pick it; the cockpit banner explains what failed.
          - Pre-fix behavior was drop-on-fail, which permanently skipped
            failed scenes. Bug 2026-07-22 on proj_a601cee9 left ch5 s2/s3
            stuck at "retry" forever after an LLM connection drop.
        Failure mode (other kinds):
          - drop-on-fail: drop_queue + fail_current_task + return error.
            The runner's outer loop sees the queue shrink and either picks
            the next item or auto-stops when empty.
        """
        task = CurrentTask(
            kind=item.kind,
            chapter_number=item.chapter_number,
            scene_id=None,
            status="active",
            started_at=None,  # manager fills in if it cares
            description=f"{item.kind} (chapter {item.chapter_number})",
            progress_pct=0,
        )
        self._mgr.set_current_task(task)

        # Non-scene kinds keep the pre-fix drop-on-fail path so the outer
        # loop can auto-stop once the queue drains (e.g. archival fails on
        # an exhausted outline — we don't want the whole session stuck in
        # paused forever, that's a recoverable end-of-work signal handled
        # by the runner's empty-queue auto-stop, not a user-actionable
        # failure).
        if item.kind != "write_scene":
            try:
                result = await self._executor.execute(item, project_id=project_id)
            except Exception as e:
                self._mgr.fail_current_task(error=str(e))
                self._mgr.drop_queue(item.id)
                return {"picked": item.id, "completed": False, "error": str(e)}
            if isinstance(result, dict) and result.get("status") == "fail":
                err = result.get("error", "scene write failed")
                self._mgr.fail_current_task(error=err)
                self._mgr.drop_queue(item.id)
                return {"picked": item.id, "completed": False, "error": err}
            self._mgr.drop_queue(item.id)
            self._mgr.complete_current_task()
            return {"picked": item.id, "completed": True, "result": result}

        last_error: Optional[str] = None
        executor_call = (
            self._executor.execute_stream
            if hasattr(self._executor, "execute_stream")
            else self._executor.execute
        )

        for attempt in range(SCENE_WRITE_MAX_RETRIES + 1):
            if attempt > 0:
                backoff = SCENE_WRITE_RETRY_BACKOFFS[attempt - 1]
                await asyncio.sleep(backoff)
                # Honor user-initiated pause/stop during the backoff window.
                s = self._mgr.load()
                if s is None or s.state != SessionState.RUNNING:
                    return {
                        "picked": item.id, "completed": False,
                        "error": f"aborted during retry backoff (state={s.state.value if s else 'none'})",
                    }

            try:
                result = await executor_call(item, project_id=project_id)
            except Exception as e:
                last_error = str(e)
                logger.warning(
                    "[autopilot] scene write attempt %d/%d failed for %s: %s",
                    attempt + 1, SCENE_WRITE_MAX_RETRIES + 1, item.id, last_error,
                )
                continue

            if isinstance(result, dict) and result.get("status") == "scene_missing":
                # Outline drift: the chapter's scene_plan no longer contains
                # this scene number. Drop the queue item and emit task_fail,
                # but DO NOT pause the session and DO NOT retry — other queue
                # items may still be valid. Pre-fix behavior was to retry 3x
                # then pause the whole session, burning 90+ seconds on a
                # deterministic failure (bug 2026-07-27 proj_bb0375eb:
                # /api/stage3/generate regenerated chapter 10 with fewer
                # scenes, stranding write-10-4 in the queue).
                err = result.get("error", "scene missing in outline")
                self._mgr.fail_current_task(error=err)
                self._mgr.drop_queue(item.id)
                logger.info(
                    "[autopilot] %s dropped (outline drift): %s", item.id, err,
                )
                return {
                    "picked": item.id, "completed": False,
                    "error": err, "skipped": "scene_missing",
                }

            if isinstance(result, dict) and result.get("status") == "fail":
                last_error = result.get("error", "scene write failed")
                logger.warning(
                    "[autopilot] scene write attempt %d/%d returned fail for %s: %s",
                    attempt + 1, SCENE_WRITE_MAX_RETRIES + 1, item.id, last_error,
                )
                continue

            # Success path: drop queue item + complete current_task.
            self._mgr.drop_queue(item.id)
            self._mgr.complete_current_task()

            # Circuit breaker: only on force_passed scenes (spec §5 row 2).
            # Delegate to mgr.record_force_pass_internal() which encapsulates
            # the increment + threshold-check + circuit_open transition in
            # one write-through.
            scene_status = result.get("scene_status") if isinstance(result, dict) else None
            if scene_status == "force_passed":
                self._mgr.record_force_pass_internal()

            return {"picked": item.id, "completed": True, "result": result}

        # Exhausted retries — pause session with reason. Queue item stays so
        # resume() can re-pick it. fail_current_task emits the task_fail
        # history event before the pause so the cockpit shows what went wrong.
        err = last_error or "scene write failed"
        self._mgr.fail_current_task(error=err)
        scene_id = item.payload.get("scene_number", "?") if isinstance(item.payload, dict) else "?"
        reason = f"scene_write_failed:write-{item.chapter_number}-{scene_id}:{err}"
        self._mgr.pause(reason=reason)
        logger.warning(
            "[autopilot] %s failed after %d attempts — pausing session (reason=%s)",
            item.id, SCENE_WRITE_MAX_RETRIES + 1, reason,
        )
        return {"picked": item.id, "completed": False, "error": err}
