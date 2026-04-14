<p align="right"><a href="https://github.com/CommonstackAI/UncommonRoute/blob/main/README.md">English</a> | <strong>简体中文</strong></p>

<div align="center">

<h1>UncommonRoute</h1>

**自动模型路由，节省 77% 的 LLM 开销。**

大部分 LLM 预算都花在了不需要顶配模型的简单任务上。
UncommonRoute 自动选最便宜、又能完成任务的模型。

<br>

<a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.11+-3776ab?style=flat-square&logo=python&logoColor=white" alt="Python 3.11+"></a>
<a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-22c55e?style=flat-square" alt="MIT"></a>
<a href="#快速开始"><img src="https://img.shields.io/badge/上手-2_分钟-000?style=flat-square" alt="2分钟上手"></a>

</div>

<br>

<p align="center">
  <img src="docs/assets/hero-home.png" alt="UncommonRoute Dashboard" width="800">
</p>

<div align="center">

**[快速开始](#快速开始)** · **[工作原理](#工作原理)** · **[性能数据](#性能数据)** · **[Dashboard](#dashboard)** · **[配置](#配置)**

</div>

---

## 快速开始

```bash
pip install uncommon-route
```

```bash
export UNCOMMON_ROUTE_UPSTREAM="https://api.openai.com/v1"  # 或任何 OpenAI 兼容的 API
export UNCOMMON_ROUTE_API_KEY="your-key"
uncommon-route serve
```

把你的客户端指向代理——只需改一行：

```python
client = OpenAI(base_url="http://localhost:8403/v1")
resp = client.chat.completions.create(model="uncommon-route/auto", messages=msgs)
# → 简单任务走便宜模型，复杂任务走顶配模型
```

支持 **Codex**、**Claude Code**、**Cursor**、**OpenAI SDK** 和 **OpenClaw**。

<details>
<summary><strong>各客户端接入方式</strong></summary>

| 客户端 | 改动 |
|---|---|
| Codex / Cursor / OpenAI SDK | `export OPENAI_BASE_URL="http://localhost:8403/v1"` |
| Claude Code | `export ANTHROPIC_BASE_URL="http://localhost:8403"` |
| OpenClaw | 插件接入——详见 [openclaw.ai](https://openclaw.ai) |

</details>

---

## 工作原理

每个请求经过三个独立信号的分析，然后路由到最便宜的合适模型：

```
"你好"                     → 🟢 nano         $0.0008
"修一下第 3 行的拼写错误"   → 🟢 deepseek     $0.0012
"重构这个 500 行的模块"    → 🟠 sonnet       $0.0337
"设计一个分布式调度系统"    → 🔴 opus         $0.0562
```

| 信号 | 分析内容 | 耗时 |
|---|---|---|
| **元数据信号** | 对话轮次、工具调用、上下文深度 | <1ms |
| **语义信号** | 与已知任务模式的相似度（embedding） | ~10ms |
| **结构信号** | 文本复杂度特征（影子模式运行） | <1ms |

三个信号投票，集成模型决定 tier，路由器在对应 tier 中选最便宜的模型。遇到不确定的情况，宁可多花一点也不冒失败风险。

**越用越准。** 信号权重根据实际路由效果自动调整，向量索引随使用量增长，低置信度预测自动升级。

---

## 为什么做 v2

v1 分类器在干净的 benchmark 上跑出了 88.5% 的准确率，我们直接上了线。

然后拿真实的 Agent 对话一测——多轮交互、工具调用、上下文乱七八糟——准确率直接掉到 43%。超过一半的路由决策是错的。

我们没有选择打补丁，而是从零开始重写。

| | v1 | v2 |
|---|---|---|
| **准确率** | 43% | **72.7%** |
| **任务完成率** | 100%（作弊——永远选最贵的） | **90.3%**（真正的路由决策） |
| **成本节省** | 0% | **77%** |

我们把这些数据告诉你，是因为比起让你被数字震撼到，我们更希望你能信任它。

---

## 性能数据

基于 [CommonRouterBench](https://github.com/CommonstackAI/CommonRouterBench) 测试——762 条真实 Agent 任务轨迹，所有数据通过生产代码端到端测量。

| 指标 | 数值 |
|---|---|
| **成本节省** | **77%**（对比全程顶配） |
| **任务完成率** | **90.3%** |
| **路由延迟** | **<10ms** |
| **准确率** | **72.7%** tier 匹配 |

```bash
python scripts/eval_v2.py  # 自己跑一遍看看
```

---

## Dashboard

```bash
uncommon-route serve
# → http://localhost:8403/dashboard/
```

实时监控、交互式 Playground、成本追踪、路由配置——采用 Nothing Design 风格的深色界面。

---

## 配置

### 路由模式

| 模式 | 模型 ID | 行为 |
|---|---|---|
| **auto** | `uncommon-route/auto` | 平衡——最佳性价比 |
| **fast** | `uncommon-route/fast` | 省钱优先——最便宜的能用的 |
| **best** | `uncommon-route/best` | 质量优先——用最强模型 |

### 花费控制

```bash
uncommon-route spend set daily 20.00
uncommon-route spend status
```

### 自带 Key（BYOK）

```bash
uncommon-route provider add openai sk-your-key
uncommon-route provider add anthropic sk-ant-your-key
```

<details>
<summary><strong>所有环境变量</strong></summary>

| 变量 | 含义 |
|---|---|
| `UNCOMMON_ROUTE_UPSTREAM` | 上游 OpenAI 兼容 API 地址 |
| `UNCOMMON_ROUTE_API_KEY` | 上游 API key |
| `UNCOMMON_ROUTE_PORT` | 本地代理端口（默认 8403） |

</details>

---

## 隐私

完全在本地运行，除非你主动选择分享，否则不会有任何数据离开你的电脑。

```bash
uncommon-route telemetry status
```

---

## 开发

```bash
git clone https://github.com/CommonstackAI/UncommonRoute.git
cd UncommonRoute && pip install -e ".[dev]"
python -m pytest tests -v
```

---

## 许可证

MIT——详见 [LICENSE](LICENSE)。
