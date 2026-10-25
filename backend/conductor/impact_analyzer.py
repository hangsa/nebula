"""nebula v1.6 Phase 3b — ImpactAnalyzer for rollback impact propagation."""
import hashlib
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from backend.config import settings
from backend.utils.regex_patterns import SF_LOG_PATTERN, PARAM_PATTERN

logger = logging.getLogger(__name__)

MONITORED_FILES = ["story_dna.json", "world.json", "characters.json", "outline.json"]


class ImpactPriority(str, Enum):
    P0_MUST_REWRITE = "P0"
    P1_SUGGEST_REVIEW = "P1"
    P2_NO_IMPACT = "P2"


@dataclass
class ImpactEntry:
    chapter_number: int          # 0 = all chapters
    scene_numbers: list[int]     # reserved for future SF_LOG scanning
    priority: ImpactPriority
    reason: str
    affected_assets: list[str]


@dataclass
class ImpactReport:
    project_id: str
    modified_files: list[str]
    entries: list[ImpactEntry]
    summary: dict[str, int]      # {"P0": n, "P1": n, "P2": n}


class ImpactAnalyzer:
    """
    Deterministic rollback impact calculator. Zero LLM calls.

    Compares current file hashes against a baseline manifest to detect
    STAGE 1-3 setup file changes, then classifies impact as P0/P1/P2
    based on which file changed and (for outline) the nature of the change.
    """

    def __init__(self, projects_dir: Optional[Path] = None):
        self._projects_dir = Path(projects_dir) if projects_dir else settings.projects_dir

    # --- Public API ---

    def analyze(
        self,
        project_id: str,
        modified_files: Optional[list[str]] = None,
    ) -> ImpactReport:
        """
        Analyze impact of file changes vs baseline.

        Args:
            project_id: Project identifier
            modified_files: Specific files to check (None = auto-detect all 4)

        Returns:
            ImpactReport with classified entries and summary counts
        """
        baseline = self._load_baseline(project_id)
        if not baseline:
            return ImpactReport(
                project_id=project_id,
                modified_files=[],
                entries=[],
                summary={"P0": 0, "P1": 0, "P2": 0},
            )

        files_to_check = modified_files or MONITORED_FILES

        entries: list[ImpactEntry] = []
        modified: list[str] = []

        for fname in files_to_check:
            current_hash = self._compute_file_hash(project_id, fname)
            if not current_hash:
                continue

            baseline_hash = baseline.get(fname, "")
            if current_hash == baseline_hash:
                continue

            modified.append(fname)
            file_entries = self._classify(project_id, fname)
            entries.extend(file_entries)

        # SF_LOG scanning: populate scene_numbers for setting changes
        sf_log_entries = self._scan_sf_logs_for_changes(project_id, modified)
        entries.extend(sf_log_entries)

        summary = {
            "P0": sum(1 for e in entries if e.priority == ImpactPriority.P0_MUST_REWRITE),
            "P1": sum(1 for e in entries if e.priority == ImpactPriority.P1_SUGGEST_REVIEW),
            "P2": sum(1 for e in entries if e.priority == ImpactPriority.P2_NO_IMPACT),
        }

        return ImpactReport(
            project_id=project_id,
            modified_files=modified,
            entries=entries,
            summary=summary,
        )

    def ensure_baseline(self, project_id: str) -> bool:
        """
        Create baseline manifest if it doesn't exist. Idempotent.

        Also saves baseline_outline_snapshot.json for outline content comparison.

        Returns True if baseline was created, False if already existed.
        """
        manifest_path = self._baseline_path(project_id)
        if manifest_path.exists():
            return False

        manifest = {}
        for fname in MONITORED_FILES:
            h = self._compute_file_hash(project_id, fname)
            if h:
                manifest[fname] = h

        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = manifest_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        tmp.replace(manifest_path)

        self._save_outline_snapshot(project_id)

        logger.info("Baseline manifest created for project=%s", project_id)
        return True

    def update_baseline(self, project_id: str) -> None:
        """Update baseline manifest with current file hashes (confirm action)."""
        manifest_path = self._baseline_path(project_id)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)

        manifest = {}
        for fname in MONITORED_FILES:
            h = self._compute_file_hash(project_id, fname)
            if h:
                manifest[fname] = h

        tmp = manifest_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        tmp.replace(manifest_path)

        self._save_outline_snapshot(project_id)

        logger.info("Baseline manifest updated for project=%s", project_id)

    def has_baseline(self, project_id: str) -> bool:
        """Check if baseline manifest exists."""
        return self._baseline_path(project_id).exists()

    def project_exists(self, project_id: str) -> bool:
        """Check if project directory exists."""
        return (self._projects_dir / project_id).is_dir()

    # --- Internal ---

    def _baseline_path(self, project_id: str) -> Path:
        return self._projects_dir / project_id / "baseline_manifest.json"

    def _save_outline_snapshot(self, project_id: str) -> None:
        """Save current outline.json as baseline snapshot for content comparison."""
        outline = self._read_project_json(project_id, "outline.json")
        if not outline:
            return
        snap_path = self._projects_dir / project_id / "baseline_outline_snapshot.json"
        tmp = snap_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(outline, f, ensure_ascii=False, indent=2)
        tmp.replace(snap_path)

    def _read_project_json(self, project_id: str, filename: str) -> Optional[dict]:
        """Read a JSON file from the project directory. Returns None on failure."""
        file_path = self._projects_dir / project_id / filename
        if not file_path.exists():
            return None
        try:
            return json.loads(file_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Failed to read %s/%s: %s", project_id, filename, e)
            return None

    def _load_baseline(self, project_id: str) -> dict[str, str]:
        path = self._baseline_path(project_id)
        if not path.exists():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("Failed to load baseline for %s: %s", project_id, e)
            return {}

    def _compute_file_hash(self, project_id: str, filename: str) -> str:
        """Compute SHA256 hash of a project file. Returns empty string on failure."""
        file_path = self._projects_dir / project_id / filename
        if not file_path.exists():
            return ""
        try:
            content = file_path.read_bytes()
            return hashlib.sha256(content).hexdigest()
        except Exception as e:
            logger.warning("Failed to hash %s/%s: %s", project_id, filename, e)
            return ""

    def _classify(self, project_id: str, filename: str) -> list[ImpactEntry]:
        """Classify impact of a changed file. Delegates to specific handlers."""
        if filename == "story_dna.json":
            return self._classify_story_dna()
        elif filename in ("world.json", "characters.json"):
            return self._classify_setting(filename)
        elif filename == "outline.json":
            return self._classify_outline(project_id)
        return []

    def _classify_story_dna(self) -> list[ImpactEntry]:
        return [ImpactEntry(
            chapter_number=0,
            scene_numbers=[],
            priority=ImpactPriority.P0_MUST_REWRITE,
            reason="story_dna.json 已变更，核心设定修改可能影响全部已完成章节",
            affected_assets=["story_dna.json"],
        )]

    def _classify_setting(self, filename: str) -> list[ImpactEntry]:
        label = "角色设定" if filename == "characters.json" else "世界规则"
        return [ImpactEntry(
            chapter_number=0,
            scene_numbers=[],
            priority=ImpactPriority.P1_SUGGEST_REVIEW,
            reason=f"{filename} 已变更，{label}修改可能影响所有已完成场景",
            affected_assets=[filename],
        )]

    def _classify_outline(self, project_id: str) -> list[ImpactEntry]:
        """Compare outline chapter lists to determine P1 (delete/reorder) vs P2 (add only)."""
        # Read current outline
        current_data = self._read_project_json(project_id, "outline.json") or {}
        current_chapters = current_data.get("chapters", [])
        current_numbers = [ch.get("chapter_number") for ch in current_chapters if ch.get("chapter_number")]

        # Read baseline outline snapshot for content comparison
        old_outline_path = self._projects_dir / project_id / "baseline_outline_snapshot.json"
        if old_outline_path.exists():
            try:
                old_data = json.loads(old_outline_path.read_text(encoding="utf-8"))
                old_numbers = [ch.get("chapter_number") for ch in old_data.get("chapters", []) if ch.get("chapter_number")]
                return self._compare_outline_changes(old_numbers, current_numbers)
            except Exception:
                pass

        # Fallback: without old snapshot, treat as P1 (conservative)
        return [ImpactEntry(
            chapter_number=0,
            scene_numbers=[],
            priority=ImpactPriority.P1_SUGGEST_REVIEW,
            reason="outline.json 已变更，无法判断是否为纯新增章节，建议复核",
            affected_assets=["outline.json"],
        )]

    def _compare_outline_changes(
        self, old_numbers: list[int], new_numbers: list[int]
    ) -> list[ImpactEntry]:
        """Compare old vs new chapter number lists."""
        old_set = set(old_numbers)
        new_set = set(new_numbers)
        removed = old_set - new_set

        if removed:
            return [ImpactEntry(
                chapter_number=0,
                scene_numbers=[],
                priority=ImpactPriority.P1_SUGGEST_REVIEW,
                reason=f"outline.json 已删除或重排章节（基线章号: {sorted(old_numbers)}，当前: {sorted(new_numbers)}）",
                affected_assets=["outline.json"],
            )]

        # Check order
        if old_numbers != new_numbers[:len(old_numbers)]:
            return [ImpactEntry(
                chapter_number=0,
                scene_numbers=[],
                priority=ImpactPriority.P1_SUGGEST_REVIEW,
                reason="outline.json 章节顺序已变更",
                affected_assets=["outline.json"],
            )]

        # Only additions
        added = new_set - old_set
        return [ImpactEntry(
            chapter_number=0,
            scene_numbers=[],
            priority=ImpactPriority.P2_NO_IMPACT,
            reason=f"outline.json 仅新增章节（新增章号: {sorted(added)}），不影响已完成内容",
            affected_assets=["outline.json"],
        )]

    # --- SF_LOG Scanning for scene-level impact ---

    def _scan_sf_logs_for_changes(
        self, project_id: str, modified_files: list[str]
    ) -> list[ImpactEntry]:
        """Scan chapter drafts for SF_LOG tags referencing changed assets.

        Populates ImpactEntry.scene_numbers with specific scene numbers
        where changed assets are referenced in SF_LOG tags.
        """
        entries: list[ImpactEntry] = []
        chapters_dir = self._projects_dir / project_id / "chapters"
        if not chapters_dir.exists():
            return entries

        draft_files = sorted(chapters_dir.glob("ch*_scene_*_draft.md"))
        if not draft_files:
            return entries

        if "characters.json" in modified_files:
            entries.extend(self._scan_character_sf_logs(project_id, draft_files))

        if "world.json" in modified_files:
            entries.extend(self._scan_world_sf_logs(draft_files))

        if "outline.json" in modified_files:
            entries.extend(self._scan_outline_sf_logs(draft_files))

        return entries

    @staticmethod
    def _parse_chapter_scene_from_draft(draft_file: Path) -> tuple[int, int]:
        """Parse chapter and scene numbers from draft filename: chX_scene_Y_draft.md."""
        name = draft_file.stem
        parts = name.split("_")
        ch_num = int(parts[0][2:]) if parts[0].startswith("ch") else 0
        sc_num = int(parts[2]) if len(parts) > 2 else 0
        return ch_num, sc_num

    def _scan_character_sf_logs(
        self, project_id: str, draft_files: list[Path]
    ) -> list[ImpactEntry]:
        """Scan drafts for SF_LOG tags mentioning character names from characters.json."""
        char_data = self._read_project_json(project_id, "characters.json")
        if not char_data:
            return []

        characters = (
            char_data if isinstance(char_data, list)
            else char_data.get("characters", [])
        )
        char_names = {c.get("name", "") for c in characters if c.get("name")}
        if not char_names:
            return []

        character_log_types = {
            "character_emotion", "character_relation_change",
            "character_location_change", "character_physical_change",
        }
        affected: dict[int, set[int]] = defaultdict(set)

        for draft_file in draft_files:
            ch_num, sc_num = self._parse_chapter_scene_from_draft(draft_file)
            try:
                text = draft_file.read_text(encoding="utf-8")
            except Exception:
                continue

            for match in SF_LOG_PATTERN.finditer(text):
                log_type = match.group(1)
                if log_type not in character_log_types:
                    continue
                params_str = match.group(2)
                for key, value in PARAM_PATTERN.findall(params_str):
                    if key in ("char", "char_a", "char_b"):
                        if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
                            value = value[1:-1]
                        if value in char_names:
                            affected[ch_num].add(sc_num)
                            break

        return self._build_chapter_entries(
            affected, "characters.json",
            "characters.json 已变更，第{ch}章包含角色相关SF_LOG标签",
            "characters.json 已变更，但未在已完成章节中发现角色相关SF_LOG标签",
        )

    def _scan_world_sf_logs(self, draft_files: list[Path]) -> list[ImpactEntry]:
        """Scan drafts for world-related SF_LOG tags (registry_create cost/power_system, knowledge_gain)."""
        affected: dict[int, set[int]] = defaultdict(set)

        for draft_file in draft_files:
            ch_num, sc_num = self._parse_chapter_scene_from_draft(draft_file)
            try:
                text = draft_file.read_text(encoding="utf-8")
            except Exception:
                continue

            for match in SF_LOG_PATTERN.finditer(text):
                log_type = match.group(1)
                if log_type == "knowledge_gain":
                    affected[ch_num].add(sc_num)
                elif log_type == "registry_create":
                    params_str = match.group(2)
                    for key, value in PARAM_PATTERN.findall(params_str):
                        if key == "type":
                            if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
                                value = value[1:-1]
                            if value in ("cost", "power_system"):
                                affected[ch_num].add(sc_num)
                                break

        return self._build_chapter_entries(
            affected, "world.json",
            "world.json 已变更，第{ch}章包含世界规则相关SF_LOG标签",
            "world.json 已变更，但未在已完成章节中发现世界规则相关SF_LOG标签",
        )

    def _scan_outline_sf_logs(self, draft_files: list[Path]) -> list[ImpactEntry]:
        """Flag all completed chapter scenes as affected by outline change."""
        reason = "outline.json 已变更，结构变化可能影响所有已完成场景"
        affected: dict[int, set[int]] = defaultdict(set)
        for draft_file in draft_files:
            ch_num, sc_num = self._parse_chapter_scene_from_draft(draft_file)
            if ch_num and sc_num:
                affected[ch_num].add(sc_num)

        entries: list[ImpactEntry] = []
        for ch_num in sorted(affected):
            entries.append(ImpactEntry(
                chapter_number=ch_num,
                scene_numbers=sorted(affected[ch_num]),
                priority=ImpactPriority.P1_SUGGEST_REVIEW,
                reason=reason,
                affected_assets=["outline.json"],
            ))
        return entries or [
            ImpactEntry(chapter_number=0, scene_numbers=[],
                        priority=ImpactPriority.P1_SUGGEST_REVIEW,
                        reason=reason, affected_assets=["outline.json"]),
        ]

    @staticmethod
    def _build_chapter_entries(
        affected: dict[int, set[int]],
        asset: str,
        found_template: str,
        not_found_reason: str,
    ) -> list[ImpactEntry]:
        """Build ImpactEntry list from affected chapter→scenes mapping."""
        entries: list[ImpactEntry] = []
        for ch_num in sorted(affected):
            entries.append(ImpactEntry(
                chapter_number=ch_num,
                scene_numbers=sorted(affected[ch_num]),
                priority=ImpactPriority.P1_SUGGEST_REVIEW,
                reason=found_template.format(ch=ch_num),
                affected_assets=[asset],
            ))
        return entries or [
            ImpactEntry(chapter_number=0, scene_numbers=[],
                        priority=ImpactPriority.P1_SUGGEST_REVIEW,
                        reason=not_found_reason, affected_assets=[asset]),
        ]
