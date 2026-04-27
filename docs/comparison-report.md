# ClawRouter (原版) vs UncommonRoute — 详细对比报告

> **HISTORICAL — v1 era, superseded by v2.**
>
> 此报告是 UncommonRoute v1 结构分类器在 3378 条手写用例上的评测，不反映当前 v2 系统的表现。
> v2 的最新指标（78% tier accuracy / 93.4% pass rate / 82% cost savings 在 CommonRouterBench 196 条 holdout 上）见 [README.md](../README.md)。
> v1 在真实 agent trace 上只有 43% 准确率，这是我们重建 v2 的原因。
>
> 保留此文档仅作为历史记录。

数据集: 3378 条手写用例 | 16 种语言 | 38 个类别

## 1. 总体指标


| 指标        | ClawRouter | UncommonRoute              | 提升          |
| --------- | ---------- | -------------------------- | ----------- |
| **总体准确率** | 41.7%      | **98.4%**                  | **+56.7pp** |
| 平均延迟      | 24us       | 356us                      | 15x         |
| 外部依赖      | viem (npm) | 零                          | —           |
| 实现语言      | TypeScript | Python                     | —           |
| 评分维度      | 14 (关键词)   | 39 (结构+Unicode+关键词+n-gram) | +25         |
| 学习模型      | 无          | Averaged Perceptron        | —           |
| 在线学习      | 不支持        | model.update()             | —           |


## 2. 各 Tier 分类指标


| Tier      | 样本数  | ClawRouter Precision | UncommonRoute Precision | ClawRouter Recall | UncommonRoute Recall | ClawRouter F1 | UncommonRoute F1 |
| --------- | ---- | -------------------- | ----------------------- | ----------------- | -------------------- | ------------- | ---------------- |
| SIMPLE    | 1085 | 41.1%                | **99.4%**               | 95.7%             | **99.0%**            | 57.5%         | **99.2%**        |
| MEDIUM    | 1231 | 37.8%                | **97.4%**               | 23.6%             | **98.8%**            | 29.1%         | **98.1%**        |
| COMPLEX   | 557  | 0.0%                 | **98.0%**               | 0.0%              | **99.3%**            | 0.0%          | **98.7%**        |
| REASONING | 505  | 100.0%               | **99.2%**               | 16.0%             | **95.4%**            | 27.6%         | **97.3%**        |


## 3. 混淆矩阵

### ClawRouter (原版)


|           | SIMPLE   | MEDIUM  | COMPLEX | REASONING |
| --------- | -------- | ------- | ------- | --------- |
| SIMPLE    | **1038** | 47      | 0       | 0         |
| MEDIUM    | 940      | **291** | 0       | 0         |
| COMPLEX   | 439      | 118     | **0**   | 0         |
| REASONING | 110      | 314     | 0       | **81**    |


### UncommonRoute


|           | SIMPLE   | MEDIUM   | COMPLEX | REASONING |
| --------- | -------- | -------- | ------- | --------- |
| SIMPLE    | **1074** | 10       | 1       | 0         |
| MEDIUM    | 6        | **1216** | 5       | 4         |
| COMPLEX   | 0        | 4        | **553** | 0         |
| REASONING | 0        | 18       | 5       | **482**   |


## 4. 各语言准确率对比


| 语言    | 样本数  | ClawRouter | UncommonRoute | 提升       |
| ----- | ---- | ---------- | ------------- | -------- |
| en    | 1913 | 43.6%      | **98.7%**     | +55.1pp  |
| zh    | 288  | 44.1%      | **97.2%**     | +53.1pp  |
| ja    | 175  | 37.7%      | **99.4%**     | +61.7pp  |
| ko    | 159  | 30.2%      | **98.7%**     | +68.6pp  |
| ru    | 129  | 38.8%      | **97.7%**     | +58.9pp  |
| ar    | 107  | 38.3%      | **98.1%**     | +59.8pp  |
| es    | 105  | 39.0%      | **99.0%**     | +60.0pp  |
| pt    | 105  | 39.0%      | **100.0%**    | +61.0pp  |
| de    | 100  | 37.0%      | **99.0%**     | +62.0pp  |
| fr    | 96   | 37.5%      | **100.0%**    | +62.5pp  |
| hi    | 54   | 44.4%      | **96.3%**     | +51.9pp  |
| tr    | 54   | 46.3%      | **96.3%**     | +50.0pp  |
| pl    | 46   | 43.5%      | **91.3%**     | +47.8pp  |
| vi    | 45   | 44.4%      | **93.3%**     | +48.9pp  |
| mixed | 1    | 0.0%       | **100.0%**    | +100.0pp |
| cs    | 1    | 0.0%       | **100.0%**    | +100.0pp |


## 5. 各类别准确率对比


| 类别                | 期望 Tier   | 样本数 | ClawRouter | UncommonRoute | 提升     |
| ----------------- | --------- | --- | ---------- | ------------- | ------ |
| code-snippet-qa   | SIMPLE    | 2   | 0.0%       | **100.0%**    | +100pp |
| edge-short        | MEDIUM    | 4   | 0.0%       | **100.0%**    | +100pp |
| system-design     | COMPLEX   | 345 | 0.0%       | **99.7%**     | +100pp |
| complex-code      | COMPLEX   | 105 | 0.0%       | **99.0%**     | +99pp  |
| architecture      | COMPLEX   | 22  | 0.0%       | **100.0%**    | +100pp |
| security-analysis | COMPLEX   | 18  | 0.0%       | **94.4%**     | +94pp  |
| infrastructure    | COMPLEX   | 16  | 0.0%       | **100.0%**    | +100pp |
| ml-pipeline       | COMPLEX   | 28  | 0.0%       | **100.0%**    | +100pp |
| migration         | COMPLEX   | 13  | 0.0%       | **100.0%**    | +100pp |
| performance       | COMPLEX   | 9   | 0.0%       | **88.9%**     | +89pp  |
| algorithm-proof   | REASONING | 101 | 1.0%       | **97.0%**     | +96pp  |
| comparison        | MEDIUM    | 142 | 4.9%       | **100.0%**    | +95pp  |
| explanation       | MEDIUM    | 313 | 6.4%       | **98.4%**     | +92pp  |
| code-review       | MEDIUM    | 99  | 11.1%      | **99.0%**     | +88pp  |
| formal-proof      | REASONING | 214 | 11.7%      | **94.9%**     | +83pp  |
| rewrite           | MEDIUM    | 24  | 12.5%      | **100.0%**    | +88pp  |
| logic-puzzle      | REASONING | 16  | 12.5%      | **100.0%**    | +88pp  |
| debugging         | MEDIUM    | 95  | 13.7%      | **96.8%**     | +83pp  |
| summary           | MEDIUM    | 86  | 15.1%      | **97.7%**     | +83pp  |
| game-theory       | REASONING | 38  | 15.8%      | **92.1%**     | +76pp  |
| formal-logic      | REASONING | 27  | 18.5%      | **100.0%**    | +81pp  |
| extraction        | MEDIUM    | 18  | 22.2%      | **100.0%**    | +78pp  |
| agentic-task      | MEDIUM    | 13  | 23.1%      | **92.3%**     | +69pp  |
| math-derivation   | REASONING | 107 | 38.3%      | **94.4%**     | +56pp  |
| testing           | MEDIUM    | 51  | 39.2%      | **100.0%**    | +61pp  |
| data-analysis     | MEDIUM    | 10  | 40.0%      | **100.0%**    | +60pp  |
| simple-code       | MEDIUM    | 286 | 46.2%      | **99.0%**     | +53pp  |
| documentation     | MEDIUM    | 10  | 50.0%      | **100.0%**    | +50pp  |
| math-word-problem | REASONING | 2   | 50.0%      | **100.0%**    | +50pp  |
| classification    | MEDIUM    | 13  | 61.5%      | **100.0%**    | +38pp  |
| brainstorming     | MEDIUM    | 46  | 63.0%      | **100.0%**    | +37pp  |
| structured-output | MEDIUM    | 9   | 77.8%      | **100.0%**    | +22pp  |
| definition        | SIMPLE    | 64  | 85.9%      | **100.0%**    | +14pp  |
| creative          | MEDIUM    | 13  | 92.3%      | **100.0%**    | +8pp   |
| factual-qa        | SIMPLE    | 902 | 96.0%      | **98.9%**     | +3pp   |
| translation       | SIMPLE    | 39  | 100.0%     | **97.4%**     | +-3pp  |
| greeting          | SIMPLE    | 73  | 100.0%     | **100.0%**    | +0pp   |
| edge-noise        | SIMPLE    | 5   | 100.0%     | **100.0%**    | +0pp   |


## 6. 一致性分析


| 情况                 | 数量   | 比例    |
| ------------------ | ---- | ----- |
| 两者都正确              | 1396 | 41.3% |
| 仅 UncommonRoute 正确 | 1929 | 57.1% |
| 仅 ClawRouter 正确    | 14   | 0.4%  |
| 两者都错误              | 39   | 1.2%  |


## 7. ClawRouter 主要错误模式


| 错误方向             | 数量  | 说明                             |
| ---------------- | --- | ------------------------------ |
| MEDIUM→SIMPLE    | 940 | MEDIUM 被短文本惩罚                  |
| COMPLEX→SIMPLE   | 439 | COMPLEX 被 tokenCount 拉到 SIMPLE |
| REASONING→MEDIUM | 314 | REASONING 降级                   |
| COMPLEX→MEDIUM   | 118 | mediumComplex 边界不可达            |
| REASONING→SIMPLE | 110 | REASONING 未识别                  |
| SIMPLE→MEDIUM    | 47  | SIMPLE 被推高                     |


## 8. 架构差异


| 维度         | ClawRouter           | UncommonRoute                                  |
| ---------- | -------------------- | ---------------------------------------------- |
| 分类方法       | 14 维关键词线性加权          | trivial override + Perceptron 模型 + 规则 fallback |
| Token 估算   | length/4 (CJK 偏低 3x) | 脚本感知 (Latin/CJK/Arabic 各自系数)                   |
| 长度特征       | 二值化 (<50 → -1.0)     | log 连续映射 (无硬阈值)                                |
| COMPLEX 检测 | 纯关键词 (F1=0%)         | 枚举密度 + 概念密度 + 需求短语 + 模型学习                      |
| 跨语言能力      | 手工多语言关键词列表           | 12 结构特征 + 15 Unicode block 特征 + 模型             |
| 学习能力       | 无                    | Averaged Perceptron，在线增量学习                     |
| 信号密度       | 大多数维度返回 0            | 结构特征在所有输入上都有值                                  |


