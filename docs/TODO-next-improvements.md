# UncommonRoute v2 — Next Improvements TODO

> Date: 2026-04-10 | Status: Deferred pending data volume increase

## Current State

- **Accuracy**: 73.6% (E2E production-path, 762 rows LLMRouterBench)
- **Pass Rate**: 90.3%
- **Cost Savings**: 77.4
- **Tests**: 88/88 pass
- **Components**: 10/10 wired into production

## Why These Items Are Deferred

**Data volume is the bottleneck.** We have 487 training samples. Analysis shows:
- Trained classifier (logistic regression) only improves +0.1pp over KNN
- mid (8%) and mid_high (7%) tiers have 12-13 samples each — any classifier fails on these
- RouteLLM's biggest accuracy gain came from 5-10x data augmentation, not model changes
- Matrix Factorization and embedding fine-tuning both need 2000-5000+ samples to outperform simple KNN

**Until we have 2000+ labeled samples, model architecture improvements will hit the same ceiling.**

---

## Deferred Items

### #1: LLM-Judge Data Augmentation (HIGHEST PRIORITY)
**What**: Use Claude/GPT-4 to label 5000-10000 production prompts with tier labels.
**Why deferred**: Requires API budget (~$50-100) and production traffic to label.
**Expected impact**: +5-10pp accuracy (RouteLLM's biggest finding).
**How to do it**:
1. Collect unlabeled prompts from production usage
2. Send each to a strong LLM with a tier classification prompt
3. Use soft labels (probability distribution over tiers) for better training
4. Retrain embedding classifier + MF router on augmented data

### #4: Matrix Factorization Router (RouteLLM-style)
**What**: Replace Averaged Perceptron with learned (query, tier) embeddings via matrix factorization.
**Why deferred**: Only 487 training samples. MF needs 2000+ to outperform simpler models. RouteLLM's MF router was trained on 55K+ samples.
**Expected impact**: +5-10pp (but only with sufficient data).
**How to do it**:
1. Learn low-rank embeddings: v_query (384d→64d) and v_tier (4×64d)
2. Score via Hadamard product: score(query, tier) = w^T (v_tier ⊙ (W v_query + b))
3. Train with binary cross-entropy on preference pairs
4. Requires: augmented dataset from #1

### #5: Asymmetric Bandit Learning (BaRP 2025)
**What**: Weight failure signals more than success signals in online learning.
**Why deferred**: Needs production traffic to learn from. Only useful after deployment.
**Expected impact**: +3-5pp over 2-4 weeks of production traffic.
**How to do it**:
1. Modify SignalWeightTracker: failure update *= 1.5, success update *= 1.0
2. Track counterfactual: "what would have happened with a different tier?"
3. Use retrial detection as implicit failure signal
4. Requires: live production deployment

### #6: Fail-Fast Cascade Routing (ETH Zurich ICLR 2025)
**What**: When the cheap model fails (HTTP error, empty response, refusal), automatically retry with a more expensive model.
**Why deferred**: Infrastructure work. Not blocked by data, but lower priority than accuracy improvements.
**Expected impact**: +2-4pp pass rate improvement.
**How to do it**:
1. After forwarding to cheap model, check response for hard-fail signals
2. On hard-fail: re-route to next tier up (single retry, max 1 escalation)
3. Agent workflows can opt out via `cascade=false` flag
4. Reuse existing circuit_breaker.py infrastructure

### #2b: Embedding Model Fine-Tuning (LoRA)
**What**: Fine-tune bge-small with triplet loss so "similar difficulty" prompts are closer in embedding space.
**Why deferred**: 487 samples → high overfitting risk. Even LoRA rank-2 has ~50K trainable params for 487 samples.
**Expected impact**: +4-8pp (but only with 2000+ augmented samples from #1).
**How to do it**:
1. Generate triplets: (anchor, same-tier-query, different-tier-query)
2. LoRA rank-4 on bge-small
3. Ordinal-aware loss: low should be farther from high than from mid
4. Requires: augmented dataset from #1

---

## Data Collection Strategy

To unblock all of the above, we need **2000-5000 labeled samples**. Three approaches:

### A. LLM-Judge Augmentation (~$50-100)
- Collect unlabeled prompts from production
- Have a strong LLM classify tier
- Cheapest and fastest path

### B. Community Data Sharing (opt-in)
- Users opt in to share anonymized routing data
- We collect: last_user_message + routing outcome + feedback signal
- Privacy-preserving: no full conversation, no personal data
- Builds a shared routing quality dataset over time

### C. Benchmark Expansion
- Add more benchmark sources beyond swebench/mtrag/qmsum/pinchbench
- Target: general coding, creative writing, data analysis, Q&A
- Addresses the distribution mismatch between benchmark and real traffic

---

## Implementation Order (Once Data Available)

```
More data (#1 / #B) → Retrain classifier (#2) → Train MF (#4) → Deploy → Bandit (#5) → Cascade (#6)
```

Each step should be validated on a held-out test set via the E2E production path
(_v2_classify, NOT the eval shortcut) to get honest accuracy numbers.
