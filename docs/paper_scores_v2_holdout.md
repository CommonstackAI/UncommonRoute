# UncommonRoute v2 — scores_v2 实验结果（holdout，未使用 benchmark 训练）

**Bench**: CommonRouterBench `question_bank.jsonl`（2026-04-17 拉取，commit `73c8803`，全量 970 行）
**Router**: UncommonRoute v0.6.0，生产配置（2-signal A+C + 条件激活 B，`risk_tolerance=0.5`，Platt calibrator）
**评估集**: `uncommon_route/data/v2_splits/holdout.jsonl` — 196 行，与 embedding seed 索引及 calibration 数据**完全无重合**
**日期**: 2026-04-17

---

## 1. 设置

970 行 CommonRouterBench 被切分为三份：

| 子集 | 行数 | 用途 |
|---|---:|---|
| `train.jsonl` | 621 | 构建 embedding KNN seed 索引（`bge-small-en-v1.5`） |
| `calibration.jsonl` | 153 | 拟合 Platt calibrator |
| **`holdout.jsonl`** | **196** | **验证集（本文档全部指标基于此）** |

- **评估入口**：`main.eval.runner.evaluate_question_bank_rows` + `main.eval.section11.compute_v2_scores`
- **Predictor**：`FunctionPredictor` 包装 `scripts/eval_v2.py::make_v2_predictor(signals=2, shadow=False)`
- **Shadow tracker**：关（确保确定性）

---

## 2. 最终结果（`scores_v2`，holdout 196 行）

| 指标 | 数值 |
|---|---:|
| 1. `case_pass_rate_percent` | **91.33** |
| 2. `case_exact_match_percent` | **81.63** |
| 3. `trajectory_pass_rate_percent` | **85.71** |
| 4. `cost_savings_score_percent` | **49.47** |
| 5. `combined_score_percent` | **77.04** |

### 2.1 API / 有效率

| 字段 | 数值 |
|---|---:|
| `sampled` | 196 |
| `api_errors` | 0 |
| `valid_response_rate` | 100.0% |
| `tier_match_accuracy` | 81.63% |

---

## 3. 按 benchmark 拆分（holdout）

### 3.1 Tier match & pass rate

| `benchmark` | rows | `tier_match_accuracy` | `pass_rate` (pred ≥ gold) |
|---|---:|---:|---:|
| bfcl | 50 | 96.00% | 96.00% |
| mtrag | 39 | 94.87% | 94.87% |
| qmsum | 30 | 90.00% | 90.00% |
| pinchbench | 10 | 70.00% | 80.00% |
| swebench | 67 | 61.19% | 88.06% |

### 3.2 Per-benchmark cost savings

| `benchmark` | rows | `cost_savings_score_percent` | macro 权重 |
|---|---:|---:|---:|
| bfcl | 50 | **91.02** | 0.255 |
| mtrag | 39 | **88.60** | 0.199 |
| qmsum | 30 | **86.15** | 0.153 |
| pinchbench | 10 | 34.56 | 0.051 |
| swebench | 67 | **−18.51** | 0.342 |

**观察**：除 swebench 外的四个 bench 都跑到 34–91% 节省。swebench 是唯一负贡献——该 bench 高 tier 样本极多（gold=high 占 33 / 67 ≈ 49%），一次决策出错就被 retry penalty × `high` baseline 重罚；34.2% 的 macro 权重叠上去后，全局 `cost_savings` 从 70+ 被拉到 49.47。

---

## 4. Cost savings 为什么是 49.47 而非更高：SWE-bench 的 trajectory retry penalty

核心原因是 **SWE-bench 被 trajectory retry penalty 一巴掌拍死**，其他 bench 的 86–91% savings 被它一家的 −18.51% 平均掉了。

### 4.1 具体机制（holdout 上的 SWE-bench）

| 字段 | 值 | 说明 |
|---|---:|---|
| rows | 67 | 占 34.2% macro 权重 |
| trajectories | 8 | 平均 ~8 步/轨 |
| **failed_trajectories** | **8/8 = 100%** | 每一条轨迹至少有一步踩坑 |
| under-predict 步数 | 8/67 = 11.9% | 其中 3/8 发生在 gold=3 |
| retry_penalty_usd | 0.567 | = D 的 28% |
| D_usd (baseline 总账单) | 2.037 | |
| N_usd | **−0.377** | 负的——**我们比 always-high 还贵** |

按 gold tier 细分的预测分布：

| `gold_tier_id` | rows | exact | under-predict | over-predict |
|---|---:|---:|---:|---:|
| 0 (low) | 19 | 11 | 0 | 8 |
| 1 (mid) | 7 | 0 | 1 | 6 |
| 2 (mid_high) | 8 | 0 | 4 | 4 |
| 3 (high) | 33 | 30 | 3 | 0 |

### 4.2 三重因果

1. **长轨迹放大错误**：一条多步轨迹只要错 1 步 → 整条轨按 high tier 重跑一遍 → retry penalty = −N × baseline。
2. **Gold=high 占 49%（33/67）是 savings 死区**：pred 对了也只省 0（baseline=high=pred），pred 错了（under）就被 retry penalty 重罚——**只有下行风险，没有上行收益**。
3. **8 个 under-predict 分散到 8 条轨上**：基本每条轨都挨一刀 → 100% trajectory fail rate → 每条轨都触发 retry penalty。

### 4.3 优化方向（future work）

SWE-bench 上加一个 conservative floor——gold=high 的分布特征（工具调用深度、diff 复杂度）触发时直接强制 pred=high，用"永远不 under-predict"换 trajectory pass rate。把 swebench 从 −18.51% 拉到 ~0，全局 `cost_savings_score` 会从 49.47 跳到 ~55+，`combined_score` 从 77.04 升到 ~79。

---

## 5. 复现

```bash
# Prereq: 安装 CommonRouterBench 和 UncommonRoute（editable）
cd /path/to/LLMRouterBench && pip install -e .
cd /path/to/UncommonRoute  && pip install -e ".[dev]"

# 跑 holdout 评估
python /tmp/eval_paper_v2.py
# 输出: /tmp/ur_scores_v2_holdout.json
```

关键 entry points：
- `main.eval.runner.evaluate_question_bank_rows`
- `main.eval.section11.compute_v2_scores`
- `scripts/eval_v2.py::make_v2_predictor(signals=2, shadow=False)`

Dataset commit: `LLMRouterBench@73c8803`（Downgrade 13 SWE-bench last-step GT labels after 3-model cascade validation）。

---

## 附录 A: `scores_v2` 原始 JSON 字段索引

`/tmp/ur_scores_v2_holdout.json` 除 `scores_v2` 外包含：

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
