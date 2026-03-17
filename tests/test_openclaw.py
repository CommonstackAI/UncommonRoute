"""Tests for OpenClaw config-patch integration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from uncommon_route import openclaw_install, openclaw_status, openclaw_uninstall
from uncommon_route.openclaw import (
    _PROVIDER_ID,
    _build_provider_block,
    install,
    status,
    uninstall,
)


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect OpenClaw config to a temp directory so tests don't touch real config."""
    fake_dir = tmp_path / ".openclaw"
    fake_config = fake_dir / "openclaw.json"
    monkeypatch.setattr("uncommon_route.openclaw._OPENCLAW_DIR", fake_dir)
    monkeypatch.setattr("uncommon_route.openclaw._CONFIG_FILE", fake_config)
    monkeypatch.setattr("uncommon_route.openclaw._PLUGINS_DIR", fake_dir / "plugins")


class TestInstall:
    def test_install_creates_config(self, tmp_path: Path) -> None:
        assert install(port=9999) is True
        config_file = tmp_path / ".openclaw" / "openclaw.json"
        assert config_file.exists()

        config = json.loads(config_file.read_text())
        providers = config["models"]["providers"]
        assert _PROVIDER_ID in providers
        assert "http://127.0.0.1:9999/v1" in providers[_PROVIDER_ID]["baseUrl"]

    def test_install_sets_default_model(self) -> None:
        install()
        s = status()
        assert s["default_model"] == "uncommon-route/auto"

    def test_install_idempotent(self) -> None:
        assert install() is True  # first: new
        assert install() is False  # second: already exists

    def test_install_registers_models(self) -> None:
        install()
        s = status()
        assert s["model_count"] > 1  # auto + all pricing models


class TestUninstall:
    def test_uninstall_removes_provider(self) -> None:
        install()
        assert uninstall() is True
        s = status()
        assert s["registered"] is False
        assert s["config_patched"] is False

    def test_uninstall_when_not_installed(self) -> None:
        assert uninstall() is False

    def test_uninstall_clears_default_model(self) -> None:
        install()
        uninstall()
        s = status()
        assert s["default_model"] is None


class TestStatus:
    def test_status_not_registered(self) -> None:
        s = status()
        assert s["registered"] is False
        assert s["plugin_installed"] is False
        assert s["config_patched"] is False

    def test_status_after_install(self) -> None:
        install(port=8403)
        s = status()
        assert s["registered"] is True
        assert s["config_patched"] is True
        assert s["base_url"] == "http://127.0.0.1:8403/v1"
        assert s["provider_id"] == _PROVIDER_ID


class TestBuildProviderBlock:
    def test_block_structure(self) -> None:
        block = _build_provider_block(8403)
        assert block["baseUrl"] == "http://127.0.0.1:8403/v1"
        assert block["api"] == "openai-completions"
        assert isinstance(block["models"], list)
        assert len(block["models"]) > 0

    def test_auto_model_is_first(self) -> None:
        block = _build_provider_block(8403)
        assert block["models"][0]["id"] == "uncommon-route/auto"

    def test_virtual_modes_are_registered(self) -> None:
        block = _build_provider_block(8403)
        ids = [m["id"] for m in block["models"][:3]]
        assert ids == [
            "uncommon-route/auto",
            "uncommon-route/fast",
            "uncommon-route/best",
        ]

    def test_model_costs(self) -> None:
        block = _build_provider_block(8403)
        auto_model = block["models"][0]
        assert auto_model["cost"]["input"] == 0
        assert auto_model["cost"]["output"] == 0


class TestPublicAPI:
    def test_openclaw_install_export(self) -> None:
        assert openclaw_install() is True

    def test_openclaw_uninstall_export(self) -> None:
        openclaw_install()
        assert openclaw_uninstall() is True

    def test_openclaw_status_export(self) -> None:
        s = openclaw_status()
        assert "registered" in s
