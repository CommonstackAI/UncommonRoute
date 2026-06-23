"""Independent request trace storage for diagnostics and support bundles."""

from __future__ import annotations

import hashlib
import json
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from uncommon_route.paths import data_dir


def _normalize_tier_label(tier: str) -> str:
    normalized = str(tier).strip().upper()
    return "COMPLEX" if normalized == "REASONING" else normalized


def _normalize_served_quality(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"economy", "balanced", "premium"} else ""


def prompt_hash(text: str) -> str:
    compact = str(text or "").strip()
    if not compact:
        return ""
    return hashlib.sha256(compact.encode("utf-8")).hexdigest()[:16]


@dataclass
class RequestTrace:
    timestamp: float
    request_id: str
    model: str
    status_code: int
    requested_model: str = ""
    mode: str = ""
    tier: str = ""
    decision_tier: str = ""
    served_quality: str = ""
    served_quality_target: str = ""
    served_quality_floor: str = ""
    capability_lane: str = ""
    method: str = ""
    api_format: str = "openai"
    endpoint: str = "chat_completions"
    is_virtual: bool = False
    session_id: str | None = None
    streaming: bool = False
    prompt_preview: str = ""
    prompt_hash: str = ""
    step_type: str = "general"
    route_reasoning: str = ""
    confidence: float = 0.0
    raw_confidence: float = 0.0
    confidence_source: str = ""
    calibration_version: str = ""
    calibration_sample_count: int = 0
    calibration_temperature: float = 1.0
    calibration_applied_tags: list[str] | tuple[str, ...] | None = None
    complexity: float = 0.33
    estimated_cost: float = 0.0
    baseline_cost: float = 0.0
    actual_cost: float | None = None
    savings: float = 0.0
    latency_us: float = 0.0
    route_latency_ms: float = 0.0
    upstream_elapsed_ms: float = 0.0
    first_token_ms: float = 0.0
    usage_input_tokens: int = 0
    usage_output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    cache_hit_ratio: float = 0.0
    transport: str = "openai-chat"
    requested_transport: str = ""
    transport_reason: str = ""
    transport_preference_source: str = ""
    cache_mode: str = "none"
    cache_family: str = "generic"
    cache_breakpoints: int = 0
    input_tokens_before: int = 0
    input_tokens_after: int = 0
    artifacts_created: int = 0
    compacted_messages: int = 0
    semantic_summaries: int = 0
    semantic_calls: int = 0
    semantic_failures: int = 0
    semantic_quality_fallbacks: int = 0
    checkpoint_created: bool = False
    rehydrated_artifacts: int = 0
    sidechannel_estimated_cost: float = 0.0
    sidechannel_actual_cost: float | None = None
    fallback_reason: str = ""
    answer_depth: str = "standard"
    constraint_tags: list[str] | None = None
    hint_tags: list[str] | None = None
    feature_tags: list[str] | None = None
    routing_features_payload: dict[str, Any] | None = None
    fallback_chain_payload: list[dict[str, Any]] | None = None
    candidate_scores_payload: list[dict[str, Any]] | None = None
    selection_weights_payload: dict[str, Any] | None = None
    attempts_payload: list[dict[str, Any]] | None = None
    error_code: str = ""
    error_stage: str = ""
    error_message: str = ""
    feedback_signal: str = ""
    feedback_ok: bool = False
    feedback_action: str = ""
    feedback_from_tier: str = ""
    feedback_to_tier: str = ""
    feedback_reason: str = ""
    feedback_submitted_at: float = 0.0

    # --- Track A: session_id v2 inputs ---
    messages_count: int = 0
    msg_hashes: list[str] | None = None
    first_user_hash_v2: str = ""
    system_hash: str = ""
    metadata_user_id: str = ""
    previous_response_id: str = ""
    user_agent: str = ""
    session_id_v2: str = ""

    # --- Track B: cold (content) fields ---
    request_messages: list[dict[str, Any]] | None = None
    request_system: str = ""
    request_tools_count: int = 0
    response_text: str = ""
    response_tool_calls: list[dict[str, Any]] | None = None
    response_finish_reason: str = ""
    content_truncated: bool = False


COLD_FIELDS: tuple[str, ...] = (
    "request_messages",
    "request_system",
    "response_text",
    "response_tool_calls",
    "response_finish_reason",
)


def _empty_for(field_name: str) -> Any:
    if field_name in ("request_messages", "response_tool_calls"):
        return None
    return ""


def _row_to_trace(row: dict[str, Any]) -> "RequestTrace":
    """Tolerate missing fields; rely on dataclass defaults for absent keys."""
    field_names = {f.name for f in RequestTrace.__dataclass_fields__.values()}
    kwargs = {k: v for k, v in row.items() if k in field_names}
    return RequestTrace(**kwargs)


class TraceStorage(ABC):
    @abstractmethod
    def append(self, record: dict[str, Any]) -> None: ...

    @abstractmethod
    def load_recent_days(
        self, days: int, *, now: float
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def load_for_request(
        self, request_id: str, timestamp: float
    ) -> dict[str, Any] | None: ...

    @abstractmethod
    def purge(self) -> None: ...


def _date_str(ts: float) -> str:
    """UTC date for grouping into daily files."""
    import datetime as _dt
    return _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).strftime("%Y-%m-%d")


class FileTraceStorage(TraceStorage):
    """Append-only JSONL store rotated daily."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base_dir = base_dir or (data_dir() / "traces")

    def append(self, record: dict[str, Any]) -> None:
        try:
            self._base_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            day = _date_str(float(record.get("timestamp", time.time())))
            path = self._base_dir / f"{day}.jsonl"
            line = json.dumps(record, default=str, ensure_ascii=False)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            try:
                path.chmod(0o600)
            except Exception:
                pass
        except Exception:
            pass

    def load_recent_days(
        self, days: int, *, now: float
    ) -> list[dict[str, Any]]:
        if not self._base_dir.exists():
            return []
        import datetime as _dt
        end = _dt.datetime.fromtimestamp(now, tz=_dt.timezone.utc).date()
        accepted = {
            (end - _dt.timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range(max(1, days))
        }
        files = sorted(
            f for f in self._base_dir.glob("*.jsonl") if f.stem in accepted
        )
        out: list[dict[str, Any]] = []
        for f in files:
            try:
                for line in f.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        continue
            except Exception:
                continue
        return out

    def load_for_request(
        self, request_id: str, timestamp: float
    ) -> dict[str, Any] | None:
        if not self._base_dir.exists():
            return None
        import datetime as _dt
        center = _dt.datetime.fromtimestamp(timestamp, tz=_dt.timezone.utc).date()
        candidates = [
            center,
            center - _dt.timedelta(days=1),
            center + _dt.timedelta(days=1),
        ]
        for day in candidates:
            f = self._base_dir / f"{day.strftime('%Y-%m-%d')}.jsonl"
            if not f.exists():
                continue
            try:
                for line in f.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    if row.get("request_id") == request_id:
                        return row
            except Exception:
                continue
        return None

    def purge(self) -> None:
        if not self._base_dir.exists():
            return
        for f in self._base_dir.glob("*.jsonl"):
            try:
                f.unlink()
            except Exception:
                pass


class InMemoryTraceStorage(TraceStorage):
    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []

    def append(self, record: dict[str, Any]) -> None:
        self._rows.append(dict(record))

    def load_recent_days(
        self, days: int, *, now: float
    ) -> list[dict[str, Any]]:
        return list(self._rows)

    def load_for_request(
        self, request_id: str, timestamp: float
    ) -> dict[str, Any] | None:
        for r in self._rows:
            if r.get("request_id") == request_id:
                return dict(r)
        return None

    def purge(self) -> None:
        self._rows.clear()


def migrate_legacy_json(legacy_path: Path, target_dir: Path) -> bool:
    """One-time migration of the old single-file traces.json into daily JSONL.

    Returns True if migration ran, False if it was skipped (target dir already
    populated, no legacy file, or legacy file is not parseable).
    """
    if target_dir.exists() and any(target_dir.glob("*.jsonl")):
        return False
    if not legacy_path.exists():
        return False
    try:
        records = json.loads(legacy_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(records, list):
        return False

    target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    for rec in records:
        if not isinstance(rec, dict):
            continue
        ts = float(rec.get("timestamp", 0.0))
        day = _date_str(ts)
        path = target_dir / f"{day}.jsonl"
        try:
            line = json.dumps(rec, default=str, ensure_ascii=False)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            continue

    try:
        legacy_path.rename(legacy_path.with_suffix(".json.bak"))
    except Exception:
        pass
    return True


class TraceStore:
    def __init__(
        self,
        storage: TraceStorage | None = None,
        now_fn: Any = None,
        *,
        hot_days: int | None = None,
    ) -> None:
        if storage is None:
            target_dir = data_dir() / "traces"
            legacy = data_dir() / "traces.json"
            try:
                migrate_legacy_json(legacy, target_dir)
            except Exception:
                pass
            storage = FileTraceStorage(base_dir=target_dir)
        self._storage = storage
        self._now = now_fn or time.time
        if hot_days is None:
            try:
                hot_days = int(os.environ.get("UNCOMMON_ROUTE_TRACE_HOT_DAYS", "2"))
            except Exception:
                hot_days = 2
        self._hot_days = max(1, hot_days)
        self._records: list[RequestTrace] = []
        self._load()

    @property
    def count(self) -> int:
        return len(self._records)

    def record(self, trace: RequestTrace) -> None:
        trace.tier = _normalize_tier_label(trace.tier)
        trace.decision_tier = _normalize_tier_label(trace.decision_tier) if trace.decision_tier else ""
        trace.served_quality = _normalize_served_quality(trace.served_quality)
        trace.served_quality_target = _normalize_served_quality(trace.served_quality_target)
        trace.served_quality_floor = _normalize_served_quality(trace.served_quality_floor)
        trace.capability_lane = str(trace.capability_lane or "").strip().lower()
        trace.feedback_from_tier = _normalize_tier_label(trace.feedback_from_tier) if trace.feedback_from_tier else ""
        trace.feedback_to_tier = _normalize_tier_label(trace.feedback_to_tier) if trace.feedback_to_tier else ""

        # Persist full row (incl. cold fields) to disk.
        payload = _trace_payload(trace)
        try:
            self._storage.append({"type": "trace", **payload})
        except Exception:
            pass

        # Hot copy: drop cold fields before keeping in memory.
        for f in COLD_FIELDS:
            setattr(trace, f, _empty_for(f))
        self._records.append(trace)
        self._cleanup()

    def record_feedback(
        self,
        request_id: str,
        *,
        signal: str,
        ok: bool,
        action: str,
        from_tier: str = "",
        to_tier: str = "",
        reason: str = "",
    ) -> bool:
        now = self._now()
        # Apply to in-memory state.
        applied = False
        for record in reversed(self._records):
            if record.request_id != request_id:
                continue
            record.feedback_signal = signal
            record.feedback_ok = ok
            record.feedback_action = action
            record.feedback_from_tier = _normalize_tier_label(from_tier) if from_tier else ""
            record.feedback_to_tier = _normalize_tier_label(to_tier) if to_tier else ""
            record.feedback_reason = reason
            record.feedback_submitted_at = now
            applied = True
            break
        # Persist as event so future loads reconstruct state.
        try:
            self._storage.append({
                "type": "feedback",
                "request_id": request_id,
                "timestamp": now,
                "feedback_signal": signal,
                "feedback_ok": bool(ok),
                "feedback_action": action,
                "feedback_from_tier": _normalize_tier_label(from_tier) if from_tier else "",
                "feedback_to_tier": _normalize_tier_label(to_tier) if to_tier else "",
                "feedback_reason": reason,
                "feedback_submitted_at": now,
            })
        except Exception:
            pass
        return applied

    def reset(self) -> None:
        self._records.clear()
        try:
            self._storage.purge()
        except Exception:
            pass

    def history(self, limit: int | None = None) -> list[RequestTrace]:
        records = list(reversed(self._records))
        return records[:limit] if limit else records

    def export_records(self, limit: int | None = None) -> list[dict[str, Any]]:
        records = self.history(limit=limit)
        return [_trace_payload(record) for record in records]

    def recent(self, limit: int = 50, *, errors_only: bool = False) -> list[dict[str, Any]]:
        records = self.history()
        if errors_only:
            records = [record for record in records if record.error_code or record.status_code >= 400]
        return [_trace_payload(record) for record in records[:limit]]

    def find(self, request_id: str) -> dict[str, Any] | None:
        target = str(request_id or "").strip()
        if not target:
            return None
        for record in reversed(self._records):
            if record.request_id == target:
                return _trace_payload(record)
        return None

    def latest_for_session(
        self,
        session_id: str,
        *,
        step_types: tuple[str, ...] | None = None,
    ) -> RequestTrace | None:
        target = str(session_id or "").strip()
        if not target:
            return None
        allowed_steps = {step for step in (step_types or ()) if step}
        for record in reversed(self._records):
            if record.session_id != target:
                continue
            if allowed_steps and record.step_type not in allowed_steps:
                continue
            return record
        return None

    def summary(self) -> dict[str, Any]:
        # PRESERVE the existing summary algorithm — do not replace with Counter.
        by_endpoint: dict[str, int] = {}
        by_mode: dict[str, int] = {}
        by_method: dict[str, int] = {}
        by_status: dict[str, int] = {}
        by_error_code: dict[str, int] = {}
        by_served_quality: dict[str, int] = {}
        by_capability_lane: dict[str, int] = {}
        error_count = 0
        virtual_count = 0
        passthrough_count = 0
        for record in self._records:
            by_endpoint[record.endpoint] = by_endpoint.get(record.endpoint, 0) + 1
            by_mode[record.mode] = by_mode.get(record.mode, 0) + 1
            by_method[record.method] = by_method.get(record.method, 0) + 1
            if record.served_quality:
                by_served_quality[record.served_quality] = by_served_quality.get(record.served_quality, 0) + 1
            if record.capability_lane:
                by_capability_lane[record.capability_lane] = by_capability_lane.get(record.capability_lane, 0) + 1
            status_key = str(record.status_code or 0)
            by_status[status_key] = by_status.get(status_key, 0) + 1
            if record.error_code:
                by_error_code[record.error_code] = by_error_code.get(record.error_code, 0) + 1
                error_count += 1
            elif record.status_code >= 400:
                error_count += 1
            if record.is_virtual:
                virtual_count += 1
            else:
                passthrough_count += 1
        return {
            "total_requests": len(self._records),
            "error_count": error_count,
            "virtual_requests": virtual_count,
            "passthrough_requests": passthrough_count,
            "by_endpoint": by_endpoint,
            "by_mode": by_mode,
            "by_method": by_method,
            "by_status": by_status,
            "by_error_code": by_error_code,
            "by_served_quality": by_served_quality,
            "by_capability_lane": by_capability_lane,
        }

    def load_content(self, request_id: str) -> dict[str, Any] | None:
        for r in self._records:
            if r.request_id == request_id:
                cold = self._storage.load_for_request(request_id, r.timestamp)
                if cold is None:
                    return None
                return {f: cold.get(f) for f in COLD_FIELDS}
        return None

    def _cleanup(self) -> None:
        cutoff_ts = self._now() - (self._hot_days * 86400.0)
        self._records = [r for r in self._records if r.timestamp >= cutoff_ts]

    def _load(self) -> None:
        rows = self._storage.load_recent_days(self._hot_days, now=self._now())
        traces: dict[str, RequestTrace] = {}
        order: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            rtype = row.get("type", "trace")
            if rtype == "trace":
                rid = str(row.get("request_id", ""))
                if not rid:
                    continue
                # Drop cold fields when constructing the hot record.
                hot_row = {k: v for k, v in row.items() if k != "type" and k not in COLD_FIELDS}
                # Backfill cold field defaults for the dataclass.
                for f in COLD_FIELDS:
                    hot_row.setdefault(f, _empty_for(f))
                try:
                    trace = _row_to_trace(hot_row)
                except Exception:
                    continue
                if rid not in traces:
                    order.append(rid)
                traces[rid] = trace
            elif rtype == "feedback":
                rid = str(row.get("request_id", ""))
                t = traces.get(rid)
                if t is None:
                    continue
                t.feedback_signal = str(row.get("feedback_signal", ""))
                t.feedback_ok = bool(row.get("feedback_ok", False))
                t.feedback_action = str(row.get("feedback_action", ""))
                t.feedback_from_tier = str(row.get("feedback_from_tier", ""))
                t.feedback_to_tier = str(row.get("feedback_to_tier", ""))
                t.feedback_reason = str(row.get("feedback_reason", ""))
                t.feedback_submitted_at = float(row.get("feedback_submitted_at", 0.0) or 0.0)
        self._records = [traces[rid] for rid in order if rid in traces]


def _trace_payload(trace: RequestTrace) -> dict[str, Any]:
    return {
        "timestamp": trace.timestamp,
        "request_id": trace.request_id,
        "requested_model": trace.requested_model,
        "model": trace.model,
        "status_code": trace.status_code,
        "mode": trace.mode,
        "tier": _normalize_tier_label(trace.tier),
        "decision_tier": _normalize_tier_label(trace.decision_tier) if trace.decision_tier else "",
        "served_quality": _normalize_served_quality(trace.served_quality),
        "served_quality_target": _normalize_served_quality(trace.served_quality_target),
        "served_quality_floor": _normalize_served_quality(trace.served_quality_floor),
        "capability_lane": str(trace.capability_lane or "").strip().lower(),
        "method": trace.method,
        "api_format": trace.api_format,
        "endpoint": trace.endpoint,
        "is_virtual": trace.is_virtual,
        "session_id": trace.session_id,
        "streaming": trace.streaming,
        "prompt_preview": trace.prompt_preview,
        "prompt_hash": trace.prompt_hash,
        "step_type": trace.step_type,
        "route_reasoning": trace.route_reasoning,
        "confidence": trace.confidence,
        "raw_confidence": trace.raw_confidence,
        "confidence_source": trace.confidence_source,
        "calibration_version": trace.calibration_version,
        "calibration_sample_count": trace.calibration_sample_count,
        "calibration_temperature": trace.calibration_temperature,
        "calibration_applied_tags": list(trace.calibration_applied_tags or []),
        "complexity": trace.complexity,
        "estimated_cost": trace.estimated_cost,
        "baseline_cost": trace.baseline_cost,
        "actual_cost": trace.actual_cost,
        "savings": trace.savings,
        "latency_us": trace.latency_us,
        "route_latency_ms": trace.route_latency_ms if trace.route_latency_ms > 0 else trace.latency_us / 1000.0,
        "upstream_elapsed_ms": trace.upstream_elapsed_ms,
        "first_token_ms": trace.first_token_ms,
        "usage_input_tokens": trace.usage_input_tokens,
        "usage_output_tokens": trace.usage_output_tokens,
        "cache_read_input_tokens": trace.cache_read_input_tokens,
        "cache_write_input_tokens": trace.cache_write_input_tokens,
        "cache_hit_ratio": trace.cache_hit_ratio,
        "transport": trace.transport,
        "requested_transport": trace.requested_transport,
        "transport_reason": trace.transport_reason,
        "transport_preference_source": trace.transport_preference_source,
        "cache_mode": trace.cache_mode,
        "cache_family": trace.cache_family,
        "cache_breakpoints": trace.cache_breakpoints,
        "input_tokens_before": trace.input_tokens_before,
        "input_tokens_after": trace.input_tokens_after,
        "artifacts_created": trace.artifacts_created,
        "compacted_messages": trace.compacted_messages,
        "semantic_summaries": trace.semantic_summaries,
        "semantic_calls": trace.semantic_calls,
        "semantic_failures": trace.semantic_failures,
        "semantic_quality_fallbacks": trace.semantic_quality_fallbacks,
        "checkpoint_created": trace.checkpoint_created,
        "rehydrated_artifacts": trace.rehydrated_artifacts,
        "sidechannel_estimated_cost": trace.sidechannel_estimated_cost,
        "sidechannel_actual_cost": trace.sidechannel_actual_cost,
        "fallback_reason": trace.fallback_reason,
        "answer_depth": trace.answer_depth,
        "constraint_tags": list(trace.constraint_tags or []),
        "hint_tags": list(trace.hint_tags or []),
        "feature_tags": list(trace.feature_tags or []),
        "routing_features_payload": dict(trace.routing_features_payload or {}),
        "fallback_chain_payload": list(trace.fallback_chain_payload or []),
        "candidate_scores_payload": list(trace.candidate_scores_payload or []),
        "selection_weights_payload": dict(trace.selection_weights_payload or {}),
        "attempts_payload": list(trace.attempts_payload or []),
        "error_code": trace.error_code,
        "error_stage": trace.error_stage,
        "error_message": trace.error_message,
        "feedback_signal": trace.feedback_signal,
        "feedback_ok": trace.feedback_ok,
        "feedback_action": trace.feedback_action,
        "feedback_from_tier": _normalize_tier_label(trace.feedback_from_tier) if trace.feedback_from_tier else "",
        "feedback_to_tier": _normalize_tier_label(trace.feedback_to_tier) if trace.feedback_to_tier else "",
        "feedback_reason": trace.feedback_reason,
        "feedback_submitted_at": trace.feedback_submitted_at,
        "messages_count": trace.messages_count,
        "msg_hashes": list(trace.msg_hashes) if trace.msg_hashes else [],
        "first_user_hash_v2": trace.first_user_hash_v2,
        "system_hash": trace.system_hash,
        "metadata_user_id": trace.metadata_user_id,
        "previous_response_id": trace.previous_response_id,
        "user_agent": trace.user_agent,
        "session_id_v2": trace.session_id_v2,
        "request_messages": list(trace.request_messages) if trace.request_messages else None,
        "request_system": trace.request_system,
        "request_tools_count": trace.request_tools_count,
        "response_text": trace.response_text,
        "response_tool_calls": list(trace.response_tool_calls) if trace.response_tool_calls else None,
        "response_finish_reason": trace.response_finish_reason,
        "content_truncated": trace.content_truncated,
    }
