"""FastAPI backend serving all three classifiers for the comparison UI."""

from __future__ import annotations

import os
import time
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from uncommon_route.router.classifier import classify as ur_classify
from bench.clawrouter_v2_compat import classify_clawrouter_v2 as cr_classify
from uncommon_route.router.config import DEFAULT_MODEL_PRICING, DEFAULT_CONFIG
from uncommon_route.router.types import RoutingMode, Tier

app = FastAPI(title="UncommonRoute vs ClawRouter vs Claude")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

TIER_MODEL = {
    "SIMPLE": DEFAULT_CONFIG.modes[RoutingMode.AUTO].tiers[Tier.SIMPLE].primary,
    "MEDIUM": DEFAULT_CONFIG.modes[RoutingMode.AUTO].tiers[Tier.MEDIUM].primary,
    "COMPLEX": DEFAULT_CONFIG.modes[RoutingMode.AUTO].tiers[Tier.COMPLEX].primary,
}

TIER_COLORS = {
    "SIMPLE": "#22c55e",
    "MEDIUM": "#3b82f6",
    "COMPLEX": "#f59e0b",
}

BASELINE = "anthropic/claude-opus-4.6"

# Claude client (lazy init)
_claude_client = None

def _get_claude():
    global _claude_client
    if _claude_client is None:
        import anthropic
        _claude_client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    return _claude_client


CLAUDE_CLASSIFY_PROMPT = """You are a query complexity classifier for an LLM router. Classify the user's query into exactly one of these three tiers:

- SIMPLE: Factual Q&A, definitions, translations, greetings, trivial lookups
- MEDIUM: Single-task code generation, explanations, comparisons, summaries, debugging, code review, rewrites
- COMPLEX: Multi-requirement system design, architecture, security audits, ML pipelines, migrations, infrastructure (typically lists 3+ requirements)
- COMPLEX also covers formal mathematical proofs, algorithm correctness proofs, derivations, game theory, and logic puzzles.

Respond with ONLY one word: SIMPLE, MEDIUM, or COMPLEX. Nothing else."""


def classify_with_claude(prompt: str) -> tuple[str, float, float]:
    """Call Claude API to classify. Returns (tier, confidence, latency_ms)."""
    try:
        client = _get_claude()
        t0 = time.perf_counter()
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=10,
            temperature=0,
            system=CLAUDE_CLASSIFY_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        latency_ms = (time.perf_counter() - t0) * 1000

        text = response.content[0].text.strip().upper()
        tier = "MEDIUM"
        for t in ("SIMPLE", "MEDIUM", "COMPLEX"):
            if t in text:
                tier = t
                break

        return tier, 0.95, latency_ms
    except Exception as e:
        print(f"Claude API error: {e}")
        return "ERROR", 0.0, 0.0


class Query(BaseModel):
    prompt: str


class RouterResult(BaseModel):
    tier: str
    model: str
    confidence: float
    cost_per_1k: float
    color: str
    latency_us: float
    signals: list[str]


class CompareResponse(BaseModel):
    uncommon_route: RouterResult
    clawrouter: RouterResult
    claude_opus: RouterResult


def _cost_per_1k(model: str) -> float:
    p = DEFAULT_MODEL_PRICING.get(model)
    if not p:
        return 0.0
    return (500 / 1_000_000) * p.input_price + (500 / 1_000_000) * p.output_price


@app.post("/api/compare", response_model=CompareResponse)
def compare(q: Query):
    prompt = q.prompt.strip()
    if not prompt:
        empty = RouterResult(tier="—", model="—", confidence=0, cost_per_1k=0, color="#888", latency_us=0, signals=[])
        return CompareResponse(uncommon_route=empty, clawrouter=empty, claude_opus=empty)

    # UncommonRoute
    t0 = time.perf_counter_ns()
    ur = ur_classify(prompt)
    ur_us = (time.perf_counter_ns() - t0) / 1000
    ur_tier = ur.tier.value if ur.tier else "MEDIUM"
    ur_model = TIER_MODEL.get(ur_tier, "moonshot/kimi-k2.5")

    # ClawRouter
    t0 = time.perf_counter_ns()
    cr_tier, cr_conf = cr_classify(prompt)
    cr_us = (time.perf_counter_ns() - t0) / 1000
    cr_model = TIER_MODEL.get(cr_tier, "moonshot/kimi-k2.5")

    # Claude (real API call)
    claude_tier, claude_conf, claude_ms = classify_with_claude(prompt)
    claude_us = claude_ms * 1000

    return CompareResponse(
        uncommon_route=RouterResult(
            tier=ur_tier, model=ur_model, confidence=round(ur.confidence, 3),
            cost_per_1k=round(_cost_per_1k(ur_model) * 1000, 4),
            color=TIER_COLORS.get(ur_tier, "#888"), latency_us=round(ur_us, 0),
            signals=ur.signals[:5],
        ),
        clawrouter=RouterResult(
            tier=cr_tier, model=cr_model, confidence=round(cr_conf, 3),
            cost_per_1k=round(_cost_per_1k(cr_model) * 1000, 4),
            color=TIER_COLORS.get(cr_tier, "#888"), latency_us=round(cr_us, 0),
            signals=[],
        ),
        claude_opus=RouterResult(
            tier=claude_tier, model="claude-opus-4.6 (judge)",
            confidence=round(claude_conf, 3),
            cost_per_1k=round(_cost_per_1k(BASELINE) * 1000, 4),
            color=TIER_COLORS.get(claude_tier, "#ef4444"),
            latency_us=round(claude_us, 0),
            signals=[f"api:{claude_ms:.0f}ms"],
        ),
    )


@app.get("/")
def index():
    return FileResponse("frontend/index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3721)
