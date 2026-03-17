from __future__ import annotations

from pathlib import Path

from uncommon_route.connections_store import (
    ConnectionsStore,
    EffectivePrimaryConnection,
    FileConnectionsStorage,
    InMemoryConnectionsStorage,
    mask_api_key,
    resolve_primary_connection,
)


def test_connections_store_round_trip(tmp_path: Path) -> None:
    storage = FileConnectionsStorage(tmp_path / "connections.json")
    store = ConnectionsStore(storage=storage)

    payload = store.set_primary(
        upstream="https://api.openai.com/v1",
        api_key="sk-test-123456",
    )

    assert payload["primary"]["upstream"] == "https://api.openai.com/v1"
    assert payload["primary"]["has_api_key"] is True

    reloaded = ConnectionsStore(storage=storage)
    assert reloaded.primary().upstream == "https://api.openai.com/v1"
    assert reloaded.primary().api_key == "sk-test-123456"


def test_connections_store_reset_clears_primary() -> None:
    storage = InMemoryConnectionsStorage()
    store = ConnectionsStore(storage=storage)
    store.set_primary(upstream="https://api.commonstack.ai/v1", api_key="csk-123")

    payload = store.reset()

    assert payload["primary"]["upstream"] == ""
    assert payload["primary"]["has_api_key"] is False


def test_resolve_primary_connection_prefers_file_when_no_overrides() -> None:
    store = ConnectionsStore(storage=InMemoryConnectionsStorage())
    store.set_primary(upstream="https://api.openai.com/v1", api_key="sk-file-key")

    resolved = resolve_primary_connection(store=store, env={})

    assert_resolved(
        resolved,
        upstream="https://api.openai.com/v1",
        api_key="sk-file-key",
        source="file",
        upstream_source="file",
        api_key_source="file",
        editable=True,
    )


def test_resolve_primary_connection_env_overrides_file() -> None:
    store = ConnectionsStore(storage=InMemoryConnectionsStorage())
    store.set_primary(upstream="https://api.openai.com/v1", api_key="sk-file-key")

    resolved = resolve_primary_connection(
        store=store,
        env={
            "UNCOMMON_ROUTE_UPSTREAM": "https://api.commonstack.ai/v1",
            "UNCOMMON_ROUTE_API_KEY": "csk-env-key",
        },
    )

    assert_resolved(
        resolved,
        upstream="https://api.commonstack.ai/v1",
        api_key="csk-env-key",
        source="env",
        upstream_source="env",
        api_key_source="env",
        editable=False,
    )


def test_resolve_primary_connection_cli_overrides_env() -> None:
    resolved = resolve_primary_connection(
        cli_upstream="http://127.0.0.1:11434/v1",
        store=ConnectionsStore(storage=InMemoryConnectionsStorage()),
        env={
            "UNCOMMON_ROUTE_UPSTREAM": "https://api.commonstack.ai/v1",
            "UNCOMMON_ROUTE_API_KEY": "csk-env-key",
        },
    )

    assert_resolved(
        resolved,
        upstream="http://127.0.0.1:11434/v1",
        api_key="csk-env-key",
        source="flag",
        upstream_source="flag",
        api_key_source="env",
        editable=False,
    )


def test_resolve_primary_connection_supports_commonstack_key_fallback() -> None:
    resolved = resolve_primary_connection(
        store=ConnectionsStore(storage=InMemoryConnectionsStorage()),
        env={"COMMONSTACK_API_KEY": "csk-fallback"},
    )

    assert_resolved(
        resolved,
        upstream="",
        api_key="csk-fallback",
        source="env",
        upstream_source="unset",
        api_key_source="env",
        editable=False,
    )


def test_mask_api_key_hides_middle_of_secret() -> None:
    assert mask_api_key("") == ""
    assert mask_api_key("sk12") == "***"
    assert mask_api_key("sk-123456789") == "sk-1...789"


def assert_resolved(
    resolved: EffectivePrimaryConnection,
    *,
    upstream: str,
    api_key: str,
    source: str,
    upstream_source: str,
    api_key_source: str,
    editable: bool,
) -> None:
    assert resolved.upstream == upstream
    assert resolved.api_key == api_key
    assert resolved.source == source
    assert resolved.upstream_source == upstream_source
    assert resolved.api_key_source == api_key_source
    assert resolved.editable is editable
