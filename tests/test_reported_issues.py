"""Reproduce reported production issues.

Issues:
  1. Routing over-concentrates on COMPLEX
  2. Simple tasks misclassified after complex context
  3. MiniMax rarely selected despite being "best"
  4. Simple tasks marked tool-heavy
  5. Feedback mechanism barely changes routing
  6. Opus/Sonnet cause retry loops (tested via proxy integration)
"""

from __future__ import annotations

import json
import copy
from dataclasses import replace

from uncommon_route.router.api import route
from uncommon_route.router.classifier import classify, extract_features, _ensure_model_loaded, update_model, save_online_model, rollback_online_model
from uncommon_route.router.config import DEFAULT_CONFIG, DEFAULT_MODEL_PRICING, DEFAULT_MODEL_CAPABILITIES
from uncommon_route.router.types import (
    ModelCapabilities,
    ModelPricing,
    RequestRequirements,
    RoutingConstraints,
    RoutingFeatures,
    RoutingMode,
    ScoringConfig,
    Tier,
    WorkloadHints,
)
from uncommon_route.model_experience import (
    CandidateExperience,
    InMemoryModelExperienceStorage,
    ModelExperienceStore,
)


# ─── Issue 1: COMPLEX over-classification ───

def test_issue1_simple_prompts_without_tools():
    """Pure classifier: simple prompts should classify as SIMPLE."""
    simple_prompts = [
        "hello",
        "what is 2+2?",
        "translate 'hello' to French",
        "who invented the telephone?",
        "what is DNS?",
    ]
    results = []
    for prompt in simple_prompts:
        r = classify(prompt)
        results.append((prompt[:40], r.tier, r.complexity))
    for prompt, tier, complexity in results:
        assert tier == Tier.SIMPLE, f"'{prompt}' classified as {tier} (complexity={complexity:.2f}), expected SIMPLE"


def test_issue1_simple_prompts_routed_without_agentic_context():
    """route() without agentic hints should keep simple tasks simple."""
    simple_prompts = [
        "what is 2+2?",
        "hello",
        "translate 'cat' to Spanish",
    ]
    for prompt in simple_prompts:
        decision = route(prompt)
        assert decision.tier in (Tier.SIMPLE, Tier.MEDIUM), (
            f"'{prompt}' routed to {decision.tier} (complexity={decision.complexity:.2f}), "
            f"expected SIMPLE or MEDIUM"
        )


def test_issue1_agentic_context_no_longer_inflates():
    """FIXED: adding tools no longer forces complexity floor.

    Previously, is_agentic=True → agentic_complexity_floor=0.40.
    Now, WorkloadHints don't override the classifier.
    """
    simple_prompt = "what is 2+2?"

    decision_plain = route(simple_prompt)
    plain_complexity = decision_plain.complexity

    decision_agentic = route(
        simple_prompt,
        workload_hints=WorkloadHints(is_agentic=True),
    )
    agentic_complexity = decision_agentic.complexity

    print(f"\n  Plain complexity: {plain_complexity:.2f} → {decision_plain.tier.value}")
    print(f"  Agentic complexity: {agentic_complexity:.2f} → {decision_agentic.tier.value}")

    assert agentic_complexity == plain_complexity, "agentic hint should NOT inflate complexity anymore"


def test_issue1_coding_detection_too_broad():
    """is_coding triggers on any mention of a language name, even in trivial context."""
    prompts_with_false_positive_coding = [
        "what does the python keyword 'yield' mean?",  # simple question, not coding
        "explain what a javascript closure is",  # explanation, not implementation
        "what is rust?",  # one word mention
    ]
    for prompt in prompts_with_false_positive_coding:
        decision = route(
            prompt,
            workload_hints=WorkloadHints(is_agentic=True, is_coding=True),
        )
        print(f"\n  '{prompt[:50]}...' → tier={decision.tier.value} complexity={decision.complexity:.2f}")


def test_issue1_tool_result_followup_no_longer_forces_complex():
    """FIXED: tool-result-followup no longer forces COMPLEX.

    Previously, tier_floor=COMPLEX was hardcoded for tool-result + coding.
    Now, the classifier decides difficulty based on the actual prompt.
    """
    features = RoutingFeatures(
        step_type="tool-result-followup",
        has_tool_results=True,
        is_agentic=True,
        needs_tool_calling=True,
    )
    decision = route(
        "thanks, looks good",
        routing_features=features,
    )
    print(f"\n  'thanks, looks good' with tool-result-followup → {decision.tier.value} (complexity={decision.complexity:.2f})")
    assert decision.tier != Tier.COMPLEX, "trivial ack should NOT be COMPLEX"


# ─── Issue 2: Context leakage ───

def test_issue2_simple_after_complex_no_context_leak():
    """Classifier should NOT leak context between requests.

    Each call to classify() should be independent.
    """
    complex_prompt = "Design a distributed consensus algorithm with Byzantine fault tolerance"
    simple_prompt = "hello"

    # Classify complex first
    r1 = classify(complex_prompt)
    assert r1.tier in (Tier.MEDIUM, Tier.COMPLEX), f"Expected MEDIUM or COMPLEX, got {r1.tier}"

    # Then classify simple — should still be SIMPLE
    r2 = classify(simple_prompt)
    assert r2.tier == Tier.SIMPLE, (
        f"Simple prompt after complex prompt classified as {r2.tier}, "
        f"expected SIMPLE. Signals: {r2.signals}"
    )


def test_issue2_route_simple_after_complex():
    """route() should also be independent between calls."""
    route("Design a microservice architecture with event sourcing and CQRS")
    decision = route("what time is it?")
    assert decision.tier in (Tier.SIMPLE, Tier.MEDIUM), (
        f"'what time is it?' routed as {decision.tier} after complex prompt"
    )


# ─── Issue 3: MiniMax rarely selected ───

def test_issue3_minimax_never_wins_for_complex():
    """MiniMax is cheap ($0.30/$1.20) so quality_prior is low.

    For COMPLEX tasks (high mu), quality_term dominates and expensive models win.
    """
    decision = route(
        "Design a distributed system",
        routing_mode=RoutingMode.AUTO,
    )
    minimax_score = None
    winner_score = None
    for cs in decision.candidate_scores:
        if "minimax" in cs.model.lower():
            minimax_score = cs
        if cs.model == decision.model:
            winner_score = cs

    print(f"\n  Winner: {decision.model} (total={winner_score.total:.4f})" if winner_score else "")
    if minimax_score:
        print(f"  MiniMax: {minimax_score.model} (total={minimax_score.total:.4f}, editorial={minimax_score.editorial:.4f})")
        print(f"  Gap: {(winner_score.total - minimax_score.total):.4f}" if winner_score else "")
    else:
        print("  MiniMax not in candidate pool!")


def test_issue3_minimax_ranking_across_tiers():
    """Check MiniMax's position in ranking for each complexity level."""
    for complexity, label in [(0.15, "SIMPLE"), (0.45, "MEDIUM"), (0.85, "COMPLEX")]:
        tier = Tier.SIMPLE if complexity < 0.33 else (Tier.MEDIUM if complexity < 0.67 else Tier.COMPLEX)
        # Use a neutral prompt and force complexity via routing features
        decision = route(
            "do something",
            routing_mode=RoutingMode.AUTO,
        )
        models = [cs.model for cs in decision.candidate_scores]
        minimax_pos = next((i for i, m in enumerate(models) if "minimax" in m.lower()), None)
        print(f"\n  {label}: winner={decision.model}, minimax at position {minimax_pos}/{len(models)}" if minimax_pos is not None else f"\n  {label}: minimax not found in pool")


def test_issue3_quality_prior_is_benchmark_based():
    """FIXED: quality_prior now uses benchmark data, not price."""
    import uncommon_route.benchmark as bm
    bm._ACTIVE_CACHE = None
    bm._PINCHBENCH_SEED = bm._load_seed_data()

    cache = bm.get_benchmark_cache()
    minimax_quality = cache.get_quality("minimax/minimax-m2.5")
    opus_quality = cache.get_quality("anthropic/claude-opus-4.6")
    oss_quality = cache.get_quality("nvidia/gpt-oss-120b")

    print(f"\n  Benchmark quality (not price-based):")
    print(f"    MiniMax: {minimax_quality:.3f}")
    print(f"    Opus:    {opus_quality:.3f}")
    print(f"    gpt-oss: {oss_quality:.3f}")

    if minimax_quality == 0.5 and oss_quality == 0.5:
        import pytest
        pytest.skip("No benchmark seed data available — run 'uncommon-route benchmark fetch' first")

    assert minimax_quality > oss_quality, "MiniMax should beat gpt-oss-120b in quality"
    assert minimax_quality > 0.6, "MiniMax should have decent benchmark quality"


# ─── Issue 4: Simple tasks classified as tool-heavy ───

def test_issue4_any_tools_means_agentic():
    """If the request body has tools, is_agentic=True regardless of prompt."""
    from uncommon_route.proxy import _classify_step, _extract_routing_features

    body_with_tools = {
        "model": "uncommon-route/auto",
        "messages": [
            {"role": "user", "content": "hello"},
        ],
        "tools": [
            {"type": "function", "function": {"name": "read_file", "parameters": {}}},
        ],
    }

    step_type, tool_names = _classify_step(body_with_tools)
    features = _extract_routing_features(
        body_with_tools,
        step_type=step_type,
        tool_names=tool_names,
        prompt="hello",
    )

    print(f"\n  Prompt: 'hello'")
    print(f"  step_type: {step_type}")
    print(f"  is_agentic: {features.is_agentic}")
    print(f"  needs_tool_calling: {features.needs_tool_calling}")
    print(f"  tier_floor: {features.tier_floor}")

    assert features.is_agentic is True, "Tools in body → is_agentic=True"
    assert step_type == "tool-selection", "user message + tools → tool-selection"


def test_issue4_tool_selection_always_triggers():
    """Even trivial messages get step_type='tool-selection' when tools present."""
    from uncommon_route.proxy import _classify_step

    trivial_messages = ["hello", "yes", "ok", "thanks"]
    for msg in trivial_messages:
        body = {
            "messages": [{"role": "user", "content": msg}],
            "tools": [{"type": "function", "function": {"name": "bash"}}],
        }
        step_type, _ = _classify_step(body)
        assert step_type == "tool-selection", f"'{msg}' got step_type={step_type}"


# ─── Issue 5: Feedback mechanism ineffective ───

def test_issue5_averaged_perceptron_update_magnitude():
    """After training, online updates barely move the averaged weights.

    The model accumulates weights over epochs*samples updates.
    A single online update changes avg_weights by ~1/total_updates.
    """
    _ensure_model_loaded()

    prompt = "write a complex distributed system"
    features = extract_features(prompt)

    # Get initial prediction
    result_before = classify(prompt)
    before_tier = result_before.tier
    before_complexity = result_before.complexity

    # Apply 5 feedback corrections saying this should be SIMPLE
    for _ in range(5):
        update_model(features, "SIMPLE")

    result_after = classify(prompt)
    after_tier = result_after.tier
    after_complexity = result_after.complexity

    print(f"\n  Before feedback: tier={before_tier}, complexity={before_complexity:.4f}")
    print(f"  After 5x SIMPLE feedback: tier={after_tier}, complexity={after_complexity:.4f}")
    print(f"  Complexity change: {after_complexity - before_complexity:+.4f}")

    # The change should be tiny because averaged weights dominate
    rollback_online_model()


def test_issue5_feedback_needs_many_iterations():
    """How many feedback iterations to actually change a prediction?"""
    _ensure_model_loaded()

    prompt = "implement a REST API with authentication"
    features = extract_features(prompt)

    initial = classify(prompt)
    initial_tier = initial.tier
    changed_at = None

    for i in range(1, 101):
        update_model(features, "SIMPLE")
        result = classify(prompt)
        if result.tier != initial_tier and changed_at is None:
            changed_at = i
            break

    print(f"\n  Initial tier: {initial_tier}")
    if changed_at:
        print(f"  Changed to {result.tier} after {changed_at} feedback iterations")
    else:
        print(f"  Still {initial_tier} after 100 feedback iterations (complexity: {result.complexity:.4f})")

    rollback_online_model()


def test_issue5_model_experience_feedback_impact():
    """Even model_experience feedback doesn't change selector output much."""
    store = ModelExperienceStore(
        storage=InMemoryModelExperienceStorage(),
        alpha=0.25,
    )

    # Record negative feedback for the expensive model
    for _ in range(10):
        store.record_feedback(
            "anthropic/claude-opus-4.6",
            RoutingMode.AUTO,
            Tier.COMPLEX,
            "weak",
        )

    # Record positive feedback for MiniMax
    for _ in range(10):
        store.record_feedback(
            "minimax/minimax-m2.5",
            RoutingMode.AUTO,
            Tier.COMPLEX,
            "ok",
        )

    opus_exp = store.snapshot("anthropic/claude-opus-4.6", RoutingMode.AUTO, Tier.COMPLEX)
    minimax_exp = store.snapshot("minimax/minimax-m2.5", RoutingMode.AUTO, Tier.COMPLEX)

    print(f"\n  After 10x weak on Opus: feedback={opus_exp.feedback:.3f} reward={opus_exp.reward_mean:.3f}")
    print(f"  After 10x ok on MiniMax: feedback={minimax_exp.feedback:.3f} reward={minimax_exp.reward_mean:.3f}")

    # Now route with experience
    decision = route(
        "implement a distributed cache",
        model_experience=store,
    )
    print(f"  Still routes to: {decision.model}")

    opus_score = next((cs for cs in decision.candidate_scores if "opus" in cs.model.lower()), None)
    minimax_score = next((cs for cs in decision.candidate_scores if "minimax" in cs.model.lower()), None)
    if opus_score and minimax_score:
        print(f"  Opus total: {opus_score.total:.4f} (feedback={opus_score.feedback:.3f})")
        print(f"  MiniMax total: {minimax_score.total:.4f} (feedback={minimax_score.feedback:.3f})")


# ─── Issue 6: Opus/Sonnet fallback loop ───

def test_issue6_fallback_only_on_model_errors():
    """_should_try_fallback only fires on 400/404/422 with model error keywords.

    If upstream returns 500/502/timeout, no fallback happens → client retries → same model → loop.
    """
    from uncommon_route.proxy import _is_model_error

    assert _is_model_error(b'{"error":{"message":"model not found"}}')
    assert _is_model_error(b'{"error":{"message":"model does not exist"}}')
    assert not _is_model_error(b'{"error":{"message":"internal server error"}}')
    assert not _is_model_error(b'{"error":{"message":"rate limit exceeded"}}')
    assert not _is_model_error(b'{"error":{"message":"timeout"}}')


def test_issue6_anthropic_model_resolution():
    """Check if Opus/Sonnet resolve correctly for Commonstack."""
    from uncommon_route.model_map import ModelMapper

    mapper = ModelMapper("https://api.commonstack.ai/v1")

    # Before discovery, gateway mode should keep full provider/model names
    opus_resolved = mapper.resolve("anthropic/claude-opus-4.6")
    sonnet_resolved = mapper.resolve("anthropic/claude-sonnet-4.6")

    print(f"\n  Pre-discovery:")
    print(f"    Opus → {opus_resolved}")
    print(f"    Sonnet → {sonnet_resolved}")
    print(f"    Is gateway: {mapper.is_gateway}")

    assert mapper.is_gateway, "Commonstack should be detected as gateway"
    # Gateway mode should keep the full provider/model prefix


def test_issue6_legacy_model_ids_resolve_to_live_successors():
    """Legacy internal ids should map onto current upstream successors."""
    from uncommon_route.model_map import DiscoveredModel, ModelMapper
    from uncommon_route.router.types import ModelCapabilities, ModelPricing

    live_models = [
        "x-ai/grok-4-1-fast-non-reasoning",
        "x-ai/grok-4.1-fast-reasoning",
        "x-ai/grok-code-fast-1",
        "google/gemini-3.1-pro-preview",
        "openai/gpt-4.1",
        "openai/gpt-5.3-codex",
        "openai/gpt-5.4-mini-2026-03-17",
        "openai/gpt-5.4-2026-03-05",
    ]

    mapper = ModelMapper("https://api.commonstack.ai/v1")
    for model_id in live_models:
        provider = model_id.split("/", 1)[0]
        mapper._pool[model_id] = DiscoveredModel(
            id=model_id,
            provider=provider,
            owned_by=provider,
            pricing=ModelPricing(0.2, 0.8),
            capabilities=ModelCapabilities(tool_calling=True, vision=False, reasoning=False),
        )
        mapper._upstream_models.add(model_id)
    mapper._build_map()
    mapper._discovered = True

    assert mapper.resolve("xai/grok-4-0709") == "x-ai/grok-4-1-fast-non-reasoning"
    assert mapper.resolve("xai/grok-4-1-fast-reasoning") == "x-ai/grok-4.1-fast-reasoning"
    assert mapper.resolve("xai/grok-4-1-fast-non-reasoning") == "x-ai/grok-4-1-fast-non-reasoning"
    assert mapper.resolve("xai/grok-code-fast-1") == "x-ai/grok-code-fast-1"
    assert mapper.resolve("google/gemini-3-pro-preview") == "google/gemini-3.1-pro-preview"
    assert mapper.resolve("google/gemini-3.1-pro") == "google/gemini-3.1-pro-preview"
    assert mapper.resolve("openai/gpt-4o") == "openai/gpt-4.1"
    assert mapper.resolve("openai/gpt-5.2-codex") == "openai/gpt-5.3-codex"
    assert mapper.resolve("openai/o1-mini") == "openai/gpt-5.4-mini-2026-03-17"
    assert mapper.resolve("openai/o3") == "openai/gpt-5.4-2026-03-05"
    assert mapper.resolve("openai/o4-mini") == "openai/gpt-5.4-mini-2026-03-17"
    assert not any(
        model in mapper.unresolved_models()
        for model in (
            "xai/grok-4-0709",
            "xai/grok-4-1-fast-reasoning",
            "xai/grok-4-1-fast-non-reasoning",
            "xai/grok-code-fast-1",
            "google/gemini-3-pro-preview",
            "google/gemini-3.1-pro",
            "openai/gpt-4o",
            "openai/gpt-5.2-codex",
            "openai/o1-mini",
            "openai/o3",
            "openai/o4-mini",
        )
    )


def test_issue6_image_generation_models_do_not_enter_chat_routing_pool():
    """Image-generation catalog entries should not become chat candidates."""
    from uncommon_route.model_map import DiscoveredModel, ModelMapper, is_routable_chat_model
    from uncommon_route.router.types import ModelCapabilities, ModelPricing

    mapper = ModelMapper("https://api.commonstack.ai/v1")
    for model_id in [
        "google/gemini-3-pro-image-preview",
        "google/gemini-3.1-flash-image-preview",
        "google/gemini-3.1-pro-preview",
        "minimax/minimax-m2.7",
    ]:
        provider = model_id.split("/", 1)[0]
        mapper._pool[model_id] = DiscoveredModel(
            id=model_id,
            provider=provider,
            owned_by=provider,
            pricing=ModelPricing(0.2, 0.8),
            capabilities=ModelCapabilities(tool_calling=True, vision=True),
        )
        mapper._upstream_models.add(model_id)
    mapper._build_map()
    mapper._discovered = True

    assert not is_routable_chat_model("google/gemini-3-pro-image-preview")
    assert mapper.resolve("google/gemini-3-pro-preview") == "google/gemini-3.1-pro-preview"
    assert "google/gemini-3-pro-image-preview" in mapper.available_models
    assert "google/gemini-3-pro-image-preview" not in mapper.routable_models
    assert "google/gemini-3.1-flash-image-preview" not in mapper.routable_models
    assert "google/gemini-3.1-pro-preview" in mapper.routable_models


# ─── Summary: combined issue reproduction ───

def test_combined_claude_code_session_simulation():
    """Simulate a realistic Claude Code session to show all issues together.

    In Claude Code, EVERY request has tools. The session alternates between
    user messages and tool-result-followup rounds. Simple tasks get
    inflated to COMPLEX just from the agentic context.
    """
    from uncommon_route.proxy import _classify_step, _extract_routing_features

    # Simulate typical Claude Code request bodies
    claude_code_tools = [
        {"type": "function", "function": {"name": "Read"}},
        {"type": "function", "function": {"name": "Write"}},
        {"type": "function", "function": {"name": "Shell"}},
        {"type": "function", "function": {"name": "Grep"}},
    ]

    scenarios = [
        {
            "label": "Simple greeting",
            "messages": [{"role": "user", "content": "hello, what can you help me with?"}],
        },
        {
            "label": "Simple question",
            "messages": [{"role": "user", "content": "what is 2+2?"}],
        },
        {
            "label": "After tool results (trivial ack)",
            "messages": [
                {"role": "user", "content": "read the README"},
                {"role": "assistant", "content": None, "tool_calls": [{"type": "function", "function": {"name": "Read", "arguments": "{}"}}]},
                {"role": "tool", "content": "# README\nHello world"},
            ],
        },
        {
            "label": "Simple code question",
            "messages": [{"role": "user", "content": "what does the python keyword 'yield' mean?"}],
        },
        {
            "label": "Actual complex task",
            "messages": [{"role": "user", "content": "Design and implement a distributed consensus algorithm with Byzantine fault tolerance, including Raft-style leader election and log replication"}],
        },
    ]

    print("\n  Claude Code Session Simulation:")
    print("  " + "=" * 80)

    for scenario in scenarios:
        body = {
            "model": "uncommon-route/auto",
            "messages": scenario["messages"],
            "tools": claude_code_tools,
            "stream": True,
        }

        step_type, tool_names = _classify_step(body)
        prompt = ""
        for msg in reversed(body["messages"]):
            if msg.get("role") == "user":
                prompt = msg.get("content", "")
                break
            if msg.get("role") == "tool":
                prompt = msg.get("content", "")[:50]
                break

        features = _extract_routing_features(
            body,
            step_type=step_type,
            tool_names=tool_names,
            prompt=prompt,
        )

        # Pure classifier result (what the classifier actually thinks)
        classifier_result = classify(prompt or "")

        # Full route result (with all the overrides)
        decision = route(
            prompt or "",
            routing_features=features,
            workload_hints=features.workload_hints(),
            request_requirements=features.request_requirements(),
        )

        print(f"\n  [{scenario['label']}]")
        print(f"    Prompt: '{(prompt or '')[:60]}'")
        print(f"    step_type: {step_type}")
        print(f"    Classifier raw: tier={classifier_result.tier}, complexity={classifier_result.complexity:.2f}")
        print(f"    Route final:    tier={decision.tier.value}, complexity={decision.complexity:.2f}")
        print(f"    is_agentic={features.is_agentic} is_coding={features.is_coding} "
              f"tier_floor={features.tier_floor} tier_cap={features.tier_cap}")
        print(f"    Selected model: {decision.model}")
        print(f"    Top 3: {', '.join(cs.model for cs in decision.candidate_scores[:3])}")

    print("\n  " + "=" * 80)
