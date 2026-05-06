"""Framework-agnostic plan/apply for `uncommon-route init`.

The TUI (and any future non-interactive driver) collects answers into
`InitAnswers`, asks `build_plan` for a human-readable preview of the
side effects, and finally runs `apply_plan` to actually mutate state.

Keeping this module free of Textual or any UI dependencies makes it
testable and lets the existing CLI flow share the same primitives.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from uncommon_route.connections_store import ConnectionsStore
from uncommon_route.onboarding import upsert_shell_block
from uncommon_route.providers import add_provider

KNOWN_PROVIDERS: tuple[str, ...] = (
    "openai",
    "anthropic",
    "google",
    "deepseek",
    "moonshot",
    "xai",
    "minimax",
)

CONNECTION_CHOICES = ("commonstack", "local", "byok", "skip")
CLIENT_CHOICES = ("claude-code", "codex", "openai", "skip")


@dataclass
class InitAnswers:
    connection_choice: str = "skip"
    upstream_url: str = ""
    upstream_api_key: str = ""
    clear_primary_for_byok: bool = False
    byok_keys: dict[str, str] = field(default_factory=dict)
    client_choice: str = "skip"
    write_rc: bool = True
    port: int = 8403
    start_proxy: bool = False


@dataclass
class PlanItem:
    target: str
    detail: str = ""
    kind: str = "info"  # write | append | skip | warn | info


def build_plan(answers: InitAnswers, *, rc_display: str) -> list[PlanItem]:
    items: list[PlanItem] = []

    if answers.connection_choice == "commonstack":
        items.append(PlanItem(
            target="~/.uncommon-route/connections.json",
            detail=f"set primary upstream → {answers.upstream_url or '(missing)'}",
            kind="write",
        ))
    elif answers.connection_choice == "local":
        items.append(PlanItem(
            target="~/.uncommon-route/connections.json",
            detail=f"set primary upstream → {answers.upstream_url or '(missing)'}",
            kind="write",
        ))
    elif answers.connection_choice == "byok":
        if answers.clear_primary_for_byok:
            items.append(PlanItem(
                target="~/.uncommon-route/connections.json",
                detail="reset primary upstream (BYOK-only)",
                kind="write",
            ))
        keys = [(name, key) for name, key in answers.byok_keys.items() if key.strip()]
        if not keys:
            items.append(PlanItem(target="BYOK providers", detail="no keys entered", kind="warn"))
        for name, _ in keys:
            items.append(PlanItem(
                target="~/.uncommon-route/providers.json",
                detail=f"add provider → {name}",
                kind="append",
            ))
    else:
        items.append(PlanItem(target="connection setup", detail="skipped", kind="skip"))

    if answers.client_choice != "skip":
        if answers.write_rc:
            items.append(PlanItem(
                target=rc_display,
                detail=f"insert/update shell exports for {answers.client_choice}",
                kind="append",
            ))
        else:
            items.append(PlanItem(
                target=f"shell exports ({answers.client_choice})",
                detail="display only",
                kind="info",
            ))
    else:
        items.append(PlanItem(target="client integration", detail="skipped", kind="skip"))

    if answers.start_proxy:
        items.append(PlanItem(
            target="proxy daemon",
            detail=f"start on 127.0.0.1:{answers.port}",
            kind="write",
        ))

    return items


def validate(answers: InitAnswers, *, step: str = "all") -> list[str]:
    """Return human-readable problems that block advancement.

    `step` scopes the checks. "all" runs everything (used by the Review
    step); "upstream"/"byok" only check fields owned by that step so a
    user is not blamed for empty fields they have not seen yet.
    """
    problems: list[str] = []
    if step in ("all", "upstream"):
        if answers.connection_choice == "commonstack":
            if not answers.upstream_url.strip():
                problems.append("Commonstack URL is required.")
            if not answers.upstream_api_key.strip():
                problems.append("Commonstack API key is required.")
        elif answers.connection_choice == "local":
            if not answers.upstream_url.strip():
                problems.append("Upstream URL is required.")
    if step in ("all", "byok"):
        if answers.connection_choice == "byok":
            if not any(v.strip() for v in answers.byok_keys.values()):
                problems.append("Add at least one BYOK provider key (or pick a different connection).")
    return problems


@dataclass
class ApplyResult:
    successes: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


def apply_plan(
    answers: InitAnswers,
    *,
    rc_path: Path,
    rc_display: str,
    render_exports: Callable[[str, int], list[str]],
    start_background_proxy: Callable[..., tuple[bool, str]] | None,
) -> ApplyResult:
    """Mutate disk + (optionally) start the proxy. Callables are injected
    so the TUI does not need to import cli.py and risk circular imports."""
    result = ApplyResult()
    store = ConnectionsStore()

    try:
        if answers.connection_choice in ("commonstack", "local"):
            store.set_primary(
                upstream=answers.upstream_url.strip(),
                api_key=answers.upstream_api_key.strip(),
            )
            result.successes.append(f"Saved primary upstream: {answers.upstream_url.strip()}")
        elif answers.connection_choice == "byok":
            if answers.clear_primary_for_byok:
                store.reset()
                result.successes.append("Cleared primary upstream")
            for name, key in answers.byok_keys.items():
                key_clean = key.strip()
                if not key_clean:
                    continue
                add_provider(name, key_clean)
                result.successes.append(f"Saved provider: {name}")
    except Exception as exc:
        result.failures.append(f"Connection setup failed: {exc}")

    if answers.client_choice != "skip" and answers.write_rc:
        try:
            exports = render_exports(answers.client_choice, answers.port)
            upsert_shell_block(rc_path, "client", exports)
            result.successes.append(f"Updated {rc_display}")
        except Exception as exc:
            result.failures.append(f"Failed to update {rc_display}: {exc}")

    if answers.start_proxy and start_background_proxy is not None:
        try:
            started, message = start_background_proxy(port=answers.port, host="127.0.0.1")
            (result.successes if started else result.failures).append(message)
        except Exception as exc:
            result.failures.append(f"Failed to start proxy: {exc}")

    return result
