<p align="right"><a href="https://github.com/CommonstackAI/UncommonRoute/blob/main/README.md">English</a> | <strong>简体中文</strong></p>

<div align="center">

<h1>UncommonRoute</h1>

<p><strong>每个 LLM 请求，自动路由到最便宜的能用的模型。</strong></p>

<p>
你 77% 的 LLM 预算花在了根本不需要顶配模型的简单任务上。<br>
UncommonRoute 把这事量化了，然后自动解决。
</p>

</div>
 
<table align="center">
<tr>
<td align="center"><h3>77%</h3><sub>成本节省（实测）</sub></td>
<td align="center"><h3>90.3%</h3><sub>任务完成率</sub></td>
<td align="center"><h3><10ms</h3><sub>路由额外延迟</sub></td>
<td align="center"><h3>407</h3><sub>测试通过</sub></td>
</tr>
</table>

<div align="center">
  
<p>
适用于 <strong>Codex</strong>、<strong>Claude Code</strong>、<strong>Cursor</strong>、<strong>OpenAI SDK</strong> 和 <strong>OpenClaw</strong>。
</p>

<p>
<a href="#两分钟上手"><strong>快速开始</strong></a> ·
<a href="#工作原理"><strong>工作原理</strong></a> ·
<a href="#试一试"><strong>Playground</strong></a> ·
<a href="#性能数据"><strong>性能数据</strong></a>
</p>

<a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.11+-3776ab?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11+"></a>&nbsp;
<a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-22c55e?style=for-the-badge" alt="MIT"></a>&nbsp;
<a href="#两分钟上手"><img src="https://img.shields.io/badge/Claude_Code-Ready-f97316?style=for-the-badge&logo=anthropic&logoColor=white" alt="Claude Code"></a>&nbsp;
<a href="#两分钟上手"><img src="https://img.shields.io/badge/Codex-Ready-412991?style=for-the-badge&logo=openai&logoColor=white" alt="Codex"></a>&nbsp;
<a href="#两分钟上手"><img src="https://img.shields.io/badge/Cursor-Compatible-007acc?style=for-the-badge&logo=visual-studio-code&logoColor=white" alt="Cursor"></a>&nbsp;
<a href="https://openclaw.ai"><img src="https://img.shields.io/badge/OpenClaw-Plugin-e11d48?style=for-the-badge" alt="OpenClaw"></a>

</div>

---

## 直接看效果

```text
"你好"                     → 🟢 gpt-4.1-nano       ($0.0008)
"修一下第 3 行的拼写错误"   → 🟢 deepseek-chat       ($0.0012)
"把这段日志总结成一句话"    → 🟢 minimax-m2.7        ($0.0097)
"重构这个 500 行的模块"    → 🟠 claude-sonnet-4.6   ($0.0337)
"设计一个分布式调度系统"    → 🔴 claude-opus-4.6     ($0.0562)
```

同一个接口，路由器自动选模型，你只管写代码。

---

## 痛点

**全程顶配 = 简单任务也在烧钱。**
你接了 Claude Opus，agent 循环里的工具调用、状态检查、日志总结全部按 Opus 的价格扣。一个 session 下来 $50+，但其中大部分请求，$0.001 的模型就能搞定。

**日常用便宜模型 = 难的任务只能硬撑。**
你买了 DeepSeek 的 Coding Plan，日常写代码够用，但遇到复杂架构设计、大规模重构，你知道应该让 Opus 来，却没有一个自动切换的机制。

**你花的每 100 块 API 费用，有 77 块是白烧的。** 我们量过。

UncommonRoute 解决的就是这件事：简单的往下走，复杂的往上走。你不用管。

```text
你的客户端
  (Codex / Claude Code / Cursor / OpenAI SDK / OpenClaw)
            |
            v
     UncommonRoute
   (跑在你本地)
            |
            v
    你的上游 API
 (Commonstack / OpenAI / Ollama / vLLM / ...)
```

---

## 为什么有 v2

v1 分类器在干净的 benchmark 数据上 88.5% 准确率。我们上了线。

然后拿真实 agent 对话一测——多轮、tool-calling、上下文乱七八糟——准确率掉到 43%。超过一半的路由决策是错的。

我们没打补丁。我们从零重写。

v2 用多信号集成分析对话结构，不只是看文本表面。在 [LLMRouterBench](../LLMRouterBench) 的 762 条真实 agent 任务轨迹上，通过生产代码路径端到端测试：

| | v1 | v2 |
|---|---|---|
| **准确率** | 43% | **73.6%** |
| **任务通过率** | 100%（作弊——总选最贵的） | **90.3%**（真实路由决策） |
| **成本节省** | 0%（总选最贵的当然不省钱） | **77%** |

我们把这些告诉你，是因为我们希望你信任我们的数据，而不只是被数据震撼到。

---

## 工作原理

每个请求经过三个独立信号分析：

1. **对话结构** — 对话有多深？有没有用工具？来回了几轮？（<1ms）
2. **语义匹配** — 这个请求和我们见过的已知任务有多像？（~10ms）
3. **文本分析** — 提示词本身的结构特征（影子模式运行，有用时自动启用）

三个信号投票，路由器选能搞定这个任务的最便宜模型。意见不一致时偏保守——宁可多花一点，不冒任务失败的风险。

**越用越准。** 信号权重根据路由结果自动调整。向量索引随使用增长。效果差的信号自动降权。

---

## 试一试

不需要 API key——看看路由器会怎么处理任何 prompt：

```bash
uncommon-route explain "写一个 Python 函数验证邮箱格式"

  Tier: mid (id=1)
  Confidence: 85.0%
  Method: direct
  Complexity: 0.40

  Signals:
    metadata      tier=1  confidence=0.50
    structural    tier=1  confidence=0.92 [shadow]
    embedding     tier=1  confidence=0.78
```

或者打开浏览器里的交互式 Playground：

```bash
uncommon-route serve
# → http://localhost:8403/dashboard/playground
```

输入 prompt，实时看路由决策变化。

---

## 两分钟上手

### 安装

```bash
pip install uncommon-route
```

### 配置上游

```bash
export UNCOMMON_ROUTE_UPSTREAM="https://api.commonstack.ai/v1"  # 或 OpenAI、Ollama 等
export UNCOMMON_ROUTE_API_KEY="your-key-here"
```

### 启动

```bash
uncommon-route serve
```

把客户端指向代理——改一行：

| 客户端 | 改这一行 |
|---|---|
| **Codex / Cursor / OpenAI SDK** | `export OPENAI_BASE_URL="http://localhost:8403/v1"` |
| **Claude Code** | `export ANTHROPIC_BASE_URL="http://localhost:8403"` |
| **OpenClaw** | 插件方式——见 [openclaw.ai](https://openclaw.ai) |

搞定。你现有的工作流已经在省钱了。

```bash
uncommon-route doctor  # 不确定哪里有问题？一条命令检查所有东西
```

---

## 什么时候不该用

UncommonRoute 在流量**混合**的时候最有效——有简单的有复杂的。如果你发的每条请求都真正需要最强模型（法律分析、前沿研究、复杂证明），路由器帮不了太多。

最佳场景：**agent 工作流**，其中 60-80% 的请求是状态检查、文件读取、简单编辑和摘要。77% 的节省就是从这里来的。

---

## Dashboard

```bash
uncommon-route serve
# → http://127.0.0.1:8403/dashboard/
```

实时监控：请求数、延迟、成本节省、模型分布、信号投票和花费限制。

**Playground** — 输入 prompt，实时看路由决策。
**路由解释** — 点击任何历史请求，查看它为什么被路由到那个模型。

---

## 配置

### 核心环境变量

| 变量 | 含义 |
|---|---|
| `UNCOMMON_ROUTE_UPSTREAM` | 上游 OpenAI 兼容 API 地址 |
| `UNCOMMON_ROUTE_API_KEY` | 上游 provider 的 API key |
| `UNCOMMON_ROUTE_PORT` | 本地代理端口（默认 8403） |

### 三种路由模式

| 模式 | 虚拟模型 ID | 行为 |
|---|---|---|
| **auto** | `uncommon-route/auto` | 平衡——最佳性价比 |
| **fast** | `uncommon-route/fast` | 省钱优先——最便宜的能用的 |
| **best** | `uncommon-route/best` | 质量优先——最强模型 |

### 花费控制

```bash
uncommon-route spend set per_request 0.10
uncommon-route spend set daily 20.00
uncommon-route spend status
```

### 自带 Key（BYOK）

```bash
uncommon-route provider add openai sk-your-openai-key
uncommon-route provider add anthropic sk-ant-your-key
uncommon-route provider list
```

---

## 性能数据

在 [LLMRouterBench](../LLMRouterBench) 上测试——762 条真实 agent 任务轨迹，覆盖 SWE-Bench、MT-RAG、QMSum 和 PinchBench。所有数字通过生产代码路径端到端测量（无评测捷径）。

| 指标 | 数值 |
|---|---|
| 对比全程顶配的成本节省 | **77%** |
| 任务通过率 | **90.3%** |
| 路由额外延迟 | **<10ms** |

```bash
python scripts/eval_v2.py --split holdout  # 自己跑一遍
```

---

## 隐私

UncommonRoute 完全在你本地运行。除非你明确 opt-in，不会有任何数据离开你的电脑。

```bash
uncommon-route telemetry status  # 查看当前状态（默认：关闭）
```

详见 [TELEMETRY.md](TELEMETRY.md)。

---

## 集成参考

### Base URL

| 客户端类型 | Base URL |
|---|---|
| OpenAI 兼容 | `http://127.0.0.1:8403/v1` |
| Anthropic 风格 | `http://127.0.0.1:8403` |

### Python SDK

```python
from uncommon_route import route

decision = route("解释拜占庭将军问题")
print(decision.model)       # "anthropic/claude-sonnet-4.6"
print(decision.tier)        # "COMPLEX"
print(decision.confidence)  # 0.87
```

### 响应头

`x-uncommon-route-model` · `x-uncommon-route-tier` · `x-uncommon-route-mode` · `x-uncommon-route-reasoning`

---

## 常见问题

| 症状 | 解决方法 |
|---|---|
| `route` 正常但真实请求失败 | 检查 `UNCOMMON_ROUTE_UPSTREAM` 和 `UNCOMMON_ROUTE_API_KEY`，跑 `uncommon-route doctor` |
| Codex / Cursor 连不上 | `OPENAI_BASE_URL` 必须以 `/v1` 结尾 |
| Claude Code 连不上 | `ANTHROPIC_BASE_URL` 指向路由器根路径，**不要**带 `/v1` |
| 不知道先跑什么 | `uncommon-route doctor` |

---

## 开发

```bash
git clone https://github.com/CommonstackAI/UncommonRoute.git
cd UncommonRoute
pip install -e ".[dev]"
python -m pytest tests -v  # 407 个测试通过
```

---

## 许可证

MIT —— 见 [LICENSE](LICENSE)。
