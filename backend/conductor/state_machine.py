from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from backend.conductor.checkpoint import CheckpointManager


class Stage(str, Enum):
    INIT = "INIT"
    STAGE1 = "STAGE1"
    STAGE2 = "STAGE2"
    STAGE3 = "STAGE3"
    STAGE4 = "STAGE4"
    STAGE5 = "STAGE5"
    STAGE6 = "STAGE6"
    COMPLETED = "COMPLETED"


STAGE_ORDER = [
    Stage.INIT,
    Stage.STAGE1,
    Stage.STAGE2,
    Stage.STAGE3,
    Stage.STAGE4,
    Stage.STAGE5,
    Stage.STAGE6,
    Stage.COMPLETED,
]

# Each check tuple: (file, json_path, check_fn, error_msg)
PRECONDITIONS: dict[Stage, list[tuple]] = {
    Stage.STAGE1: [
        ("project.json", "title", lambda v: bool(v), "title 不能为空"),
        ("project.json", "genre", lambda v: bool(v), "genre 不能为空"),
    ],
    Stage.STAGE2: [
        (
            "concept_and_dna.json",
            "story_dna.core_contradiction.statement",
            lambda v: bool(v),
            "core_contradiction.statement 不能为空",
        ),
    ],
    Stage.STAGE3: [
        (
            "characters.json",
            "characters",
            lambda v: isinstance(v, list) and len(v) >= 1,
            "characters 数组长度必须 >= 1",
        ),
        (
            "world.json",
            "",
            lambda v: v is not None,
            "world.json 不能为空",
        ),
    ],
    Stage.STAGE4: [
        ("outline.json", "chapters", lambda v: isinstance(v, list) and len(v) >= 1, "chapters 数组不能为空"),
        ("outline.json", "chapters[0].scene_plan", lambda v: bool(v), "chapters[0].scene_plan 不能为空"),
    ],
    Stage.COMPLETED: [
        ("exports/novel.md", "", lambda v: v is not None, "exports/novel.md 不存在"),
    ],
}

# STAGE5 and STAGE6 preconditions
PRECONDITIONS[Stage.STAGE5] = [
    (
        "progress.json",
        "chapters",
        lambda v: isinstance(v, list)
        and all(
            s.get("status") in ("completed", "force_passed")
            for ch in v
            for s in ch.get("scenes", [])
        ),
        "所有 Scene 状态必须为 completed 或 force_passed",
    ),
]
PRECONDITIONS[Stage.STAGE6] = [
    (
        "diagnosis_report.json",
        "issues",
        lambda v: isinstance(v, list)
        and all(
            i.get("status") in ("resolved", "accepted")
            for i in v
            if i.get("severity") == "P0"
        ),
        "所有 P0 问题必须为 resolved 或 accepted",
    ),
]


@dataclass
class TransitionResult:
    allowed: bool
    from_stage: Stage
    to_stage: Stage
    missing_files: list[str] = field(default_factory=list)
    failed_checks: list[str] = field(default_factory=list)
    message: str = ""


class ProjectNotFoundError(Exception):
    """Raised by state machine operations when the project directory
    does not exist. Lets the API layer map to HTTP 404 without coupling
    to error-message string content."""
    pass


class StageStateMachine:
    def __init__(self, projects_dir: Path):
        self.projects_dir = Path(projects_dir)

    def _project_dir(self, project_id: str) -> Path:
        return self.projects_dir / project_id

    def _read_json(self, project_id: str, filename: str) -> Optional[dict]:
        path = self._project_dir(project_id) / filename
        if not path.exists():
            return None
        import json
        # Handle non-JSON files (e.g., novel.md for COMPLETED check)
        if not filename.endswith(".json"):
            return {"_file_exists": True, "_path": str(path)}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Normalize old-format outline.json (single chapter without chapters wrapper)
        if filename == "outline.json" and "chapters" not in data and "scene_plan" in data:
            data = {"chapters": [data]}
        return data

    def get_current_stage(self, project_id: str) -> Stage:
        data = self._read_json(project_id, "project.json")
        if not data:
            return Stage.INIT
        return Stage(data.get("current_stage", "INIT"))

    def get_next_stage(self, current: Stage) -> Optional[Stage]:
        try:
            idx = STAGE_ORDER.index(current)
            if idx < len(STAGE_ORDER) - 1:
                return STAGE_ORDER[idx + 1]
        except ValueError:
            pass
        return None

    def get_precondition(self, target_stage: Stage) -> list[tuple]:
        return PRECONDITIONS.get(target_stage, [])

    def _resolve_nested(self, data: dict, path: str) -> Optional[object]:
        keys = path.replace("[", ".").replace("]", "").split(".")
        current = data
        for key in keys:
            if isinstance(current, dict):
                current = current.get(key)
            elif isinstance(current, list):
                try:
                    current = current[int(key)]
                except (IndexError, ValueError):
                    return None
            else:
                return None
            if current is None:
                return None
        return current

    def transition_check(self, project_id: str, target_stage: Stage) -> TransitionResult:
        current = self.get_current_stage(project_id)
        expected_next = self.get_next_stage(current)

        if expected_next is None:
            return TransitionResult(
                allowed=False,
                from_stage=current,
                to_stage=target_stage,
                message=f"无法从 {current.value} 推进：已到达最终阶段",
            )

        if target_stage != expected_next:
            return TransitionResult(
                allowed=False,
                from_stage=current,
                to_stage=target_stage,
                message=f"无效的阶段转换：{current.value} → {target_stage.value}，"
                f"期望 → {expected_next.value}",
            )

        checks = self.get_precondition(target_stage)
        missing_files: list[str] = []
        failed_checks: list[str] = []
        seen_missing: set[str] = set()

        for filename, json_path, check_fn, error_msg in checks:
            if filename in seen_missing:
                continue

            data = self._read_json(project_id, filename)
            if data is None:
                missing_files.append(filename)
                seen_missing.add(filename)
                continue

            if json_path:
                value = self._resolve_nested(data, json_path)
            else:
                value = data

            if not check_fn(value):
                failed_checks.append(f"{filename}: {error_msg}")

        if missing_files or failed_checks:
            parts = []
            if missing_files:
                parts.append(f"缺少文件: {', '.join(missing_files)}")
            if failed_checks:
                parts.append(f"检查未通过: {'; '.join(failed_checks)}")
            return TransitionResult(
                allowed=False,
                from_stage=current,
                to_stage=target_stage,
                missing_files=missing_files,
                failed_checks=failed_checks,
                message=" | ".join(parts),
            )

        return TransitionResult(
            allowed=True,
            from_stage=current,
            to_stage=target_stage,
            message=f"阶段转换 {current.value} → {target_stage.value} 允许",
        )

    def advance(self, project_id: str, target_stage: Stage) -> TransitionResult:
        result = self.transition_check(project_id, target_stage)

        if not result.allowed:
            return result

        project_file = self._project_dir(project_id) / "project.json"
        data = self._read_json(project_id, "project.json")
        if data is None:
            return TransitionResult(
                allowed=False,
                from_stage=result.from_stage,
                to_stage=target_stage,
                message="project.json 不存在",
            )

        data["current_stage"] = target_stage.value
        transition = {
            "from_stage": result.from_stage.value,
            "to_stage": target_stage.value,
            "timestamp": datetime.utcnow().isoformat(),
        }
        if "stage_history" not in data:
            data["stage_history"] = []
        data["stage_history"].append(transition)

        import json

        tmp_file = project_file.with_suffix(".tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp_file.replace(project_file)

        return result

    def regress_to_stage3(self, project_id: str) -> TransitionResult:
        """反向推进到 STAGE3（章节大纲阶段）：清空 stage4 运行时产物 + current_stage=STAGE3。

        v2.1 行为变更：早期版本写回 INIT，会触发 STAGE3 前置检查 STAGE_NOT_READY
        导致用户无法重新生成全文大纲（proj_1a7d7fcf 2026-08-23 反馈）。改写 STAGE3
        后，章节大纲端点（/stage3/generate、/stage3/regenerate-chapter-outline、
        /stage3/regenerate-novel-outline-section）的 STAGE_ORDER.index 检查均通过，
        用户可重新生成章节大纲（走 wizard 或 regenerate-chapter-outline 路径）。
        STAGE3 不要求 diagnosis_report.json，与上一节 STAGE4 写作阶段的产物清空
        解耦。

        原子性范围（v2.1 明确权衡）：任何文件 IO 失败都抛 OSError，不回滚。
        补救路径：用户重试。regress_to_stage3 是幂等的（已删除文件跳过、
        project.json stage 字段是覆盖写）。若未来需要严格原子性，
        可引入"先备份 .reset_backup/ 再原子提交"两步提交模式。

        保留：concept_and_dna / world / characters / novel_outline / outline
        （init 阶段产物） + stage_history（向前追溯记录）。

        项目目录不存在时抛 ProjectNotFoundError（由 API 层映射为 HTTP 404）。
        """
        import json as _json

        project_dir = self._project_dir(project_id)
        if not project_dir.exists():
            raise ProjectNotFoundError(f"项目 {project_id} 不存在")

        # Capture the actual current stage BEFORE mutating project.json, so
        # the returned TransitionResult.from_stage honestly reports where we
        # came from (e.g. STAGE4 → STAGE3, not STAGE3 → STAGE3).
        current_stage = self.get_current_stage(project_id)

        # 1. 删除章节草稿（idempotent：glob miss 是 no-op）
        chapters_dir = project_dir / "chapters"
        if chapters_dir.exists():
            for f in chapters_dir.glob("ch*_scene_*_draft.md"):
                f.unlink()

        # 2. 删除运行时状态文件
        for rel in (
            "progress.json",
            CheckpointManager.CHECKPOINT_FILENAME,
            CheckpointManager.LEGACY_CHECKPOINT_FILENAME,
        ):
            p = project_dir / rel
            if p.exists():
                p.unlink()

        # 3. 清 autopilot 流式 chunk 缓冲（保留父目录方便 SceneChunkStore 复用）
        chunks_dir = project_dir / "autopilot" / "chunks"
        if chunks_dir.exists():
            for f in chunks_dir.glob("*.jsonl"):
                f.unlink()

        # 4. 改 project.json current_stage=STAGE3（原子写：tmp + replace）
        project_file = project_dir / "project.json"
        data = self._read_json(project_id, "project.json") or {}
        data["current_stage"] = Stage.STAGE3.value
        tmp_file = project_file.with_suffix(".tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            _json.dump(data, f, ensure_ascii=False, indent=2)
        tmp_file.replace(project_file)

        return TransitionResult(
            allowed=True,
            from_stage=current_stage,
            to_stage=Stage.STAGE3,
            message=f"{current_stage.value} → STAGE3 已重置",
        )
