#!/usr/bin/env node
// Thin CLI wrapper around ClawRouter's production classifier.
// Reads one JSON request per line on stdin, writes one JSON response per line
// on stdout. Keeps a single Node process warm for the whole benchmark run.

import { createInterface } from "node:readline";
import { route, DEFAULT_ROUTING_CONFIG } from "../../../ClawRouter/dist/index.js";

const TIER_TO_ID = { SIMPLE: 0, MEDIUM: 1, COMPLEX: 2, REASONING: 3 };
const DEFAULT_MAX_OUTPUT = 4096;

// ClawRouter's route() signature needs a modelPricing map. Pricing values
// don't affect tier classification — route() only uses pricing to cost the
// chosen tier. We supply a stub so it compiles.
const STUB_MODEL_PRICING = new Map();
for (const tier of Object.values(DEFAULT_ROUTING_CONFIG.tiers)) {
  for (const model of [tier.primary, ...(tier.fallback ?? [])]) {
    STUB_MODEL_PRICING.set(model, { input: 0, output: 0 });
  }
}

function predict(prompt, systemPrompt, maxOutput) {
  const decision = route(
    prompt,
    systemPrompt ?? undefined,
    maxOutput ?? DEFAULT_MAX_OUTPUT,
    { config: DEFAULT_ROUTING_CONFIG, modelPricing: STUB_MODEL_PRICING },
  );
  const tierId = TIER_TO_ID[decision.tier] ?? 1;
  return { tier_id: tierId, tier: decision.tier, confidence: decision.confidence };
}

const rl = createInterface({ input: process.stdin });
rl.on("line", (line) => {
  const trimmed = line.trim();
  if (!trimmed) return;
  try {
    const req = JSON.parse(trimmed);
    const res = predict(req.prompt ?? "", req.system_prompt ?? null, req.max_output_tokens);
    process.stdout.write(JSON.stringify(res) + "\n");
  } catch (err) {
    process.stdout.write(JSON.stringify({ error: String(err?.message ?? err), tier_id: 1 }) + "\n");
  }
});
rl.on("close", () => process.exit(0));
