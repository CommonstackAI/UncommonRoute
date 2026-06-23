# Transport Routing Design

## Summary

UncommonRoute should route **both**:

- `selected_model`
- `selected_transport`

Today, the router mainly selects a model, then often normalizes the upstream request into OpenAI Chat Completions. That works for many simple text requests, but it is not robust for tool-heavy Anthropic-native clients such as Claude Code.

The new goal is to make transport selection a first-class routing result rather than an implementation detail.


## Problem

The current architecture mixes two separate decisions:

1. Which model should handle the request?
2. Which protocol should carry the request upstream?

Those two decisions are correlated, but they are not the same.

This becomes visible in Anthropic-native agent loops:

- `POST /v1/messages`
- multi-turn `tool_use` / `tool_result`
- thinking or interleaved reasoning
- large tool catalogs
- tool-result follow-up rounds

In these cases, translating an Anthropic request into OpenAI Chat can lose or distort protocol-specific semantics. Even when the converted payload is "valid enough" for many models, some model families or gateways reject it, especially on later tool turns.


## Design Principles

### 1. Protocol is part of routing

Routing should output:

- model
- transport
- reasoning

not just model.

### 2. Preserve client-native semantics when possible

- Anthropic-native requests should prefer Anthropic-native upstream transport.
- OpenAI-native requests should prefer OpenAI-native upstream transport.

Cross-protocol conversion should be a fallback path, not the default path.

### 3. Provider preferences matter

Different providers are not equally strong on both protocols.

Example:

- MiniMax explicitly recommends its Anthropic-compatible API for Anthropic-style clients and documents full support for `tool_use`, `tool_result`, and `thinking`.
- Anthropic itself treats its OpenAI compatibility layer as useful, but not the best production path for the full Claude feature set.

Transport choice should therefore consider provider-native strengths, not just client input format.

### 4. Keep external API compatibility

We must continue to expose both:

- OpenAI-compatible ingress
- Anthropic-compatible ingress

This design changes internal routing behavior, not the public product surface.


## Proposed Architecture

### A. Split routing into two layers

#### Layer 1: Workload routing

Decide:

- complexity/tier
- mode
- model candidate ranking

This is the existing router's job and should stay mostly unchanged.

#### Layer 2: Transport routing

Decide:

- `anthropic-messages`
- `openai-chat`
- later, optionally `openai-responses`

based on:

- ingress API format
- request shape
- routing features
- selected model/provider family
- provider protocol preference


## Transport Decision Model

Introduce a small internal decision object:

```python
TransportDecision(
    transport="anthropic-messages" | "openai-chat",
    reason="...",
    preferred=True | False,
)
```

This should be computed after model selection but before upstream body preparation.


## First-Version Policy

### Prefer Anthropic transport when all are true

- ingress request came through Anthropic Messages
- request is tool-heavy or agentic
- request is a `tool-selection` or `tool-result-followup` turn, or includes thinking
- selected provider/model family has good Anthropic-native support

Initial provider families that should prefer Anthropic transport:

- `anthropic`
- `minimax`

This is not a giant compatibility matrix. It is a short, intentional protocol-preference policy based on official provider guidance and real request shape.

### Prefer OpenAI transport when any are true

- ingress request came through OpenAI Chat/Responses
- selected provider is OpenAI-native and the request is not Anthropic-specific
- request depends on OpenAI client semantics rather than Anthropic block semantics

### Neutral cases

For simple single-turn text requests:

- either transport may be fine
- prefer the provider's native or best-supported transport


## Request Shape Signals That Should Influence Transport

Extend transport selection to look at existing `RoutingFeatures` plus a few transport-specific signals:

- `api_format`
- `step_type`
- `has_tool_results`
- `needs_tool_calling`
- `streaming`
- `requested_max_output_tokens`
- `session_present`
- `thinking_requested`
- `anthropic_beta_present`
- `tool_count`

These are not model-selection-only hints anymore; they are also transport-selection hints.


## Concrete Code Changes

### 1. Add transport preference logic

New helper in proxy or a dedicated module:

- `choose_transport(...)`

Inputs:

- ingress `api_format`
- selected model
- provider entry
- mapper/upstream provider
- routing features
- raw request/body hints

Outputs:

- transport decision

### 2. Keep `_supports_native_anthropic_transport`, but narrow its role

Today `_supports_native_anthropic_transport(...)` acts like a low-level capability check.

After this change:

- `choose_transport(...)` should make the policy decision
- `_supports_native_anthropic_transport(...)` should remain a capability guard

In other words:

- `choose_transport` answers "should we prefer Anthropic here?"
- `_supports_native_anthropic_transport` answers "can we actually do that?"

### 3. Preserve protocol-specific request bodies longer

Today `handle_messages(...)` immediately converts Anthropic input via:

- `anthropic_to_openai_request(raw)`

That is too early.

We should instead:

- preserve the original Anthropic request body for as long as possible
- derive routing signals without forcing an immediate protocol conversion
- only convert when the chosen transport actually requires conversion

### 4. Expose transport reasoning in traces

Add explicit trace/debug fields:

- `requested_transport`
- `selected_transport`
- `transport_reason`
- `transport_preference_source`

This matters because future debugging will otherwise repeat the same ambiguity:

- "Did the model fail?"
- "Did the protocol conversion fail?"
- "Did we choose the wrong transport?"


## Why Not "Just Use Anthropic Everywhere"

Because UncommonRoute serves multiple client ecosystems.

If we force Anthropic transport everywhere:

- OpenAI SDK / Codex / Cursor flows become awkward or lossy
- OpenAI Responses compatibility gets worse
- OpenAI-specific expectations move onto a protocol that does not match them cleanly

So the right abstraction is not:

- "switch the whole product to Anthropic"

It is:

- "route each request onto the protocol that best preserves its semantics"


## Why Not "Just Add Fallback"

Fallback is still useful, but it is not the primary fix.

If transport choice is wrong, fallback only masks the problem:

- more retries
- higher latency
- noisier traces
- hidden transport bugs

The primary fix should be selecting the correct transport up front.


## Rollout Plan

### Phase 1: Transport-aware routing

- add `choose_transport(...)`
- preserve Anthropic request bodies longer
- prefer Anthropic transport for Anthropic tool-heavy agentic turns on Anthropic-friendly providers
- add trace/debug fields

### Phase 2: Transport-aware ranking

Incorporate transport preference into candidate scoring:

- small positive adjustment when a model/provider pair aligns with the request's native protocol
- no giant hardcoded matrix

This keeps the pool adaptive while still favoring protocol-safe paths.

### Phase 3: Explicit operator visibility

Expose in dashboard/support bundle:

- selected transport
- converted vs native path
- transport failures by provider/model


## Success Criteria

The design is successful when:

- Claude Code / Anthropic SDK tool-heavy sessions stop failing due to avoidable cross-protocol conversion
- OpenAI clients continue to work without needing Anthropic-specific configuration
- traces clearly explain both model choice and transport choice
- transport fallback becomes rare rather than normal


## Short Version

UncommonRoute should stop thinking of transport as "whatever body we happen to send upstream."

It should explicitly route:

- the request's **model**
- the request's **protocol**

That is the cleanest way to support Claude Code, OpenAI clients, and provider-specific strengths at the same time.
