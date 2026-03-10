<p align="right"><a href="https://github.com/anjieyang/UncommonRoute/blob/main/README.md">English</a> | <strong>简体中文</strong></p>

<div align="center">

<h1>UncommonRoute</h1>

<p><strong>按难度路由，不按习惯路由。</strong></p>

<p>
UncommonRoute 是一个运行在本机的 LLM Router，位于你的客户端和模型提供方之间。
简单请求走更便宜的模型，复杂请求走更强的模型；如果首选模型失败，还会自动走 fallback 链。
</p>

<p>
适用于 <strong>Codex</strong>、<strong>Claude Code</strong>、<strong>Cursor</strong>、<strong>OpenAI SDK</strong> 和 <strong>OpenClaw</strong>。
</p>

<p>
保留集路由基准：<strong>92.3% 准确率</strong> ·
平均路由延迟：<strong>约 0.5ms</strong> ·
相对 always-Opus 的模拟编程会话成本：<strong>节省 67%</strong>
</p>

<p>
<a href="#快速开始"><strong>快速开始</strong></a> ·
<a href="#连接你的客户端"><strong>连接你的客户端</strong></a> ·
<a href="#agent-快速参考"><strong>Agent 快速参考</strong></a> ·
<a href="#路由是怎么工作的"><strong>路由是怎么工作的</strong></a>
</p>

<a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.11+-3776ab?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11+"></a>&nbsp;
<a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-22c55e?style=for-the-badge" alt="MIT"></a>&nbsp;
<img src="https://img.shields.io/badge/Tests-169_passing-16a34a?style=for-the-badge&logo=pytest&logoColor=white" alt="169 tests">&nbsp;
<a href="#连接你的客户端"><img src="https://img.shields.io/badge/Claude_Code-Ready-f97316?style=for-the-badge&logo=anthropic&logoColor=white" alt="Claude Code"></a>&nbsp;
<a href="#连接你的客户端"><img src="https://img.shields.io/badge/Codex-Ready-412991?style=for-the-badge&logo=openai&logoColor=white" alt="Codex"></a>&nbsp;
<a href="#连接你的客户端"><img src="https://img.shields.io/badge/Cursor-Compatible-007acc?style=for-the-badge&logo=visual-studio-code&logoColor=white" alt="Cursor"></a>&nbsp;
<a href="https://openclaw.ai"><img src="https://img.shields.io/badge/OpenClaw-Plugin-e11d48?style=for-the-badge" alt="OpenClaw"></a>

</div>

---

## 为什么需要它

很多 AI 工具会把所有请求都发给同一个模型。

这样最省事，但通常也最浪费：

- “What is 2+2?” 不需要和 “设计一个容错分布式数据库” 用同一个模型。
- 工具型 agent workflow 中，大量中间步骤其实很便宜。
- 整个工作流全切到最贵模型很简单，但成本会迅速变高。

UncommonRoute 的思路是：每个请求都在本地做一次决策。

1. 先判断这个请求有多难。
2. 再根据难度和 routing profile 选模型。
3. 同时准备好 fallback 链，以防上游拒绝或失败。

你只需要一个本地 endpoint，模型选择交给 Router。

---

## 15 秒理解它

```text
你的客户端
  (Codex / Claude Code / Cursor / OpenAI SDK)
            |
            v
     UncommonRoute
      (运行在本机)
            |
            v
       上游模型 API
 (Parallax / Commonstack / OpenAI / Ollama / vLLM / ...)
```

示例上游：[Parallax](https://github.com/GradientHQ/parallax)、[Commonstack](https://commonstack.ai/)、OpenAI、Ollama、vLLM。

几个关键术语：

| 术语 | 中文解释 |
|---|---|
| **Client** | 你已经在用的东西，比如 Codex 或 Claude Code |
| **Upstream** | 真正生成回复的模型 API |
| **Profile** | 路由策略，比如 `auto`、`eco`、`premium` |
| **Tier** | 难度层级：`SIMPLE`、`MEDIUM`、`COMPLEX`、`REASONING` |
| **Virtual model** | 像 `uncommon-route/auto` 这样的虚拟模型名，意思是“你帮我选” |

> **对新手最重要的一句：** UncommonRoute **不托管模型**。它只是把请求转发并路由到你指定的上游 provider。

---

## 快速开始

如果你是第一次接触，按这个顺序来。

### 0. 你需要什么

- Python 3.11 或更高版本
- 一个终端
- 如果你想拿到真实模型回复：至少一个上游 API

常见上游选择：

- [**Commonstack**](https://commonstack.ai/)：一个 key 访问多个 provider
- **OpenAI**：如果你本来就直接用 OpenAI
- [**Parallax**](https://github.com/GradientHQ/parallax) / **Ollama / vLLM**：如果你想接本地 OpenAI-compatible server

### 1. 安装

```bash
pip install uncommon-route
```

或者直接用安装脚本：

```bash
curl -fsSL https://anjieyang.github.io/uncommon-route/install | bash
```

### 2. 先在本地验证 router 本身

这一步 **不需要** API key。

```bash
uncommon-route route "write a Python function that validates email addresses"
uncommon-route debug "prove that sqrt(2) is irrational"
```

这一步能证明：

- 包安装成功了
- 本地 classifier 正常工作
- router 能正常选 tier 和 model

这一步不能证明：

- 你的 upstream 配好了
- 你的客户端已经能通过 proxy 工作

### 3. 配置一个 upstream

下面任选一种：

```bash
# Commonstack：一个 key，多个 provider
export UNCOMMON_ROUTE_UPSTREAM="https://api.commonstack.ai/v1"
export UNCOMMON_ROUTE_API_KEY="csk-..."
```

```bash
# OpenAI 官方 API
export UNCOMMON_ROUTE_UPSTREAM="https://api.openai.com/v1"
export UNCOMMON_ROUTE_API_KEY="sk-..."
```

```bash
# Parallax scheduler endpoint（实验性本地 OpenAI 风格上游）
export UNCOMMON_ROUTE_UPSTREAM="http://127.0.0.1:3001/v1"
```

```bash
# 其他本地 OpenAI-compatible server（Ollama、vLLM 等）
export UNCOMMON_ROUTE_UPSTREAM="http://127.0.0.1:11434/v1"
```

如果你的 upstream 不需要 key，可以不设置 `UNCOMMON_ROUTE_API_KEY`。

Parallax 目前我把它视为“实验性 upstream”：它的公开文档和源码里明确有 `POST /v1/chat/completions`，但我没有找到公开的 `/v1/models` 路由，所以 UncommonRoute 的 model discovery 可能会受限。

### 4. 启动 proxy

```bash
uncommon-route serve
```

如果 upstream 已配置好，启动 banner 会显示：

- 上游 host
- 本地 proxy URL
- dashboard URL
- 一个快速健康检查命令

如果 upstream 还没配置好，banner 也会直接告诉你下一步该执行哪些 `export`。

### 5. 确认服务健康

```bash
uncommon-route doctor
curl http://127.0.0.1:8403/health
```

只要你觉得哪儿不对，第一条命令应该总是：

```bash
uncommon-route doctor
```

如果你用的是 Ollama、vLLM、Parallax 这种本地 upstream，要确保那个本地服务本身已经先启动了，否则 `doctor` 的 reachability 检查会失败。

### 6. 接入你的客户端

按你正在使用的客户端选一项：

| 如果你在用 | 这样做 |
|---|---|
| **Codex** | `uncommon-route setup codex` |
| **Claude Code** | `uncommon-route setup claude-code` |
| **OpenAI SDK / Cursor** | `uncommon-route setup openai` |
| **OpenClaw** | `openclaw plugins install @anjieyang/uncommon-route` |

每个 `setup` 命令都会输出准确的下一步配置。

---

## 连接你的客户端

你只需要展开你正在使用的那一项。

<details>
<summary><strong>Codex</strong> · 用本地 OpenAI-compatible endpoint 接 Codex</summary>

```bash
uncommon-route setup codex
```

手动配置时，最关键的是：

```bash
export OPENAI_BASE_URL="http://localhost:8403/v1"
export OPENAI_API_KEY="not-needed"
```

然后：

```bash
uncommon-route serve
codex
```

想启用智能路由时，用：

```text
model = "uncommon-route/auto"
```

</details>

<details>
<summary><strong>Claude Code</strong> · 用 Anthropic 风格 endpoint 接 Claude Code</summary>

```bash
uncommon-route setup claude-code
```

手动配置时，最关键的是：

```bash
export ANTHROPIC_BASE_URL="http://localhost:8403"
export ANTHROPIC_API_KEY="not-needed"
```

然后：

```bash
uncommon-route serve
claude
```

Claude Code 走的是 Anthropic 风格的 `/v1/messages`。UncommonRoute 会自动做协议转换并完成智能路由。

</details>

<details>
<summary><strong>OpenAI SDK / Cursor</strong> · 一个本地 base URL 统一接入</summary>

```bash
uncommon-route setup openai
```

Python 示例：

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8403/v1",
    api_key="not-needed",
)

response = client.chat.completions.create(
    model="uncommon-route/auto",
    messages=[{"role": "user", "content": "hello"}],
)
```

Cursor 只需要把 `OpenAI Base URL` 指到 `http://localhost:8403/v1`。

</details>

<details>
<summary><strong>OpenClaw</strong> · 通过插件接入</summary>

```bash
openclaw plugins install @anjieyang/uncommon-route
```

这个插件会自动安装 Python 依赖、启动 proxy，并完成注册。

</details>

---

## Agent 快速参考

如果你是把 UncommonRoute 接到另一个脚本、工具或 agent loop 里，先看这部分就够了。

### Base URLs

| 客户端类型 | Base URL |
|---|---|
| **OpenAI-compatible clients** | `http://127.0.0.1:8403/v1` |
| **Anthropic-style clients** | `http://127.0.0.1:8403` |

### 虚拟路由 profile

| Model ID | 含义 |
|---|---|
| `uncommon-route/auto` | 平衡型默认策略 |
| `uncommon-route/eco` | 优先选择最便宜且足够的模型 |
| `uncommon-route/premium` | 质量优先 |
| `uncommon-route/free` | 优先免费模型，其次最便宜可用 fallback |
| `uncommon-route/agentic` | 为 tool-heavy workflow 优化 |

### 脚本里常用命令

```bash
uncommon-route route --json --no-feedback "summarize this log file"
uncommon-route doctor
uncommon-route stats
uncommon-route logs --follow
```

### 常用响应头

- `x-uncommon-route-model`
- `x-uncommon-route-tier`
- `x-uncommon-route-profile`
- `x-uncommon-route-step`
- `x-uncommon-route-reasoning`

### 常用 endpoints

| Endpoint | 用途 |
|---|---|
| `GET /health` | 基础存活和配置状态 |
| `GET /v1/models` | Router 暴露的虚拟模型 |
| `GET /v1/models/mapping` | 内部模型名到上游模型名的映射 |
| `GET /v1/stats` | 路由统计摘要 |
| `POST /v1/stats` | 重置路由统计 |
| `GET /v1/stats/recent` | 最近路由请求及反馈状态 |
| `GET /v1/selector` | 查看 selector 当前状态 |
| `POST /v1/selector` | 预览某个 prompt 或 request body 会如何路由 |
| `GET /dashboard/` | 面向人类的监控页面 |

### 接入成功的判断标准

满足这三条，基本就算接通了：

- `uncommon-route doctor` 显示 upstream 和 key 已就绪
- `GET /health` 返回 `{"status": "ok", ...}`
- 路由请求里带有 `x-uncommon-route-model` 和 `x-uncommon-route-tier`

---

## 日常使用

<details>
<summary><strong>CLI</strong> · 只看本地路由决策，不真正请求上游</summary>

```bash
uncommon-route route "what is 2+2"
uncommon-route route --json --no-feedback "design a distributed database"
uncommon-route debug "explain quicksort"
```

这些命令分别是：

- `route`：查看选中的 tier、model、节省估算和 fallback 链
- `route --json`：同样的信息，但机器可读
- `debug`：查看分类背后的 feature breakdown

</details>

<details>
<summary><strong>Python SDK</strong> · 在 Python 里直接调用路由器</summary>

```python
from uncommon_route import classify, route

decision = route("explain the Byzantine Generals Problem")
print(decision.model)
print(decision.tier)
print(decision.confidence)

result = classify("hello")
print(result.tier)
print(result.signals)
```

</details>

<details>
<summary><strong>HTTP Proxy</strong> · 把真实客户端和应用接到 UncommonRoute 前面</summary>

```bash
uncommon-route serve --port 8403
```

OpenAI-compatible 示例：

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8403/v1",
    api_key="not-needed",
)

response = client.chat.completions.create(
    model="uncommon-route/auto",
    messages=[{"role": "user", "content": "hello"}],
)
```

如果你传入的是非虚拟模型名，UncommonRoute 会直接 passthrough，不会强制替你改模型。

</details>

---

## Dashboard 与诊断

启动 proxy 后，打开：

```text
http://127.0.0.1:8403/dashboard/
```

Dashboard 会展示：

- 请求数量、延迟、成本、节省
- tier 和 model 分布
- upstream transport 和 cache 行为
- 实时 routing 配置
- 活跃 session
- spend limit 和最近使用情况

本地常用命令：

```bash
uncommon-route doctor
uncommon-route serve --daemon
uncommon-route stop
uncommon-route logs
uncommon-route logs --follow
uncommon-route sessions
uncommon-route stats
```

后台模式的文件位置：

- PID: `~/.uncommon-route/serve.pid`
- 日志: `~/.uncommon-route/serve.log`

---

## 配置

### 核心环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `UNCOMMON_ROUTE_UPSTREAM` | — | 上游 OpenAI-compatible API URL |
| `UNCOMMON_ROUTE_API_KEY` | — | 上游 provider 的 API key |
| `UNCOMMON_ROUTE_PORT` | `8403` | 本地 proxy 端口 |
| `UNCOMMON_ROUTE_DISABLED` | `false` | 关闭路由，直接 passthrough |
| `UNCOMMON_ROUTE_COMPOSITION_CONFIG` | — | composition policy JSON 文件路径 |
| `UNCOMMON_ROUTE_COMPOSITION_CONFIG_JSON` | — | 内联 composition policy JSON |

### Bring Your Own Key（BYOK）

如果你有某些 provider 的直连 key，并且希望 router 更偏向这些模型，可以这样注册：

```bash
uncommon-route provider add openai sk-your-openai-key
uncommon-route provider add anthropic sk-ant-your-key
uncommon-route provider list
```

BYOK key 在可能的情况下会自动验证。provider 配置保存在：

```text
~/.uncommon-route/providers.json
```

### 实时路由配置

你可以按 profile + tier 覆盖默认模型表：

```bash
uncommon-route config show
uncommon-route config set-tier auto SIMPLE moonshot/kimi-k2.5 --fallback google/gemini-2.5-flash-lite,deepseek/deepseek-chat
uncommon-route config set-tier premium COMPLEX anthropic/claude-opus-4.6 --fallback anthropic/claude-sonnet-4.6 --mode hard-pin
uncommon-route config reset-tier auto SIMPLE
```

如果你希望某个 tier 除非模型真的挂了，否则始终优先使用配置里的 primary，就用 `--mode hard-pin`。

### Spend Control

你可以设置预算保护：

```bash
uncommon-route spend set per_request 0.10
uncommon-route spend set hourly 5.00
uncommon-route spend set daily 20.00
uncommon-route spend set session 3.00
uncommon-route spend status
uncommon-route spend history
```

当触发限制时，proxy 会返回 HTTP `429`，并带 `reset_in_seconds`。

spending 数据保存在：

```text
~/.uncommon-route/spending.json
```

---

## 路由是怎么工作的

你不需要理解所有内部细节才能使用它，但理解这个模型会很有帮助。

### 1. 每个请求先被分到四个 tier 之一

| Tier | 典型请求 | 默认主模型 |
|---|---|---|
| `SIMPLE` | 问候、短查询、基础翻译 | `moonshot/kimi-k2.5` |
| `MEDIUM` | 代码任务、解释、总结 | `moonshot/kimi-k2.5` |
| `COMPLEX` | 多约束设计与实现 | `google/gemini-3.1-pro` |
| `REASONING` | 证明、推导、困难数学推理 | `xai/grok-4-1-fast-reasoning` |

### 2. routing profile 决定选型风格

| Profile | 适合什么 |
|---|---|
| `auto` | 平衡型默认策略 |
| `eco` | 最低预期成本 |
| `premium` | 质量优先 |
| `free` | 先免费，再用最便宜可用 fallback |
| `agentic` | 工具型工作流 |

### 3. 本地 selector 会选主模型和 fallback 链

selector 会综合考虑：

- profile 偏好
- token 成本估算
- 观测到的延迟和可靠性
- cache affinity
- 显式用户反馈
- BYOK / free / local bias

### 4. session 会减少不必要的模型切换

默认情况下，session 会：

- 在同一任务中尽量保持已经足够好的模型
- 当任务变难时自动升级
- 避免无意义地上下抖动
- 30 分钟无活动后自动过期

### 5. agentic step 会被特殊处理

工具型 workflow 往往有很多很便宜的中间步骤。

UncommonRoute 会区分：

- tool selection
- tool-result follow-up
- 一般 chat turn

这样它就能把便宜且支持工具的模型留给中间步骤，把强推理模型留给真正需要推理的步骤。

---

## 常见问题

### “`route` 能跑，但我的 app 还是拿不到回复”

`uncommon-route route ...` 只是在本地做一次路由决策，并不会真的调用 upstream。

如果真实请求失败，优先检查：

- `UNCOMMON_ROUTE_UPSTREAM`
- 如果 provider 需要 key，就检查 `UNCOMMON_ROUTE_API_KEY`
- 运行 `uncommon-route doctor`

### “Codex 连不上”

对于 OpenAI 风格客户端，`OPENAI_BASE_URL` 必须以 `/v1` 结尾：

```bash
export OPENAI_BASE_URL="http://localhost:8403/v1"
```

### “Claude Code 连不上”

对于 Anthropic 风格客户端，`ANTHROPIC_BASE_URL` 应该指向 router 根路径，而不是 `/v1`：

```bash
export ANTHROPIC_BASE_URL="http://localhost:8403"
```

### “我不知道第一条命令该跑什么”

先跑这个：

```bash
uncommon-route doctor
```

它通常会直接告诉你缺了什么。

---

## 高级功能

等基础流程都跑通之后，这几项功能会很有价值。

### Model Mapping

不同 upstream 的模型 ID 不一样。UncommonRoute 会拉取 `/v1/models`，把内部模型名映射到上游模型名，如果首选模型不可用，还会自动走 fallback retry。

常用命令：

```bash
uncommon-route doctor
curl http://127.0.0.1:8403/v1/models/mapping
```

### Composition Pipeline

很大的工具输出不会默认原样转发。

Proxy 可以：

- 压缩过长文本和 JSON
- 把超大工具输出转存为本地 artifact
- 为大工具结果生成 semantic side-channel 摘要
- 为超长历史生成 checkpoint
- 按需 rehydrate `artifact://...`

artifact 存在：

```text
~/.uncommon-route/artifacts/
```

常用响应头：

- `x-uncommon-route-input-before`
- `x-uncommon-route-input-after`
- `x-uncommon-route-artifacts`
- `x-uncommon-route-semantic-calls`
- `x-uncommon-route-semantic-fallbacks`
- `x-uncommon-route-checkpoints`
- `x-uncommon-route-rehydrated`

### Anthropic-Native Transport

如果最终路由到了 Anthropic 系模型，且 upstream 支持，UncommonRoute 可以保留 Anthropic-native transport 和缓存语义，同时对 OpenAI 风格客户端维持正常返回。

### 本地训练

classifier 是本地模型，不是黑盒 SaaS。你可以用自己的 benchmark 数据重新训练：

```bash
python - <<'PY'
from uncommon_route.router.classifier import train_and_save_model
train_and_save_model("bench/data/train.jsonl")
PY
```

---

## Benchmarks

最关键的其实是两个问题：

1. 它能不能正确判断请求难度？
2. 这种判断能不能在真实 coding session 里省下钱？

### 保留集路由基准

评测集包含 **763 条手写 prompt**，覆盖 **15 种语言** 和 **35 个类别**。

| 指标 | UncommonRoute | ClawRouter | NotDiamond (cost) |
|---|---|---|---|
| Accuracy | **92.3%** | 52.6% | 46.1% |
| Weighted F1 | **92.3%** | 47.0% | 38.0% |
| Latency / request | **0.5ms** | 0.6ms | 37.6ms |
| MEDIUM F1 | **88.7%** | 43.6% | 6.2% |
| REASONING F1 | **97.8%** | 61.7% | 0.0% |

### 真实成本模拟

基于一个 **131 请求的 agent coding session**，对比 always send to `anthropic/claude-opus-4.6`：

| 指标 | Always Opus | UncommonRoute |
|---|---|---|
| Total cost | $1.7529 | **$0.5801** |
| Cost saved | — | **67%** |
| Quality retained | 100% | **93.5%** |
| Routing accuracy | — | **90.8%** |

### 复现实验

```bash
cd ../router-bench && python -m router_bench.run
```

---

## 项目结构

```text
├── uncommon_route/           # 核心 Python 包
│   ├── router/               # classifier + selector + model table
│   ├── proxy.py              # ASGI proxy（OpenAI + Anthropic endpoints）
│   ├── session.py            # session 持久化与升级
│   ├── spend_control.py      # 支出限制
│   ├── providers.py          # BYOK provider 管理
│   ├── feedback.py           # 在线反馈
│   ├── composition.py        # tool result 压缩 / checkpoint
│   ├── artifacts.py          # 本地 artifact 存储
│   ├── stats.py              # 路由分析
│   └── static/               # 已构建 dashboard 静态资源
├── frontend/dashboard/       # Dashboard 前端源码
├── openclaw-plugin/          # OpenClaw 集成
├── tests/                    # 单元 / 集成 / 端到端测试
├── bench/                    # Benchmark 数据与训练脚本
├── scripts/install.sh        # 安装脚本
└── pyproject.toml            # 打包和依赖声明
```

---

## 开发

```bash
git clone https://github.com/anjieyang/UncommonRoute.git
cd UncommonRoute
pip install -e ".[dev]"
python -m pytest tests/ -v
```

---

## License

MIT — 见 [LICENSE](LICENSE)。

---

<div align="center">
<sub>Built by <a href="https://github.com/anjieyang">Anjie Yang</a> · <a href="https://commonstack.ai/">Commonstack-compatible</a></sub>
</div>
