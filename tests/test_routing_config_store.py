from __future__ import annotations

from uncommon_route.router.types import RoutingMode, Tier
from uncommon_route.routing_config_store import InMemoryRoutingConfigStorage, RoutingConfigStore


def test_set_tier_marks_override_and_normalizes_fallback() -> None:
    store = RoutingConfigStore(storage=InMemoryRoutingConfigStorage())

    payload = store.set_tier(
        RoutingMode.AUTO,
        Tier.SIMPLE,
        primary="openai/gpt-4o-mini",
        fallback=["openai/gpt-4o-mini", "moonshot/kimi-k2.5", "moonshot/kimi-k2.5", ""],
    )

    row = payload["modes"]["auto"]["tiers"]["SIMPLE"]
    assert row["primary"] == "openai/gpt-4o-mini"
    assert row["fallback"] == ["moonshot/kimi-k2.5"]
    assert row["overridden"] is True
    assert row["hard_pin"] is False
    assert row["selection_mode"] == "adaptive"


def test_set_tier_can_enable_hard_pin() -> None:
    store = RoutingConfigStore(storage=InMemoryRoutingConfigStorage())

    payload = store.set_tier(
        RoutingMode.AUTO,
        Tier.SIMPLE,
        primary="openai/gpt-4o-mini",
        fallback=["moonshot/kimi-k2.5"],
        hard_pin=True,
    )

    row = payload["modes"]["auto"]["tiers"]["SIMPLE"]
    assert row["hard_pin"] is True
    assert row["selection_mode"] == "hard-pin"


def test_reset_to_default_clears_override() -> None:
    store = RoutingConfigStore(storage=InMemoryRoutingConfigStorage())

    store.set_tier(
        RoutingMode.AUTO,
        Tier.SIMPLE,
        primary="openai/gpt-4o-mini",
        fallback=["moonshot/kimi-k2.5"],
    )
    payload = store.reset_tier(RoutingMode.AUTO, Tier.SIMPLE)

    row = payload["modes"]["auto"]["tiers"]["SIMPLE"]
    assert row["primary"] == ""
    assert row["fallback"] == []
    assert row["overridden"] is False


def test_setting_default_values_drops_override() -> None:
    store = RoutingConfigStore(storage=InMemoryRoutingConfigStorage())

    payload = store.export()

    row = payload["modes"]["fast"]["tiers"]["SIMPLE"]
    assert row["primary"] == ""
    assert row["fallback"] == []
    assert row["overridden"] is False
    assert row["selection_mode"] == "adaptive"


def test_default_mode_can_be_persisted() -> None:
    store = RoutingConfigStore(storage=InMemoryRoutingConfigStorage())

    payload = store.set_default_mode(RoutingMode.BEST)

    assert payload["default_mode"] == "best"


def test_reset_clears_default_mode_and_overrides() -> None:
    store = RoutingConfigStore(storage=InMemoryRoutingConfigStorage())

    store.set_default_mode(RoutingMode.FAST)
    store.set_tier(
        RoutingMode.AUTO,
        Tier.SIMPLE,
        primary="openai/gpt-4o-mini",
        fallback=["moonshot/kimi-k2.5"],
    )

    payload = store.reset()

    assert payload["default_mode"] == "auto"
    assert payload["modes"]["auto"]["tiers"]["SIMPLE"]["primary"] == ""
    assert payload["modes"]["auto"]["tiers"]["SIMPLE"]["overridden"] is False
