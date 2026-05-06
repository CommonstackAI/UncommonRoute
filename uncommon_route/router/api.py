"""Public API — the route() entry point.

v2: Uses multi-signal ensemble (metadata heuristics + embedding KNN) instead
of v1's single text classifier. Signal B (structural) is conditionally active
on longer conversations and otherwise remains in shadow mode. PlattCalibrator
applied to ensemble confidence. Model selection unchanged.
"""

from __future__ import annotations

import logging
import json
import re
from dataclasses import dataclass, replace
from typing import Any

from uncommon_route.calibration import get_active_route_confidence_calibrator
from uncommon_route.router.types import (
    AnswerDepth,
    ModelCapabilities,
    ModelPricing,
    RequestRequirements,
    RoutingConfig,
    RoutingConstraints,
    RoutingDecision,
    RoutingFeatures,
    RoutingMode,
    Tier,
    WorkloadHints,
    pressure_rescue_active,
    pressure_rescue_premium_window,
)
from uncommon_route.router.config import DEFAULT_MODEL_PRICING
from uncommon_route.router.selector import select_from_pool, _derive_tier
from uncommon_route.router.structural import estimate_tokens
from uncommon_route.router.config import (
    DEFAULT_CONFIG,
    get_bandit_config,
    get_selection_weights,
)
from uncommon_route.signals.base import TierVote
from uncommon_route.signals.metadata import MetadataSignal
from uncommon_route.signals.embedding import EmbeddingSignal
from uncommon_route.signals.structural import StructuralSignal
from uncommon_route.decision.ensemble import Ensemble
from uncommon_route.decision.calibration import PlattCalibrator, load_calibrator

logger = logging.getLogger("uncommon-route")

# ─── v2 Signal Singletons ───

_v2_sig_a: MetadataSignal | None = None
_v2_sig_b: StructuralSignal | None = None  # conditional + shadow
_v2_sig_c: EmbeddingSignal | None = None
_v2_calibrator: PlattCalibrator | None = None
_v2_initialized = False

# tier_id → complexity (inverse of _derive_tier boundaries)
_TIER_ID_TO_COMPLEXITY = {0: 0.0, 1: 0.40, 2: 0.68, 3: 0.90}
_TIER_ORDER = {Tier.SIMPLE: 0, Tier.MEDIUM: 1, Tier.COMPLEX: 2}
_PRESSURE_RESCUE_NEXT_TIER = {
    Tier.SIMPLE: Tier.MEDIUM,
    Tier.MEDIUM: Tier.COMPLEX,
    Tier.COMPLEX: Tier.COMPLEX,
}
_PUBLIC_TIER_COMPLEXITY = {
    Tier.SIMPLE: 0.0,
    Tier.MEDIUM: 0.40,
    Tier.COMPLEX: 0.68,
}
_EXPLICIT_HIGH_COMPLEXITY_MARKERS = (
    "byzantine",
    "consensus algorithm",
    "distributed consensus",
    "formal correctness",
    "formal proof",
    "correctness proof",
    "cryptographic protocol",
    "zero-knowledge",
    "compiler",
    "type system",
    "kernel",
)
_TOOL_FAILURE_MARKERS = (
    "traceback",
    "exception",
    "assertionerror",
    "syntaxerror",
    "importerror",
    "modulenotfounderror",
    "failed",
    "error:",
    "command not found",
    "no such file",
)
_TOOL_FAILURE_PATTERNS = (
    re.compile(r"\b\d+\s+failed\b", re.IGNORECASE),
)
_EXPLICIT_FAIL_STATUS_RE = re.compile(
    r"(?:^|\n)\s*(?:[\w./:= -]+\s*[:=]\s*)?FAIL(?:\s|$)",
    re.IGNORECASE,
)
_SUCCESSFUL_FAILURE_SUMMARY_RE = re.compile(
    r"\b(?:0\s+(?:failed|failures?|errors?)|(?:failed|failures?|errors?)\s*[:=]\s*0|no\s+(?:failures?|errors?))\b",
    re.IGNORECASE,
)
_NONZERO_FAILURE_SUMMARY_RE = re.compile(
    r"\b(?:[1-9]\d*\s+(?:failed|failures?|errors?)|(?:failed|failures?|errors?)\s*[:=]\s*[1-9]\d*)\b",
    re.IGNORECASE,
)
_VERIFICATION_FAILURE_MARKERS = (
    "assertionerror",
    "short test summary info",
    "=== failures ===",
    "=== errors ===",
    "error collecting",
)
_VERIFICATION_CONTEXT_MARKERS = (
    "final verification",
    "verification",
    "verify",
    "test from",
    "tests:",
    "pytest",
    "unittest",
    "runtests",
    "failures",
    "assertionerror",
    "expected",
    "actual",
    "lint",
    "typecheck",
    "type-check",
    "mypy",
    "tsc",
    "eslint",
    "ruff",
)
_INVOCATION_FAILURE_MARKERS = (
    "unittest.loader._failedtest",
    "failedtest",
    "failed to import test module",
    "error importing test module",
    "no tests ran",
    "not found:",
    "module has no attribute",
)
_ENVIRONMENT_FAILURE_MARKERS = (
    "modulenotfounderror",
    "importerror",
    "no module named",
    "module not found",
    "command not found",
    "could not find a version",
    "no matching distribution",
    "successfully installed",
    "successfully uninstalled",
    "pip install",
    "site-packages/numpy",
    "module 'numpy' has no attribute",
)
_READ_ONLY_COMMAND_RE = re.compile(
    r"^\s*(?:"
    r"cat|sed\b|grep\b|rg\b|find\b|ls\b|head\b|tail\b|"
    r"git\s+(?:diff|status|show|log|rev-parse)\b"
    r")",
    re.IGNORECASE,
)
_XML_RETURN_CODE_RE = re.compile(r"<returncode>\s*(-?\d+)\s*</returncode>", re.IGNORECASE)


def _message_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
                elif item.get("type") == "tool_result":
                    parts.append(_message_text(item.get("content")))
                else:
                    parts.append(str(item))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        return "\n".join(
            _message_text(value.get(key))
            for key in ("content", "text", "output", "error")
            if value.get(key) is not None
        )
    return str(value)


def _message_has_tool_result(value: Any) -> bool:
    if isinstance(value, list):
        return any(
            isinstance(item, dict) and item.get("type") == "tool_result"
            for item in value
        )
    return False


def _tool_call_command(tool_call: dict[str, Any]) -> str:
    fn = tool_call.get("function") or {}
    if not isinstance(fn, dict):
        return ""
    raw_args = fn.get("arguments")
    if isinstance(raw_args, str):
        try:
            parsed = json.loads(raw_args)
        except json.JSONDecodeError:
            return raw_args
        if isinstance(parsed, dict) and isinstance(parsed.get("command"), str):
            return str(parsed["command"])
    return ""


def _command_for_tool_result(
    messages: list[dict[str, Any]],
    *,
    before_index: int,
    tool_call_id: str,
) -> str:
    if not tool_call_id:
        return ""
    for prior in reversed(messages[:before_index]):
        if not isinstance(prior, dict):
            continue
        for tool_call in prior.get("tool_calls") or ():
            if isinstance(tool_call, dict) and tool_call.get("id") == tool_call_id:
                command = _tool_call_command(tool_call)
                if command:
                    return command
    return ""


def _latest_tool_result_message(
    messages: list[dict[str, Any]] | None,
) -> tuple[dict[str, Any] | None, str, str]:
    if not messages:
        return None, "", ""
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if message.get("role") == "tool":
            command = _command_for_tool_result(
                messages,
                before_index=index,
                tool_call_id=str(message.get("tool_call_id") or ""),
            )
            return message, _message_text(content), command
        if _message_has_tool_result(content):
            command = ""
            if isinstance(content, list):
                for block in reversed(content):
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        command = _command_for_tool_result(
                            messages,
                            before_index=index,
                            tool_call_id=str(block.get("tool_use_id") or ""),
                        )
                        break
            return message, _message_text(content), command
    return None, "", ""


def _has_zero_xml_returncode(text: str) -> bool:
    match = _XML_RETURN_CODE_RE.search(text or "")
    if match is None:
        return False
    try:
        return int(match.group(1)) == 0
    except ValueError:
        return False


def _command_is_read_only_observation(command: str) -> bool:
    return bool(_READ_ONLY_COMMAND_RE.search(command or ""))


def _tool_result_is_error(message: dict[str, Any] | None, text: str, command: str = "") -> bool:
    if not message:
        return False
    if bool(message.get("is_error")):
        return True
    if (
        _has_zero_xml_returncode(text)
        and _command_is_read_only_observation(command)
        and not _NONZERO_FAILURE_SUMMARY_RE.search(text)
    ):
        summary_stripped = _SUCCESSFUL_FAILURE_SUMMARY_RE.sub(" ", text)
        has_verification_context = any(
            marker in f"{command}\n{text}".lower()
            for marker in _VERIFICATION_CONTEXT_MARKERS
        )
        if not (has_verification_context and _EXPLICIT_FAIL_STATUS_RE.search(summary_stripped)):
            return False
    lowered = text.lower()
    if "<returncode>" in lowered and "<returncode>0</returncode>" not in lowered:
        return True
    if _tool_result_is_verification_failure(text):
        return True
    if any(pattern.search(text) for pattern in _TOOL_FAILURE_PATTERNS):
        return True
    return any(marker in lowered for marker in _TOOL_FAILURE_MARKERS)


def _tool_result_is_verification_failure(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    if _NONZERO_FAILURE_SUMMARY_RE.search(text):
        return True
    if any(marker in lowered for marker in _VERIFICATION_FAILURE_MARKERS):
        return True
    summary_stripped = _SUCCESSFUL_FAILURE_SUMMARY_RE.sub(" ", text)
    has_verification_context = any(
        marker in lowered for marker in _VERIFICATION_CONTEXT_MARKERS
    )
    return bool(has_verification_context and _EXPLICIT_FAIL_STATUS_RE.search(summary_stripped))


def _tool_result_failure_kind(text: str, command: str = "") -> str:
    """Classify the latest tool failure by routing significance.

    Semantic failures mean the attempted solution is probably wrong and can
    justify premium review. Environment and invocation failures are still
    high-risk, but usually need cheaper recovery steps rather than stronger
    reasoning.
    """
    if not text:
        return ""
    haystack = f"{command}\n{text}".lower()
    if any(marker in haystack for marker in _ENVIRONMENT_FAILURE_MARKERS):
        return "environment"
    if any(marker in haystack for marker in _INVOCATION_FAILURE_MARKERS):
        return "invocation"
    if _tool_result_is_verification_failure(text):
        return "semantic"
    return "unknown" if _tool_result_is_error({"role": "tool"}, text, command) else ""


def _agent_state_pressure(messages: list[dict[str, Any]] | None, step_risk: str) -> tuple[int, float]:
    """Estimate how much the current agent trajectory needs stronger review.

    This is deliberately continuous and model-agnostic. It does not say "use
    Opus after N steps"; it says long tool trajectories and accumulated tool
    failures should reduce the force of cheap/routine caps.
    """
    if not messages:
        return 0, 0.0

    tool_call_steps = 0
    tool_result_steps = 0
    failure_steps = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        has_tool_call = bool(message.get("tool_calls"))
        is_tool_result = message.get("role") == "tool" or _message_has_tool_result(message.get("content"))
        if has_tool_call:
            tool_call_steps += 1
        if is_tool_result:
            tool_result_steps += 1
            text = _message_text(message.get("content"))
            if _tool_result_is_error(message, text):
                failure_steps += 1

    tool_steps = max(tool_call_steps, tool_result_steps)
    step_component = min(0.45, max(0, tool_steps) / 24.0)
    failure_component = min(0.35, failure_steps / 6.0)
    risk_component = 0.20 if str(step_risk or "").lower() == "high" else 0.0
    pressure = min(1.0, step_component + failure_component + risk_component)
    return tool_steps, pressure


def _infer_routing_features_from_messages(
    messages: list[dict[str, Any]] | None,
    *,
    max_output_tokens: int,
) -> RoutingFeatures:
    tool_message, tool_text, tool_command = _latest_tool_result_message(messages)
    has_tool_results = tool_message is not None
    last_message = messages[-1] if messages else None
    last_is_tool_result = bool(
        isinstance(last_message, dict)
        and (
            last_message.get("role") == "tool"
            or _message_has_tool_result(last_message.get("content"))
        )
    )
    step_type = (
        "tool-result-followup"
        if last_is_tool_result
        else ("general_agent" if has_tool_results else "general")
    )

    step_risk = "normal"
    verification_failed = False
    failure_kind = ""
    if step_type == "tool-result-followup":
        failure_kind = _tool_result_failure_kind(tool_text, tool_command)
        verification_failed = failure_kind == "semantic"
        if _tool_result_is_error(tool_message, tool_text, tool_command):
            step_risk = "high"
        elif tool_text and len(tool_text) <= 800:
            step_risk = "low"

    agent_step_count, agent_pressure = _agent_state_pressure(messages, step_risk)

    return RoutingFeatures(
        step_type=step_type,
        has_tool_results=has_tool_results,
        step_risk=step_risk,
        is_agentic=has_tool_results,
        is_coding=has_tool_results,
        prefers_reasoning=False,
        requested_max_output_tokens=max(1, int(max_output_tokens)),
        tier_floor=Tier.MEDIUM if step_risk == "high" else None,
        tier_cap=(
            Tier.MEDIUM
            if step_risk == "low" or failure_kind in {"environment", "invocation"}
            else None
        ),
        tier_cap_reason=(
            "environment-recovery"
            if failure_kind == "environment"
            else (
                "invocation-recovery"
                if failure_kind == "invocation"
                else ("low-risk" if step_risk == "low" else "")
            )
        ),
        agent_step_count=agent_step_count,
        agent_pressure=agent_pressure,
        verification_failed=verification_failed,
        failure_kind=failure_kind,
    )


def _enrich_features_from_messages(
    features: RoutingFeatures,
    messages: list[dict[str, Any]] | None,
    *,
    max_output_tokens: int,
) -> RoutingFeatures:
    """Preserve caller-provided features while honoring explicit latest failures."""
    if not messages:
        return features

    inferred = _infer_routing_features_from_messages(
        messages,
        max_output_tokens=max_output_tokens,
    )
    if not inferred.verification_failed:
        return features

    tier_floor = features.tier_floor
    if tier_floor is None or _TIER_ORDER[tier_floor] < _TIER_ORDER[Tier.MEDIUM]:
        tier_floor = Tier.MEDIUM

    return replace(
        features,
        has_tool_results=features.has_tool_results or inferred.has_tool_results,
        step_risk="high",
        is_agentic=features.is_agentic or inferred.is_agentic,
        is_coding=features.is_coding or inferred.is_coding,
        tier_floor=tier_floor,
        tier_cap=None,
        tier_cap_reason="",
        agent_step_count=max(features.agent_step_count, inferred.agent_step_count),
        agent_pressure=max(features.agent_pressure, inferred.agent_pressure),
        verification_failed=True,
        failure_kind=inferred.failure_kind or features.failure_kind,
    )


def _apply_tier_bounds(
    complexity: float,
    *,
    tier_floor: Tier | None,
    tier_cap: Tier | None,
) -> tuple[float, list[str]]:
    bounded = complexity
    notes: list[str] = []
    effective_tier = _derive_tier(bounded)

    if tier_floor is not None and _TIER_ORDER[effective_tier] < _TIER_ORDER[tier_floor]:
        bounded = _PUBLIC_TIER_COMPLEXITY[tier_floor]
        effective_tier = tier_floor
        notes.append(f"tier-floor={tier_floor.value}")

    if tier_cap is not None and _TIER_ORDER[effective_tier] > _TIER_ORDER[tier_cap]:
        bounded = _PUBLIC_TIER_COMPLEXITY[tier_cap]
        notes.append(f"tier-cap={tier_cap.value}")

    return bounded, notes


def _agent_pressure_supports_rescue(features: RoutingFeatures) -> bool:
    return pressure_rescue_active(
        agent_pressure=features.agent_pressure,
        agent_step_count=features.agent_step_count,
        has_tool_results=features.has_tool_results,
        is_agentic=features.is_agentic,
        is_coding=features.is_coding,
    )


def _pressure_rescue_tier_floor(
    v2: V2ClassifyResult,
    features: RoutingFeatures,
) -> tuple[Tier | None, str | None]:
    if not _agent_pressure_supports_rescue(features):
        return None, None

    predicted_tier = _derive_tier(v2.complexity)

    if features.verification_failed:
        return Tier.COMPLEX, "agent-pressure-floor=COMPLEX(verification-failed)"

    if not pressure_rescue_premium_window(
        agent_pressure=features.agent_pressure,
        agent_step_count=features.agent_step_count,
        has_tool_results=features.has_tool_results,
        is_agentic=features.is_agentic,
        is_coding=features.is_coding,
    ):
        return None, None

    rescue_floor = _PRESSURE_RESCUE_NEXT_TIER[predicted_tier]
    return rescue_floor, f"agent-pressure-floor={rescue_floor.value}(from={predicted_tier.value})"


def _soften_tier_cap_for_agent_state(
    v2: V2ClassifyResult,
    features: RoutingFeatures,
    tier_cap: Tier | None,
    pressure_rescue_floor: Tier | None,
) -> tuple[Tier | None, str | None]:
    if tier_cap is None:
        return tier_cap, None

    predicted_tier = _derive_tier(v2.complexity)
    embedding_support = (
        not v2.vote_c.abstained
        and (v2.vote_c.tier_id or 0) >= 2
        and v2.vote_c.confidence >= 0.45
    )
    pressure_support = features.agent_pressure >= 0.55 and v2.tier_id >= 2
    high_risk_support = features.step_risk == "high" and v2.tier_id >= 2
    cap_reason = str(features.tier_cap_reason or "").strip().lower()
    rescue_exceeds_cap = (
        pressure_rescue_floor is not None
        and _TIER_ORDER[pressure_rescue_floor] > _TIER_ORDER[tier_cap]
    )

    if cap_reason == "environment-or-routine":
        if str(features.step_risk or "").strip().lower() == "low":
            return tier_cap, f"tier-cap-preserved({cap_reason}:low-risk-step)"
        routine_pressure_support = (
            features.agent_pressure >= 0.65
            and v2.tier_id >= 3
            and (embedding_support or v2.confidence >= 0.60)
        )
        if not routine_pressure_support:
            return tier_cap, f"tier-cap-preserved({cap_reason})"
    elif cap_reason == "environment-recovery":
        return tier_cap, f"tier-cap-preserved({cap_reason})"
    elif cap_reason == "invocation-recovery":
        return tier_cap, f"tier-cap-preserved({cap_reason})"
    elif cap_reason == "low-risk":
        pressure_cap_support = (
            rescue_exceeds_cap
            and (embedding_support or v2.tier_id >= 1 or v2.confidence >= 0.30)
        )
        if pressure_cap_support:
            return None, f"tier-cap-softened(agent-pressure={features.agent_pressure:.2f})"
        return tier_cap, f"tier-cap-preserved({cap_reason})"
    elif cap_reason in {
        "routine-success",
        "short-observation",
        "recoverable-tool-error",
    }:
        pressure_cap_support = (
            rescue_exceeds_cap
            and (embedding_support or v2.tier_id >= 1 or v2.confidence >= 0.30)
        )
        if pressure_cap_support:
            return None, f"tier-cap-softened(agent-pressure={features.agent_pressure:.2f})"
        return tier_cap, f"tier-cap-preserved({cap_reason})"

    if _TIER_ORDER[predicted_tier] <= _TIER_ORDER[tier_cap]:
        return tier_cap, None

    if embedding_support or pressure_support or high_risk_support:
        reasons: list[str] = []
        if embedding_support:
            reasons.append("embedding")
        if pressure_support:
            reasons.append(f"agent-pressure={features.agent_pressure:.2f}")
        if high_risk_support:
            reasons.append("risk=high")
        return None, "tier-cap-softened(" + ",".join(reasons) + ")"

    return tier_cap, None


def _should_activate_signal_b(row: dict[str, Any], vote_b: TierVote | None = None) -> bool:
    """Enable Signal B on longer conversations where it improves pass rate.

    Disabled on tool-heavy workflows: in deep agent conversations the last
    user message is often a repeated task description, which makes B's
    structural classifier lock onto high-tier text features even when the
    current step is routine. Let Signal A+C carry in that regime.

    For short, standalone prompts, activate B when it has a strong opinion.
    That keeps obviously hard asks and structured-output requests from being
    flattened by metadata priors that only look at message count.
    """
    messages = row.get("messages", [])
    tool_msg_count = sum(1 for m in messages if m.get("role") == "tool" or m.get("tool_calls"))
    if tool_msg_count >= 4:
        return False
    if len(messages) >= 4:
        return True
    if vote_b is None or vote_b.abstained:
        return False
    return vote_b.confidence >= 0.95 and (vote_b.tier_id or 0) >= 1


def _strongest_non_structural_tier(vote_a: TierVote, vote_c: TierVote) -> int | None:
    """Return the strongest sufficiently confident non-structural support."""
    supported: list[int] = []
    if not vote_a.abstained and vote_a.tier_id is not None and vote_a.confidence >= 0.65:
        supported.append(vote_a.tier_id)
    if not vote_c.abstained and vote_c.tier_id is not None:
        # For short standalone tasks, Signal C is the only semantic signal that
        # can corroborate Signal B's structural "this is hard" read. Require
        # strong confidence for mid-tier support, but allow moderate confidence
        # when the embedding classifier says the task is highest-tier; the cap
        # below still limits this to public COMPLEX, not tier_id=3.
        min_confidence = 0.55 if vote_c.tier_id >= 3 else 0.70
        if vote_c.confidence >= min_confidence:
            supported.append(vote_c.tier_id)
    return max(supported) if supported else None


def _has_explicit_high_complexity_text(row: dict[str, Any]) -> bool:
    text_parts: list[str] = []
    for message in row.get("messages", []):
        content = message.get("content", "")
        if isinstance(content, str):
            text_parts.append(content)
    text = "\n".join(text_parts).lower()
    return any(marker in text for marker in _EXPLICIT_HIGH_COMPLEXITY_MARKERS)


def _cap_uncorroborated_structural_high(
    row: dict[str, Any],
    vote_a: TierVote,
    vote_b: TierVote,
    vote_c: TierVote,
) -> tuple[TierVote, bool, int | None]:
    """Prevent short-prompt structure alone from forcing the highest v2 tier.

    Signal B is a useful safety floor, but on standalone agent prompts it can
    over-read task scaffolding ("plan, use tools, edit files") as highest-tier
    complexity. Require Signal A or C to corroborate before allowing B to vote
    at tier 3 on short no-tool context.
    """
    if vote_b.abstained or vote_b.tier_id is None:
        return vote_b, False, None
    if vote_b.tier_id < 3 or vote_b.confidence < 0.95:
        return vote_b, False, None

    messages = row.get("messages", [])
    tool_msg_count = sum(1 for m in messages if m.get("role") == "tool" or m.get("tool_calls"))
    if tool_msg_count > 0 or len(messages) > 3:
        return vote_b, False, None

    support_tier = _strongest_non_structural_tier(vote_a, vote_c)
    explicit_high_complexity = _has_explicit_high_complexity_text(row)
    if support_tier is not None and support_tier >= 3:
        return vote_b, False, None
    capped_tier = (
        2
        if (support_tier is not None and support_tier >= 2) or explicit_high_complexity
        else 1
    )
    if vote_b.tier_id <= capped_tier:
        return vote_b, False, None
    return TierVote(capped_tier, vote_b.confidence), True, capped_tier


@dataclass(frozen=True)
class V2ClassifyResult:
    """Full result from v2 classification, including all signal votes."""
    complexity: float
    confidence: float
    tier_id: int
    method: str
    signals_text: tuple[str, ...]
    vote_a: TierVote
    vote_b: TierVote
    vote_c: TierVote
    query_embedding: Any = None  # cached for index growth (numpy array or None)


def _ensure_v2_signals() -> None:
    global _v2_sig_a, _v2_sig_b, _v2_sig_c, _v2_calibrator, _v2_initialized
    if _v2_initialized:
        return
    _v2_initialized = True
    _v2_sig_a = MetadataSignal()
    _v2_sig_b = StructuralSignal()  # always loaded for conditional/shadow use
    # Auto-deploy seed index from package data if not in user data dir
    try:
        ensure_seed_index_deployed()
    except Exception:
        pass
    # Try to load embedding index
    try:
        from uncommon_route.paths import data_dir
        splits_dir = data_dir() / "v2_splits"
        emb_path = splits_dir / "seed_embeddings.npy"
        labels_path = splits_dir / "seed_labels.json"
        if emb_path.exists() and labels_path.exists():
            _v2_sig_c = EmbeddingSignal(
                index_path=emb_path, labels_path=labels_path,
                model_name="BAAI/bge-small-en-v1.5",
            )
            logger.info("v2 embedding signal loaded from %s", splits_dir)
        else:
            _v2_sig_c = EmbeddingSignal(model_name=None)
            logger.info("v2 embedding index not found — Signal C will abstain")
    except Exception as e:
        _v2_sig_c = EmbeddingSignal(model_name=None)
        logger.warning("v2 embedding init failed: %s", e)
    # Try to load PlattCalibrator
    try:
        from uncommon_route.paths import data_dir
        cal_path = data_dir() / "v2_splits" / "calibration_params.json"
        if cal_path.exists():
            _v2_calibrator = load_calibrator(cal_path)
            logger.info("v2 PlattCalibrator loaded (temperature=%.2f)", _v2_calibrator.temperature)
        else:
            logger.info("v2 calibration params not found — using uncalibrated confidence")
    except Exception as e:
        logger.warning("v2 calibrator load failed: %s", e)


def _build_signal_row(
    prompt: str,
    system_prompt: str | None,
    messages: list[dict[str, Any]] | None,
    routing_features: RoutingFeatures | None,
    context_features: dict[str, float] | None,
) -> dict[str, Any]:
    """Build a row dict for v2 signals from available proxy data."""
    # If full messages available, normalize content to strings but preserve other keys
    if messages:
        msgs = []
        for m in messages:
            normalized = dict(m)  # preserve tool_calls, tool_call_id, name, etc.
            content = normalized.get("content", "")
            if not isinstance(content, str):
                if isinstance(content, list):
                    parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                    content = " ".join(parts)
                else:
                    content = str(content)
                normalized["content"] = content
            msgs.append(normalized)
    else:
        msgs = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.append({"role": "user", "content": prompt})

    msg_count = len(msgs)
    step_index = max(1, msg_count // 2)
    total_steps = max(step_index + 3, 10)

    scenario = "general"
    if routing_features:
        if routing_features.is_coding:
            scenario = "code_swe"
        elif routing_features.step_type == "tool-result-followup":
            scenario = "general_agent"

    return {
        "messages": msgs,
        "benchmark": "",
        "scenario": scenario,
        "step_index": step_index,
        "total_steps": total_steps,
    }


def _v2_classify(
    prompt: str,
    system_prompt: str | None,
    messages: list[dict[str, Any]] | None,
    routing_features: RoutingFeatures | None,
    context_features: dict[str, float] | None,
    risk_tolerance: float = 0.5,
) -> V2ClassifyResult:
    """Run v2 signal ensemble. Returns full result with all signal votes."""
    _ensure_v2_signals()

    row = _build_signal_row(prompt, system_prompt, messages, routing_features, context_features)
    tool_msg_count = sum(1 for m in row.get("messages", []) if m.get("role") == "tool" or m.get("tool_calls"))
    has_system_prompt = any(m.get("role") == "system" for m in row.get("messages", []))
    vote_a = _v2_sig_a.predict(row) if _v2_sig_a else TierVote(tier_id=1, confidence=0.4)
    vote_b = _v2_sig_b.predict(row) if _v2_sig_b else TierVote(tier_id=None, confidence=0.0)
    vote_c = _v2_sig_c.predict(row) if _v2_sig_c else TierVote(tier_id=None, confidence=0.0)
    effective_vote_b, structural_high_capped, structural_cap_tier = _cap_uncorroborated_structural_high(
        row,
        vote_a,
        vote_b,
        vote_c,
    )

    # Cache query embedding for potential index growth
    query_embedding = None
    if _v2_sig_c and _v2_sig_c._embed_fn:
        try:
            from uncommon_route.signals.embedding import _extract_last_user_message
            text = _extract_last_user_message(row.get("messages", []))
            if text.strip():
                query_embedding = _v2_sig_c._embed_fn(text)
        except Exception:
            pass

    # Signal B is active for longer conversations and can also be
    # globally promoted by the lifecycle tracker.
    try:
        from uncommon_route import v2_lifecycle as _lc
        signal_b_promoted = _lc.is_signal_b_promoted()
        tracker_weights = _lc._weight_tracker.weights if _lc._weight_tracker else None
    except Exception:
        # Keep the core router usable in lightweight installs where optional
        # lifecycle dependencies (for example numpy-backed index growth) are
        # unavailable. Lifecycle learning is additive, not required for routing.
        signal_b_promoted = False
        tracker_weights = None
    use_signal_b = _should_activate_signal_b(row, effective_vote_b) or signal_b_promoted

    # Get learned weights from tracker (falls back to defaults if not initialized)

    # Build ensemble with active signals, using learned weights when available
    if use_signal_b:
        active_votes = [vote_a]
        active_weights = [tracker_weights[0] if tracker_weights and len(tracker_weights) >= 3 else 0.50]
        if not effective_vote_b.abstained:
            active_votes.append(effective_vote_b)
            active_weights.append(tracker_weights[1] if tracker_weights and len(tracker_weights) >= 3 else 0.10)
        if not vote_c.abstained:
            active_votes.append(vote_c)
            active_weights.append(tracker_weights[2] if tracker_weights and len(tracker_weights) >= 3 else 0.40)
    else:
        active_votes = [vote_a]
        active_weights = [tracker_weights[0] if tracker_weights and len(tracker_weights) >= 2 else 0.55]
        if not vote_c.abstained:
            active_votes.append(vote_c)
            active_weights.append(tracker_weights[1] if tracker_weights and len(tracker_weights) >= 2 else 0.45)

    ensemble = Ensemble(
        weights=active_weights,
        risk_tolerance=risk_tolerance,
        calibrator=_v2_calibrator,
    )
    result = ensemble.decide(active_votes)

    tier_id = result.tier_id if result.tier_id is not None else 1
    weak_metadata_only_cap_applied = False
    if (
        not use_signal_b
        and vote_c.abstained
        and vote_a.confidence <= 0.35
        and tier_id > 1
    ):
        # In deep tool-heavy trajectories Signal B is intentionally shadowed
        # because it tends to over-escalate on stale conversation structure.
        # If Signal C is unavailable too, the only active vote is Signal A's
        # weak metadata prior. Do not let that weak prior route routine steps
        # to premium models.
        tier_id = 1
        weak_metadata_only_cap_applied = True
    structural_floor_applied = False
    if (
        tool_msg_count == 0
        and len(row.get("messages", [])) <= 3
        and not effective_vote_b.abstained
        and effective_vote_b.confidence >= (0.70 if has_system_prompt else 0.95)
        and (effective_vote_b.tier_id or 0) >= 1
        and tier_id < effective_vote_b.tier_id
    ):
        tier_id = effective_vote_b.tier_id
        structural_floor_applied = True
    structural_medium_floor_applied = False
    if (
        not structural_floor_applied
        and tool_msg_count == 0
        and len(row.get("messages", [])) <= 3
        and not effective_vote_b.abstained
        and effective_vote_b.confidence >= 0.70
        and (effective_vote_b.tier_id or 0) >= 1
        and tier_id < 1
    ):
        # Short standalone implementation/design prompts are often capped to
        # 0.70 confidence by Signal B's short-text dampener. Do not let
        # metadata-only priors route those steps as economy, but avoid
        # escalating them all the way to premium unless Signal B is stronger.
        tier_id = 1
        structural_medium_floor_applied = True
    complexity = _TIER_ID_TO_COMPLEXITY.get(tier_id, 0.40)

    signals_parts = [
        f"v2:metadata={vote_a.tier_id}({vote_a.confidence:.2f})",
        f"v2:structural={vote_b.tier_id}({vote_b.confidence:.2f})[{'active' if use_signal_b else 'shadow'}]",
        f"v2:embedding={vote_c.tier_id}({vote_c.confidence:.2f})",
        f"v2:tier={tier_id} complexity={complexity:.2f} method={result.method}",
    ]
    if structural_floor_applied:
        signals_parts.append("v2:structural-floor")
    if structural_medium_floor_applied:
        signals_parts.append("v2:structural-medium-floor")
    if structural_high_capped:
        signals_parts.append(f"v2:structural-high-cap={structural_cap_tier}")
    if weak_metadata_only_cap_applied:
        signals_parts.append("v2:weak-metadata-only-cap")
    signals_text = tuple(signals_parts)

    return V2ClassifyResult(
        complexity=complexity,
        confidence=result.confidence,
        tier_id=tier_id,
        method=result.method,
        signals_text=signals_text,
        vote_a=vote_a,
        vote_b=vote_b,
        vote_c=vote_c,
        query_embedding=query_embedding,
    )


def route(
    prompt: str,
    system_prompt: str | None = None,
    max_output_tokens: int = 4096,
    config: RoutingConfig | None = None,
    routing_mode: RoutingMode | str = RoutingMode.AUTO,
    request_requirements: RequestRequirements | None = None,
    routing_constraints: RoutingConstraints | None = None,
    workload_hints: WorkloadHints | None = None,
    routing_features: RoutingFeatures | None = None,
    answer_depth: AnswerDepth | str = AnswerDepth.STANDARD,
    user_keyed_models: set[str] | None = None,
    model_experience: object | None = None,
    route_confidence_calibrator: object | None = None,
    context_features: dict[str, float] | None = None,
    pricing: dict[str, ModelPricing] | None = None,
    available_models: list[str] | None = None,
    model_capabilities: dict[str, ModelCapabilities] | None = None,
    messages: list[dict[str, Any]] | None = None,
    record_lifecycle: bool = True,
    # Legacy parameters — accepted but ignored
    tier_cap: Tier | None = None,
    tier_floor: Tier | None = None,
) -> RoutingDecision:
    """Route a prompt to the best model using v2 multi-signal ensemble."""
    cfg = config or DEFAULT_CONFIG
    constraints = routing_constraints or RoutingConstraints()
    features = routing_features or _infer_routing_features_from_messages(
        messages,
        max_output_tokens=max_output_tokens,
    )
    if routing_features is not None:
        features = _enrich_features_from_messages(
            features,
            messages,
            max_output_tokens=max_output_tokens,
        )
    requirements = features.request_requirements() if routing_features else (request_requirements or RequestRequirements())
    hints = features.workload_hints() if routing_features else (workload_hints or WorkloadHints())
    mode = routing_mode if isinstance(routing_mode, RoutingMode) else RoutingMode(routing_mode)
    depth = answer_depth if isinstance(answer_depth, AnswerDepth) else AnswerDepth(str(answer_depth).strip().lower())
    effective_max_output_tokens = features.requested_max_output_tokens or max_output_tokens

    estimated_tokens = estimate_tokens(prompt)

    # ─── v2: multi-signal ensemble ───
    v2 = _v2_classify(prompt, system_prompt, messages, features, context_features)

    sel_weights = get_selection_weights(cfg, mode)
    bc = get_bandit_config(cfg, mode)
    caps = cfg.model_capabilities if model_capabilities is None else model_capabilities
    pool = list(DEFAULT_MODEL_PRICING.keys()) if available_models is None else available_models
    effective_pricing = DEFAULT_MODEL_PRICING if pricing is None else pricing

    # v1's route-confidence calibrator still runs on top of v2's ensemble confidence
    confidence_calibrator = route_confidence_calibrator or get_active_route_confidence_calibrator()

    effective_tier_floor = features.tier_floor or tier_floor
    effective_tier_cap = features.tier_cap or tier_cap
    pressure_rescue_floor, pressure_floor_note_candidate = _pressure_rescue_tier_floor(v2, features)
    pressure_floor_note = None
    if (
        pressure_rescue_floor is not None
        and (
            effective_tier_floor is None
            or _TIER_ORDER[effective_tier_floor] < _TIER_ORDER[pressure_rescue_floor]
        )
    ):
        effective_tier_floor = pressure_rescue_floor
        pressure_floor_note = pressure_floor_note_candidate
    effective_tier_cap, cap_softened_note = _soften_tier_cap_for_agent_state(
        v2,
        features,
        effective_tier_cap,
        pressure_rescue_floor,
    )
    bounded_complexity, bound_notes = _apply_tier_bounds(
        v2.complexity,
        tier_floor=effective_tier_floor,
        tier_cap=effective_tier_cap,
    )
    final_tier = _derive_tier(bounded_complexity)
    confidence_estimate = confidence_calibrator.calibrate(
        v2.confidence,
        mode=mode,
        tier=final_tier,
        complexity=bounded_complexity,
        step_type=features.step_type,
        answer_depth=depth,
        constraint_tags=constraints.tags(),
        hint_tags=hints.tags(),
        feature_tags=features.tags(),
        streaming=features.streaming,
    )
    reasoning_parts = list(v2.signals_text)
    if pressure_floor_note:
        reasoning_parts.append(pressure_floor_note)
    if cap_softened_note:
        reasoning_parts.append(cap_softened_note)
    reasoning_parts.extend(bound_notes)
    reasoning = ", ".join(reasoning_parts)

    decision = select_from_pool(
        complexity=bounded_complexity,
        mode=mode,
        confidence=confidence_estimate.confidence,
        reasoning_text=reasoning,
        available_models=pool,
        estimated_input_tokens=estimated_tokens,
        max_output_tokens=effective_max_output_tokens,
        prompt=prompt,
        pricing=effective_pricing,
        capabilities=caps,
        requirements=requirements,
        constraints=constraints,
        workload_hints=hints,
        routing_features=features,
        answer_depth=depth,
        answer_depth_multiplier=cfg.answer_depth.multiplier(depth),
        agentic_score=0.0,
        user_keyed_models=user_keyed_models,
        selection_weights=sel_weights,
        bandit_config=bc,
        model_experience=model_experience,
        raw_confidence=confidence_estimate.raw_confidence,
        confidence_source=confidence_estimate.source,
        calibration_version=confidence_estimate.version,
        calibration_sample_count=confidence_estimate.sample_count,
        calibration_temperature=confidence_estimate.temperature,
        calibration_applied_tags=confidence_estimate.applied_adjustments,
    )

    if record_lifecycle:
        # ─── v2: record to lifecycle (metrics, shadow, logging) ───
        try:
            from uncommon_route.v2_lifecycle import on_route_complete
            on_route_complete(
                request_id="",  # filled by proxy if available
                tier_id=v2.tier_id,
                model=decision.model,
                method=v2.method,
                confidence=v2.confidence,
                signal_a_tier=v2.vote_a.tier_id,
                signal_a_conf=v2.vote_a.confidence,
                signal_b_tier=v2.vote_b.tier_id,
                signal_b_conf=v2.vote_b.confidence,
                signal_c_tier=v2.vote_c.tier_id,
                signal_c_conf=v2.vote_c.confidence,
                query_embedding=v2.query_embedding,
            )
        except Exception:
            pass  # lifecycle not initialized yet (e.g. during tests)

    return decision


def ensure_seed_index_deployed() -> None:
    """Copy seed index from package data to user data dir if not already present."""
    from uncommon_route.v2_assets import ensure_v2_assets_deployed

    ensure_v2_assets_deployed()
