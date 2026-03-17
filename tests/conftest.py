"""Shared fixtures for UncommonRoute tests."""

from __future__ import annotations

import pytest

from uncommon_route.model_experience import (
    InMemoryModelExperienceStorage,
    ModelExperienceStore,
)
from uncommon_route.providers import ProvidersConfig
from uncommon_route.routing_config_store import InMemoryRoutingConfigStorage, RoutingConfigStore
from uncommon_route.spend_control import InMemorySpendControlStorage, SpendControl


@pytest.fixture
def spend_control() -> SpendControl:
    return SpendControl(storage=InMemorySpendControlStorage())


@pytest.fixture(autouse=True)
def _isolate_proxy_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "uncommon_route.proxy.load_providers",
        lambda: ProvidersConfig(),
    )
    monkeypatch.setattr(
        "uncommon_route.proxy.ModelExperienceStore",
        lambda: ModelExperienceStore(storage=InMemoryModelExperienceStorage()),
    )
    monkeypatch.setattr(
        "uncommon_route.proxy.RoutingConfigStore",
        lambda: RoutingConfigStore(storage=InMemoryRoutingConfigStorage()),
    )
