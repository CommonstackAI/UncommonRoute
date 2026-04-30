"""Shared fixtures for UncommonRoute tests."""

from __future__ import annotations

import httpx
import pytest

from uncommon_route.connections_store import ConnectionsStore, InMemoryConnectionsStorage
from uncommon_route.model_experience import (
    InMemoryModelExperienceStorage,
    ModelExperienceStore,
)
from uncommon_route.providers import ProvidersConfig
from uncommon_route.routing_config_store import InMemoryRoutingConfigStorage, RoutingConfigStore
from uncommon_route.spend_control import InMemorySpendControlStorage, SpendControl


class _ImmediateFailingClient:
    is_closed = False

    async def post(self, *args, **kwargs):
        raise httpx.ConnectError("connection refused")

    def build_request(self, *args, **kwargs):
        raise httpx.ConnectError("connection refused")

    async def send(self, *args, **kwargs):
        raise httpx.ConnectError("connection refused")


@pytest.fixture
def spend_control() -> SpendControl:
    return SpendControl(storage=InMemorySpendControlStorage())


@pytest.fixture(autouse=True)
def _isolate_proxy_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "uncommon_route.proxy._LOCAL_CLIENT_HOSTS",
        {"127.0.0.1", "::1", "localhost", "testclient"},
    )
    monkeypatch.setattr(
        "uncommon_route.proxy.ConnectionsStore",
        lambda: ConnectionsStore(storage=InMemoryConnectionsStorage()),
    )
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
    monkeypatch.setattr(
        "uncommon_route.proxy._get_client",
        lambda: _ImmediateFailingClient(),
    )
