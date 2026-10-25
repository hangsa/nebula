import re as _re
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException

from backend.config import settings
from backend.conductor.checkpoint import CheckpointManager
from backend.conductor.state_machine import ProjectNotFoundError, StageStateMachine
from backend.models.project import Project
from backend.utils.file_manager import FileManager

router = APIRouter(prefix="/api/project", tags=["project"])
fm = FileManager(settings.projects_dir)


def _resolve_display_title(file_manager: FileManager, project_id: str, fallback: str) -> str:
    """Pick the title shown on the bookshelf.

    Priority: concept_and_dna.json's `concept.title` (set by init wizard
    step 1) when non-empty, else the project.json title (set at create
    time from the user's explicit title or intent prefix), else
    `fallback` (the long-standing "未命名" sentinel).

    This is a read-time derivation so it works for every existing
    project without migration, and naturally tracks concept edits
    without a parallel write to project.json on each concept save.
    """
    concept_doc = file_manager.read_json(project_id, "concept_and_dna.json")
    if isinstance(concept_doc, dict):
        concept = concept_doc.get("concept")
        if isinstance(concept, dict):
            concept_title = concept.get("title")
            if isinstance(concept_title, str) and concept_title.strip():
                return concept_title
    return fallback


@router.get("/list")
async def list_projects():
    projects = []
    projects_dir = settings.projects_dir
    # Use a fresh FileManager so the current settings.projects_dir is honored
    # (the module-level `fm` is bound at import time and would not pick up
    # test-time changes to settings.projects_dir).
    fm_local = FileManager(projects_dir)
    if projects_dir.exists():
        proj_dirs = [d for d in projects_dir.iterdir() if d.is_dir()]
        proj_dirs.sort(key=lambda d: d.name, reverse=True)
        for proj_dir in proj_dirs:
            proj_file = proj_dir / "project.json"
            if not proj_file.exists():
                continue
            try:
                data = fm_local.read_json(proj_dir.name, "project.json")
                if data:
                    latest_mtime = proj_file.stat().st_mtime
                    for entry in proj_dir.iterdir():
                        try:
                            if entry.is_file():
                                latest_mtime = max(latest_mtime, entry.stat().st_mtime)
                            elif entry.is_dir():
                                for sub in entry.rglob("*"):
                                    if sub.is_file():
                                        latest_mtime = max(latest_mtime, sub.stat().st_mtime)
                        except OSError:
                            continue
                    fallback_title = data.get("title", "未命名") or "未命名"
                    chapter_count = 0
                    outline_file = proj_dir / "outline.json"
                    if outline_file.exists():
                        try:
                            outline = fm_local.read_json(proj_dir.name, "outline.json")
                            chapters = outline.get("chapters", []) if outline else []
                            if isinstance(chapters, list):
                                chapter_count = len(chapters)
                        except Exception:
                            chapter_count = 0
                    word_count = 0
                    chapters_dir = proj_dir / "chapters"
                    if chapters_dir.exists() and chapters_dir.is_dir():
                        for draft_file in chapters_dir.iterdir():
                            if not draft_file.is_file() or not draft_file.name.endswith(".md"):
                                continue
                            try:
                                text = draft_file.read_text(encoding="utf-8")
                                visible = _re.sub(r"<!--.*?-->", "", text, flags=_re.DOTALL)
                                word_count += len(visible)
                            except (OSError, UnicodeDecodeError):
                                continue
                    projects.append({
                        "id": data.get("id", proj_dir.name),
                        "title": _resolve_display_title(
                            fm_local, proj_dir.name, fallback_title,
                        ),
                        "genre": data.get("genre", ""),
                        "current_stage": data.get("current_stage", "INIT"),
                        "created_at": data.get("created_at", ""),
                        "chapter_count": chapter_count,
                        "word_count": word_count,
                        "updated_at": latest_mtime,
                        "min_words": data.get("min_words", 2000),
                        "target_total_words": data.get("target_total_words", 1_000_000),
                        "target_length_category": data.get("target_length_category", "标准商业连载"),
                    })
            except Exception:
                continue

    return {
        "error": False,
        "code": "OK",
        "message": "",
        "detail": projects,
    }


@router.post("/create")
async def create_project(data: dict):
    # Title is the only free-text identity we capture at create time.
    # Creative intent is collected later in the init wizard step 1
    # (CreativeDivergenceStep) and lives in `creative_divergence.json`.
    title = (data.get("title") or "").strip()
    if not title:
        raise HTTPException(
            status_code=400,
            detail={
                "error": True,
                "code": "VALIDATION_ERROR",
                "message": "项目名称必填",
                "detail": {},
            },
        )
    genre = data.get("genre", "cool_novel")
    # Per-chapter target is uniform across the new length options (短篇快穿 /
    # 标准商业连载 / 宏大史诗巨著), each ~2000 字/章 — see CreateProjectCard.tsx.
    # Old clients still send `min_words` directly; accept it but default to 2000.
    min_words = data.get("min_words", 2000)
    target_total_words = data.get("target_total_words", 1_000_000)
    target_length_category = data.get("target_length_category", "标准商业连载")

    project_id = f"proj_{uuid.uuid4().hex[:8]}"
    project = Project(
        id=project_id,
        title=title,
        genre=genre,
        min_words=min_words,
        target_total_words=target_total_words,
        target_length_category=target_length_category,
        current_stage="INIT",
        created_at=datetime.utcnow().isoformat(),
    )

    fm.write_json(project_id, "project.json", project.model_dump())

    return {
        "error": False,
        "code": "OK",
        "message": "项目创建成功",
        "detail": project.model_dump(),
    }


@router.delete("/{project_id}")
async def delete_project(project_id: str):
    if not fm.project_exists(project_id):
        raise HTTPException(
            status_code=404,
            detail={
                "error": True,
                "code": "PROJECT_NOT_FOUND",
                "message": f"项目 {project_id} 不存在",
                "detail": {},
            },
        )
    fm.delete_project(project_id)
    return {
        "error": False,
        "code": "OK",
        "message": "项目已删除",
        "detail": {"project_id": project_id},
    }


@router.get("/{project_id}/reset-preview")
async def reset_preview(project_id: str):
    """列出 /reset 将删除的文件与计数，用于前端 ConfirmDialog 文案。

    返回 draft_count（chapters/ 下 ch*_scene_*_draft.md 文件数）、
    has_progress / has_checkpoint / has_chunks 布尔值。项目目录不存在时
    返回 404，与 `delete_project` 一致；chapters / autopilot 子目录缺失则
    按零计数返回（与 /reset 的幂等行为一致）。
    """
    project_dir = settings.projects_dir / project_id
    if not project_dir.exists():
        raise HTTPException(
            status_code=404,
            detail={
                "error": True,
                "code": "PROJECT_NOT_FOUND",
                "message": f"项目 {project_id} 不存在",
                "detail": {},
            },
        )
    chapters_dir = project_dir / "chapters"
    draft_count = (
        sum(1 for f in chapters_dir.glob("ch*_scene_*_draft.md") if f.is_file())
        if chapters_dir.exists() else 0
    )
    chunks_dir = project_dir / "autopilot" / "chunks"
    has_chunks = chunks_dir.exists() and any(chunks_dir.glob("*.jsonl"))
    return {
        "draft_count": draft_count,
        "has_progress": (project_dir / "progress.json").exists(),
        "has_checkpoint": any(
            (project_dir / name).exists()
            for name in (
                CheckpointManager.CHECKPOINT_FILENAME,
                CheckpointManager.LEGACY_CHECKPOINT_FILENAME,
            )
        ),
        "has_chunks": has_chunks,
    }


@router.post("/{project_id}/reset")
async def reset_to_stage3(project_id: str):
    """原子地清空章节草稿 + 运行时状态，并将 current_stage 写回 STAGE3（章节大纲阶段）。

    保留：concept/world/character/novel_outline/outline 等 init 阶段产物
    + stage_history（向后追溯能力）。

    v2.1 行为变更：早期版本写回 INIT，会导致 STAGE3 endpoint 拒绝
    （STAGE_NOT_READY）；改写 STAGE3 后，/stage3/generate、
    /stage3/regenerate-chapter-outline、/stage3/regenerate-novel-outline-section
    等章节大纲端点均可正常调用，用户可重新生成章节大纲。

    不实现严格事务回滚：中途失败时已删除的文件不回滚，由前端 toast 报错
    并由用户重试。regress_to_stage3 幂等（已删除文件跳过）。

    错误：ProjectNotFoundError → 404；其他 OSError → 500（FastAPI 默认）。
    """
    sm = StageStateMachine(settings.projects_dir)
    try:
        sm.regress_to_stage3(project_id)
    except ProjectNotFoundError as e:
        raise HTTPException(
            status_code=404,
            detail={
                "error": True,
                "code": "PROJECT_NOT_FOUND",
                "message": str(e),
                "detail": {},
            },
        )
    return {
        "error": False,
        "code": "OK",
        "message": "项目已重置到章节大纲阶段",
        "detail": {"project_id": project_id},
    }


@router.post("/bulk-delete")
async def bulk_delete_projects(data: dict):
    ids = data.get("project_ids")
    if not isinstance(ids, list) or not ids:
        raise HTTPException(
            status_code=400,
            detail={
                "error": True,
                "code": "VALIDATION_ERROR",
                "message": "project_ids 必须是非空数组",
                "detail": {},
            },
        )

    # Use a fresh FileManager so the current settings.projects_dir is honored
    # (the module-level `fm` is bound at import time and would not pick up
    # test-time changes to settings.projects_dir).
    fm_local = FileManager(settings.projects_dir)

    deleted: list = []
    failed: list = []
    for pid in ids:
        if not isinstance(pid, str) or not pid:
            failed.append({"id": str(pid), "error": "invalid_id"})
            continue
        try:
            if not fm_local.project_exists(pid):
                failed.append({"id": pid, "error": "not_found"})
                continue
            fm_local.delete_project(pid)
            deleted.append(pid)
        except Exception as e:
            failed.append({"id": pid, "error": str(e)})

    return {
        "error": False,
        "code": "OK",
        "message": f"已删除 {len(deleted)} 个，失败 {len(failed)} 个",
        "detail": {
            "deleted": deleted,
            "failed": failed,
            "deleted_count": len(deleted),
            "failed_count": len(failed),
        },
    }


_KNOWN_STAGES = ("INIT", "STAGE1", "STAGE2", "STAGE3", "STAGE4", "STAGE5", "STAGE6", "COMPLETED")
_SF_LOG_COMMENT_RE = _re.compile(r"<!--.*?-->", _re.DOTALL)


@router.get("/stats")
async def get_project_stats():
    projects_dir = settings.projects_dir
    fm_local = FileManager(projects_dir)
    stage_distribution = {s: 0 for s in _KNOWN_STAGES}
    total_books = 0
    total_chapters = 0
    total_words = 0
    # (chapter mtime, chapter char count) pairs collected in iteration order;
    # sorted by mtime ascending, then reduced to a cumulative word-count
    # series for the sparkline on 总字数. Pair form keeps the data-source
    # local to this loop instead of walking the chapter dirs twice.
    chapter_points: list[tuple[float, int]] = []

    if projects_dir.exists():
        for proj_dir in projects_dir.iterdir():
            if not proj_dir.is_dir():
                continue
            proj_file = proj_dir / "project.json"
            if not proj_file.exists():
                continue

            try:
                project_data = fm_local.read_json(proj_dir.name, "project.json")
            except Exception:
                continue
            if not project_data:
                continue

            total_books += 1
            stage = project_data.get("current_stage", "INIT")
            if stage in stage_distribution:
                stage_distribution[stage] += 1

            outline = fm_local.read_json(proj_dir.name, "outline.json")
            if isinstance(outline, dict):
                chapters = outline.get("chapters", [])
                if isinstance(chapters, list):
                    total_chapters += len(chapters)

            chapters_dir = proj_dir / "chapters"
            if chapters_dir.exists() and chapters_dir.is_dir():
                for draft_file in chapters_dir.iterdir():
                    if not draft_file.is_file():
                        continue
                    if not draft_file.name.endswith(".md"):
                        continue
                    try:
                        text = draft_file.read_text(encoding="utf-8")
                    except Exception:
                        continue
                    visible = _SF_LOG_COMMENT_RE.sub("", text)
                    chars = len(visible)
                    total_words += chars
                    try:
                        mtime = draft_file.stat().st_mtime
                    except OSError:
                        mtime = 0.0
                    chapter_points.append((mtime, chars))

    chapter_points.sort(key=lambda p: p[0])
    cumulative = 0
    word_count_series: list[int] = []
    for _, chars in chapter_points:
        cumulative += chars
        word_count_series.append(cumulative)

    return {
        "error": False,
        "code": "OK",
        "message": "",
        "detail": {
            "total_books": total_books,
            "total_chapters": total_chapters,
            "total_words": total_words,
            "stage_distribution": stage_distribution,
            "word_count_series": word_count_series,
        },
    }


@router.get("/{project_id}/status")
async def get_project_status(project_id: str):
    data = fm.read_json(project_id, "project.json")
    if data is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": True,
                "code": "PROJECT_NOT_FOUND",
                "message": f"项目 {project_id} 不存在",
                "detail": {},
            },
        )

    fallback_title = data.get("title", "") or ""
    return {
        "error": False,
        "code": "OK",
        "message": "",
        "detail": {
            "project_id": project_id,
            "current_stage": data.get("current_stage", "INIT"),
            "title": _resolve_display_title(fm, project_id, fallback_title),
            "created_at": data.get("created_at", ""),
            "genre": data.get("genre", ""),
        },
    }
