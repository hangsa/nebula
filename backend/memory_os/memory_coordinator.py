"""nebula v1.6 Phase 2.5 — MemoryCoordinator: unified context assembly across L0-L4."""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from backend.config import settings
from backend.memory_os.l0_runtime import L0Runtime
from backend.memory_os.l1_hot import L1Hot
from backend.memory_os.l2_warm import L2WarmMemory
from backend.memory_os.l3_cold import L3ColdMemory
from backend.memory_os.l4_narrative import L4NarrativeMemory

logger = logging.getLogger(__name__)


@dataclass
class MemoryContext:
    """Complete assembled memory context for a scene write request."""
    l0_context: str = ""
    l1_context: str = ""
    l2_context: str = ""
    l3_context: str = ""
    l4_context: str = ""
    growth_stage_hint: str = ""


class MemoryCoordinator:
    """
    Coordinates all 5 memory tiers for context assembly.

    Retrieval priority: L0 → L1 → L4 → L2 → L3

    All assembly is tier_0 — deterministic, zero LLM calls.
    L3 and L4 gracefully degrade to empty strings on failure.
    """

    # Growth stage hints by chapter range
    GROWTH_STAGES = [
        (1, 3, "开篇建立期——读者正在了解世界观和角色，控制信息投放节奏，避免信息过载。"),
        (4, 6, "发展展开期——冲突升级，角色关系深化，每章至少推进一个子目标。"),
        (7, 9, "转折升级期——重大转折频发，主线矛盾激化，为高潮积蓄张力。"),
        (10, 15, "高潮冲刺期——最高强度冲突爆发，伏笔密集回收，读者情绪处于峰值。"),
        (16, 20, "收尾解谜期——完成角色弧线，化解主线矛盾，给予读者情感满足。"),
    ]

    def __init__(
        self,
        project_id: str,
        projects_dir: Optional[Path] = None,
    ):
        self.project_id = project_id
        self.projects_dir = Path(projects_dir) if projects_dir else settings.projects_dir
        self._project_dir = self.projects_dir / project_id

    def assemble_for_scene(
        self,
        scene_number: int,
        scene_goal: str = "",
        scene_conflict: str = "",
        character_names: Optional[list[str]] = None,
        chapter_number: int = 1,
        scene_location: Optional[str] = None,
        character_id: Optional[str] = None,
    ) -> MemoryContext:
        """
        Assemble full memory context for a scene write request.

        Returns MemoryContext with all tier strings populated.
        Each tier failure is caught independently.

        Optional kwargs (backward-compatible — call sites may omit):
          - scene_location: canonical location name; if present and the
            project has a map.json, a 地图卡 (map card) is appended to
            l2_context for the Writer to see. Failure to build is
            non-fatal.
          - character_id: optional character scope, passed through to
            the map card for footprint row.
        """
        ctx = MemoryContext()
        character_names = character_names or []

        # L0 — per-request, fresh instance
        ctx.l0_context = self._safe_get("L0", lambda: self._assemble_l0(
            scene_number, scene_goal
        ))

        # L1 — per-request, scan disk for recent scenes
        ctx.l1_context = self._safe_get("L1", lambda: self._assemble_l1(chapter_number))

        # L4 — narrative pattern awareness (sync first to reflect cascade changes)
        self._safe_get("L4", lambda: self._sync_l4(chapter_number))
        ctx.l4_context = self._safe_get("L4", lambda: self._assemble_l4())

        # L2 — chapter summaries + active narrative state
        ctx.l2_context = self._safe_get("L2", lambda: self._assemble_l2())

        # L3 — vector + keyword hybrid search
        l3_query = self._build_l3_query(scene_goal, scene_conflict, character_names)
        ctx.l3_context = self._safe_get("L3", lambda: self._assemble_l3(
            l3_query, chapter_number
        ))

        # Growth stage hint
        ctx.growth_stage_hint = self._compute_growth_stage(chapter_number)

        # Map card — appended at end of l2_context so Writer sees it last
        # (closest to the prose section in the rendered prompt). Only
        # fires when caller passes scene_location AND the project has a
        # map.json. Failure to build the card is non-fatal (caller still
        # gets the rest of the memory tiers).
        if scene_location:
            try:
                from backend.map_system.map_card import build_map_card
                card = build_map_card(
                    self.project_id,
                    scene_location,
                    chapter_number=chapter_number,
                    character_id=character_id,
                )
                if card:
                    prefix = ctx.l2_context.rstrip()
                    ctx.l2_context = (prefix + "\n\n## 地图卡\n" + card) if prefix else ("## 地图卡\n" + card)
            except Exception as e:
                logger.warning("build_map_card failed (non-blocking): %s", e)

        return ctx

    def assemble_for_chapter_advance(
        self,
        chapter_number: int,
        scene_drafts: list[str],
    ) -> None:
        """
        Trigger L3 indexing and L4 sync at chapter advancement.

        Called from advance_chapter endpoint after L2 update.
        """
        # L4 sync (reads StoryOS registries + L2 state)
        self._safe_get("L4", lambda: self._sync_l4(chapter_number))

        # L3 indexing (chunk + embed + index scene drafts)
        self._safe_get("L3", lambda: self._index_l3(chapter_number, scene_drafts))

    # --- Per-tier assembly ---

    def _assemble_l0(self, scene_number: int, scene_goal: str) -> str:
        l0 = L0Runtime()
        l0.set_scene_context(scene_number, scene_goal)
        return l0.get_context_string()

    def _assemble_l1(self, chapter_number: int) -> str:
        l1 = L1Hot()
        chapters_dir = self._project_dir / "chapters"
        if not chapters_dir.exists():
            return l1.get_context_string()

        # Scan existing draft files for recent scenes
        for draft_file in sorted(chapters_dir.glob("ch*_scene_*_draft.md")):
            try:
                text = draft_file.read_text(encoding="utf-8")
                # Parse chapter and scene from filename: chX_scene_Y_draft.md
                name = draft_file.stem  # chX_scene_Y_draft
                parts = name.split("_")
                ch_num = int(parts[0][2:]) if parts[0].startswith("ch") else 0
                sc_num = int(parts[1].replace("scene", "")) if len(parts) > 1 else 0
                l1.append_scene(sc_num, text, summary="", chapter_number=ch_num)
            except Exception:
                pass

        return l1.get_context_string()

    def _assemble_l2(self) -> str:
        l2 = L2WarmMemory(self.project_id, self.projects_dir)
        return l2.get_context_string(max_tokens=2000)

    def _assemble_l3(self, query: str, chapter_number: int) -> str:
        l3 = L3ColdMemory(self.project_id, self.projects_dir)
        if not l3.available or not query.strip():
            return ""
        # Exclude current chapter from L3 search (don't echo current content)
        return l3.search(query, top_k=10, chapter_number=chapter_number)

    def _assemble_l4(self) -> str:
        l4 = L4NarrativeMemory(self.project_id, self.projects_dir)
        return l4.get_context_string(max_tokens=3000)

    def _sync_l4(self, chapter_number: int) -> None:
        l4 = L4NarrativeMemory(self.project_id, self.projects_dir)
        l4.sync_from_registries(chapter_number)

    def _index_l3(self, chapter_number: int, scene_drafts: list[str]) -> int:
        l3 = L3ColdMemory(self.project_id, self.projects_dir)
        return l3.index_chapter(chapter_number, scene_drafts)

    # --- Helpers ---

    def _build_l3_query(
        self,
        scene_goal: str,
        scene_conflict: str,
        character_names: list[str],
    ) -> str:
        """Build a search query for L3 from scene parameters."""
        parts = [scene_goal, scene_conflict] + character_names
        return " ".join(p for p in parts if p)

    def _compute_growth_stage(self, chapter_number: int) -> str:
        """Compute growth stage hint from chapter progress."""
        for start, end, hint in self.GROWTH_STAGES:
            if start <= chapter_number <= end:
                return hint
        # Beyond chapter 20
        return "终章阶段——完成所有角色弧线和叙事承诺。"

    @staticmethod
    def _safe_get(tier_name: str, fn, default: str = "") -> str:
        """Wrapper that catches all exceptions and returns default."""
        try:
            return fn()
        except Exception as e:
            logger.warning("Memory tier %s unavailable: %s", tier_name, e)
            return default
