"""nebula v1.6 Phase 2.4 — L4NarrativeMemory: StoryOS registry summary for narrative awareness."""

import json
import logging
from pathlib import Path
from typing import Optional

from backend.config import settings

logger = logging.getLogger(__name__)

REGISTRY_FILES = {
    "conflict": "conflicts.json",
    "mystery": "mysteries.json",
    "twist": "twists.json",
    "goal": "goals.json",
    "promise": "promises.json",
    "reveal": "reveals.json",
    "expectation": "expectations.json",
    "foreshadowing": "foreshadowing.json",
}


class L4NarrativeMemory:
    """
    L4 Narrative Memory — narrative pattern and structure awareness.

    Reads StoryOS registries and L2 narrative state to produce a ~3K token
    summary of the narrative landscape. Zero LLM calls — all deterministic.

    Storage: projects/{project_id}/memory/l4/narrative_patterns.json

    Updated at chapter advancement.
    """

    MAX_TOKENS = 3000

    def __init__(
        self,
        project_id: str,
        projects_dir: Optional[Path] = None,
    ):
        self.project_id = project_id
        self.projects_dir = Path(projects_dir) if projects_dir else settings.projects_dir
        self._project_dir = self.projects_dir / project_id
        self._l4_dir = self._project_dir / "memory" / "l4"
        self._storyos_dir = self._project_dir / "storyos"
        self._l2_dir = self._project_dir / "memory" / "l2"

    def _ensure_dir(self) -> None:
        self._l4_dir.mkdir(parents=True, exist_ok=True)

    def _read_json(self, path: Path) -> dict:
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _read_list(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def sync_from_registries(self, chapter_number: int) -> None:
        """
        Read all 8 StoryOS registries + L2 active narrative state + outline data.
        Compute narrative pattern summary and persist to disk.
        """
        self._ensure_dir()

        patterns: dict = {
            "chapter_number": chapter_number,
            "asset_counts": {},
            "narrative_state": {},
            "foreshadowing_health": {},
            "chapter_roles": [],
        }

        # 1. Count assets by type and status
        for asset_type, filename in REGISTRY_FILES.items():
            items = self._read_list(self._storyos_dir / filename)
            status_counts: dict[str, int] = {}
            for item in items:
                status = item.get("status", "unknown")
                status_counts[status] = status_counts.get(status, 0) + 1
            patterns["asset_counts"][asset_type] = {
                "total": len(items),
                "by_status": status_counts,
            }

        # 2. Active narrative state (from L2)
        l2_state = self._read_json(self._l2_dir / "active_narrative_state.json")
        if l2_state:
            patterns["narrative_state"] = {
                "unresolved_conflicts": l2_state.get("unresolved_conflicts", []),
                "open_mysteries": l2_state.get("open_mysteries", []),
                "pending_promises": l2_state.get("pending_promises", []),
                "unrevealed_twists": l2_state.get("unrevealed_twists", []),
                "active_goals": l2_state.get("active_goals", []),
                "planted_foreshadowings": l2_state.get("planted_foreshadowings", []),
            }

        # 3. Foreshadowing health
        fs_items = self._read_list(self._storyos_dir / "foreshadowing.json")
        fs_statuses = {}
        for fs in fs_items:
            s = fs.get("status", "unknown")
            fs_statuses[s] = fs_statuses.get(s, 0) + 1

        # Find stale foreshadowings (planted > 5 chapters ago with 0 clues)
        stale = []
        for fs in fs_items:
            if fs.get("status") == "planted":
                created_ch = fs.get("created_chapter", 0)
                clues = fs.get("clues", [])
                if chapter_number - created_ch >= 5 and len(clues) == 0:
                    stale.append(fs.get("id", "unknown"))

        patterns["foreshadowing_health"] = {
            "by_status": fs_statuses,
            "stale_without_clues": stale,
            "stale_count": len(stale),
        }

        # 4. Chapter narrative role distribution (from outline if available)
        outline_path = self._project_dir / "outlines" / "outline.json"
        outline = self._read_json(outline_path)
        chapters = outline.get("chapters", [])
        for ch in chapters:
            ch_num = ch.get("number", 0)
            if ch_num <= chapter_number:
                patterns["chapter_roles"].append({
                    "chapter": ch_num,
                    "narrative_role": ch.get("narrative_role", "unknown"),
                })

        # Persist
        self._write_json(self._l4_dir / "narrative_patterns.json", patterns)

    def get_context_string(self, max_tokens: int = 3000) -> str:
        """
        Generate a compact narrative context string for the Writer.

        Returns a formatted Chinese string with:
        - Active narrative assets (counts + IDs)
        - Foreshadowing health report
        - Chapter narrative role distribution
        """
        patterns = self._read_json(self._l4_dir / "narrative_patterns.json")
        if not patterns:
            return ""

        parts: list[str] = []
        token_estimate = 0
        # Rough estimate: 1 token ≈ 1.5 Chinese chars

        # 1. Active narrative assets
        asset_lines = ["【叙事资产状态】"]
        counts = patterns.get("asset_counts", {})
        for asset_type, data in sorted(counts.items()):
            total = data.get("total", 0)
            by_status = data.get("by_status", {})
            if total == 0:
                continue
            status_str = ", ".join(f"{s}:{c}" for s, c in sorted(by_status.items()))
            asset_lines.append(f"  {asset_type}: {total}条 ({status_str})")
        parts.append("\n".join(asset_lines))
        token_estimate += len(parts[-1]) // 2

        # 2. Active narrative state (from L2)
        state = patterns.get("narrative_state", {})
        state_lines = ["【当前叙事状态】"]
        if state.get("unresolved_conflicts"):
            state_lines.append(
                f"  未解决冲突: {', '.join(state['unresolved_conflicts'][:5])}"
            )
        if state.get("open_mysteries"):
            state_lines.append(
                f"  未解谜团: {', '.join(state['open_mysteries'][:5])}"
            )
        if state.get("pending_promises"):
            state_lines.append(
                f"  待兑现承诺: {', '.join(state['pending_promises'][:5])}"
            )
        if state.get("unrevealed_twists"):
            state_lines.append(
                f"  未揭示转折: {', '.join(state['unrevealed_twists'][:5])}"
            )
        if state.get("active_goals"):
            state_lines.append(
                f"  进行中目标: {', '.join(state['active_goals'][:5])}"
            )
        if len(state_lines) > 1:
            parts.append("\n".join(state_lines))
            token_estimate += len(parts[-1]) // 2

        # 3. Foreshadowing health
        fs_health = patterns.get("foreshadowing_health", {})
        fs_lines = ["【伏笔健康度】"]
        by_status = fs_health.get("by_status", {})
        if by_status:
            fs_lines.append(f"  状态分布: {', '.join(f'{s}:{c}' for s, c in sorted(by_status.items()))}")
        stale = fs_health.get("stale_without_clues", [])
        if stale:
            fs_lines.append(f"  ⚠ 长期无线索伏笔: {', '.join(stale[:5])}")
        if len(fs_lines) > 1:
            parts.append("\n".join(fs_lines))
            token_estimate += len(parts[-1]) // 2

        # 4. Chapter narrative roles
        roles = patterns.get("chapter_roles", [])
        if roles:
            role_lines = ["【章节叙事角色分布】"]
            # Show last 5 chapters
            for ch in roles[-5:]:
                role_lines.append(f"  第{ch['chapter']}章: {ch['narrative_role']}")
            parts.append("\n".join(role_lines))
            token_estimate += len(parts[-1]) // 2

        # 5. Actionable hints
        hints = self._build_actionable_hints(patterns)
        if hints:
            parts.append(hints)
            token_estimate += len(hints) // 2

        # Enforce token budget
        result = "\n\n".join(parts)
        max_chars = max_tokens * 2  # rough: 1 token ≈ 2 chars for Chinese
        if len(result) > max_chars:
            result = result[:max_chars] + "\n..."

        return result

    def _write_json(self, path: Path, data: dict) -> None:
        self._ensure_dir()
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(path)

    def _build_actionable_hints(self, patterns: dict) -> str:
        """Generate actionable writing hints from narrative state gaps."""
        hints: list[str] = []
        state = patterns.get("narrative_state", {})

        open_mysteries = state.get("open_mysteries", [])
        if open_mysteries:
            hints.append(f"  💡 未解谜团({len(open_mysteries)}个): 考虑在本章提供线索推进谜团揭示")

        unresolved = state.get("unresolved_conflicts", [])
        if unresolved:
            hints.append(f"  💡 未解决冲突({len(unresolved)}个): 考虑升级冲突或触发转折")

        pending = state.get("pending_promises", [])
        if pending:
            hints.append(f"  💡 待兑现承诺({len(pending)}个): 考虑在本章兑现或推进承诺进展")

        unrevealed = state.get("unrevealed_twists", [])
        if unrevealed:
            hints.append(f"  💡 未揭示转折({len(unrevealed)}个): 考虑铺设伏笔为转折做准备")

        fs_health = patterns.get("foreshadowing_health", {})
        stale = fs_health.get("stale_without_clues", [])
        if stale:
            hints.append(f"  ⚠ 长期无线索伏笔({len(stale)}个): 建议尽快添加线索或揭示伏笔")

        if not hints:
            return ""

        return "【写作建议】\n" + "\n".join(hints)
