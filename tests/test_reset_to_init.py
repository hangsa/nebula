"""StageStateMachine.regress_to_stage3() — atomically clear STAGE4 runtime
state and regress current_stage to STAGE3 (章节大纲阶段), preserving
init-phase artifacts and stage_history.

The regress_to_stage3 helper is the foundation of the workspace "初始化"
button: it deletes chapters/*.md + progress.json + .nebula_checkpoint.json
+ autopilot/chunks/*.jsonl, then writes project.json with current_stage=STAGE3.
It is intentionally NOT transactional — idempotent retry is the recovery
path for partial-write failures (see spec §错误处理).

v2.1 behavior change: target stage was INIT in early versions, but writing
INIT blocks STAGE3+ endpoints (STAGE_NOT_READY) and prevents the user from
re-generating the chapter/novel outline after a reset. Writing STAGE3 instead
keeps the chapter-outline endpoints (which require current_stage >= STAGE3
via STAGE_ORDER.index) reachable while clearing all STAGE4 runtime state.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.conductor.state_machine import Stage, StageStateMachine


@pytest.fixture
def projects_dir(tmp_path: Path):
    return tmp_path


@pytest.fixture
def sm(projects_dir):
    return StageStateMachine(projects_dir)


def _seed_project(projects_dir: Path, pid: str, *,
                  stage: str = "STAGE4",
                  stage_history: list | None = None) -> dict:
    project_dir = projects_dir / pid
    project_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "id": pid,
        "title": "测试小说",
        "genre": "cool_novel",
        "current_stage": stage,
        "stage_history": stage_history or [],
        "created_at": "2026-01-01T00:00:00",
    }
    (project_dir / "project.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return data


def test_regress_deletes_chapter_drafts(sm, projects_dir):
    pid = "proj_test"
    _seed_project(projects_dir, pid)
    chapters = projects_dir / pid / "chapters"
    chapters.mkdir()
    (chapters / "ch01_scene_001_draft.md").write_text("scene 1 body")
    (chapters / "ch01_scene_002_draft.md").write_text("scene 2 body")
    (chapters / "ch02_scene_001_draft.md").write_text("ch2 scene 1")

    result = sm.regress_to_stage3(pid)

    assert result.allowed is True
    assert list(chapters.glob("ch*_scene_*_draft.md")) == []


def test_regress_deletes_progress_and_checkpoint(sm, projects_dir):
    pid = "proj_test"
    proj = projects_dir / pid
    _seed_project(projects_dir, pid)
    (proj / "progress.json").write_text('{"chapters":[]}', encoding="utf-8")
    (proj / ".nebula_checkpoint.json").write_text('{"pipeline_stage":"x"}', encoding="utf-8")

    sm.regress_to_stage3(pid)

    assert not (proj / "progress.json").exists()
    assert not (proj / ".nebula_checkpoint.json").exists()


def test_regress_clears_chunks_but_keeps_dir(sm, projects_dir):
    pid = "proj_test"
    proj = projects_dir / pid
    _seed_project(projects_dir, pid)
    chunks = proj / "autopilot" / "chunks"
    chunks.mkdir(parents=True)
    (chunks / "ch01_scene_001.jsonl").write_text('{"seq":1,"text":"a"}', encoding="utf-8")
    (chunks / "ch02_scene_003.jsonl").write_text('{"seq":1,"text":"b"}', encoding="utf-8")

    sm.regress_to_stage3(pid)

    assert list(chunks.glob("*.jsonl")) == []
    assert chunks.exists()  # SceneChunkStore expects the parent dir to exist


def test_regress_writes_current_stage_stage3(sm, projects_dir):
    pid = "proj_test"
    proj = projects_dir / pid
    _seed_project(projects_dir, pid, stage="STAGE4")
    (proj / "progress.json").write_text('{}', encoding="utf-8")

    sm.regress_to_stage3(pid)

    data = json.loads((proj / "project.json").read_text(encoding="utf-8"))
    assert data["current_stage"] == "STAGE3"


def test_regress_preserves_stage_history(sm, projects_dir):
    pid = "proj_test"
    proj = projects_dir / pid
    history = [
        {"from_stage": "INIT", "to_stage": "STAGE1", "timestamp": "2026-01-01T00:00:00"},
        {"from_stage": "STAGE1", "to_stage": "STAGE4", "timestamp": "2026-01-02T00:00:00"},
    ]
    _seed_project(projects_dir, pid, stage="STAGE4", stage_history=history)

    sm.regress_to_stage3(pid)

    data = json.loads((proj / "project.json").read_text(encoding="utf-8"))
    assert data["stage_history"] == history


def test_regress_preserves_init_artifacts(sm, projects_dir):
    """outline.json, characters.json, world.json, novel_outline.json,
    concept_and_dna.json — these belong to init phase and must NOT be deleted."""
    pid = "proj_test"
    proj = projects_dir / pid
    _seed_project(projects_dir, pid)
    for fn in ("outline.json", "characters.json", "world.json",
               "novel_outline.json", "concept_and_dna.json"):
        (proj / fn).write_text(f'{{"file":"{fn}"}}', encoding="utf-8")
    chapters = proj / "chapters"
    chapters.mkdir()
    (chapters / "ch01_scene_001_draft.md").write_text("body", encoding="utf-8")

    sm.regress_to_stage3(pid)

    for fn in ("outline.json", "characters.json", "world.json",
               "novel_outline.json", "concept_and_dna.json"):
        assert (proj / fn).exists(), f"{fn} should be preserved"


def test_regress_raises_project_not_found_when_project_missing(sm):
    from backend.conductor.state_machine import ProjectNotFoundError
    with pytest.raises(ProjectNotFoundError, match="不存在"):
        sm.regress_to_stage3("proj_nonexistent")


def test_regress_is_idempotent(sm, projects_dir):
    """第二次调用应成功（已删除的文件跳过）。"""
    pid = "proj_test"
    proj = projects_dir / pid
    _seed_project(projects_dir, pid)
    (proj / "progress.json").write_text('{}', encoding="utf-8")

    r1 = sm.regress_to_stage3(pid)
    r2 = sm.regress_to_stage3(pid)

    assert r1.allowed is True
    assert r2.allowed is True
    data = json.loads((proj / "project.json").read_text(encoding="utf-8"))
    assert data["current_stage"] == "STAGE3"


def test_regress_reports_actual_from_stage(sm, projects_dir):
    """from_stage should reflect the project's actual current_stage before reset,
    not be hardcoded to Stage.STAGE3."""
    pid = "proj_test"
    proj = projects_dir / pid
    _seed_project(projects_dir, pid, stage="STAGE5")
    (proj / "progress.json").write_text('{}', encoding="utf-8")

    result = sm.regress_to_stage3(pid)

    assert result.allowed is True
    assert result.from_stage == Stage.STAGE5
    assert result.to_stage == Stage.STAGE3
    assert "STAGE5" in result.message and "STAGE3" in result.message


# === /reset-preview endpoint tests ===

from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(projects_dir, monkeypatch):
    from backend import config as _config
    from backend.api.project import router
    monkeypatch.setattr(_config.settings, "projects_dir", projects_dir)
    # Force the module-level `fm` to use the patched projects_dir too.
    from backend.api import project as _project_mod
    monkeypatch.setattr(_project_mod, "fm", _project_mod.FileManager(projects_dir))
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_reset_preview_returns_draft_count(client, projects_dir):
    pid = "proj_test"
    proj = projects_dir / pid
    proj.mkdir(parents=True)
    (proj / "project.json").write_text(
        json.dumps({"id": pid, "current_stage": "STAGE4"}),
        encoding="utf-8",
    )
    chapters = proj / "chapters"
    chapters.mkdir()
    for n in (1, 2, 3):
        (chapters / f"ch{n:02d}_scene_001_draft.md").write_text("body")

    resp = client.get(f"/api/project/{pid}/reset-preview")
    assert resp.status_code == 200
    assert resp.json() == {
        "draft_count": 3,
        "has_progress": False,
        "has_checkpoint": False,
        "has_chunks": False,
    }


def test_reset_preview_detects_runtime_state(client, projects_dir):
    pid = "proj_test"
    proj = projects_dir / pid
    proj.mkdir(parents=True)
    (proj / "project.json").write_text(
        json.dumps({"id": pid, "current_stage": "STAGE4"}),
        encoding="utf-8",
    )
    (proj / "progress.json").write_text('{"chapters":[]}', encoding="utf-8")
    (proj / ".nebula_checkpoint.json").write_text("{}", encoding="utf-8")
    chunks = proj / "autopilot" / "chunks"
    chunks.mkdir(parents=True)
    (chunks / "ch01_scene_001.jsonl").write_text('{"seq":1}')

    resp = client.get(f"/api/project/{pid}/reset-preview")
    body = resp.json()
    assert body["has_progress"] is True
    assert body["has_checkpoint"] is True
    assert body["has_chunks"] is True


def test_reset_preview_404_for_missing_project(client):
    resp = client.get("/api/project/proj_nonexistent/reset-preview")
    assert resp.status_code == 404


def test_reset_preview_handles_no_chapters_dir(client, projects_dir):
    """Project exists but never wrote any chapter drafts → all zeros."""
    pid = "proj_test"
    proj = projects_dir / pid
    proj.mkdir(parents=True)
    (proj / "project.json").write_text(
        json.dumps({"id": pid, "current_stage": "INIT"}),
        encoding="utf-8",
    )

    resp = client.get(f"/api/project/{pid}/reset-preview")
    assert resp.status_code == 200
    assert resp.json()["draft_count"] == 0


# === /reset endpoint tests ===

def test_reset_endpoint_clears_state(client, projects_dir):
    pid = "proj_test"
    proj = projects_dir / pid
    proj.mkdir(parents=True)
    (proj / "project.json").write_text(
        json.dumps({"id": pid, "current_stage": "STAGE4"}),
        encoding="utf-8",
    )
    chapters = proj / "chapters"
    chapters.mkdir()
    (chapters / "ch01_scene_001_draft.md").write_text("body")
    (proj / "progress.json").write_text('{"chapters":[]}', encoding="utf-8")
    (proj / ".nebula_checkpoint.json").write_text("{}", encoding="utf-8")

    resp = client.post(f"/api/project/{pid}/reset")
    assert resp.status_code == 200
    assert resp.json() == {
        "error": False,
        "code": "OK",
        "message": "项目已重置到章节大纲阶段",
        "detail": {"project_id": pid},
    }

    assert list((proj / "chapters").glob("ch*_scene_*_draft.md")) == []
    assert not (proj / "progress.json").exists()
    assert not (proj / ".nebula_checkpoint.json").exists()
    assert json.loads((proj / "project.json").read_text(encoding="utf-8"))["current_stage"] == "STAGE3"


def test_reset_endpoint_preserves_init_artifacts(client, projects_dir):
    pid = "proj_test"
    proj = projects_dir / pid
    proj.mkdir(parents=True)
    (proj / "project.json").write_text(
        json.dumps({"id": pid, "current_stage": "STAGE4"}),
        encoding="utf-8",
    )
    for fn in ("outline.json", "characters.json", "world.json",
               "novel_outline.json", "concept_and_dna.json"):
        (proj / fn).write_text(f'{{"file":"{fn}"}}', encoding="utf-8")

    resp = client.post(f"/api/project/{pid}/reset")
    assert resp.status_code == 200
    assert resp.json()["code"] == "OK"
    assert resp.json()["detail"] == {"project_id": pid}

    for fn in ("outline.json", "characters.json", "world.json",
               "novel_outline.json", "concept_and_dna.json"):
        assert (proj / fn).exists()


def test_reset_endpoint_404_for_missing_project(client):
    resp = client.post("/api/project/proj_nonexistent/reset")
    assert resp.status_code == 404


def test_reset_endpoint_is_idempotent(client, projects_dir):
    """第二次 POST 也返回 200（regress_to_stage3 幂等）。"""
    pid = "proj_test"
    proj = projects_dir / pid
    proj.mkdir(parents=True)
    (proj / "project.json").write_text(
        json.dumps({"id": pid, "current_stage": "STAGE4"}),
        encoding="utf-8",
    )

    r1 = client.post(f"/api/project/{pid}/reset")
    r2 = client.post(f"/api/project/{pid}/reset")

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["code"] == "OK"
    assert r2.json()["code"] == "OK"


def test_reset_endpoint_handles_oserror_mid_execution(projects_dir, monkeypatch):
    """中途 IO 失败 → 500 + 结构化错误信息。验证 partial-write 的故障传播路径。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend import config as _config
    from backend.api.project import router

    pid = "proj_test"
    proj = projects_dir / pid
    proj.mkdir(parents=True)
    (proj / "project.json").write_text(
        json.dumps({"id": pid, "current_stage": "STAGE4"}),
        encoding="utf-8",
    )
    chapters = proj / "chapters"
    chapters.mkdir()
    (chapters / "ch01_scene_001_draft.md").write_text("body")
    (proj / "progress.json").write_text('{}', encoding="utf-8")

    # Monkeypatch Path.unlink to raise OSError mid-loop. This simulates a
    # permission denied or disk-full error after some files have been deleted.
    from backend.conductor import state_machine as sm_module
    real_unlink = sm_module.Path.unlink

    def unlink_with_failure(self, *args, **kwargs):
        # Fail on the second unlink to simulate partial completion.
        if self.name == "progress.json":
            raise OSError("simulated disk full")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(sm_module.Path, "unlink", unlink_with_failure)

    monkeypatch.setattr(_config.settings, "projects_dir", projects_dir)
    from backend.api import project as _project_mod
    monkeypatch.setattr(_project_mod, "fm", _project_mod.FileManager(projects_dir))

    app = FastAPI()
    app.include_router(router)
    # FastAPI's default exception handler returns 500 for uncaught OSError;
    # raise_server_exceptions=False makes TestClient return it instead of
    # re-raising so we can assert on the HTTP status code.
    no_raise_client = TestClient(app, raise_server_exceptions=False)

    resp = no_raise_client.post(f"/api/project/{pid}/reset")
    assert resp.status_code == 500
    # The draft.md was deleted before progress.json failed; the project
    # state is partial but the endpoint correctly surfaces the failure.
    assert not (chapters / "ch01_scene_001_draft.md").exists()
    # project.json stage was NOT yet rewritten when the error fired.
    assert json.loads((proj / "project.json").read_text(encoding="utf-8"))["current_stage"] == "STAGE4"
