"""Tests for BYOK provider management."""

from __future__ import annotations

from pathlib import Path

import pytest

from uncommon_route.providers import (
    ProvidersConfig,
    ProviderEntry,
    add_provider,
    load_providers,
    resolve_upstream_model,
    remove_provider,
    select_preferred_model,
)
from uncommon_route.router.types import RoutingFeatures, Tier


@pytest.fixture(autouse=True)
def _isolate_providers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("uncommon_route.providers._DATA_DIR", tmp_path)
    monkeypatch.setattr("uncommon_route.providers._PROVIDERS_FILE", tmp_path / "providers.json")


class TestProviderConfig:
    def test_empty_config(self) -> None:
        cfg = load_providers()
        assert len(cfg.providers) == 0
        assert cfg.keyed_models() == set()

    def test_add_provider(self) -> None:
        cfg = add_provider("deepseek", "sk-test-key-123")
        assert "deepseek" in cfg.providers
        entry = cfg.providers["deepseek"]
        assert entry.api_key == "sk-test-key-123"
        assert entry.base_url == "https://api.deepseek.com/v1"
        assert "deepseek/deepseek-chat" in entry.models

    def test_add_provider_with_plan(self) -> None:
        cfg = add_provider("minimax", "eyJ-test", plan="coding-plan")
        entry = cfg.providers["minimax"]
        assert entry.plan == "coding-plan"
        assert entry.models == [
            "minimax/minimax-m2.5",
            "minimax/minimax-m3",
            "minimax/minimax-m2.7",
        ]

    def test_add_provider_custom_url(self) -> None:
        cfg = add_provider("openai", "sk-openai", base_url="https://my-proxy.com/v1")
        assert cfg.providers["openai"].base_url == "https://my-proxy.com/v1"

    def test_resolve_minimax_upstream_model_ids(self) -> None:
        assert resolve_upstream_model("minimax", "minimax/minimax-m3") == "MiniMax-M3"
        assert resolve_upstream_model("minimax", "minimax/minimax-m2.7") == "MiniMax-M2.7"
        assert resolve_upstream_model("minimax", "minimax/unknown") == "minimax/unknown"

    def test_add_provider_custom_models(self) -> None:
        cfg = add_provider(
            "custom",
            "sk-custom",
            base_url="https://custom.example/v1",
            models=["custom/private-model", "custom/fast-model"],
        )
        assert cfg.providers["custom"].models == [
            "custom/private-model",
            "custom/fast-model",
        ]

    def test_remove_provider(self) -> None:
        add_provider("deepseek", "sk-key")
        assert remove_provider("deepseek") is True
        cfg = load_providers()
        assert "deepseek" not in cfg.providers

    def test_remove_nonexistent(self) -> None:
        assert remove_provider("ghost") is False

    def test_persistence(self, tmp_path: Path) -> None:
        add_provider("deepseek", "sk-key")
        cfg2 = load_providers()
        assert "deepseek" in cfg2.providers
        assert cfg2.providers["deepseek"].api_key == "sk-key"

    def test_keyed_models(self) -> None:
        add_provider("deepseek", "sk-1")
        add_provider("minimax", "mm-1")
        cfg = load_providers()
        keyed = cfg.keyed_models()
        assert "deepseek/deepseek-chat" in keyed
        assert "deepseek/deepseek-reasoner" in keyed
        assert "minimax/minimax-m2.5" in keyed

    def test_get_for_model(self) -> None:
        add_provider("deepseek", "sk-1")
        cfg = load_providers()
        entry = cfg.get_for_model("deepseek/deepseek-chat")
        assert entry is not None
        assert entry.name == "deepseek"
        assert cfg.get_for_model("nonexistent/model") is None


class TestSelectPreferred:
    def test_prefer_keyed_model(self) -> None:
        cfg = ProvidersConfig(providers={
            "deepseek": ProviderEntry(
                name="deepseek", api_key="sk-1",
                base_url="https://api.deepseek.com/v1",
                models=["deepseek/deepseek-chat"],
            ),
        })
        candidates = ["moonshot/kimi-k2.5", "deepseek/deepseek-chat", "google/gemini-2.5-flash-lite"]
        model, entry = select_preferred_model(candidates, cfg)
        assert model == "deepseek/deepseek-chat"
        assert entry is not None
        assert entry.name == "deepseek"

    def test_no_match(self) -> None:
        cfg = ProvidersConfig(providers={
            "deepseek": ProviderEntry(
                name="deepseek", api_key="sk-1",
                base_url="", models=["deepseek/deepseek-chat"],
            ),
        })
        candidates = ["moonshot/kimi-k2.5", "google/gemini-2.5-flash-lite"]
        model, entry = select_preferred_model(candidates, cfg)
        assert model is None
        assert entry is None

    def test_first_keyed_wins(self) -> None:
        cfg = ProvidersConfig(providers={
            "deepseek": ProviderEntry(name="deepseek", api_key="sk-1", base_url="", models=["deepseek/deepseek-chat"]),
            "minimax": ProviderEntry(name="minimax", api_key="mm-1", base_url="", models=["minimax/minimax-m2.5"]),
        })
        candidates = ["minimax/minimax-m2.5", "deepseek/deepseek-chat"]
        model, _ = select_preferred_model(candidates, cfg)
        assert model == "minimax/minimax-m2.5"


class TestRouteWithBYOK:
    def test_route_prefers_keyed_model(self) -> None:
        from uncommon_route import route
        keyed = {"deepseek/deepseek-chat"}
        decision = route("what is 2+2", user_keyed_models=keyed)
        # deepseek/deepseek-chat is in SIMPLE/MEDIUM fallback, should be preferred
        assert decision.model == "deepseek/deepseek-chat"
        assert "byok-preferred" in decision.method

    def test_route_without_byok_uses_default(self) -> None:
        from uncommon_route import route
        decision = route("what is 2+2")
        assert "byok-preferred" not in decision.method

    def test_route_byok_reasoning_tier(self) -> None:
        from uncommon_route import route
        keyed = {"deepseek/deepseek-reasoner"}
        decision = route(
            "prove that sqrt(2) is irrational",
            routing_features=RoutingFeatures(prefers_reasoning=True, tier_floor=Tier.COMPLEX),
            user_keyed_models=keyed,
        )
        assert decision.model == "deepseek/deepseek-reasoner"
        assert "byok-preferred" in decision.method


class TestCLI:
    def test_provider_list_empty(self) -> None:
        import subprocess
        import sys
        r = subprocess.run(
            [sys.executable, "-m", "uncommon_route.cli", "provider", "list"],
            capture_output=True, text=True,
        )
        assert r.returncode == 0
        assert "No providers" in r.stdout

    def test_provider_help(self) -> None:
        import subprocess
        import sys
        r = subprocess.run(
            [sys.executable, "-m", "uncommon_route.cli", "--help"],
            capture_output=True, text=True,
        )
        assert "provider" in r.stdout

    def test_provider_add_cli_accepts_custom_models(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from uncommon_route.providers import cmd_provider

        monkeypatch.setattr(
            "uncommon_route.providers.verify_key",
            lambda _base_url, _api_key: (True, "ok"),
        )

        cmd_provider([
            "add",
            "custom",
            "sk-custom",
            "--url",
            "https://custom.example/v1",
            "--models",
            "custom/private-model, custom/fast-model",
        ])

        cfg = load_providers()
        assert cfg.providers["custom"].models == [
            "custom/private-model",
            "custom/fast-model",
        ]
        assert "custom/private-model, custom/fast-model" in capsys.readouterr().out
