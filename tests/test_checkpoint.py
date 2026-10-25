"""
CheckpointManager unit tests — save, load, recover, restore.
Covers AC-7: Checkpoint recovery after crash.
"""

import json
import tempfile
from pathlib import Path

import pytest

from backend.conductor.checkpoint import CheckpointManager


@pytest.fixture
def projects_dir():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def cm(projects_dir):
    return CheckpointManager("test_project", projects_dir=projects_dir)


class TestSave:
    def test_save_creates_checkpoint_file(self, cm):
        cm.save(
            pipeline_stage="scene_writing",
            current_chapter=2,
            current_scene=3,
        )
        assert cm.has_checkpoint()

    def test_save_includes_all_fields(self, cm):
        checkpoint = cm.save(
            pipeline_stage="scene_reviewing",
            current_chapter=1,
            current_scene=2,
            l0_snapshot={"林峰": {"location": "星辰宗"}},
            registry_snapshots={
                "conflicts": [{"id": "cf_001"}],
                "mysteries": [{"id": "mys_001"}],
            },
            character_states=[{"name": "林峰", "location": "星辰宗"}],
        )
        assert checkpoint["project_id"] == "test_project"
        assert checkpoint["pipeline_stage"] == "scene_reviewing"
        assert checkpoint["current_chapter"] == 1
        assert checkpoint["current_scene"] == 2
        assert checkpoint["l0_snapshot"]["林峰"]["location"] == "星辰宗"
        assert len(checkpoint["registry_snapshots"]["conflicts"]) == 1
        assert len(checkpoint["character_states"]) == 1
        assert "timestamp" in checkpoint


class TestLoad:
    def test_load_returns_none_when_no_checkpoint(self, cm):
        assert cm.load() is None

    def test_load_returns_saved_checkpoint(self, cm):
        cm.save(pipeline_stage="writing", current_chapter=3, current_scene=1)
        checkpoint = cm.load()
        assert checkpoint is not None
        assert checkpoint["pipeline_stage"] == "writing"
        assert checkpoint["current_chapter"] == 3


class TestDelete:
    def test_delete_removes_checkpoint(self, cm):
        cm.save(pipeline_stage="writing", current_chapter=1, current_scene=1)
        cm.delete()
        assert not cm.has_checkpoint()
        assert cm.load() is None

    def test_delete_when_no_checkpoint_is_safe(self, cm):
        cm.delete()
        assert not cm.has_checkpoint()


class TestRecover:
    def test_recover_with_no_checkpoint(self, cm):
        result = cm.recover()
        assert not result["recoverable"]
        assert "未找到检查点文件" in result["message"]

    def test_recover_with_missing_registry_files(self, cm):
        cm.save(
            pipeline_stage="reviewing",
            current_chapter=1,
            current_scene=2,
            registry_snapshots={
                "conflicts": [{"id": "cf_001"}],
                "mysteries": [{"id": "mys_001"}],
            },
        )
        result = cm.recover()
        assert result["recoverable"]
        assert len(result["missing_files"]) > 0

    def test_recover_when_all_files_present(self, cm, projects_dir):
        cm.save(
            pipeline_stage="reviewing",
            current_chapter=1,
            current_scene=2,
            registry_snapshots={"conflicts": [{"id": "cf_001"}]},
        )
        registry_dir = projects_dir / "test_project" / "storyos"
        registry_dir.mkdir(parents=True, exist_ok=True)
        (registry_dir / "conflicts.json").write_text("[]")
        result = cm.recover()
        assert result["recoverable"]
        assert len(result["missing_files"]) == 0


class TestRestoreRegistries:
    def test_restore_creates_registry_files(self, cm, projects_dir):
        snapshot = {
            "conflicts": [{"id": "cf_001", "intensity": "high"}],
            "mysteries": [{"id": "mys_001", "title": "起源"}],
        }
        cm.restore_registries_from_snapshot(snapshot)

        conflicts_path = projects_dir / "test_project" / "storyos" / "conflicts.json"
        mysteries_path = projects_dir / "test_project" / "storyos" / "mysteries.json"

        assert conflicts_path.exists()
        assert mysteries_path.exists()

        conflicts = json.loads(conflicts_path.read_text())
        assert len(conflicts) == 1
        assert conflicts[0]["id"] == "cf_001"


class TestLegacyFilenameMigration:
    """The StoryForge → nebula rename moved the checkpoint file from
    `.storyforge_checkpoint.json` to `.nebula_checkpoint.json`. Projects
    created before the rename must keep their resume state, so the legacy
    file is migrated in place on first touch."""

    def test_legacy_file_is_migrated_on_load(self, cm, projects_dir):
        project_dir = projects_dir / "test_project"
        project_dir.mkdir(parents=True, exist_ok=True)
        legacy = project_dir / ".storyforge_checkpoint.json"
        legacy.write_text(
            json.dumps({"pipeline_stage": "scene_writing", "current_chapter": 7}),
            encoding="utf-8",
        )

        assert cm.has_checkpoint()
        data = cm.load()
        assert data["current_chapter"] == 7
        # Legacy file was renamed, not copied.
        assert not legacy.exists()
        assert (project_dir / ".nebula_checkpoint.json").exists()

    def test_legacy_file_is_migrated_on_save(self, cm, projects_dir):
        project_dir = projects_dir / "test_project"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / ".storyforge_checkpoint.json").write_text(
            json.dumps({"pipeline_stage": "scene_writing", "current_chapter": 3}),
            encoding="utf-8",
        )

        cm.save(pipeline_stage="scene_reviewing", current_chapter=4, current_scene=1)

        assert not (project_dir / ".storyforge_checkpoint.json").exists()
        assert (project_dir / ".nebula_checkpoint.json").exists()
        assert cm.load()["current_chapter"] == 4

    def test_new_filename_wins_when_both_exist(self, cm, projects_dir):
        project_dir = projects_dir / "test_project"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / ".storyforge_checkpoint.json").write_text(
            json.dumps({"current_chapter": 1}), encoding="utf-8"
        )
        (project_dir / ".nebula_checkpoint.json").write_text(
            json.dumps({"current_chapter": 9}), encoding="utf-8"
        )

        assert cm.load()["current_chapter"] == 9

    def test_delete_removes_both_filenames(self, cm, projects_dir):
        project_dir = projects_dir / "test_project"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / ".storyforge_checkpoint.json").write_text("{}", encoding="utf-8")
        (project_dir / ".nebula_checkpoint.json").write_text("{}", encoding="utf-8")

        cm.delete()

        assert not (project_dir / ".storyforge_checkpoint.json").exists()
        assert not (project_dir / ".nebula_checkpoint.json").exists()
