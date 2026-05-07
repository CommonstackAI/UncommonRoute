# UncommonRoute v2 — 训练贡献对比实验

**Bench**: CommonRouterBench `question_bank.jsonl`（2026-04-17 拉取，commit `73c8803`，970 行）
**Router**: UncommonRoute v0.6.0，2-signal A+C 架构（`risk_tolerance=0.5`）
**日期**: 2026-04-17

---

## 1. 实验目标

量化 **"在 v2 bench 上做训练"** 对综合得分的净贡献。
核心思路：架构、权重、阈值、conditional-B 激活规则**完全锁死**，只切 v2-bench 训练产物这一个开关。

---

## 2. 条件定义

| ID | 条件 | 架构 | 加载的 v2 产物 | 来源 |
|---|---|---|---|---|
| F | `always_high` | — | 无 | Floor — 永远给 high tier，不用 router |
| **U** | **UncommonRoute (untrained)** | 2-sig A+C | **无** | MetadataSignal + EmbeddingSignal（bge 加载，但 seeds/classifier/Platt 全未加载 → C 永远 abstain）|
| **T** | **UncommonRoute (trained, prod)** | 2-sig A+C | 全部 | `seed_embeddings.npy` + `embedding_classifier.pkl` + `meta_scaler.pkl` + `calibration_params.json` |
| S | Semantic (KNN) | bge + KNN | bench 训练 | 同学截图数据 |
| R | ClawRouter (Rules) | 关键词规则 | — | 同学截图数据 |

**U 与 T 完全同架构**（同 `make_v2_predictor(signals=2, shadow=False)` 入口），区别只在 `index_dir` 是否指向真实产物目录。T − U 就是我们训练的净贡献。

---

## 3. 主对比（full bank, 970 行）

> ⚠️ *Leakage 说明*：T 的 KNN 索引从 `train.jsonl` 编出，`train.jsonl` 是 `full_bank` 的子集（621/970）。因此 T-on-full_bank 包含一部分训练样本，是 *上界估计*。U、F、S、R 不存在此问题。Leakage-free 的净值见 §5。

| 指标 | F: always_high | **U: Untrained** | **T: Trained (prod)** | S: Semantic KNN | R: ClawRouter Rules |
|---|---:|---:|---:|---:|---:|
| 1. `case_pass_rate_percent` | 100.00 | 88.35 | **97.32** | 91.86 | 80.82 |
| 2. `case_exact_match_percent` | 17.53 | 61.13 | **93.40** | 78.76 | 11.75 |
| 3. `trajectory_pass_rate_percent` | 100.00 | 62.37 | **87.63** | 84.74 | 64.64 |
| 4. `cost_savings_score_percent` | 0.00 | 3.68 | 47.30 | **48.33** | 32.30 |
| 5. **`combined_score_percent`** | 54.38 | 53.88 | **81.41** | 75.92 | 47.38 |

### 3.1 训练净贡献 Δ = T − U

| 指标 | Untrained | Trained | **Δ** |
|---|---:|---:|---:|
| case_pass | 88.35 | 97.32 | **+8.97** |
| exact_match | 61.13 | 93.40 | **+32.27** |
| trajectory_pass | 62.37 | 87.63 | **+25.26** |
| cost_savings | 3.68 | 47.30 | **+43.62** |
| **combined** | **53.88** | **81.41** | **+27.53** |

**观察**：
- **训练把 combined 从 53.88 → 81.41**，净提升 27.53pp。
- 最大增量来自 `cost_savings`（+43.62pp）——未训版不敢降档、基本在"always-high"附近；训练后学到了"什么时候能降"。
- `exact_match` +32.27pp 证明训练学到了细粒度的 4 档区分能力，未训版只能靠 has_tools 粗分。
- 即便不训练，U 的 combined（53.88）也比 ClawRouter Rules（47.38）高 6.5pp——说明架构本身就有一定价值。
- 训练完的 T（81.41）比 Semantic KNN（75.92）高 5.49pp，比 ClawRouter（47.38）高 34.03pp。

### 3.2 Floor 对照

F（always_high）combined = 54.38，比 U（53.88）高 0.50pp。这意味着 **U 的水平几乎等于"永远给最贵的模型"**，只是交换了 savings 和 exact_match 的构成：U 省了点钱（3.68 vs 0）但 pass_rate/traj_pass 比 F 低。换句话说，**不训练的 UncommonRoute 和"不装 router"基本在同一条线上**——这正是我们要用训练来超越的 baseline。

---

## 4. Per-benchmark 拆分（full bank）

### 4.1 Tier match accuracy

| bench | rows | **U** | **T** | Δ |
|---|---:|---:|---:|---:|
| bfcl | 248 | 50.00% | 99.19% | +49.19pp |
| mtrag | 193 | 94.82% | 98.45% | +3.63pp |
| qmsum | 145 | 91.03% | 96.55% | +5.52pp |
| pinchbench | 48 | 29.17% | 91.67% | +62.50pp |
| swebench | 336 | 41.67% | 85.12% | +43.45pp |

### 4.2 Pass rate (pred ≥ gold)

| bench | rows | **U** | **T** |
|---|---:|---:|---:|
| bfcl | 248 | 97.58% | 99.19% |
| mtrag | 193 | 94.82% | 98.45% |
| qmsum | 145 | 91.03% | 96.55% |
| pinchbench | 48 | 97.92% | 95.83% |
| swebench | 336 | 75.30% | 95.83% |

### 4.3 Per-benchmark cost savings

| bench | rows | weight | **U** | **T** | Δ |
|---|---:|---:|---:|---:|---:|
| bfcl | 248 | 0.256 | +44.56 | **+93.31** | +48.75 |
| mtrag | 193 | 0.199 | +86.72 | **+93.79** | +7.07 |
| qmsum | 145 | 0.149 | +83.58 | **+92.22** | +8.64 |
| pinchbench | 48 | 0.049 | +36.64 | **+52.76** | +16.12 |
| swebench | 336 | 0.346 | **−113.38** | **−33.53** | +79.85 |

**关键**：swebench 在 U 下是 −113.38%（惊天大负）——规则 pred 完全压不住长轨迹的 retry penalty。训练把它拉到 −33.53%（虽然仍为负，但损失缩窄 3.4×）。swebench 占 34.6% macro 权重，这一项拉回就贡献了全局 savings 的 ~28pp。

---

## 5. Leakage-free 验证（holdout, 196 行）

holdout 与 `train.jsonl`、`calibration.jsonl` **完全不重合**——T 在 holdout 上的数字可视作泛化下界。U 和 F 在哪个 split 都一样（它们不依赖 split）。

| 指标 | F: always_high | **U: Untrained** | **T: Trained** | Δ (T−U) |
|---|---:|---:|---:|---:|
| case_pass | 100.00 | 89.80 | **91.33** | +1.53 |
| exact_match | 16.84 | 61.22 | **81.63** | +20.41 |
| trajectory_pass | 100.00 | 82.65 | **85.71** | +3.06 |
| cost_savings | 0.00 | 27.47 | **49.47** | +22.00 |
| **combined** | 54.21 | **65.29** | **77.04** | **+11.75** |

**观察**：
- Δ combined 从 full_bank 的 +27.53 收窄到 holdout 的 +11.75——前者有训练泄漏加成，后者是诚实的泛化增益。两者都显著为正，训练**不是**纯 overfitting。
- `exact_match` 在 holdout 仍 +20.41pp，`cost_savings` +22.00pp——说明训练学到的是可泛化的 tier 区分能力，不是死记硬背。
- holdout 上 U 的 combined（65.29）比 full_bank 上的 U（53.88）反而更高——因为 holdout 样本偏短、swebench 比例也稍低（34.2% vs 34.6%），规则兜底得分略好。

### 5.1 Per-benchmark（holdout）

| bench | rows | **U tier / pass / savings** | **T tier / pass / savings** |
|---|---:|---|---|
| bfcl | 50 | 58.00% / 98.00% / +34.16 | 96.00% / 96.00% / +91.02 |
| mtrag | 39 | 94.87% / 94.87% / +88.60 | 94.87% / 94.87% / +88.60 |
| qmsum | 30 | 90.00% / 90.00% / +86.15 | 90.00% / 90.00% / +86.15 |
| pinchbench | 10 | 20.00% / 100.00% / +55.19 | 70.00% / 80.00% / +34.56 |
| swebench | 67 | 37.31% / 79.10% / **−43.50** | 61.19% / 88.06% / **−18.51** |

swebench 的 U→T savings 提升（−43.50 → −18.51，+24.99pp）再次验证训练的主要作用是"压住长轨迹的 retry 损失"。

---

## 6. Paper takeaways

1. **训练的净贡献**：Combined score 在 full_bank 上 +27.53pp（53.88 → 81.41），在 leakage-free holdout 上 +11.75pp（65.29 → 77.04）。两个数字都 >> 0，且 holdout 上也足够大，支持"训练带来真实的泛化能力提升"这一 claim。
2. **贡献主要集中在 cost_savings 和 exact_match**——说明训练主要学会了"精细档位 + 合理降档"，而不是单纯靠 routing 架构。
3. **UncommonRoute trained（81.41）领先所有对比方法**：比 Semantic KNN +5.49，比 ClawRouter +34.03。
4. **UncommonRoute untrained（53.88）≈ always_high floor（54.38）**——说明未训版和"不装 router"差不多。这反向印证我们训练的 27.53pp 增益是结构性收益，不是架构 bias。
5. **Leakage 诚实声明**：T 在 full_bank 的数字因训练集 621 行重合而偏高。读者应以 holdout 的 77.04 作为泛化基线，以 full_bank 的 81.41 作为 in-distribution 上界。

---

## 7. 复现

```bash
cd /path/to/LLMRouterBench && pip install -e .
cd /path/to/UncommonRoute  && pip install -e ".[dev]"

python /tmp/eval_paper_comparison.py
# Outputs (6 files):
#   /tmp/ur_comparison_always_high_full_bank.json
#   /tmp/ur_comparison_untrained_full_bank.json
#   /tmp/ur_comparison_trained_full_bank.json
#   /tmp/ur_comparison_always_high_holdout.json
#   /tmp/ur_comparison_untrained_holdout.json
#   /tmp/ur_comparison_trained_holdout.json
```

**关键 entry points**：

- `main.eval.runner.evaluate_question_bank_rows`
- `main.eval.section11.compute_v2_scores`
- `scripts/eval_v2.py::make_v2_predictor(signals=2, shadow=False)`
  - **Untrained**: 传不存在的 `index_dir` → EmbeddingSignal 无 seeds/classifier → abstain；Platt 无文件 → 不加载
  - **Trained**: 传 `uncommon_route/data/v2_splits` → 全加载

Dataset commit: `LLMRouterBench@73c8803`（Downgrade 13 SWE-bench last-step GT labels after 3-model cascade validation）。

---

## 附录 A: Floor 条件的解释

`always_high` 总是预测 tier 3（high）：
- `case_pass_rate = 100%`（pred=3 ≥ gold 恒成立）
- `trajectory_pass_rate = 100%`（永远不 under-predict → 无 retry）
- `cost_savings = 0`（N_usd = D_usd − spend = 0，因为 spend = D_usd）
- `exact_match` = bench 中 gold=3 的比例（full_bank 17.53%，holdout 16.84%）

这条 baseline 的意义：**告诉读者"不装 router、默认用最贵模型"长什么样**。

---

## 附录 B: `scores_v2` 原始 JSON 字段索引

每个 JSON 除 `scores_v2` 外包含：

- `classifier`, `shard`, `sample_mode`, `sampled`, `seed`
- `proportional_quotas`, `benchmark_counts`
- `exact_match`, `tier_match_accuracy`, `accuracy_excluding_errors`
- `api_errors`, `valid_response_rate`
- `section_11`（legacy），`router_accounting`（legacy）
- `by_benchmark`（`tier_match_accuracy` / `pass_rate` per benchmark）
- `scores_v2.by_benchmark`（每个 benchmark 的 `row_count`, `step_count`, `failed_trajectory_count`, `retry_penalty_usd`, `D_usd`, `N_usd`, `cost_savings_score_percent`, `weight_in_global_cost_savings`）
- `errors`, `rows`（逐行明细）
