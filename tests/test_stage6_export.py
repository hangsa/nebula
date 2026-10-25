"""
STAGE 6 export module unit tests.
Tests: SF_LOG stripping, TOC generation, title page, chapter ordering, file output, API endpoints.
"""
import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.stage6_export import NovelExporter
from backend.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def projects_dir():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


def _write_json(base: Path, project_id: str, rel_path: str, data):
    path = base / project_id / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _write_md(base: Path, project_id: str, rel_path: str, text: str):
    path = base / project_id / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _make_progress(chapters: list[dict]) -> dict:
    return {
        "project_id": "proj_test",
        "current_chapter": len(chapters),
        "total_chapters": len(chapters),
        "current_stage": "STAGE6",
        "chapters": chapters,
        "circuit_breaker_events": [],
    }


def _make_project(stage: str = "STAGE6", title: str = "测试小说") -> dict:
    return {
        "id": "proj_test",
        "title": title,
        "genre": "cool_novel",
        "min_words": 4000,
        "current_stage": stage,
        "initial_intent": {"free_text": "测试"},
        "stage_history": [],
        "created_at": "2025-01-01T00:00:00",
    }


# ── SF_LOG Stripping ────────────────────────────────────────────────


class TestSFLogStripping:
    def test_strips_sf_log_tags(self, projects_dir):
        """SF_LOG tags are removed when strip_sf_logs=True."""
        _write_json(projects_dir, "proj_test", "project.json", _make_project())
        _write_json(projects_dir, "proj_test", "progress.json", _make_progress([
            {"chapter_number": 1, "status": "completed", "scenes": [
                {"scene_number": 1, "status": "completed", "coherence_score": 85},
            ]},
        ]))
        _write_md(projects_dir, "proj_test", "chapters/ch01_scene_001_draft.md",
            '林峰站在废墟上。\n'
            '<!-- SF_LOG character_emotion char="林峰" emotion="愤怒" -->\n'
            '<!-- SF_LOG character_location_change char="林峰" from="未知" to="城西烂尾楼" -->\n'
            '他握紧了拳头。\n'
        )

        exporter = NovelExporter("proj_test", projects_dir)
        text, _filename = exporter.export({"strip_sf_logs": True, "add_toc": False, "include_title_page": False})

        assert "SF_LOG" not in text
        assert "林峰站在废墟上" in text
        assert "他握紧了拳头" in text

    def test_preserves_sf_log_when_disabled(self, projects_dir):
        """SF_LOG tags are preserved when strip_sf_logs=False."""
        _write_json(projects_dir, "proj_test", "project.json", _make_project())
        _write_json(projects_dir, "proj_test", "progress.json", _make_progress([
            {"chapter_number": 1, "status": "completed", "scenes": [
                {"scene_number": 1, "status": "completed", "coherence_score": 85},
            ]},
        ]))
        _write_md(projects_dir, "proj_test", "chapters/ch01_scene_001_draft.md",
            '林峰站在废墟上。\n'
            '<!-- SF_LOG character_emotion char="林峰" emotion="愤怒" -->\n'
        )

        exporter = NovelExporter("proj_test", projects_dir)
        text, _filename = exporter.export({"strip_sf_logs": False, "add_toc": False, "include_title_page": False})

        assert "SF_LOG" in text
        assert "character_emotion" in text

    def test_strips_multiline_sf_log(self, projects_dir):
        """Multi-line SF_LOG tags are properly stripped."""
        _write_json(projects_dir, "proj_test", "project.json", _make_project())
        _write_json(projects_dir, "proj_test", "progress.json", _make_progress([
            {"chapter_number": 1, "status": "completed", "scenes": [
                {"scene_number": 1, "status": "completed", "coherence_score": 85},
            ]},
        ]))
        _write_md(projects_dir, "proj_test", "chapters/ch01_scene_001_draft.md",
            '开始文本。\n'
            '<!-- SF_LOG registry_create type="conflict"\n'
            '  data=\'{"owner":"林峰","target":"师父","type":"betrayal"}\' -->\n'
            '结束文本。\n'
        )

        exporter = NovelExporter("proj_test", projects_dir)
        text, _filename = exporter.export({"strip_sf_logs": True, "add_toc": False, "include_title_page": False})

        assert "SF_LOG" not in text
        assert "开始文本" in text
        assert "结束文本" in text


# ── TOC Generation ───────────────────────────────────────────────────


class TestTOCGeneration:
    def test_generates_toc(self, projects_dir):
        _write_json(projects_dir, "proj_test", "project.json", _make_project())
        _write_json(projects_dir, "proj_test", "progress.json", _make_progress([
            {"chapter_number": 1, "status": "completed", "scenes": [
                {"scene_number": 1, "status": "completed", "coherence_score": 85},
                {"scene_number": 2, "status": "completed", "coherence_score": 90},
            ]},
            {"chapter_number": 2, "status": "completed", "scenes": [
                {"scene_number": 3, "status": "completed", "coherence_score": 88},
            ]},
        ]))
        _write_md(projects_dir, "proj_test", "chapters/ch01_scene_001_draft.md", "第一章场景一内容。")
        _write_md(projects_dir, "proj_test", "chapters/ch01_scene_002_draft.md", "第一章场景二内容。")
        _write_md(projects_dir, "proj_test", "chapters/ch02_scene_003_draft.md", "第二章场景一内容。")

        exporter = NovelExporter("proj_test", projects_dir)
        text, _filename = exporter.export({"strip_sf_logs": True, "add_toc": True, "include_title_page": False})

        assert "# 目录" in text
        assert "第1章" in text
        assert "第2章" in text

    def test_no_toc_when_disabled(self, projects_dir):
        _write_json(projects_dir, "proj_test", "project.json", _make_project())
        _write_json(projects_dir, "proj_test", "progress.json", _make_progress([
            {"chapter_number": 1, "status": "completed", "scenes": [
                {"scene_number": 1, "status": "completed", "coherence_score": 85},
            ]},
        ]))
        _write_md(projects_dir, "proj_test", "chapters/ch01_scene_001_draft.md", "内容。")

        exporter = NovelExporter("proj_test", projects_dir)
        text, _filename = exporter.export({"strip_sf_logs": True, "add_toc": False, "include_title_page": False})

        assert "# 目录" not in text


# ── Title Page ───────────────────────────────────────────────────────


class TestTitlePage:
    def test_generates_title_page(self, projects_dir):
        _write_json(projects_dir, "proj_test", "project.json", _make_project(title="星辰大海"))
        _write_json(projects_dir, "proj_test", "progress.json", _make_progress([
            {"chapter_number": 1, "status": "completed", "scenes": [
                {"scene_number": 1, "status": "completed", "coherence_score": 85},
            ]},
        ]))
        _write_md(projects_dir, "proj_test", "chapters/ch01_scene_001_draft.md", "内容。")

        exporter = NovelExporter("proj_test", projects_dir)
        text, _filename = exporter.export({"strip_sf_logs": True, "add_toc": False, "include_title_page": True})

        assert "# 星辰大海" in text
        assert "nebula" in text

    def test_no_title_page_when_disabled(self, projects_dir):
        _write_json(projects_dir, "proj_test", "project.json", _make_project(title="星辰大海"))
        _write_json(projects_dir, "proj_test", "progress.json", _make_progress([
            {"chapter_number": 1, "status": "completed", "scenes": [
                {"scene_number": 1, "status": "completed", "coherence_score": 85},
            ]},
        ]))
        _write_md(projects_dir, "proj_test", "chapters/ch01_scene_001_draft.md", "内容。")

        exporter = NovelExporter("proj_test", projects_dir)
        text, _filename = exporter.export({"strip_sf_logs": True, "add_toc": False, "include_title_page": False})

        assert "# 星辰大海" not in text

    def test_fallback_title_when_no_project_title(self, projects_dir):
        _write_json(projects_dir, "proj_test", "project.json", {"id": "proj_test", "current_stage": "STAGE6"})
        _write_json(projects_dir, "proj_test", "progress.json", _make_progress([
            {"chapter_number": 1, "status": "completed", "scenes": [
                {"scene_number": 1, "status": "completed", "coherence_score": 85},
            ]},
        ]))
        _write_md(projects_dir, "proj_test", "chapters/ch01_scene_001_draft.md", "内容。")

        exporter = NovelExporter("proj_test", projects_dir)
        text, _filename = exporter.export({"strip_sf_logs": True, "add_toc": False, "include_title_page": True})

        assert "未命名作品" in text


# ── Chapter Ordering & Scene Filtering ────────────────────────────────


class TestChapterOrdering:
    def test_chapters_in_numerical_order(self, projects_dir):
        _write_json(projects_dir, "proj_test", "project.json", _make_project())
        _write_json(projects_dir, "proj_test", "progress.json", _make_progress([
            {"chapter_number": 3, "status": "completed", "scenes": [
                {"scene_number": 3, "status": "completed", "coherence_score": 85},
            ]},
            {"chapter_number": 1, "status": "completed", "scenes": [
                {"scene_number": 1, "status": "completed", "coherence_score": 85},
            ]},
            {"chapter_number": 2, "status": "completed", "scenes": [
                {"scene_number": 2, "status": "completed", "coherence_score": 85},
            ]},
        ]))
        _write_md(projects_dir, "proj_test", "chapters/ch01_scene_001_draft.md", "第一章")
        _write_md(projects_dir, "proj_test", "chapters/ch02_scene_002_draft.md", "第二章")
        _write_md(projects_dir, "proj_test", "chapters/ch03_scene_003_draft.md", "第三章")

        exporter = NovelExporter("proj_test", projects_dir)
        text, _filename = exporter.export({"strip_sf_logs": True, "add_toc": False, "include_title_page": False})

        pos1 = text.index("第一章")
        pos2 = text.index("第二章")
        pos3 = text.index("第三章")
        assert pos1 < pos2 < pos3

    def test_filters_non_completed_scenes(self, projects_dir):
        """Only 'completed' and 'force_passed' scenes are included."""
        _write_json(projects_dir, "proj_test", "project.json", _make_project())
        _write_json(projects_dir, "proj_test", "progress.json", _make_progress([
            {"chapter_number": 1, "status": "completed", "scenes": [
                {"scene_number": 1, "status": "completed", "coherence_score": 85},
                {"scene_number": 2, "status": "failed", "coherence_score": 40},
                {"scene_number": 3, "status": "force_passed", "coherence_score": 50},
                {"scene_number": 4, "status": "skipped", "coherence_score": 0},
            ]},
        ]))
        _write_md(projects_dir, "proj_test", "chapters/ch01_scene_001_draft.md", "已完成场景")
        _write_md(projects_dir, "proj_test", "chapters/ch01_scene_002_draft.md", "失败场景")
        _write_md(projects_dir, "proj_test", "chapters/ch01_scene_003_draft.md", "强制通过场景")
        _write_md(projects_dir, "proj_test", "chapters/ch01_scene_004_draft.md", "跳过场景")

        exporter = NovelExporter("proj_test", projects_dir)
        text, _filename = exporter.export({"strip_sf_logs": True, "add_toc": False, "include_title_page": False})

        assert "已完成场景" in text
        assert "强制通过场景" in text
        assert "失败场景" not in text
        assert "跳过场景" not in text

    def test_skips_chapter_zero(self, projects_dir):
        """Chapter 0 is ignored."""
        _write_json(projects_dir, "proj_test", "project.json", _make_project())
        _write_json(projects_dir, "proj_test", "progress.json", _make_progress([
            {"chapter_number": 0, "status": "completed", "scenes": [
                {"scene_number": 0, "status": "completed", "coherence_score": 85},
            ]},
            {"chapter_number": 1, "status": "completed", "scenes": [
                {"scene_number": 1, "status": "completed", "coherence_score": 85},
            ]},
        ]))
        _write_md(projects_dir, "proj_test", "chapters/ch00_scene_000_draft.md", "第零章")
        _write_md(projects_dir, "proj_test", "chapters/ch01_scene_001_draft.md", "第一章")

        exporter = NovelExporter("proj_test", projects_dir)
        text, _filename = exporter.export({"strip_sf_logs": True, "add_toc": False, "include_title_page": False})

        assert "第一章" in text
        assert "第零章" not in text


# ── File Output ──────────────────────────────────────────────────────


class TestFileOutput:
    def test_writes_export_file(self, projects_dir):
        _write_json(projects_dir, "proj_test", "project.json", _make_project())
        _write_json(projects_dir, "proj_test", "progress.json", _make_progress([
            {"chapter_number": 1, "status": "completed", "scenes": [
                {"scene_number": 1, "status": "completed", "coherence_score": 85},
            ]},
        ]))
        _write_md(projects_dir, "proj_test", "chapters/ch01_scene_001_draft.md", "小说内容。")

        exporter = NovelExporter("proj_test", projects_dir)
        exporter.export({"strip_sf_logs": True, "add_toc": False, "include_title_page": False})

        export_path = projects_dir / "proj_test" / "exports" / "novel.md"
        assert export_path.exists()
        content = export_path.read_text(encoding="utf-8")
        assert "小说内容" in content

    def test_creates_exports_dir_if_missing(self, projects_dir):
        _write_json(projects_dir, "proj_test", "project.json", _make_project())
        _write_json(projects_dir, "proj_test", "progress.json", _make_progress([
            {"chapter_number": 1, "status": "completed", "scenes": [
                {"scene_number": 1, "status": "completed", "coherence_score": 85},
            ]},
        ]))
        _write_md(projects_dir, "proj_test", "chapters/ch01_scene_001_draft.md", "内容。")

        exports_dir = projects_dir / "proj_test" / "exports"
        assert not exports_dir.exists()

        exporter = NovelExporter("proj_test", projects_dir)
        exporter.export({"strip_sf_logs": True, "add_toc": False, "include_title_page": False})

        assert exports_dir.exists()
        assert (exports_dir / "novel.md").exists()


# ── Edge Cases ───────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_project_no_drafts(self, projects_dir):
        """Project with no scene drafts exports empty text."""
        _write_json(projects_dir, "proj_test", "project.json", _make_project())
        _write_json(projects_dir, "proj_test", "progress.json", _make_progress([]))

        exporter = NovelExporter("proj_test", projects_dir)
        text, _filename = exporter.export({"strip_sf_logs": True, "add_toc": False, "include_title_page": False})

        assert text == ""

    def test_no_chapters_dir(self, projects_dir):
        """Project with no chapters/ directory should not crash."""
        _write_json(projects_dir, "proj_test", "project.json", _make_project())
        _write_json(projects_dir, "proj_test", "progress.json", _make_progress([
            {"chapter_number": 1, "status": "completed", "scenes": [
                {"scene_number": 1, "status": "completed", "coherence_score": 85},
            ]},
        ]))

        exporter = NovelExporter("proj_test", projects_dir)
        text, _filename = exporter.export({"strip_sf_logs": True, "add_toc": False, "include_title_page": False})

        assert text == ""

    def test_full_export_with_all_options(self, projects_dir):
        """End-to-end: title page + TOC + chapters with SF_LOG stripped."""
        _write_json(projects_dir, "proj_test", "project.json", _make_project(title="龙族崛起"))
        _write_json(projects_dir, "proj_test", "progress.json", _make_progress([
            {"chapter_number": 1, "status": "completed", "scenes": [
                {"scene_number": 1, "status": "completed", "coherence_score": 85},
                {"scene_number": 2, "status": "completed", "coherence_score": 88},
            ]},
            {"chapter_number": 2, "status": "completed", "scenes": [
                {"scene_number": 3, "status": "completed", "coherence_score": 90},
            ]},
        ]))
        _write_md(projects_dir, "proj_test", "chapters/ch01_scene_001_draft.md",
            '龙族觉醒。\n<!-- SF_LOG character_emotion char="龙啸" emotion="愤怒" -->\n'
        )
        _write_md(projects_dir, "proj_test", "chapters/ch01_scene_002_draft.md",
            '力量爆发。\n<!-- SF_LOG conflict_escalate id="cf_001" new_intensity="critical" -->\n'
        )
        _write_md(projects_dir, "proj_test", "chapters/ch02_scene_003_draft.md",
            '决战开始。\n'
        )

        exporter = NovelExporter("proj_test", projects_dir)
        text, _filename = exporter.export({"strip_sf_logs": True, "add_toc": True, "include_title_page": True})

        # Title page
        assert "# 龙族崛起" in text
        assert "nebula" in text
        # TOC
        assert "# 目录" in text
        assert "第1章" in text
        assert "第2章" in text
        # Chapters
        assert "龙族觉醒" in text
        assert "力量爆发" in text
        assert "决战开始" in text
        # SF_LOG stripped
        assert "SF_LOG" not in text
        # Order: title page before TOC before chapters
        tp_pos = text.index("龙族崛起")
        toc_pos = text.index("# 目录")
        ch1_pos = text.index("龙族觉醒")
        assert tp_pos < toc_pos < ch1_pos


# ── API Endpoints ────────────────────────────────────────────────────


def _write_prereq_file(proj_dir: Path, filename: str, data: dict):
    """Write a prerequisite file for stage advancement."""
    path = proj_dir / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _advance_to_stage6(client, proj_id: str):
    """Advance a project from INIT to STAGE6 by creating prerequisite files at each step."""
    from backend.config import settings

    proj_dir = settings.projects_dir / proj_id

    # INIT → STAGE1 (project.json has title+genre from create, so this should work)
    resp = client.post("/api/conductor/advance", json={
        "project_id": proj_id, "target_stage": "STAGE1",
    })
    assert resp.status_code == 200, f"INIT→STAGE1 failed: {resp.json()}"

    # STAGE1 → STAGE2: needs concept_and_dna.json
    _write_prereq_file(proj_dir, "concept_and_dna.json", {
        "concept": {"title": "测试", "genre": "cool_novel", "premise": "测试", "tone": "dark"},
        "story_dna": {"core_contradiction": {"statement": "测试矛盾", "side_a": "A", "side_b": "B"}},
    })
    resp = client.post("/api/conductor/advance", json={
        "project_id": proj_id, "target_stage": "STAGE2",
    })
    assert resp.status_code == 200, f"STAGE1→STAGE2 failed: {resp.json()}"

    # STAGE2 → STAGE3: needs characters.json + world.json (novel_outline is
    # generated inside STAGE3, not a precondition for entering it)
    _write_prereq_file(proj_dir, "characters.json", {
        "characters": [{"id": "char_001", "name": "测试角色"}],
    })
    _write_prereq_file(proj_dir, "world.json", {"era": "古代"})
    resp = client.post("/api/conductor/advance", json={
        "project_id": proj_id, "target_stage": "STAGE3",
    })
    assert resp.status_code == 200, f"STAGE2→STAGE3 failed: {resp.json()}"

    # STAGE3 → STAGE4: needs outline.json
    _write_prereq_file(proj_dir, "outline.json", {
        "chapters": [{"chapter_number": 1, "title": "测试", "scene_plan": [{"scene_number": 1}]}],
    })
    resp = client.post("/api/conductor/advance", json={
        "project_id": proj_id, "target_stage": "STAGE4",
    })
    assert resp.status_code == 200, f"STAGE3→STAGE4 failed: {resp.json()}"

    # STAGE4 → STAGE5: needs progress.json with all scenes completed
    _write_prereq_file(proj_dir, "progress.json", {
        "project_id": proj_id,
        "current_chapter": 1,
        "chapters": [
            {"chapter_number": 1, "status": "completed", "scenes": [
                {"scene_number": 1, "status": "completed", "coherence_score": 85},
            ]},
        ],
    })
    resp = client.post("/api/conductor/advance", json={
        "project_id": proj_id, "target_stage": "STAGE5",
    })
    assert resp.status_code == 200, f"STAGE4→STAGE5 failed: {resp.json()}"

    # STAGE5 → STAGE6: needs diagnosis_report.json with all P0 resolved
    _write_prereq_file(proj_dir, "diagnosis_report.json", {
        "project_id": proj_id,
        "issues": [],
        "summary": {"p0_count": 0, "p1_count": 0, "p2_count": 0},
    })
    resp = client.post("/api/conductor/advance", json={
        "project_id": proj_id, "target_stage": "STAGE6",
    })
    assert resp.status_code == 200, f"STAGE5→STAGE6 failed: {resp.json()}"


class TestExportAPI:
    def test_export_success(self, client):
        """POST /api/stage6/export returns preview and total_chars."""
        create_resp = client.post("/api/project/create", json={
            "title": "测试导出", "genre": "cool_novel", "min_words": 4000,
            "free_text": "测试导出内容",
        })
        proj_id = create_resp.json()["detail"]["id"]

        _advance_to_stage6(client, proj_id)

        resp = client.post("/api/stage6/export", json={
            "project_id": proj_id,
            "options": {"strip_sf_logs": True, "add_toc": True, "include_title_page": True},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["error"] is False
        assert "preview" in data["detail"]
        assert "total_chars" in data["detail"]
        assert "file_path" in data["detail"]

    def test_export_missing_project_id(self, client):
        resp = client.post("/api/stage6/export", json={"project_id": "", "options": {}})
        assert resp.status_code == 400
        data = resp.json()
        assert data["detail"]["code"] == "VALIDATION_ERROR"

    def test_export_wrong_stage(self, client):
        """Export should fail if project is not at STAGE6 or COMPLETED."""
        create_resp = client.post("/api/project/create", json={
            "title": "测试", "genre": "cool_novel", "min_words": 4000,
            "free_text": "测试",
        })
        proj_id = create_resp.json()["detail"]["id"]

        # Project is at INIT, not STAGE6
        resp = client.post("/api/stage6/export", json={
            "project_id": proj_id, "options": {},
        })
        assert resp.status_code == 400
        data = resp.json()
        assert data["detail"]["code"] == "STAGE_NOT_READY"

    def test_export_at_completed_stage(self, client):
        """Export should work at COMPLETED stage too."""
        create_resp = client.post("/api/project/create", json={
            "title": "测试", "genre": "cool_novel", "min_words": 4000,
            "free_text": "测试",
        })
        proj_id = create_resp.json()["detail"]["id"]

        _advance_to_stage6(client, proj_id)

        # Advance STAGE6 → COMPLETED (needs exports/novel.md)
        from backend.config import settings
        proj_dir = settings.projects_dir / proj_id
        _write_prereq_file(proj_dir, "exports/novel.md", {})

        resp = client.post("/api/conductor/advance", json={
            "project_id": proj_id, "target_stage": "COMPLETED",
        })
        assert resp.status_code == 200, f"STAGE6→COMPLETED failed: {resp.json()}"

        resp = client.post("/api/stage6/export", json={
            "project_id": proj_id, "options": {},
        })
        assert resp.status_code == 200


class TestDownloadAPI:
    def test_download_missing_file(self, client):
        """Download should 404 if no export file exists."""
        create_resp = client.post("/api/project/create", json={
            "title": "测试", "genre": "cool_novel", "min_words": 4000,
            "free_text": "测试",
        })
        proj_id = create_resp.json()["detail"]["id"]

        resp = client.get(f"/api/stage6/download?project_id={proj_id}")
        assert resp.status_code == 404
        data = resp.json()
        assert data["detail"]["code"] == "EXPORT_NOT_FOUND"

    def test_download_missing_project_id(self, client):
        resp = client.get("/api/stage6/download?project_id=")
        assert resp.status_code == 400
        data = resp.json()
        assert data["detail"]["code"] == "VALIDATION_ERROR"

    def test_download_after_export(self, client):
        """Download should work after a successful export."""
        create_resp = client.post("/api/project/create", json={
            "title": "测试下载", "genre": "cool_novel", "min_words": 4000,
            "free_text": "测试下载",
        })
        proj_id = create_resp.json()["detail"]["id"]

        _advance_to_stage6(client, proj_id)

        # Export first
        resp = client.post("/api/stage6/export", json={
            "project_id": proj_id, "options": {},
        })
        assert resp.status_code == 200, f"Export failed: {resp.json()}"

        # Then download
        resp = client.get(f"/api/stage6/download?project_id={proj_id}")
        assert resp.status_code == 200
        assert "text/markdown" in resp.headers["content-type"]
        assert "attachment" in resp.headers["content-disposition"]
