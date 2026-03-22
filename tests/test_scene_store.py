"""Tests for scene_store — named routing scenes."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from uncommon_route.scene_store import SceneConfig, SceneStore, _serialize_scene, _deserialize_scene
from uncommon_route.router.types import Tier


class TestSceneConfig:
    def test_model_pool_deduplicates(self):
        scene = SceneConfig(
            name="test",
            primary="anthropic/claude-opus-4.6",
            fallback=["anthropic/claude-sonnet-4.6", "anthropic/claude-opus-4.6", "openai/gpt-5.2"],
        )
        pool = scene.model_pool()
        assert pool == [
            "anthropic/claude-opus-4.6",
            "anthropic/claude-sonnet-4.6",
            "openai/gpt-5.2",
        ]

    def test_model_pool_strips_whitespace(self):
        scene = SceneConfig(name="t", primary=" anthropic/opus ", fallback=[" google/gemini "])
        pool = scene.model_pool()
        assert pool == ["anthropic/opus", "google/gemini"]

    def test_model_pool_empty_fallback(self):
        scene = SceneConfig(name="t", primary="a/b")
        assert scene.model_pool() == ["a/b"]

    def test_as_routing_constraints(self):
        scene = SceneConfig(
            name="test",
            primary="a/b",
            fallback=["c/d"],
            allowed_providers=["anthropic"],
            max_cost_per_request=0.05,
        )
        constraints = scene.as_routing_constraints()
        assert constraints.allowed_models == ("a/b", "c/d")
        assert constraints.allowed_providers == ("anthropic",)
        assert constraints.max_cost == 0.05

    def test_as_selection_weights_none_by_default(self):
        scene = SceneConfig(name="t", primary="a/b")
        assert scene.as_selection_weights() is None

    def test_as_selection_weights_override(self):
        scene = SceneConfig(
            name="t",
            primary="a/b",
            selection_weights_override={"editorial": 0.8, "cost": 0.1},
        )
        weights = scene.as_selection_weights()
        assert weights is not None
        assert weights.editorial == 0.8
        assert weights.cost == 0.1
        # Unspecified fields use defaults
        assert weights.latency == 0.1


class TestSerialization:
    def test_roundtrip(self):
        scene = SceneConfig(
            name="intimate",
            primary="anthropic/claude-opus-4.6",
            fallback=["anthropic/claude-sonnet-4.6"],
            hard_pin=True,
            description="Private chat with BB",
            tier_floor=Tier.COMPLEX,
        )
        data = _serialize_scene(scene)
        restored = _deserialize_scene(data)
        assert restored is not None
        assert restored.name == "intimate"
        assert restored.primary == "anthropic/claude-opus-4.6"
        assert restored.hard_pin is True
        assert restored.tier_floor == Tier.COMPLEX

    def test_deserialize_invalid_returns_none(self):
        assert _deserialize_scene({}) is None
        assert _deserialize_scene({"name": "x"}) is None  # missing primary
        assert _deserialize_scene({"primary": "x"}) is None  # missing name

    def test_deserialize_normalizes_name(self):
        scene = _deserialize_scene({"name": "  INTIMATE  ", "primary": "a/b"})
        assert scene is not None
        assert scene.name == "intimate"

    def test_tier_enum_serialization(self):
        scene = SceneConfig(name="t", primary="a/b", tier_floor=Tier.MEDIUM, tier_cap=Tier.COMPLEX)
        data = _serialize_scene(scene)
        assert data.get("tier_floor") == "MEDIUM"
        assert data.get("tier_cap") == "COMPLEX"


class TestSceneStore:
    def _make_store(self, tmp_path: Path) -> SceneStore:
        return SceneStore(path=tmp_path / "scenes.json")

    def test_add_and_get(self, tmp_path):
        store = self._make_store(tmp_path)
        scene = SceneConfig(name="intimate", primary="anthropic/claude-opus-4.6", hard_pin=True)
        stored = store.add(scene)
        assert stored.name == "intimate"
        assert stored.hard_pin is True

        fetched = store.get("intimate")
        assert fetched is not None
        assert fetched.primary == "anthropic/claude-opus-4.6"

    def test_get_case_insensitive(self, tmp_path):
        store = self._make_store(tmp_path)
        store.add(SceneConfig(name="Intimate", primary="a/b"))
        assert store.get("intimate") is not None
        assert store.get("INTIMATE") is not None

    def test_remove(self, tmp_path):
        store = self._make_store(tmp_path)
        store.add(SceneConfig(name="test", primary="a/b"))
        assert store.remove("test") is True
        assert store.get("test") is None
        assert store.remove("nonexistent") is False

    def test_list_sorted(self, tmp_path):
        store = self._make_store(tmp_path)
        store.add(SceneConfig(name="cron", primary="a/b"))
        store.add(SceneConfig(name="alpha", primary="c/d"))
        store.add(SceneConfig(name="work", primary="e/f"))
        names = [s.name for s in store.list()]
        assert names == ["alpha", "cron", "work"]

    def test_persistence(self, tmp_path):
        path = tmp_path / "scenes.json"
        store1 = SceneStore(path=path)
        store1.add(SceneConfig(name="intimate", primary="a/opus", hard_pin=True))
        store1.add(SceneConfig(name="crew", primary="g/flash"))

        # New store instance reads from same file
        store2 = SceneStore(path=path)
        assert store2.get("intimate") is not None
        assert store2.get("intimate").hard_pin is True
        assert store2.get("crew") is not None
        assert len(store2.list()) == 2

    def test_add_deduplicates_fallback(self, tmp_path):
        store = self._make_store(tmp_path)
        scene = SceneConfig(
            name="test",
            primary="a/opus",
            fallback=["a/sonnet", "a/opus", "a/haiku"],  # opus is duplicate of primary
        )
        stored = store.add(scene)
        assert "a/opus" not in stored.fallback
        assert stored.fallback == ["a/sonnet", "a/haiku"]

    def test_resolve_missing_returns_none(self, tmp_path):
        store = self._make_store(tmp_path)
        assert store.resolve("nonexistent") is None

    def test_resolve_existing_returns_scene(self, tmp_path):
        store = self._make_store(tmp_path)
        store.add(SceneConfig(name="intimate", primary="a/b"))
        resolved = store.resolve("intimate")
        assert resolved is not None
        assert resolved.name == "intimate"

    def test_corrupt_file_handled_gracefully(self, tmp_path):
        path = tmp_path / "scenes.json"
        path.write_text("not valid json {{{")
        store = SceneStore(path=path)
        assert len(store.list()) == 0

    def test_export_import_roundtrip(self, tmp_path):
        store1 = self._make_store(tmp_path)
        store1.add(SceneConfig(name="a", primary="x/y"))
        store1.add(SceneConfig(name="b", primary="z/w", hard_pin=True))
        exported = store1.export()

        store2 = self._make_store(tmp_path / "other")
        count = store2.import_scenes(exported)
        assert count == 2
        assert store2.get("a") is not None
        assert store2.get("b").hard_pin is True

    def test_update_existing_scene(self, tmp_path):
        store = self._make_store(tmp_path)
        store.add(SceneConfig(name="test", primary="a/b"))
        store.add(SceneConfig(name="test", primary="c/d", hard_pin=True))
        scene = store.get("test")
        assert scene.primary == "c/d"
        assert scene.hard_pin is True
        assert len(store.list()) == 1  # Not duplicated
