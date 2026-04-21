"""Independent request trace storage for diagnostics and support bundles."""

from __future__ import annotations

import hashlib
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from uncommon_route.paths import data_dir

RETENTION_S = 14 * 86_400
MAX_TRACES = 20_000


def _normalize_tier_label(tier: str) -> str:
    normalized = str(tier).strip().upper()
    return "COMPLEX" if normalized == "REASONING" else normalized


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
    usage_input_tokens: int = 0
    usage_output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    cache_hit_ratio: float = 0.0
    transport: str = "openai-chat"
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


class TraceStorage(ABC):
    @abstractmethod
    def load(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def save(self, records: list[dict[str, Any]]) -> None: ...


class FileTraceStorage(TraceStorage):
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (data_dir() / "traces.json")

    def load(self) -> list[dict[str, Any]]:
        try:
            if self._path.exists():
                data = json.loads(self._path.read_text())
                if isinstance(data, list):
                    return data
        except Exception:
            pass
        return []

    def save(self, records: list[dict[str, Any]]) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._path.write_text(json.dumps(records, default=str))
            self._path.chmod(0o600)
        except Exception:
            pass


class InMemoryTraceStorage(TraceStorage):
    def __init__(self) -> None:
        self._data: list[dict[str, Any]] = []

    def load(self) -> list[dict[str, Any]]:
        return list(self._data)

    def save(self, records: list[dict[str, Any]]) -> None:
        self._data = list(records)


class TraceStore:
    def __init__(
        self,
        storage: TraceStorage | None = None,
        now_fn: Any = None,
    ) -> None:
        self._storage = storage or FileTraceStorage()
        self._now = now_fn or time.time
        self._records: list[RequestTrace] = []
        self._load()

    @property
    def count(self) -> int:
        return len(self._records)

    def record(self, trace: RequestTrace) -> None:
        trace.tier = _normalize_tier_label(trace.tier)
        trace.decision_tier = _normalize_tier_label(trace.decision_tier) if trace.decision_tier else ""
        trace.feedback_from_tier = _normalize_tier_label(trace.feedback_from_tier) if trace.feedback_from_tier else ""
        trace.feedback_to_tier = _normalize_tier_label(trace.feedback_to_tier) if trace.feedback_to_tier else ""
        self._records.append(trace)
        self._cleanup()
        self._save()

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
        for record in reversed(self._records):
            if record.request_id != request_id:
                continue
            record.feedback_signal = signal
            record.feedback_ok = ok
            record.feedback_action = action
            record.feedback_from_tier = _normalize_tier_label(from_tier) if from_tier else ""
            record.feedback_to_tier = _normalize_tier_label(to_tier) if to_tier else ""
            record.feedback_reason = reason
            record.feedback_submitted_at = self._now()
            self._save()
            return True
        return False

    def reset(self) -> None:
        self._records = []
        self._save()

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

    def summary(self) -> dict[str, Any]:
        by_endpoint: dict[str, int] = {}
        by_mode: dict[str, int] = {}
        by_method: dict[str, int] = {}
        by_status: dict[str, int] = {}
        by_error_code: dict[str, int] = {}
        error_count = 0
        virtual_count = 0
        passthrough_count = 0
        for record in self._records:
            by_endpoint[record.endpoint] = by_endpoint.get(record.endpoint, 0) + 1
            by_mode[record.mode] = by_mode.get(record.mode, 0) + 1
            by_method[record.method] = by_method.get(record.method, 0) + 1
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
        }

    def _cleanup(self) -> None:
        cutoff = self._now() - RETENTION_S
        self._records = [record for record in self._records if record.timestamp >= cutoff]
        if len(self._records) > MAX_TRACES:
            self._records = self._records[-MAX_TRACES:]

    def _save(self) -> None:
        self._storage.save([_trace_payload(record) for record in self._records])

    def _load(self) -> None:
        for payload in self._storage.load():
            if not isinstance(payload, dict) or "timestamp" not in payload or "request_id" not in payload:
                continue
            self._records.append(RequestTrace(
                timestamp=float(payload.get("timestamp", 0.0) or 0.0),
                request_id=str(payload.get("request_id", "")),
                requested_model=str(payload.get("requested_model", "")),
                model=str(payload.get("model", "")),
                status_code=int(payload.get("status_code", 0) or 0),
                mode=str(payload.get("mode", "")),
                tier=_normalize_tier_label(str(payload.get("tier", ""))),
                decision_tier=_normalize_tier_label(str(payload.get("decision_tier", ""))) if payload.get("decision_tier", "") else "",
                method=str(payload.get("method", "")),
                api_format=str(payload.get("api_format", "openai")),
                endpoint=str(payload.get("endpoint", "chat_completions")),
                is_virtual=bool(payload.get("is_virtual", False)),
                session_id=payload.get("session_id"),
                streaming=bool(payload.get("streaming", False)),
                prompt_preview=str(payload.get("prompt_preview", "")),
                prompt_hash=str(payload.get("prompt_hash", "")),
                step_type=str(payload.get("step_type", "general")),
                route_reasoning=str(payload.get("route_reasoning", "")),
                confidence=float(payload.get("confidence", 0.0) or 0.0),
                raw_confidence=float(payload.get("raw_confidence", 0.0) or 0.0),
                confidence_source=str(payload.get("confidence_source", "")),
                calibration_version=str(payload.get("calibration_version", "")),
                calibration_sample_count=int(payload.get("calibration_sample_count", 0) or 0),
                calibration_temperature=float(payload.get("calibration_temperature", 1.0) or 1.0),
                calibration_applied_tags=list(payload.get("calibration_applied_tags", []) or []),
                complexity=float(payload.get("complexity", 0.33) or 0.33),
                estimated_cost=float(payload.get("estimated_cost", 0.0) or 0.0),
                baseline_cost=float(payload.get("baseline_cost", 0.0) or 0.0),
                actual_cost=payload.get("actual_cost"),
                savings=float(payload.get("savings", 0.0) or 0.0),
                latency_us=float(payload.get("latency_us", 0.0) or 0.0),
                usage_input_tokens=int(payload.get("usage_input_tokens", 0) or 0),
                usage_output_tokens=int(payload.get("usage_output_tokens", 0) or 0),
                cache_read_input_tokens=int(payload.get("cache_read_input_tokens", 0) or 0),
                cache_write_input_tokens=int(payload.get("cache_write_input_tokens", 0) or 0),
                cache_hit_ratio=float(payload.get("cache_hit_ratio", 0.0) or 0.0),
                transport=str(payload.get("transport", "openai-chat")),
                cache_mode=str(payload.get("cache_mode", "none")),
                cache_family=str(payload.get("cache_family", "generic")),
                cache_breakpoints=int(payload.get("cache_breakpoints", 0) or 0),
                input_tokens_before=int(payload.get("input_tokens_before", 0) or 0),
                input_tokens_after=int(payload.get("input_tokens_after", 0) or 0),
                artifacts_created=int(payload.get("artifacts_created", 0) or 0),
                compacted_messages=int(payload.get("compacted_messages", 0) or 0),
                semantic_summaries=int(payload.get("semantic_summaries", 0) or 0),
                semantic_calls=int(payload.get("semantic_calls", 0) or 0),
                semantic_failures=int(payload.get("semantic_failures", 0) or 0),
                semantic_quality_fallbacks=int(payload.get("semantic_quality_fallbacks", 0) or 0),
                checkpoint_created=bool(payload.get("checkpoint_created", False)),
                rehydrated_artifacts=int(payload.get("rehydrated_artifacts", 0) or 0),
                sidechannel_estimated_cost=float(payload.get("sidechannel_estimated_cost", 0.0) or 0.0),
                sidechannel_actual_cost=payload.get("sidechannel_actual_cost"),
                fallback_reason=str(payload.get("fallback_reason", "")),
                answer_depth=str(payload.get("answer_depth", "standard")),
                constraint_tags=list(payload.get("constraint_tags", []) or []),
                hint_tags=list(payload.get("hint_tags", []) or []),
                feature_tags=list(payload.get("feature_tags", []) or []),
                routing_features_payload=dict(payload.get("routing_features_payload", {}) or {}),
                fallback_chain_payload=list(payload.get("fallback_chain_payload", []) or []),
                candidate_scores_payload=list(payload.get("candidate_scores_payload", []) or []),
                selection_weights_payload=dict(payload.get("selection_weights_payload", {}) or {}),
                attempts_payload=list(payload.get("attempts_payload", []) or []),
                error_code=str(payload.get("error_code", "")),
                error_stage=str(payload.get("error_stage", "")),
                error_message=str(payload.get("error_message", "")),
                feedback_signal=str(payload.get("feedback_signal", "")),
                feedback_ok=bool(payload.get("feedback_ok", False)),
                feedback_action=str(payload.get("feedback_action", "")),
                feedback_from_tier=_normalize_tier_label(str(payload.get("feedback_from_tier", ""))) if payload.get("feedback_from_tier", "") else "",
                feedback_to_tier=_normalize_tier_label(str(payload.get("feedback_to_tier", ""))) if payload.get("feedback_to_tier", "") else "",
                feedback_reason=str(payload.get("feedback_reason", "")),
                feedback_submitted_at=float(payload.get("feedback_submitted_at", 0.0) or 0.0),
            ))
        self._cleanup()


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
        "usage_input_tokens": trace.usage_input_tokens,
        "usage_output_tokens": trace.usage_output_tokens,
        "cache_read_input_tokens": trace.cache_read_input_tokens,
        "cache_write_input_tokens": trace.cache_write_input_tokens,
        "cache_hit_ratio": trace.cache_hit_ratio,
        "transport": trace.transport,
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
    }
