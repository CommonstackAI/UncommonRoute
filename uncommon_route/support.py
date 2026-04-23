"""Support diagnostics export for UncommonRoute."""

from __future__ import annotations

import json
import platform
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

from uncommon_route.connections_store import ConnectionsStore, mask_api_key, resolve_primary_connection
from uncommon_route.model_experience import ModelExperienceStore
from uncommon_route.paths import data_dir
from uncommon_route.providers import load_providers
from uncommon_route.routing_config_store import RoutingConfigStore
from uncommon_route.spend_control import SpendControl
from uncommon_route.stats import RouteStats
from uncommon_route.traces import TraceStore


def _now_stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.localtime())


def _support_dir() -> Path:
    root = data_dir() / "support"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def _output_path(output_path: str | None) -> Path:
    if output_path:
        path = Path(output_path).expanduser()
        if path.suffix.lower() != ".zip":
            path = path.with_suffix(".zip")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path
    return _support_dir() / f"uncommon-route-support-{_now_stamp()}.zip"


def _package_version() -> str:
    try:
        from importlib.metadata import version

        return version("uncommon-route")
    except Exception:
        return "0.7.9"


def _feedback_buffer_summary(root: Path) -> dict[str, Any]:
    path = root / "feedback_buffer.json"
    if not path.exists():
        return {"pending_count": 0, "request_ids": []}
    try:
        raw = json.loads(path.read_text())
    except Exception as exc:
        return {"pending_count": 0, "request_ids": [], "load_error": str(exc)}
    if not isinstance(raw, dict):
        return {"pending_count": 0, "request_ids": [], "load_error": "unexpected format"}
    request_ids = sorted(str(key) for key in raw.keys())
    return {
        "pending_count": len(request_ids),
        "request_ids": request_ids[:100],
    }


def _tail_text(path: Path, limit: int = 200) -> str:
    if not path.exists():
        return ""
    try:
        lines = path.read_text().splitlines()
    except Exception as exc:
        return f"[support] failed to read {path.name}: {exc}\n"
    return "\n".join(lines[-max(1, limit) :]) + ("\n" if lines else "")


def _providers_payload() -> dict[str, Any]:
    providers = load_providers()
    return {
        "count": len(providers.providers),
        "providers": [
            {
                "name": entry.name,
                "base_url": entry.base_url,
                "api_key_preview": mask_api_key(entry.api_key),
                "models": list(entry.models),
                "plan": entry.plan,
            }
            for entry in providers.providers.values()
        ],
    }


def _connections_payload() -> dict[str, Any]:
    store = ConnectionsStore()
    effective = resolve_primary_connection(store=store)
    return {
        "stored": store.export(),
        "effective": {
            "upstream": effective.upstream,
            "has_api_key": bool(effective.api_key),
            "api_key_preview": mask_api_key(effective.api_key),
            "source": effective.source,
            "upstream_source": effective.upstream_source,
            "api_key_source": effective.api_key_source,
            "editable": effective.editable,
        },
    }


def _spending_payload() -> dict[str, Any]:
    status = SpendControl().status()
    return {
        "limits": {
            key: value
            for key, value in vars(status.limits).items()
            if value is not None
        },
        "spent": status.spent,
        "remaining": {
            key: value
            for key, value in status.remaining.items()
            if value is not None
        },
        "calls": status.calls,
    }


def _stats_summary_payload(stats: RouteStats) -> dict[str, Any]:
    summary = stats.summary()
    return {
        "total_requests": summary.total_requests,
        "time_range_s": round(summary.time_range_s, 3),
        "avg_confidence": round(summary.avg_confidence, 6),
        "avg_savings": round(summary.avg_savings, 6),
        "avg_latency_ms": round(summary.avg_latency_us / 1000.0, 6),
        "total_estimated_cost": round(summary.total_estimated_cost, 8),
        "total_actual_cost": round(summary.total_actual_cost, 8),
        "total_savings_absolute": round(summary.total_savings_absolute, 8),
        "total_savings_ratio": round(summary.total_savings_ratio, 6),
        "by_mode": summary.by_mode,
        "by_method": summary.by_method,
        "by_decision_tier": summary.by_decision_tier,
        "by_transport": {
            key: {"count": value.count, "total_cost": round(value.total_cost, 8)}
            for key, value in summary.by_transport.items()
        },
        "by_cache_mode": {
            key: {"count": value.count, "total_cost": round(value.total_cost, 8)}
            for key, value in summary.by_cache_mode.items()
        },
        "by_cache_family": {
            key: {"count": value.count, "total_cost": round(value.total_cost, 8)}
            for key, value in summary.by_cache_family.items()
        },
    }


def _trace_store() -> TraceStore:
    return TraceStore()


def _trace_summary_payload(traces: TraceStore) -> dict[str, Any]:
    return traces.summary()


def _legacy_recent_traces(limit: int) -> list[dict[str, Any]]:
    return RouteStats().export_records(limit=limit)


def _recent_traces_payload(limit: int) -> list[dict[str, Any]]:
    traces = _trace_store()
    recent = traces.export_records(limit=limit)
    if recent or traces.count > 0:
        return recent
    return _legacy_recent_traces(limit)


def _recent_errors_payload(limit: int) -> list[dict[str, Any]]:
    traces = _trace_store()
    if traces.count > 0:
        return traces.recent(limit=limit, errors_only=True)
    return [
        trace for trace in _legacy_recent_traces(limit)
        if trace.get("error_code") or int(trace.get("status_code", 0) or 0) >= 400
    ]


def _telemetry_status_payload() -> dict[str, Any]:
    try:
        import uncommon_route.telemetry as telemetry
        return telemetry.status()
    except Exception as exc:
        return {
            "enabled": False,
            "available": False,
            "load_error": str(exc),
        }


def _bundle_manifest(path: Path, *, recent_traces: list[dict[str, Any]]) -> dict[str, Any]:
    error_count = sum(1 for trace in recent_traces if trace.get("error_code"))
    return {
        "bundle_version": 1,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
        "package_version": _package_version(),
        "python_version": sys.version,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python_implementation": platform.python_implementation(),
        },
        "data_dir": str(data_dir()),
        "bundle_path": str(path),
        "recent_trace_count": len(recent_traces),
        "recent_error_count": error_count,
    }


def build_support_bundle(
    *,
    limit: int = 50,
    output_path: str | None = None,
) -> Path:
    """Create a diagnostics bundle from local state files."""

    safe_limit = max(1, min(int(limit), 500))
    root = data_dir()
    traces = _trace_store()
    stats = RouteStats()
    recent_traces = _recent_traces_payload(safe_limit)
    recent_errors = _recent_errors_payload(safe_limit)
    bundle_path = _output_path(output_path)
    bundle_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    telemetry_status = _telemetry_status_payload()

    diagnostics_overview = {
        "recent_trace_count": len(recent_traces),
        "recent_error_count": len(recent_errors),
        "feedback_pending": _feedback_buffer_summary(root)["pending_count"],
        "telemetry_enabled": bool(telemetry_status.get("enabled", False)),
    }
    model_experience = ModelExperienceStore().summary()
    connections = _connections_payload()
    routing_config = RoutingConfigStore().export()
    providers = _providers_payload()
    spending = _spending_payload()
    feedback_summary = _feedback_buffer_summary(root)
    stats_summary = _stats_summary_payload(stats)
    trace_summary = _trace_summary_payload(traces)
    logs_tail = _tail_text(root / "serve.log")

    manifest = _bundle_manifest(bundle_path, recent_traces=recent_traces)

    with zipfile.ZipFile(bundle_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        _write_json(zf, "manifest.json", manifest)
        _write_json(zf, "diagnostics/overview.json", diagnostics_overview)
        _write_json(zf, "diagnostics/stats_summary.json", stats_summary)
        _write_json(zf, "diagnostics/trace_summary.json", trace_summary)
        _write_json(zf, "diagnostics/recent_traces.json", recent_traces)
        _write_json(zf, "diagnostics/recent_errors.json", recent_errors)
        _write_json(zf, "state/connections.json", connections)
        _write_json(zf, "state/providers.json", providers)
        _write_json(zf, "state/routing_config.json", routing_config)
        _write_json(zf, "state/spending.json", spending)
        _write_json(zf, "state/model_experience.json", model_experience)
        _write_json(zf, "state/telemetry.json", telemetry_status)
        _write_json(zf, "state/feedback_buffer.json", feedback_summary)
        if logs_tail:
            zf.writestr("logs/serve.log.tail.txt", logs_tail)

    return bundle_path


def find_trace(request_id: str) -> dict[str, Any] | None:
    target = str(request_id or "").strip()
    if not target:
        return None
    record = _trace_store().find(target)
    if record is not None:
        return record
    stats = RouteStats()
    for legacy_record in stats.export_records():
        if legacy_record.get("request_id") == target:
            return legacy_record
    return None


def _write_json(bundle: zipfile.ZipFile, name: str, payload: dict[str, Any] | list[dict[str, Any]]) -> None:
    bundle.writestr(name, json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
