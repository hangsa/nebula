import re
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from backend.config import settings
from backend.utils.file_manager import FileManager
from backend.conductor.state_machine import StageStateMachine, Stage, STAGE_ORDER

router = APIRouter(prefix="/api/stage6", tags=["stage6"])

SF_LOG_STRIP = re.compile(r"<!--\s*SF_LOG\s.*?-->\s*", re.DOTALL)


class NovelExporter:
    """Zero-LLM export: stitch scene drafts, strip SF_LOG, generate TOC and title page."""

    def __init__(self, project_id: str, projects_dir: Optional[Path] = None):
        self.project_id = project_id
        self._projects_dir = Path(projects_dir) if projects_dir else settings.projects_dir
        self._fm = FileManager(self._projects_dir)
        self._project_dir = self._projects_dir / project_id

    def export(self, options: dict) -> str:
        strip_logs = options.get("strip_sf_logs", True)
        add_toc = options.get("add_toc", True)
        include_title_page = options.get("include_title_page", True)

        # Load project info
        project = self._fm.read_json(self.project_id, "project.json") or {}
        progress = self._fm.read_json(self.project_id, "progress.json") or {}

        title = project.get("title", "未命名作品")
        author = "nebula"
        chapters_progress = progress.get("chapters", [])

        # Build chapter→scenes mapping from progress.json
        chapter_scenes: dict[int, list[int]] = {}
        for ch in chapters_progress:
            ch_num = ch.get("chapter_number", 0)
            if ch_num == 0:
                continue
            chapter_scenes[ch_num] = [
                s.get("scene_number", 0)
                for s in ch.get("scenes", [])
                if s.get("status") in ("completed", "force_passed")
            ]

        # Read and stitch scene drafts in order
        chapters_dir = self._project_dir / "chapters"
        chapter_texts: dict[int, str] = {}

        if chapters_dir.exists():
            for draft_file in sorted(chapters_dir.glob("ch*_scene_*_draft.md")):
                match = re.match(r"ch(\d+)_scene_(\d+)_draft\.md", draft_file.name)
                if not match:
                    continue
                ch_num = int(match.group(1))
                scene_num = int(match.group(2))

                # Only include completed or force-passed scenes
                if ch_num not in chapter_scenes or scene_num not in chapter_scenes[ch_num]:
                    continue

                text = draft_file.read_text(encoding="utf-8")

                if strip_logs:
                    text = SF_LOG_STRIP.sub("", text)

                if ch_num not in chapter_texts:
                    chapter_texts[ch_num] = ""
                chapter_texts[ch_num] += f"\n\n## 第{ch_num}章 · Scene {scene_num}\n\n{text.strip()}"

        # Build output
        output_parts: list[str] = []

        # Title page
        if include_title_page:
            output_parts.append(self._generate_title_page(title, author, len(chapter_texts)))

        # Table of contents
        if add_toc:
            output_parts.append(self._generate_toc(chapter_texts))

        # Chapters in order
        for ch_num in sorted(chapter_texts.keys()):
            output_parts.append(f"\n\n# 第{ch_num}章\n{chapter_texts[ch_num]}")

        novel_text = "\n".join(output_parts).strip()

        # Generate filename: {safe_title}_{YYYYMMDD}.md
        safe_title = re.sub(r"[^\w一-鿿\-]", "_", title).strip("_") or "novel"
        today = date.today().strftime("%Y%m%d")
        export_filename = f"{safe_title}_{today}.md"

        # Write to exports/
        exports_dir = self._project_dir / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        (exports_dir / export_filename).write_text(novel_text, encoding="utf-8")
        # Also save as novel.md for download endpoint compatibility
        (exports_dir / "novel.md").write_text(novel_text, encoding="utf-8")

        return novel_text, export_filename

    def _generate_title_page(self, title: str, author: str, total_chapters: int) -> str:
        lines = [
            f"# {title}",
            "",
            f"作者：{author}",
            f"总章节数：{total_chapters}",
            "",
            "---",
        ]
        return "\n".join(lines)

    def _generate_toc(self, chapter_texts: dict[int, str]) -> str:
        lines = ["# 目录", ""]
        for ch_num in sorted(chapter_texts.keys()):
            # Count scenes in this chapter
            scene_count = chapter_texts[ch_num].count("## 第")
            lines.append(f"- 第{ch_num}章（{scene_count} 个场景）")
        lines.append("")
        lines.append("---")
        return "\n".join(lines)


# --- API Endpoints ---


@router.post("/export")
async def export_novel(data: dict):
    project_id = data.get("project_id", "")
    options = data.get("options", {})

    if not project_id:
        raise HTTPException(
            status_code=400,
            detail={"error": True, "code": "VALIDATION_ERROR", "message": "project_id 不能为空", "detail": {}},
        )

    sm = StageStateMachine(settings.projects_dir)
    current = sm.get_current_stage(project_id)
    if STAGE_ORDER.index(current) < STAGE_ORDER.index(Stage.STAGE6):
        raise HTTPException(
            status_code=400,
            detail={
                "error": True,
                "code": "STAGE_NOT_READY",
                "message": f"当前阶段为 {current.value}，无法执行 STAGE6 导出",
                "detail": {},
            },
        )

    try:
        exporter = NovelExporter(project_id)
        full_text, export_filename = exporter.export(options)

        # Return preview (first 500 chars)
        preview = full_text[:500] if len(full_text) > 500 else full_text

        return {
            "error": False,
            "code": "OK",
            "message": "导出完成",
            "detail": {
                "preview": preview,
                "total_chars": len(full_text),
                "file_path": f"projects/{project_id}/exports/{export_filename}",
            },
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"error": True, "code": "EXPORT_FAILED", "message": f"导出失败: {str(e)}", "detail": {}},
        )


@router.get("/download")
async def download_novel(project_id: str):
    if not project_id:
        raise HTTPException(
            status_code=400,
            detail={"error": True, "code": "VALIDATION_ERROR", "message": "project_id 不能为空", "detail": {}},
        )

    exports_dir = settings.projects_dir / project_id / "exports"
    novel_path = exports_dir / "novel.md"
    if not novel_path.exists():
        raise HTTPException(
            status_code=404,
            detail={"error": True, "code": "EXPORT_NOT_FOUND", "message": "导出文件不存在，请先执行导出", "detail": {}},
        )

    # Find the dated export file for the download filename
    download_name = "novel.md"
    for f in sorted(exports_dir.glob("*.md"), reverse=True):
        if f.name != "novel.md":
            download_name = f.name
            break

    return FileResponse(
        path=str(novel_path),
        filename=download_name,
        media_type="text/markdown",
    )
