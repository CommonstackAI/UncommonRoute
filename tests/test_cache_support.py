from __future__ import annotations

from uncommon_route.cache_support import apply_anthropic_cache_breakpoints


def test_anthropic_cache_breakpoints_do_not_upgrade_after_existing_5m() -> None:
    body = {
        "system": [
            {"type": "text", "text": "stable instructions", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "recent instructions"},
        ],
        "messages": [{"role": "user", "content": "continue"}],
    }

    plan = apply_anthropic_cache_breakpoints(
        body,
        session_id="sess-1",
        step_type="tool-result-followup",
    )

    assert plan.anthropic_ttl == "5m"
    assert body["system"][-1]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_cache_breakpoints_preserve_existing_cache_control() -> None:
    body = {
        "tools": [
            {"name": "search", "input_schema": {"type": "object"}},
            {"name": "write", "input_schema": {"type": "object"}, "cache_control": {"type": "ephemeral"}},
        ],
        "system": [{"type": "text", "text": "policy"}],
        "messages": [{"role": "user", "content": "continue"}],
    }

    plan = apply_anthropic_cache_breakpoints(
        body,
        session_id="sess-1",
        step_type="tool-selection",
    )

    assert plan.anthropic_ttl == "5m"
    assert body["tools"][-1]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_cache_breakpoints_still_use_1h_when_safe() -> None:
    body = {
        "tools": [{"name": "search", "input_schema": {"type": "object"}}],
        "system": [{"type": "text", "text": "policy"}],
        "messages": [{"role": "user", "content": "continue"}],
    }

    plan = apply_anthropic_cache_breakpoints(
        body,
        session_id="sess-1",
        step_type="tool-selection",
    )

    assert plan.anthropic_ttl == "1h"
    assert body["tools"][-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert body["system"][-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
