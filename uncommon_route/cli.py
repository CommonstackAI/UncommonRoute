"""CLI entry point for UncommonRoute.

Subcommands:
    route    — classify a prompt and print the routing decision (with interactive feedback)
    serve    — start the OpenAI-compatible proxy server
    debug    — show per-dimension scoring breakdown
    feedback — manage online learning (status/rollback)
    openclaw — manage OpenClaw integration (install/uninstall/status)
    spend    — manage spending limits (set/clear/status/history)
    provider — API key management (BYOK)
    stats    — routing analytics (summary/history/reset)
    sessions — show active session stats

Global flags:
    --version / -v
    --help    / -h
"""

from __future__ import annotations

import json
import sys
import time

from uncommon_route.router.api import route
from uncommon_route.router.classifier import classify
from uncommon_route.router.structural import extract_structural_features, extract_unicode_block_features
from uncommon_route.router.keywords import extract_keyword_features

VERSION = "0.1.2"


def _print_help() -> None:
    print(f"""uncommon-route v{VERSION} — SOTA LLM Router

Usage:
  uncommon-route route <prompt>         Route a prompt (with interactive feedback)
  uncommon-route serve                  Start OpenAI-compatible proxy server
  uncommon-route debug <prompt>         Show per-dimension scoring breakdown
  uncommon-route feedback <sub>         Online learning (status|rollback)
  uncommon-route openclaw <sub>         OpenClaw integration (install|uninstall|status)
  uncommon-route spend <sub>            Spending limits (status|set|clear|history)
  uncommon-route provider <sub>          API key management (list|add|remove|models)
  uncommon-route stats [sub]            Routing analytics (summary|history|reset)
  uncommon-route sessions               Show active session stats
  uncommon-route --version              Show version

Route options:
  --system-prompt <text>              System prompt for context
  --max-tokens <n>                    Max output tokens (default: 4096)
  --json                              Output as JSON
  --no-feedback                       Skip interactive feedback prompt

Feedback subcommands:
  feedback status                     Show online learning status
  feedback rollback                   Discard online weights, revert to base model

Serve options:
  --port <n>                          Port to listen on (default: 8403)
  --host <addr>                       Host to bind (default: 127.0.0.1)
  --upstream <url>                    Upstream API base URL

OpenClaw subcommands:
  openclaw install [--port <n>]       Register as OpenClaw provider
  openclaw uninstall                  Remove from OpenClaw
  openclaw status                     Check registration

Provider subcommands:
  provider list                         Show configured API keys
  provider add <name> <key>             Add key (e.g. deepseek, minimax, openai)
  provider remove <name>                Remove a key
  provider models                       List user-keyed models

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

Examples:
  uncommon-route route "what is 2+2"
  uncommon-route serve --port 8403
  uncommon-route openclaw install
  uncommon-route spend set hourly 5.00
  uncommon-route spend status
  uncommon-route provider add deepseek sk-...
  uncommon-route provider add minimax eyJ... --plan coding-plan
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


_TIER_ORDER = ["SIMPLE", "MEDIUM", "COMPLEX", "REASONING"]


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
        "json": False,
        "no-feedback": False,
    })

    prompt = " ".join(rest)
    if not prompt:
        print("Error: no prompt provided", file=sys.stderr)
        sys.exit(1)

    system_prompt = str(flags["system-prompt"]) if "system-prompt" in flags else None
    max_tokens = int(flags.get("max-tokens", 4096))
    output_json = bool(flags.get("json", False))
    no_feedback = bool(flags.get("no-feedback", False))

    start = time.perf_counter_ns()
    decision = route(prompt, system_prompt=system_prompt, max_output_tokens=max_tokens)
    elapsed_us = (time.perf_counter_ns() - start) / 1000

    if output_json:
        print(json.dumps({
            "model": decision.model,
            "tier": decision.tier.value,
            "confidence": round(decision.confidence, 3),
            "cost_estimate": round(decision.cost_estimate, 6),
            "savings": round(decision.savings, 3),
            "reasoning": decision.reasoning,
            "suggested_output_budget": decision.suggested_output_budget,
            "fallback_chain": [
                {"model": fb.model, "cost": round(fb.cost_estimate, 6)}
                for fb in decision.fallback_chain
            ],
            "latency_us": round(elapsed_us, 1),
        }, indent=2))
        return

    print(f"  Model:      {decision.model}")
    print(f"  Tier:       {decision.tier.value}")
    print(f"  Confidence: {decision.confidence:.2f}")
    print(f"  Cost:       ${decision.cost_estimate:.6f}")
    print(f"  Savings:    {decision.savings:.0%}")
    print(f"  Latency:    {elapsed_us:.0f}µs")
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
    kw_dims = extract_keyword_features(prompt)

    tier_str = result.tier.value if result.tier else "AMBIGUOUS"
    print(f"  Tier:       {tier_str}")
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

    print()
    print("  Keyword Features:")
    for d in kw_dims:
        sig = f"  [{d.signal}]" if d.signal else ""
        print(f"    {d.name:<28} {d.score:>7.3f}{sig}")


def _cmd_serve(args: list[str]) -> None:
    flags, _ = _parse_flags(args, {
        "port": True,
        "host": True,
        "upstream": True,
    })

    from uncommon_route.proxy import DEFAULT_PORT, DEFAULT_UPSTREAM, serve

    port = int(flags.get("port", DEFAULT_PORT))
    host = str(flags.get("host", "127.0.0.1"))
    upstream = str(flags.get("upstream", DEFAULT_UPSTREAM))

    serve(port=port, host=host, upstream=upstream)


def _cmd_openclaw(args: list[str]) -> None:
    from uncommon_route.openclaw import cmd_openclaw
    cmd_openclaw(args)


def _cmd_spend(args: list[str]) -> None:
    from uncommon_route.spend_control import SpendControl, format_duration

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
        print(f"  Avg latency:    {s.avg_latency_us:.0f}µs")
        print(f"  Total cost:     ${s.total_actual_cost:.4f} (estimated: ${s.total_estimated_cost:.4f})")

        if s.by_tier:
            print(f"\n  By Tier:")
            for tier in ("SIMPLE", "MEDIUM", "COMPLEX", "REASONING"):
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
            print(f"\n  By Model:")
            ranked = sorted(s.by_model.items(), key=lambda x: -x[1].count)
            for model, ms in ranked[:8]:
                print(f"    {model:<40} {ms.count:>5} reqs  ${ms.total_cost:.4f}")

        if s.by_method:
            print(f"\n  By Method:")
            for method, count in sorted(s.by_method.items(), key=lambda x: -x[1]):
                pct = count / s.total_requests * 100
                print(f"    {method:<20} {count:>5} ({pct:.1f}%)")
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
            print(f"    {ts}  {r.tier:<10} {r.model:<35} {cost_str}  [{r.method}]")

    elif sub == "reset":
        rs.reset()
        print("  Stats reset")

    else:
        print(f"Unknown stats subcommand: {sub}", file=sys.stderr)
        print("  Available: summary, history, reset", file=sys.stderr)
        sys.exit(1)


def _cmd_feedback(args: list[str]) -> None:
    from uncommon_route.router.classifier import (
        _get_online_model_path,
        rollback_online_model,
    )

    if not args:
        args = ["status"]

    sub = args[0]

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
        print("  How feedback works:")
        print("    1. Run `uncommon-route route <prompt>` and rate the tier")
        print("    2. Your feedback updates a local model overlay (~/.uncommon-route/model_online.json)")
        print("    3. The base model is never modified — rollback anytime with `feedback rollback`")

    elif sub == "rollback":
        deleted = rollback_online_model()
        if deleted:
            print("  ✓ Online weights deleted — reverted to base model")
        else:
            print("  No online weights found (already using base model)")

    else:
        print(f"Unknown feedback subcommand: {sub}", file=sys.stderr)
        print("  Available: status, rollback", file=sys.stderr)
        sys.exit(1)


def _cmd_sessions(args: list[str]) -> None:
    from uncommon_route.session import SessionStore
    store = SessionStore()
    stats = store.stats()
    print(f"  Active sessions: {stats['count']}")
    if stats["sessions"]:
        for s in stats["sessions"]:
            print(f"    {s['id']}  model={s['model']}  tier={s['tier']}  requests={s['requests']}  age={s['age_s']}s")
    else:
        print("  (no active sessions — sessions are in-memory, start `serve` first)")


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
        "debug": _cmd_debug,
        "openclaw": _cmd_openclaw,
        "spend": _cmd_spend,
        "provider": _cmd_provider,
        "stats": _cmd_stats,
        "sessions": _cmd_sessions,
        "feedback": _cmd_feedback,
    }

    handler = commands.get(cmd)
    if handler:
        handler(sub_args)
    else:
        _cmd_route(args)


if __name__ == "__main__":
    main()
