import stat

import pytest

from uncommon_route.artifacts import ArtifactStore, content_persistence_enabled


def test_artifact_files_are_private(tmp_path):
    store = ArtifactStore(root=tmp_path / "artifacts")
    record = store.store_text("sensitive content", role="tool")

    for suffix in (".txt", ".json"):
        path = store.root / f"{record.id}{suffix}"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_artifact_store_can_be_disabled(tmp_path):
    store = ArtifactStore(root=tmp_path / "artifacts", enabled=False)

    assert store.enabled is False
    assert store.count() == 0
    assert store.list() == []
    assert store.get("missing") is None
    with pytest.raises(RuntimeError, match="Artifact storage is disabled"):
        store.store_text("do not persist", role="tool")


def test_capture_content_env_disables_artifact_persistence():
    assert content_persistence_enabled({"UNCOMMON_ROUTE_CAPTURE_CONTENT": "0"}) is False
    assert content_persistence_enabled({"UNCOMMON_ROUTE_DISABLE_ARTIFACTS": "true"}) is False
    assert content_persistence_enabled({}) is True
