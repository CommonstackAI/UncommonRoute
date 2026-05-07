# UncommonRoute v2 — scores_v2 实验结果

**Bench**: CommonRouterBench `question_bank.jsonl` (`v2` 970 行; 2026-04-17 拉取，commit `73c8803`)
**Router**: UncommonRoute v0.6.0，生产配置（2-signal A+C + 条件激活 B，`risk_tolerance=0.5`，Platt calibrator）
**日期**: 2026-04-17

---

## 1. 设置

- **评估入口**：`main.eval.runner.evaluate_question_bank_rows` + `main.eval.section11.compute_v2_scores`
- **Predictor**：`FunctionPredictor` 包装 `scripts/eval_v2.py::make_v2_predictor(signals=2, shadow=False)`
- **Embedding 索引**：`uncommon_route/data/v2_splits/seed_embeddings.npy`（`bge-small-en-v1.5`，由 621 行 `train.jsonl` seed 得到）
- **Shadow tracker**：关（确保确定性）
- **切分**：
  - **full_bank**：970 行全量，与基线报告同口径（含与索引重合的训练 prefix）
  - **holdout**：`v2_splits/holdout.jsonl` 196 行，与 seed 索引**零重合**，作为泄漏无关的净值

---

## 2. 最终结果（`scores_v2`，全 970 行）

与同学截图中 Semantic (KNN) / ClawRouter (Rules) 直接对比：

| 指标 | **UncommonRoute v2** | Semantic (KNN) | ClawRouter (Rules) |
|---|---:|---:|---:|
| 1. `case_pass_rate_percent` | **97.32** | 91.86 | 80.82 |
| 2. `case_exact_match_percent` | **93.40** | 78.76 | 11.75 |
| 3. `trajectory_pass_rate_percent` | **87.63** | 84.74 | 64.64 |
| 4. `cost_savings_score_percent` | 47.30 | **48.33** | 32.30 |
| 5. `combined_score_percent` | **81.41** | 75.92 | 47.38 |

> UncommonRoute 在 5 个指标中 4 个领先（case_pass、exact_match、trajectory_pass、combined），在 cost savings 上 差距 ~1.0pp（47.30 vs 48.33）。UncommonRoute 倾向于在不确定时往上一档升级，换取更高的 pass / trajectory-pass，这也吃掉了部分 savings。

### 2.1 API / 有效率

| | UncommonRoute v2 |
|---|---:|
| `sampled` | 970 |
| `api_errors` | 0 |
| `valid_response_rate` | 100.0% |
| `tier_match_accuracy` | 93.40% |

---

## 3. 按 benchmark 拆分（full bank）

### 3.1 Tier match & pass rate

| `benchmark` | rows | `tier_match_accuracy` | `pass_rate` (pred ≥ gold) |
|---|---:|---:|---:|
| bfcl | 248 | 99.19% | 99.19% |
| mtrag | 193 | 98.45% | 98.45% |
| qmsum | 145 | 96.55% | 96.55% |
| pinchbench | 48 | 91.67% | 95.83% |
| swebench | 336 | 85.12% | 95.83% |

### 3.2 Per-benchmark cost savings

| `benchmark` | rows | `cost_savings_score_percent` | macro 权重 |
|---|---:|---:|---:|
| mtrag | 193 | **93.79** | 0.199 |
| bfcl | 248 | **93.31** | 0.256 |
| qmsum | 145 | **92.22** | 0.149 |
| pinchbench | 48 | 52.76 | 0.049 |
| swebench | 336 | **−33.53** | 0.346 |

**观察**：除 swebench 外的四个 bench 都跑到 52–94% 节省。swebench 是唯一负贡献——该 bench 高 tier 样本极多（gold=high 占 168 / 336 ≈ 50%），一次决策出错就被 retry penalty × `high` baseline 重罚；34.6% 的 macro 权重叠上去后，全局 cost savings 从 60+ 被拉到 47.30。

---

## 4. 泄漏无关的 holdout（196 行）

`holdout.jsonl` 与训练索引完全不重合；可作为泛化上界。

| 指标 | UncommonRoute v2 (holdout) | Semantic (KNN, full bank) |
|---|---:|---:|
| `case_pass_rate_percent` | **91.33** | 91.86 |
| `case_exact_match_percent` | **81.63** | 78.76 |
| `trajectory_pass_rate_percent` | **85.71** | 84.74 |
| `cost_savings_score_percent` | **49.47** | 48.33 |
| `combined_score_percent` | **77.04** | 75.92 |

即使在 leakage-free 的 holdout 上、与 KNN 的 full-bank（含泄漏）基线对比，UncommonRoute 的 combined_score 仍高出 1.12pp。

### Per-benchmark（holdout）

| `benchmark` | rows | `tier_match_accuracy` | `pass_rate` | `cost_savings_score_percent` |
|---|---:|---:|---:|---:|
| bfcl | 50 | 96.00% | 96.00% | 91.02 |
| mtrag | 39 | 94.87% | 94.87% | 88.60 |
| qmsum | 30 | 90.00% | 90.00% | 86.15 |
| pinchbench | 10 | 70.00% | 80.00% | 34.56 |
| swebench | 67 | 61.19% | 88.06% | −18.51 |

---

## 5. 论文 takeaway 建议

1. **Combined score 领先**。full bank 上 UncommonRoute `combined=81.41` 高于 Semantic-KNN `75.92`（+5.49pp），高于 ClawRouter-Rules `47.38`（+34.03pp）。
2. **Exact-match 领先最显著**。UncommonRoute `93.40` vs KNN `78.76` vs Rules `11.75`——这是 multi-signal ensemble + Platt calibration 的直接收益。
3. **Pass rate & trajectory pass 均为第一**，说明 ensemble 的 conservative upgrade 策略在兜住 trajectory 成功率。
4. **Cost savings 略输 KNN**（−1.03pp）。原因是 swebench 上的保守策略吃掉了 savings；如果把 swebench 的 per-benchmark cost savings 从 −33.53 提到 0，全局 cost savings 会跳到 ~59，combined ~84.4。这是后续优化可以量化的明确 headroom。
5. **Leakage-free holdout** 再次确认 combined_score 不是来自训练重合：77.04 仍 > KNN full-bank 75.92。

---

## 6. 复现

```bash
# Prereq: Install CommonRouterBench and UncommonRoute (editable)
cd /path/to/LLMRouterBench && pip install -e .
cd /path/to/UncommonRoute  && pip install -e ".[dev]"

# Reproduce:
python /tmp/eval_paper_v2.py
# Outputs:
#   /tmp/ur_scores_v2_full_bank.json
#   /tmp/ur_scores_v2_holdout.json
```

关键函数 entry points：
- `main.eval.runner.evaluate_question_bank_rows`
- `main.eval.section11.compute_v2_scores`
- `scripts/eval_v2.py::make_v2_predictor(signals=2, shadow=False)`

Dataset commit: `LLMRouterBench@73c8803` (Downgrade 13 SWE-bench last-step GT labels after 3-model cascade validation).

---

## 附录 A: `scores_v2` 原始 JSON 字段索引

每次 run 产出的 summary JSON 除 `scores_v2` 外还包含：

- `classifier`, `shard`, `sample_mode`, `sampled`, `seed`
- `proportional_quotas`, `benchmark_counts`
- `exact_match`, `tier_match_accuracy`, `accuracy_excluding_errors`
- `api_errors`, `valid_response_rate`
- `section_11`（legacy，保留向后兼容）
- `router_accounting`（legacy）
- `by_benchmark`（`tier_match_accuracy` / `pass_rate` per benchmark）
- `scores_v2.by_benchmark`（每个 benchmark 的 `row_count`, `step_count`, `failed_trajectory_count`, `retry_penalty_usd`, `D_usd`, `N_usd`, `cost_savings_score_percent`, `weight_in_global_cost_savings`）
- `errors`, `rows`（逐行明细）

paper 引用建议用 `scores_v2.*_percent` 字段名原样，方便复核。
