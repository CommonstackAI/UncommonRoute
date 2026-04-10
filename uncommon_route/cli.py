"""CLI entry point for UncommonRoute.

Subcommands:
    route    — classify a prompt and print the routing decision (with interactive feedback)
    serve    — start the OpenAI-compatible proxy server
    stop     — stop a background proxy instance
    debug    — show per-dimension scoring breakdown
    doctor   — check configuration and upstream health
    logs     — tail the background-proxy log file
    feedback — manage online learning (status/rollback)
    openclaw — manage OpenClaw integration (install/uninstall/status)
    spend    — manage spending limits (set/clear/status/history)
    provider — API key management (BYOK)
    config   — routing mode/tier defaults
    stats    — routing analytics (summary/history/reset)

Global flags:
    --version / -v
    --help    / -h
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from urllib.parse import urlparse

from uncommon_route.connections_store import ConnectionsStore, resolve_primary_connection
from uncommon_route.paths import data_dir
from uncommon_route.router.api import route
from uncommon_route.router.classifier import classify
from uncommon_route.router.structural import extract_structural_features, extract_unicode_block_features

VERSION = "0.4.1"
_DATA_DIR = data_dir()
_PID_FILE = _DATA_DIR / "serve.pid"
_LOG_FILE = _DATA_DIR / "serve.log"


def _print_help() -> None:
    print(f"""uncommon-route v{VERSION} — Local LLM Router

Quick start:
  uncommon-route serve              Start proxy (set UNCOMMON_ROUTE_UPSTREAM first)
  uncommon-route doctor             Check if everything is configured
  uncommon-route route "hello"      Test routing locally (no upstream needed)

Commands:
  route <prompt>                    Route a prompt (with interactive feedback)
  serve                             Start proxy server (OpenAI + Anthropic)
  stop                              Stop background proxy instance
  doctor                            Check configuration & upstream health
  logs                              Tail background-proxy log
  setup <client>                    Generate config for a client (claude-code)
  debug <prompt>                    Show per-dimension scoring breakdown
  feedback <sub>                    Online learning + confidence calibration (status|rollback|calibrate)
  openclaw <sub>                    OpenClaw integration (install|uninstall|status)
  spend <sub>                       Spending limits (status|set|clear|history)
  provider <sub>                    API key management (list|add|remove|models)
  config <sub>                      Routing config (show|set-default-mode|set-tier|reset-tier|reset)
  stats [sub]                       Routing analytics (summary|history|reset)
  explain <prompt>                  Show v2 signal breakdown for a prompt
  --version                         Show version

Route options:
  --system-prompt <text>              System prompt for context
  --max-tokens <n>                    Max output tokens (default: 4096)
  --mode <name>                       Routing mode: auto|fast|best
  --json                              Output as JSON
  --no-feedback                       Skip interactive feedback prompt

Serve options:
  --port <n>                          Port to listen on (default: 8403)
  --host <addr>                       Host to bind (default: 127.0.0.1)
  --upstream <url>                    Upstream API base URL
  --composition-config <path>         JSON composition policy override
  --daemon                            Run in background (logs to ~/.uncommon-route/serve.log)

Logs options:
  --limit <n>                         Number of lines to show (default: 50)
  --follow                            Stream new lines (like tail -f)

Feedback subcommands:
  feedback status                     Show online learning and route-confidence calibration status
  feedback rollback                   Discard online weights, revert to base model
  feedback calibrate                  Fit route-confidence calibration from labeled local traces

OpenClaw subcommands:
  openclaw install [--port <n>]       Register as OpenClaw provider
  openclaw uninstall                  Remove from OpenClaw
  openclaw status                     Check registration

Provider subcommands:
  provider list                       Show configured API keys
  provider add <name> <key>           Add key (e.g. deepseek, minimax, openai)
  provider remove <name>              Remove a key
  provider models                     List user-keyed models

Spend subcommands:
  spend status                        Show spending status & limits
  spend set <window> <amount>         Set limit (window: per_request|hourly|daily|session)
  spend clear <window>                Remove a limit
  spend history [--limit <n>]         Show recent spending records

Stats subcommands:
  stats                               Show routing summary (default)
  stats summary                       Same as above
  stats history [--limit <n>]         Recent routing decisions
  stats reset                         Clear all stats

Config subcommands:
  config show [--json]                Show active default mode and tier overrides
  config set-default-mode <mode>      Set the default mode used when no model is specified
  config set-tier <mode> <tier> <primary> [--fallback <csv>] [--strategy adaptive|hard-pin]
                                      Override one mode/tier routing table
  config reset-tier <mode> <tier>     Remove one override
  config reset                        Clear all routing overrides

Setup subcommands:
  setup claude-code [--port <n>]      Generate Claude Code environment config
  setup codex [--port <n>]            Generate OpenAI Codex environment config
  setup openai [--port <n>]           Generate OpenAI SDK / Cursor config

Examples:
  uncommon-route route "what is 2+2"
  uncommon-route serve --port 8403
  uncommon-route serve --daemon
  uncommon-route setup claude-code
  uncommon-route doctor
  uncommon-route logs --follow
  uncommon-route spend set hourly 5.00
  uncommon-route provider add deepseek sk-...
  uncommon-route config set-default-mode fast
  uncommon-route config set-tier auto SIMPLE openai/gpt-4o-mini --fallback moonshot/kimi-k2.5 --strategy hard-pin
""")


def _parse_flags(args: list[str], known_flags: dict[str, bool]) -> tuple[dict[str, str | bool], list[str]]:
    """Parse flags from args. known_flags maps flag name -> has_value."""
    flags: dict[str, str | bool] = {}
    rest: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        clean = arg.lstrip("-")
        if arg.startswith("--") and clean in known_flags:
            if known_flags[clean]:
                if i + 1 < len(args):
                    flags[clean] = args[i + 1]
                    i += 2
                else:
                    print(f"Error: {arg} requires a value", file=sys.stderr)
                    sys.exit(1)
            else:
                flags[clean] = True
                i += 1
        else:
            rest.append(arg)
            i += 1
    return flags, rest


_TIER_ORDER = ["SIMPLE", "MEDIUM", "COMPLEX"]


def _apply_feedback(features: dict[str, float], current_tier: str, signal: str) -> str | None:
    """Apply one online learning update from CLI feedback. Returns the target tier or None."""
    from uncommon_route.router.classifier import save_online_model, update_model

    idx = _TIER_ORDER.index(current_tier) if current_tier in _TIER_ORDER else 1
    if signal == "u":
        target = _TIER_ORDER[min(idx + 1, len(_TIER_ORDER) - 1)]
    elif signal == "d":
        target = _TIER_ORDER[max(idx - 1, 0)]
    elif signal == "ok":
        target = current_tier
    else:
        return None

    if not update_model(features, target):
        return None
    save_online_model()
    return target


def _cmd_route(args: list[str]) -> None:
    flags, rest = _parse_flags(args, {
        "system-prompt": True,
        "max-tokens": True,
        "mode": True,
        "json": False,
        "no-feedback": False,
    })

    prompt = " ".join(rest)
    if not prompt:
        print("Error: no prompt provided", file=sys.stderr)
        sys.exit(1)

    system_prompt = str(flags["system-prompt"]) if "system-prompt" in flags else None
    max_tokens = int(flags.get("max-tokens", 4096))
    if "mode" in flags:
        mode_flag = str(flags["mode"]).strip().lower()
    else:
        from uncommon_route.routing_config_store import RoutingConfigStore
        mode_flag = RoutingConfigStore().default_mode().value
    output_json = bool(flags.get("json", False))
    no_feedback = bool(flags.get("no-feedback", False))

    from uncommon_route.router.types import RoutingInfeasibleError, RoutingMode

    try:
        routing_mode = RoutingMode(mode_flag)
    except ValueError:
        print(
            "Error: --mode must be one of auto, fast, best",
            file=sys.stderr,
        )
        sys.exit(1)

    start = time.perf_counter_ns()
    try:
        decision = route(
            prompt,
            system_prompt=system_prompt,
            max_output_tokens=max_tokens,
            routing_mode=routing_mode,
        )
    except RoutingInfeasibleError as exc:
        detail = exc.infeasibility.as_dict()
        if output_json:
            print(json.dumps({"error": detail}, indent=2))
        else:
            print(f"  Error:      {detail['message']}", file=sys.stderr)
            if detail.get("failed_constraints"):
                print(
                    "  Constraints: "
                    + ", ".join(str(tag) for tag in detail["failed_constraints"]),
                    file=sys.stderr,
                )
            if detail.get("missing_capabilities"):
                print(
                    "  Missing:    "
                    + ", ".join(str(tag) for tag in detail["missing_capabilities"]),
                    file=sys.stderr,
                )
        sys.exit(2)
    elapsed_us = (time.perf_counter_ns() - start) / 1000

    if output_json:
        print(json.dumps({
            "mode": decision.mode.value,
            "model": decision.model,
            "tier": decision.tier.value,
            "confidence": round(decision.confidence, 3),
            "raw_confidence": round(decision.raw_confidence, 3),
            "confidence_source": decision.confidence_source,
            "confidence_calibration": {
                "version": decision.calibration_version,
                "sample_count": decision.calibration_sample_count,
                "temperature": round(decision.calibration_temperature, 3),
                "applied_tags": list(decision.calibration_applied_tags),
            },
            "cost_estimate": round(decision.cost_estimate, 6),
            "savings": round(decision.savings, 3),
            "reasoning": decision.reasoning,
            "suggested_output_budget": decision.suggested_output_budget,
            "fallback_chain": [
                {"model": fb.model, "cost": round(fb.cost_estimate, 6)}
                for fb in decision.fallback_chain
            ],
            "latency_ms": round(elapsed_us / 1000.0, 3),
        }, indent=2))
        return

    print(f"  Mode:       {decision.mode.value}")
    print(f"  Model:      {decision.model}")
    print(f"  Tier:       {decision.tier.value}")
    print(f"  Confidence: {decision.confidence:.2f}")
    print(f"  Raw conf:   {decision.raw_confidence:.2f} ({decision.confidence_source})")
    if decision.calibration_version:
        print(
            "  Calib:      "
            f"{decision.calibration_version} "
            f"(labels={decision.calibration_sample_count}, T={decision.calibration_temperature:.2f})"
        )
    print(f"  Cost:       ${decision.cost_estimate:.6f}")
    print(f"  Savings:    {decision.savings:.0%}")
    print(f"  Latency:    {elapsed_us / 1000.0:.3f}ms")
    print(f"  Reasoning:  {decision.reasoning}")
    if decision.fallback_chain:
        print(f"  Fallback:   {' → '.join(fb.model for fb in decision.fallback_chain)}")

    if no_feedback or not sys.stdin.isatty():
        return

    print()
    print("  Feedback: [Enter] ok  [u] should be harder  [d] should be easier  [s] skip")
    try:
        choice = input("  > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return

    if not choice or choice == "ok":
        choice = "ok"
    elif choice not in ("u", "d"):
        return

    from uncommon_route.router.classifier import extract_features
    features = extract_features(prompt, system_prompt)
    target = _apply_feedback(features, decision.tier.value, choice)
    if target:
        action = "reinforced" if target == decision.tier.value else f"learned: {decision.tier.value} → {target}"
        print(f"  ✓ {action} (weights updated)")


def _cmd_debug(args: list[str]) -> None:
    flags, rest = _parse_flags(args, {"system-prompt": True})

    prompt = " ".join(rest)
    if not prompt:
        print("Error: no prompt provided", file=sys.stderr)
        sys.exit(1)

    system_prompt = str(flags["system-prompt"]) if "system-prompt" in flags else None
    full_text = f"{system_prompt or ''} {prompt}".strip()

    result = classify(prompt, system_prompt)

    struct_dims = extract_structural_features(full_text)
    unicode_blocks = extract_unicode_block_features(full_text)

    tier_str = result.tier.value if result.tier else "AMBIGUOUS"
    print(f"  Tier:       {tier_str}")
    print(f"  Complexity: {result.complexity:.3f}")
    print(f"  Confidence: {result.confidence:.3f}")
    print(f"  Signals:    {', '.join(result.signals)}")
    print()

    print("  Structural Features:")
    for d in struct_dims:
        sig = f"  [{d.signal}]" if d.signal else ""
        print(f"    {d.name:<28} {d.score:>7.3f}{sig}")

    print()
    print("  Unicode Blocks:")
    for name, prop in sorted(unicode_blocks.items(), key=lambda x: -x[1]):
        if prop > 0.001:
            print(f"    {name:<28} {prop:>7.3f}")


def _cmd_serve(args: list[str]) -> None:
    flags, _ = _parse_flags(args, {
        "port": True,
        "host": True,
        "upstream": True,
        "composition-config": True,
        "daemon": False,
        "background": False,
    })

    from uncommon_route.proxy import DEFAULT_PORT

    port = int(flags.get("port", DEFAULT_PORT))
    host = str(flags.get("host", "127.0.0.1"))
    upstream = str(flags.get("upstream", "")).strip() or None
    composition_config = str(flags.get("composition-config", "")).strip()
    daemon = bool(flags.get("daemon") or flags.get("background"))

    if daemon:
        _DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        if _PID_FILE.exists():
            old_pid = int(_PID_FILE.read_text().strip())
            try:
                os.kill(old_pid, 0)
                print(f"  Already running (PID {old_pid}). Stop first: uncommon-route stop")
                return
            except OSError:
                _PID_FILE.unlink(missing_ok=True)

        cmd = [sys.executable, "-m", "uncommon_route.cli", "serve",
               "--port", str(port), "--host", host]
        if upstream:
            cmd.extend(["--upstream", upstream])
        if composition_config:
            cmd.extend(["--composition-config", composition_config])

        with open(_LOG_FILE, "a") as lf:
            proc = subprocess.Popen(
                cmd,
                stdout=lf,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        _PID_FILE.write_text(str(proc.pid))
        print(f"  Started in background (PID {proc.pid})")
        print(f"  Logs:  {_LOG_FILE}")
        print("  Stop:  uncommon-route stop")
        return

    from uncommon_route.proxy import serve
    if composition_config:
        os.environ["UNCOMMON_ROUTE_COMPOSITION_CONFIG"] = composition_config
    serve(port=port, host=host, upstream=upstream)


def _cmd_stop(args: list[str]) -> None:
    if not _PID_FILE.exists():
        print("  No running instance found")
        return
    try:
        pid = int(_PID_FILE.read_text().strip())
    except ValueError:
        _PID_FILE.unlink(missing_ok=True)
        print("  Corrupt PID file removed")
        return
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"  Stopped (PID {pid})")
    except ProcessLookupError:
        print(f"  Process {pid} not found (already stopped)")
    _PID_FILE.unlink(missing_ok=True)


def _cmd_doctor(args: list[str]) -> None:
    import asyncio

    checks: list[tuple[str, bool, str]] = []

    # Python version
    vi = sys.version_info
    ok = vi >= (3, 11)
    checks.append(("Python version", ok, f"{vi.major}.{vi.minor}.{vi.micro}"))

    connection = resolve_primary_connection(store=ConnectionsStore())
    upstream = connection.upstream
    checks.append(("Upstream configured", bool(upstream), upstream or "(not set)"))

    def _upstream_is_local(url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}

    # API key configured
    api_key = connection.api_key
    local_upstream = bool(upstream) and _upstream_is_local(upstream)
    key_preview = f"{api_key[:8]}..." if len(api_key) > 8 else ("(set)" if api_key else "(not set)")
    if api_key:
        checks.append(("API key configured", True, key_preview))
    elif local_upstream:
        checks.append(("API key configured", True, "(not needed for local upstream)"))
    else:
        checks.append(("API key configured", False, key_preview))

    # Upstream reachable + model discovery
    if upstream and (api_key or local_upstream):
        from uncommon_route.model_map import ModelMapper
        mapper = ModelMapper(upstream)
        gw_tag = " (gateway)" if mapper.is_gateway else ""
        count = asyncio.run(mapper.discover(api_key or None))
        provider_label = f"{mapper.provider}{gw_tag}"
        reachability_detail = provider_label if count > 0 else f"{provider_label} (discovery failed)"
        checks.append(("Upstream reachable", count > 0, reachability_detail))
        checks.append(("Models discovered", count > 0, f"{count} models" if count > 0 else "0 models"))
        if mapper.discovered:
            unresolved = mapper.unresolved_models()
            if unresolved:
                names = ", ".join(unresolved[:3])
                extra = f" (+{len(unresolved) - 3} more)" if len(unresolved) > 3 else ""
                checks.append(("Model mapping", False, f"{len(unresolved)} unresolved: {names}{extra}"))
            else:
                checks.append(("Model mapping", True, "all internal models resolved"))
        else:
            checks.append(("Model mapping", False, "cannot verify (discovery failed)"))
    elif upstream:
        checks.append(("Upstream reachable", False, "cannot test without API key"))

    # BYOK providers
    from uncommon_route.providers import load_providers
    providers = load_providers()
    if providers.providers:
        for name, entry in providers.providers.items():
            reachable = asyncio.run(_check_provider(entry.base_url, entry.api_key))
            checks.append((f"BYOK: {name}", reachable, entry.base_url))
    else:
        checks.append(("BYOK providers", True, "none configured (optional)"))

    # Claude Code integration
    anth_base = os.environ.get("ANTHROPIC_BASE_URL", "")
    if anth_base:
        checks.append(("Claude Code", True, f"ANTHROPIC_BASE_URL={anth_base}"))
    else:
        checks.append(("Claude Code", True, "not configured (optional — run: uncommon-route setup claude-code)"))

    # Daemon status
    if _PID_FILE.exists():
        pid = int(_PID_FILE.read_text().strip())
        try:
            os.kill(pid, 0)
            checks.append(("Proxy daemon", True, f"running (PID {pid})"))
        except OSError:
            checks.append(("Proxy daemon", False, f"stale PID file (PID {pid})"))
    else:
        checks.append(("Proxy daemon", True, "not running (foreground or stopped)"))

    checks.append((
        "Primary connection source",
        True,
        f"source={connection.source} upstream={connection.upstream_source} api_key={connection.api_key_source}",
    ))

    # Output
    all_ok = all(ok for _, ok, _ in checks)
    print()
    for label, ok, detail in checks:
        icon = "✓" if ok else "✗"
        print(f"  {icon} {label}: {detail}")
    print()
    if all_ok:
        print("  All checks passed")
    else:
        print("  Some checks failed — see above")


async def _check_provider(base_url: str, api_key: str) -> bool:
    """Lightweight connectivity check for a provider endpoint."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
            resp = await client.get(
                f"{base_url.rstrip('/')}/models",
                headers={"authorization": f"Bearer {api_key}"},
            )
            return resp.status_code == 200
    except Exception:  # noqa: BLE001
        return False


def _cmd_logs(args: list[str]) -> None:
    flags, _ = _parse_flags(args, {"limit": True, "follow": False})
    limit = int(flags.get("limit", 50))
    follow = bool(flags.get("follow"))

    if not _LOG_FILE.exists():
        print("  No log file found")
        print("  Start the proxy in background: uncommon-route serve --daemon")
        return

    if follow:
        print(f"  Tailing {_LOG_FILE}  (Ctrl+C to stop)")
        try:
            with open(_LOG_FILE) as f:
                f.seek(0, 2)
                while True:
                    line = f.readline()
                    if line:
                        print(line, end="")
                    else:
                        time.sleep(0.3)
        except KeyboardInterrupt:
            pass
        return

    lines = _LOG_FILE.read_text().splitlines()
    for line in lines[-limit:]:
        print(line)


def _cmd_openclaw(args: list[str]) -> None:
    from uncommon_route.openclaw import cmd_openclaw
    cmd_openclaw(args)


def _cmd_spend(args: list[str]) -> None:
    from uncommon_route.spend_control import SpendControl

    sc = SpendControl()

    if not args:
        args = ["status"]

    sub = args[0]

    if sub == "status":
        s = sc.status()
        print("  Spending Limits:")
        if s.limits.per_request is not None:
            print(f"    Per-request:  ${s.limits.per_request:.2f}")
        if s.limits.hourly is not None:
            rem = s.remaining.get("hourly")
            print(f"    Hourly:       ${s.limits.hourly:.2f}  (spent: ${s.spent['hourly']:.4f}, remaining: ${rem:.4f})" if rem is not None else "")
        if s.limits.daily is not None:
            rem = s.remaining.get("daily")
            print(f"    Daily:        ${s.limits.daily:.2f}  (spent: ${s.spent['daily']:.4f}, remaining: ${rem:.4f})" if rem is not None else "")
        if s.limits.session is not None:
            rem = s.remaining.get("session")
            print(f"    Session:      ${s.limits.session:.2f}  (spent: ${s.spent['session']:.4f}, remaining: ${rem:.4f})" if rem is not None else "")
        if all(v is None for v in vars(s.limits).values()):
            print("    (no limits set)")
        print(f"\n  Total calls this session: {s.calls}")

    elif sub == "set":
        if len(args) < 3:
            print("Usage: uncommon-route spend set <window> <amount>", file=sys.stderr)
            print("  Windows: per_request, hourly, daily, session", file=sys.stderr)
            sys.exit(1)
        window = args[1]
        amount = float(args[2])
        sc.set_limit(window, amount)  # type: ignore[arg-type]
        print(f"  Set {window} limit: ${amount:.2f}")

    elif sub == "clear":
        if len(args) < 2:
            print("Usage: uncommon-route spend clear <window>", file=sys.stderr)
            sys.exit(1)
        window = args[1]
        sc.clear_limit(window)  # type: ignore[arg-type]
        print(f"  Cleared {window} limit")

    elif sub == "history":
        flags, _ = _parse_flags(args[1:], {"limit": True})
        limit = int(flags.get("limit", 20))
        records = sc.history(limit=limit)
        if not records:
            print("  No spending records")
            return
        print(f"  Recent spending ({len(records)} records):")
        for r in records:
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r.timestamp))
            model_str = f"  [{r.model}]" if r.model else ""
            print(f"    {ts}  ${r.amount:.6f}{model_str}")

    else:
        print(f"Unknown spend subcommand: {sub}", file=sys.stderr)
        print("  Available: status, set, clear, history", file=sys.stderr)
        sys.exit(1)


def _cmd_provider(args: list[str]) -> None:
    from uncommon_route.providers import cmd_provider
    cmd_provider(args)


def _print_routing_config(payload: dict[str, object], *, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(payload, indent=2))
        return
    print("  Routing Config:")
    print(f"    Source:    {payload.get('source', 'local-file')}")
    print(f"    Editable:  {'yes' if payload.get('editable', False) else 'no'}")
    print(f"    Default:   {payload.get('default_mode', 'auto')}")
    modes = payload.get("modes", {})
    if not isinstance(modes, dict):
        return
    for mode_name in ("auto", "fast", "best"):
        mode_payload = modes.get(mode_name)
        if not isinstance(mode_payload, dict):
            continue
        tiers = mode_payload.get("tiers", {})
        if not isinstance(tiers, dict):
            continue
        print(f"\n  {mode_name}:")
        for tier_name in ("SIMPLE", "MEDIUM", "COMPLEX"):
            row = tiers.get(tier_name)
            if not isinstance(row, dict):
                continue
            primary = str(row.get("primary", ""))
            fallback = row.get("fallback", [])
            overridden = bool(row.get("overridden", False))
            selection_mode = str(row.get("selection_mode", "adaptive"))
            suffix = "  [override]" if overridden else ""
            label = primary or "Discovery-managed"
            print(f"    {tier_name:<10} {label}{suffix}")
            print(f"               strategy: {selection_mode}")
            if isinstance(fallback, list) and fallback:
                print(f"               fallback: {', '.join(str(item) for item in fallback)}")


def _cmd_config(args: list[str]) -> None:
    from uncommon_route.router.types import RoutingMode, Tier
    from uncommon_route.routing_config_store import RoutingConfigStore

    flags, rest = _parse_flags(args, {"json": False, "fallback": True, "strategy": True})
    store = RoutingConfigStore()
    if not rest:
        rest = ["show"]

    sub = rest[0]
    output_json = bool(flags.get("json", False))

    if sub == "show":
        _print_routing_config(store.export(), as_json=output_json)
        return

    if sub == "set-default-mode":
        if len(rest) < 2:
            print("Usage: uncommon-route config set-default-mode <mode>", file=sys.stderr)
            sys.exit(1)
        try:
            routing_mode = RoutingMode(rest[1].strip().lower())
        except ValueError:
            print("Error: invalid mode", file=sys.stderr)
            sys.exit(1)
        payload = store.set_default_mode(routing_mode)
        _print_routing_config(payload, as_json=output_json)
        return

    if sub == "set-tier":
        if len(rest) < 4:
            print(
                "Usage: uncommon-route config set-tier <mode> <tier> <primary> [--fallback <csv>] [--strategy adaptive|hard-pin]",
                file=sys.stderr,
            )
            sys.exit(1)
        try:
            routing_mode = RoutingMode(rest[1].strip().lower())
            tier = Tier(rest[2].strip().upper())
        except ValueError:
            print("Error: invalid mode or tier", file=sys.stderr)
            sys.exit(1)
        primary = rest[3].strip()
        fallback_csv = str(flags.get("fallback", rest[4] if len(rest) > 4 else ""))
        fallback = [part.strip() for part in fallback_csv.split(",") if part.strip()]
        strategy = str(flags.get("strategy", "adaptive")).strip().lower()
        if strategy not in {"adaptive", "hard-pin", "hard_pin", "pinned"}:
            print("Error: --strategy must be adaptive or hard-pin", file=sys.stderr)
            sys.exit(1)
        payload = store.set_tier(
            routing_mode,
            tier,
            primary=primary,
            fallback=fallback,
            hard_pin=strategy in {"hard-pin", "hard_pin", "pinned"},
        )
        _print_routing_config(payload, as_json=output_json)
    elif sub == "reset-tier":
        if len(rest) < 3:
            print("Usage: uncommon-route config reset-tier <mode> <tier>", file=sys.stderr)
            sys.exit(1)
        try:
            routing_mode = RoutingMode(rest[1].strip().lower())
            tier = Tier(rest[2].strip().upper())
        except ValueError:
            print("Error: invalid mode or tier", file=sys.stderr)
            sys.exit(1)
        payload = store.reset_tier(routing_mode, tier)
        _print_routing_config(payload, as_json=output_json)
    elif sub == "reset":
        payload = store.reset()
        _print_routing_config(payload, as_json=output_json)
    else:
        print(f"Unknown config subcommand: {sub}", file=sys.stderr)
        print("  Available: show, set-default-mode, set-tier, reset-tier, reset", file=sys.stderr)
        sys.exit(1)

    if _PID_FILE.exists() and not output_json:
        print()
        print("  Note: persisted overrides were updated.")
        print("  If a local proxy is already running, restart it or use /v1/routing-config for live updates.")


def _cmd_stats(args: list[str]) -> None:
    from uncommon_route.stats import RouteStats

    rs = RouteStats()
    if not args:
        args = ["summary"]
    sub = args[0]

    if sub == "summary":
        s = rs.summary()
        if s.total_requests == 0:
            print("  No routing data recorded yet.")
            print("  Start the proxy with `uncommon-route serve` and send requests.")
            return
        hours = s.time_range_s / 3600
        print(f"\n  Routing Statistics ({hours:.1f}h window, {s.total_requests} requests)")
        print(f"  {'─' * 50}")
        print(f"  Avg confidence: {s.avg_confidence:.2f}")
        print(f"  Avg savings:    {s.avg_savings:.0%}")
        print(f"  Avg latency:    {s.avg_latency_us / 1000.0:.3f}ms")
        print(f"  Avg reduction:  {s.avg_input_reduction_ratio:.0%}")
        print(f"  Avg cache hit:  {s.avg_cache_hit_ratio:.0%}")
        print(f"  Total cost:     ${s.total_actual_cost:.4f} (estimated: ${s.total_estimated_cost:.4f})")
        print(
            f"  Input tokens:   {s.total_input_tokens_before} -> {s.total_input_tokens_after}"
            f"  | artifacts: {s.total_artifacts_created}"
            f"  | compacted: {s.total_compacted_messages}"
        )
        print(
            f"  Upstream usage: in {s.total_usage_input_tokens}"
            f"  | out {s.total_usage_output_tokens}"
            f"  | cache-read {s.total_cache_read_input_tokens}"
            f"  | cache-write {s.total_cache_write_input_tokens}"
            f"  | breakpoints {s.total_cache_breakpoints}"
        )
        print(
            f"  Semantic:       calls {s.total_semantic_calls}"
            f"  | summaries {s.total_semantic_summaries}"
            f"  | checkpoints {s.total_checkpoints_created}"
            f"  | rehydrated {s.total_rehydrated_artifacts}"
            f"  | failures {s.total_semantic_failures}"
            f"  | quality-fallbacks {s.total_semantic_quality_fallbacks}"
        )

        if s.by_tier:
            print("\n  By Tier:")
            for tier in ("SIMPLE", "MEDIUM", "COMPLEX"):
                ts = s.by_tier.get(tier)
                if not ts:
                    continue
                pct = ts.count / s.total_requests * 100
                print(
                    f"    {tier:<10} │ {ts.count:>5} ({pct:4.1f}%)"
                    f"  │ conf: {ts.avg_confidence:.2f}"
                    f"  │ savings: {ts.avg_savings:.0%}"
                    f"  │ ${ts.total_cost:.4f}"
                )

        if s.by_model:
            print("\n  By Model:")
            ranked = sorted(s.by_model.items(), key=lambda x: -x[1].count)
            for model, ms in ranked[:8]:
                print(f"    {model:<40} {ms.count:>5} reqs  ${ms.total_cost:.4f}")

        if s.by_mode:
            print("\n  By Mode:")
            for mode, count in sorted(s.by_mode.items(), key=lambda x: -x[1]):
                pct = count / s.total_requests * 100
                print(f"    {mode:<20} {count:>5} ({pct:.1f}%)")

        if s.by_method:
            print("\n  By Method:")
            for method, count in sorted(s.by_method.items(), key=lambda x: -x[1]):
                pct = count / s.total_requests * 100
                print(f"    {method:<20} {count:>5} ({pct:.1f}%)")

        if s.by_transport:
            print("\n  By Transport:")
            for transport, ms in sorted(s.by_transport.items(), key=lambda x: -x[1].count):
                pct = ms.count / s.total_requests * 100
                print(f"    {transport:<20} {ms.count:>5} ({pct:.1f}%)  ${ms.total_cost:.4f}")

        if s.by_cache_mode:
            print("\n  By Cache Mode:")
            for cache_mode, ms in sorted(s.by_cache_mode.items(), key=lambda x: -x[1].count):
                pct = ms.count / s.total_requests * 100
                print(f"    {cache_mode:<20} {ms.count:>5} ({pct:.1f}%)  ${ms.total_cost:.4f}")

        if s.by_cache_family:
            print("\n  By Cache Family:")
            for cache_family, ms in sorted(s.by_cache_family.items(), key=lambda x: -x[1].count):
                pct = ms.count / s.total_requests * 100
                print(f"    {cache_family:<20} {ms.count:>5} ({pct:.1f}%)  ${ms.total_cost:.4f}")
        print()

    elif sub == "history":
        flags, _ = _parse_flags(args[1:], {"limit": True})
        limit = int(flags.get("limit", 20))
        records = rs.history(limit=limit)
        if not records:
            print("  No routing records")
            return
        print(f"  Recent routing decisions ({len(records)} records):")
        for r in records:
            ts = time.strftime("%H:%M:%S", time.localtime(r.timestamp))
            cost_str = f"${r.actual_cost:.6f}" if r.actual_cost is not None else f"~${r.estimated_cost:.6f}"
            token_delta = ""
            if r.input_tokens_before > 0:
                token_delta = f"  {r.input_tokens_before}->{r.input_tokens_after}t"
            artifact_tag = f"  art:{r.artifacts_created}" if r.artifacts_created else ""
            semantic_tag = f"  sem:{r.semantic_calls}" if r.semantic_calls else ""
            quality_tag = f"  qfb:{r.semantic_quality_fallbacks}" if r.semantic_quality_fallbacks else ""
            transport_tag = f"  {r.transport}"
            cache_tag = f"  cache:{r.cache_mode}"
            breakpoint_tag = f"  bp:{r.cache_breakpoints}" if r.cache_breakpoints else ""
            print(
                f"    {ts}  {r.mode:<8} {r.tier:<10} {r.model:<35}"
                f" {cost_str}  [{r.method}]{transport_tag}{cache_tag}{breakpoint_tag}"
                f"{token_delta}{artifact_tag}{semantic_tag}{quality_tag}"
            )

    elif sub == "reset":
        rs.reset()
        print("  Stats reset")

    else:
        print(f"Unknown stats subcommand: {sub}", file=sys.stderr)
        print("  Available: summary, history, reset", file=sys.stderr)
        sys.exit(1)


def _cmd_feedback(args: list[str]) -> None:
    from uncommon_route.calibration import get_active_route_confidence_calibrator
    from uncommon_route.router.classifier import (
        _get_online_model_path,
        rollback_online_model,
    )
    from uncommon_route.stats import RouteStats

    if not args:
        args = ["status"]

    sub = args[0]
    calibrator = get_active_route_confidence_calibrator()

    if sub == "status":
        online_path = _get_online_model_path()
        active = online_path.exists()
        print(f"  Online model: {'active' if active else 'inactive (using base model)'}")
        if active:
            import os
            size_kb = os.path.getsize(online_path) / 1024
            mtime = os.path.getmtime(online_path)
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))
            print(f"  Path:         {online_path}")
            print(f"  Size:         {size_kb:.0f} KB")
            print(f"  Last updated: {ts}")
        print()
        calibration = calibrator.status()
        print(
            "  Route confidence calibration: "
            f"{'active' if calibration['active'] else 'inactive'}"
        )
        print(f"  Labels:       {calibration['labeled_examples']}")
        if calibration.get("training_examples"):
            print(
                "  Split:        "
                f"{calibration['training_examples']} train / {calibration['holdout_examples']} holdout"
            )
        if calibration.get("selected_strategy"):
            print(f"  Strategy:     {calibration['selected_strategy']}")
        print(f"  Temperature:  {calibration['temperature']:.2f}")
        if calibration["version"]:
            print(f"  Version:      {calibration['version']}")
            print(
                "  ECE:          "
                f"{calibration['raw_ece']:.3f} -> {calibration['calibrated_ece']:.3f}"
            )
        if calibration.get("holdout_examples"):
            print(
                "  Holdout ECE:  "
                f"{calibration['holdout_raw_ece']:.3f} -> {calibration['holdout_calibrated_ece']:.3f}"
            )
        if calibration.get("stale"):
            print("  Snapshot:     stale (waiting for fresher labeled traces)")
        print()
        print("  How feedback works:")
        print("    1. Run `uncommon-route route <prompt>` and rate the tier")
        print("    2. Your feedback updates a local model overlay (~/.uncommon-route/model_online.json)")
        print("    3. Labeled route traces also fit calibrated runtime confidence")
        print("    4. The base model is never modified — rollback anytime with `feedback rollback`")

    elif sub == "rollback":
        deleted = rollback_online_model()
        if deleted:
            print("  ✓ Online weights deleted — reverted to base model")
        else:
            print("  No online weights found (already using base model)")

    elif sub == "calibrate":
        stats = RouteStats()
        snapshot = calibrator.fit_from_route_records(stats.history())
        status = calibrator.status()
        if not status["active"]:
            print("  Route-confidence calibration remains inactive.")
            if snapshot.labeled_examples < snapshot.min_examples:
                print(
                    f"  Found {snapshot.labeled_examples} labeled traces "
                    f"(need at least {snapshot.min_examples})."
                )
            elif status.get("stale"):
                print("  The saved snapshot is stale; collect fresher feedback and rebuild.")
            elif snapshot.selected_strategy == "raw" and snapshot.holdout_examples > 0:
                print("  Holdout gate rejected the fitted calibration; keeping raw runtime confidence.")
                print(
                    "  Holdout ECE:  "
                    f"{snapshot.holdout_raw_ece:.3f} -> {snapshot.holdout_calibrated_ece:.3f}"
                )
            else:
                print("  No robust calibration model was selected from the available traces.")
            return
        print("  ✓ Rebuilt route-confidence calibration from local traces")
        print(f"  Version:      {snapshot.version}")
        print(f"  Labels:       {snapshot.labeled_examples}")
        print(f"  Strategy:     {snapshot.selected_strategy or 'full'}")
        if snapshot.holdout_examples > 0:
            print(
                "  Split:        "
                f"{snapshot.training_examples} train / {snapshot.holdout_examples} holdout"
            )
        print(f"  Temperature:  {snapshot.temperature:.2f}")
        print(f"  ECE:          {snapshot.raw_ece:.3f} -> {snapshot.calibrated_ece:.3f}")
        print(f"  Adjustments:  {len(snapshot.adjustments)}")

    else:
        print(f"Unknown feedback subcommand: {sub}", file=sys.stderr)
        print("  Available: status, rollback, calibrate", file=sys.stderr)
        sys.exit(1)


def _cmd_setup(args: list[str]) -> None:
    if not args:
        print("Usage: uncommon-route setup <client>", file=sys.stderr)
        print("  Available clients: claude-code, codex, openai", file=sys.stderr)
        sys.exit(1)

    sub = args[0]
    handlers = {
        "claude-code": _setup_claude_code,
        "openai": _setup_openai,
        "codex": _setup_codex,
    }
    handler = handlers.get(sub)
    if handler:
        handler(args[1:])
    else:
        print(f"Unknown client: {sub}", file=sys.stderr)
        print(f"  Available: {', '.join(handlers)}", file=sys.stderr)
        sys.exit(1)


def _detect_rc_file() -> str:
    shell = os.environ.get("SHELL", "")
    if "zsh" in shell:
        return "~/.zshrc"
    if "fish" in shell:
        return "~/.config/fish/config.fish"
    return "~/.bashrc"


def _setup_env_display() -> tuple[str, str, str]:
    """Return (upstream_val, key_display, status_msg) from current env."""
    upstream = os.environ.get("UNCOMMON_ROUTE_UPSTREAM", "")
    api_key = os.environ.get("UNCOMMON_ROUTE_API_KEY", "")
    upstream_val = upstream or "https://api.commonstack.ai/v1"
    key_display = f"{api_key[:12]}..." if len(api_key) > 12 else (api_key or "csk-your-key-here")
    if upstream and api_key:
        status = "upstream and key already configured — ready to go."
    elif upstream:
        status = "upstream set, but UNCOMMON_ROUTE_API_KEY is missing."
    else:
        status = "UNCOMMON_ROUTE_UPSTREAM and UNCOMMON_ROUTE_API_KEY need to be set."
    return upstream_val, key_display, status


def _setup_claude_code(args: list[str]) -> None:
    from uncommon_route.proxy import DEFAULT_PORT

    flags, _ = _parse_flags(args, {"port": True})
    port = int(flags.get("port", DEFAULT_PORT))
    rc = _detect_rc_file()
    upstream_val, key_display, status = _setup_env_display()

    print(f"""
  UncommonRoute + Claude Code
  {'=' * 40}

  Add to {rc}:

    # --- UncommonRoute proxy ---
    export UNCOMMON_ROUTE_UPSTREAM="{upstream_val}"
    export UNCOMMON_ROUTE_API_KEY="{key_display}"

    # --- Claude Code → UncommonRoute ---
    export ANTHROPIC_BASE_URL="http://localhost:{port}"
    export ANTHROPIC_API_KEY="not-needed"

  Then:

    1. source {rc}
    2. uncommon-route serve          (terminal 1)
    3. claude                        (terminal 2)

  How it works:

    Claude Code  --POST /v1/messages-->  UncommonRoute  --best model-->  Upstream API
                                         (smart routing)

    - Auth is managed by the proxy. Claude Code does not need a real API key.
    - All requests are smart-routed to the best model automatically.
    - Responses are converted back to Anthropic format transparently.
""")
    print(f"  Status: {status}")


def _setup_codex(args: list[str]) -> None:
    from uncommon_route.proxy import DEFAULT_PORT

    flags, _ = _parse_flags(args, {"port": True})
    port = int(flags.get("port", DEFAULT_PORT))
    rc = _detect_rc_file()
    upstream_val, key_display, status = _setup_env_display()

    print(f"""
  UncommonRoute + OpenAI Codex
  {'=' * 40}

  Add to {rc}:

    # --- UncommonRoute proxy ---
    export UNCOMMON_ROUTE_UPSTREAM="{upstream_val}"
    export UNCOMMON_ROUTE_API_KEY="{key_display}"

    # --- Codex → UncommonRoute ---
    export OPENAI_BASE_URL="http://localhost:{port}/v1"
    export OPENAI_API_KEY="not-needed"

  Then:

    1. source {rc}
    2. uncommon-route serve          (terminal 1)
    3. codex                         (terminal 2)

  How it works:

    Codex  --POST /v1/chat/completions-->  UncommonRoute  --best model-->  Upstream API
                                           (smart routing)

    - Set model to "uncommon-route/auto" for smart routing.
    - Auth is managed by the proxy. Codex does not need a real API key.
    - Non-virtual model names are passed through unchanged.
""")
    print(f"  Status: {status}")


def _setup_openai(args: list[str]) -> None:
    from uncommon_route.proxy import DEFAULT_PORT

    flags, _ = _parse_flags(args, {"port": True})
    port = int(flags.get("port", DEFAULT_PORT))
    rc = _detect_rc_file()
    upstream_val, key_display, status = _setup_env_display()

    print(f"""
  UncommonRoute + OpenAI SDK / Cursor
  {'=' * 40}

  Add to {rc}:

    # --- UncommonRoute proxy ---
    export UNCOMMON_ROUTE_UPSTREAM="{upstream_val}"
    export UNCOMMON_ROUTE_API_KEY="{key_display}"

  Then:

    1. source {rc}
    2. uncommon-route serve

  Python usage:

    from openai import OpenAI
    client = OpenAI(
        base_url="http://localhost:{port}/v1",
        api_key="not-needed",
    )
    client.chat.completions.create(
        model="uncommon-route/auto",   # smart routing
        messages=[{{"role": "user", "content": "hello"}}],
    )

  Cursor: set "OpenAI Base URL" to http://localhost:{port}/v1 in settings.
""")
    print(f"  Status: {status}")



def _cmd_explain(args: list[str]) -> None:
    """Show v2 signal breakdown for a prompt."""
    prompt = " ".join(args) if args else ""
    if not prompt:
        print("Usage: uncommon-route explain <prompt>")
        print('Example: uncommon-route explain "Design a distributed rate limiter"')
        return
    from uncommon_route.api_v2 import route_preview
    result = route_preview(prompt=prompt, risk_tolerance=0.5)
    print(f"\n  Tier: {result['tier_name']} (id={result['tier']})")
    print(f"  Confidence: {result['confidence']:.1%}")
    print(f"  Method: {result['method']}")
    print(f"  Cost: ${result['cost_estimate']:.4f} (vs ${result['cost_baseline']:.4f} baseline)")
    print(f"\n  Signals:")
    for s in result["signals"]:
        shadow = " [shadow]" if s.get("shadow") else ""
        tier = s["tier"] if s["tier"] is not None else "abstain"
        print(f"    {s['name']:12s}  tier={tier}  confidence={s['confidence']:.2f}{shadow}")
    print()


def main() -> None:
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        _print_help()
        sys.exit(0)

    if args[0] in ("--version", "-v"):
        print(VERSION)
        sys.exit(0)

    cmd = args[0]
    sub_args = args[1:]

    commands = {
        "route": _cmd_route,
        "serve": _cmd_serve,
        "setup": _cmd_setup,
        "stop": _cmd_stop,
        "doctor": _cmd_doctor,
        "logs": _cmd_logs,
        "debug": _cmd_debug,
        "openclaw": _cmd_openclaw,
        "spend": _cmd_spend,
        "provider": _cmd_provider,
        "config": _cmd_config,
        "stats": _cmd_stats,
        "feedback": _cmd_feedback,
        "explain": _cmd_explain,
    }

    handler = commands.get(cmd)
    if handler:
        handler(sub_args)
    else:
        _cmd_route(args)


if __name__ == "__main__":
    main()
