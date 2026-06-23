# 静态 Benchmark 的训练/校准/验证集切分方法

本文档说明 UncommonRoute v2 在静态 CommonRouterBench / LLMRouterBench 数据集上的数据切分方式。该切分用于训练路由器的难度档位预测组件，并用于报告无训练重合的 held-out 结果。

## 数据来源

- 数据集：`CommonRouterBench question_bank.jsonl`
- 样本数：970 条
- 数据版本：`LLMRouterBench@73c8803`
- 数据内容：真实 agent task trajectories，覆盖 SWE-Bench、BFCL、MT-RAG、QMSum 和 PinchBench

## 切分目标

我们没有将数据简单随机二分，而是切分为三部分：

| split | 行数 | 用途 |
|---|---:|---|
| `train.jsonl` | 621 | 训练路由分类器，并构建 embedding seed index |
| `calibration.jsonl` | 153 | 拟合置信度校准参数 |
| `holdout.jsonl` | 196 | 最终报告用的 held-out 验证集 |

其中，`holdout.jsonl` 不参与分类器训练，也不参与置信度校准。论文中报告的 leakage-free 结果均基于该 split。

## 切分算法

切分脚本为：

```text
scripts/split_data.py
```

核心方法是固定随机种子的分层三分切分：

```text
seed = 42
train_frac = 0.64
calibration_frac = 0.16
holdout_frac = remaining approximately 0.20
stratification_key = benchmark + target_tier
```

具体步骤如下：

1. 对所有样本按 `benchmark` 和 `target_tier` 的组合分桶，例如 `swebench_high`、`bfcl_low`、`qmsum_mid`。
2. 对每个桶内部使用固定随机种子 `42` 进行 shuffle。
3. 每个桶内约 64% 样本进入 `train`，约 16% 进入 `calibration`，剩余样本进入 `holdout`。
4. 对于样本数大于 1 的桶，切分逻辑会尽量保证该桶在 `holdout` 中至少保留 1 条样本。
5. 完成所有桶内切分后，再分别 shuffle `train`、`calibration` 和 `holdout`。

该设计的目的是避免某个 benchmark 或某个目标难度档位只出现在训练集中，而在 holdout 中缺失。换句话说，held-out 评估仍然保留了 benchmark 类型和 target tier 分布上的覆盖性。

## 实际 split 分布

### 按 benchmark 分布

| split | BFCL | MT-RAG | PinchBench | QMSum | SWE-Bench | total |
|---|---:|---:|---:|---:|---:|---:|
| `train` | 159 | 124 | 31 | 92 | 215 | 621 |
| `calibration` | 39 | 30 | 7 | 23 | 54 | 153 |
| `holdout` | 50 | 39 | 10 | 30 | 67 | 196 |

### 按 target tier 分布

| split | low | mid | mid_high | high | total |
|---|---:|---:|---:|---:|---:|
| `train` | 440 | 39 | 32 | 110 | 621 |
| `calibration` | 110 | 9 | 7 | 27 | 153 |
| `holdout` | 139 | 14 | 10 | 33 | 196 |

### Holdout 按 benchmark 和 target tier 的交叉分布

| benchmark | low | mid | mid_high | high | total |
|---|---:|---:|---:|---:|---:|
| BFCL | 48 | 2 | 0 | 0 | 50 |
| MT-RAG | 37 | 2 | 0 | 0 | 39 |
| PinchBench | 8 | 1 | 1 | 0 | 10 |
| QMSum | 27 | 2 | 1 | 0 | 30 |
| SWE-Bench | 19 | 7 | 8 | 33 | 67 |

## 训练与评估边界

训练阶段只使用 `train.jsonl` 中的样本来拟合路由分类器或构建 embedding index。置信度校准只使用 `calibration.jsonl`。最终报告的 holdout 指标只在 `holdout.jsonl` 上计算。

因此，`holdout` 结果可以被视为无训练样本重合的泛化评估；而在完整 970 条 full-bank 上的结果包含训练 split，因此更适合作为 in-distribution 上界，而不是严格的 held-out 泛化指标。
