<p align="right"><a href="https://github.com/CommonstackAI/UncommonRoute/blob/main/README.md">English</a> | <strong>简体中文</strong></p>

<div align="center">

<h1>UncommonRoute</h1>

<p><strong>别再为每条请求花冤枉钱了。</strong></p>

<p>
一个轻量的本地 LLM Router，自动在成本和质量之间找到最优解，为你节省90%以上的成本。
</p>

</div>
 
<table align="center">
<tr>
<td align="center"><h3>~90–95%</h3><sub>实测费用降低</sub></td>
<td align="center"><h3>88.5%</h3><sub>分类器准确率</sub></td>
<td align="center"><h3>28/28</h3><sub>请求成功率</sub></td>
<td align="center"><h3>341</h3><sub>测试通过</sub></td>
</tr>
</table>
 
<div align="center">
  
<p>
适用于 <strong>OpenClaw</strong>, <strong>Codex</strong>, <strong>Claude Code</strong>, <strong>Cursor</strong>, and the <strong>OpenAI SDK</strong>.
<p>
<a href="#快速开始"><strong>快速开始</strong></a> ·
<a href="#路由到底怎么做决定"><strong>工作原理</strong></a> ·
<a href="#真正会影响行为的配置"><strong>配置</strong></a> ·
<a href="#详细-benchmark"><strong>Benchmark</strong></a>
</p>

<a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.11+-3776ab?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11+"></a>&nbsp;
<a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-22c55e?style=for-the-badge" alt="MIT"></a>&nbsp;
<img src="https://img.shields.io/badge/Tests-281_passing-16a34a?style=for-the-badge&logo=pytest&logoColor=white" alt="281 passing tests">&nbsp;
<a href="#快速开始"><img src="https://img.shields.io/badge/Claude_Code-Ready-f97316?style=for-the-badge&logo=anthropic&logoColor=white" alt="Claude Code"></a>&nbsp;
<a href="#快速开始"><img src="https://img.shields.io/badge/Codex-Ready-412991?style=for-the-badge&logo=openai&logoColor=white" alt="Codex"></a>&nbsp;
<a href="#快速开始"><img src="https://img.shields.io/badge/Cursor-Compatible-007acc?style=for-the-badge&logo=visual-studio-code&logoColor=white" alt="Cursor"></a>&nbsp;
<a href="https://openclaw.ai"><img src="https://img.shields.io/badge/OpenClaw-Plugin-e11d48?style=for-the-badge" alt="OpenClaw"></a>

</div>

---

## 为什么你需要 Uncommonroute

很多 AI 工具都有一个很贵的默认值：所有请求都发给同一个模型。

这件事平时不明显，但一旦进入真实工作流，成本会很快失控：

- “what is 2+2?” 和 “设计一个容错分布式数据库” 被送去同一个模型
- tool selection、tool-result follow-up 这种中间步骤也在烧 premium model
- 一整个 agent loop 里，真正需要强推理的 turn 其实没那么多

UncommonRoute 做的事情很简单，就是把这个默认值改掉。

```text
你的客户端
  (Codex / Claude Code / Cursor / OpenAI SDK / OpenClaw)
            |
            v
     UncommonRoute
      (跑在本机)
            |
            v
       上游模型 API
 (Commonstack / OpenAI / Ollama / vLLM / Parallax / ...)
```

它不是模型托管服务，也不是 SaaS router。它做的是本地决策、本地转发，以及在上游模型名、可用性、价格和能力不完全稳定时，尽量把请求送到一个更合适的地方。

---

## 一行命令，砍掉你 90%+ 的API账单

一句话总结：你只保留一个本地 endpoint，剩下的模型选择交给 router。

- **92.3% 保留集路由准确率**
- **131 个请求的 coding session 模拟里，成本比 always Opus 低 90-95%**
- **平均路由延迟约 0.5ms**
- **当前测试套件 281 条全部通过**

成本对比的一个直观切片：

| 场景 | 总成本 |
| --- | ---: |
| 始终使用 `anthropic/claude-opus-4.6` | `$1.7529` |
| 使用 UncommonRoute | `$0.236` |

这就是项目想解决的问题：把贵模型的钱花在真的值钱的地方，而不是花在流程噪音上。

---

## 快速开始

第一次上手，照着下面做就够了。

### 1. 安装

```bash
pip install uncommon-route
```

或者直接用安装脚本：

```bash
curl -fsSL https://anjieyang.github.io/uncommon-route/install | bash
```

### 2. 先验证本地路由本身

这一步 **不需要** upstream，也不需要 API key。

```bash
uncommon-route route "write a Python function that validates email addresses"
uncommon-route debug "prove that sqrt(2) is irrational"
```

这一步能证明：

- 包安装成功
- 本地 classifier 正常工作
- router 能给出 tier、model 和 fallback chain

这一步不能证明：

- upstream 已经配好
- 你的客户端已经接到 proxy 上

### 3. 配一个 upstream

下面选一个你顺手的：

```bash
# Commonstack：一个 key 覆盖多个 provider
export UNCOMMON_ROUTE_UPSTREAM="https://api.commonstack.ai/v1"
export UNCOMMON_ROUTE_API_KEY="csk-..."
```

```bash
# OpenAI 官方 API
export UNCOMMON_ROUTE_UPSTREAM="https://api.openai.com/v1"
export UNCOMMON_ROUTE_API_KEY="sk-..."
```

```bash
# 本地 OpenAI-compatible server（Ollama、vLLM 等）
export UNCOMMON_ROUTE_UPSTREAM="http://127.0.0.1:11434/v1"
```

```bash
# Parallax scheduler endpoint（实验性）
export UNCOMMON_ROUTE_UPSTREAM="http://127.0.0.1:3001/v1"
```

如果你的 upstream 不需要 key，可以不设置 `UNCOMMON_ROUTE_API_KEY`。

Parallax 目前更适合看作“实验性 upstream”：公开资料里很明确有 `POST /v1/chat/completions`，但公开的 `/v1/models` 支持不算清晰，所以 discovery-driven routing 可能受限。

### 4. 启动 proxy

```bash
uncommon-route serve
```

如果 upstream 已配置好，启动 banner 会告诉你：

- 当前上游是谁
- 本地 proxy 地址是什么
- dashboard 在哪
- 下一条可以直接复制的健康检查命令

如果 upstream 还没配好，banner 也会直接提示你下一步该 export 什么。

### 5. 接入你已经在用的客户端

选你正在用的那条路径。

<details>
<summary><strong>Codex</strong> · 走本地 OpenAI-compatible endpoint</summary>

```bash
uncommon-route setup codex
```

手动配置时，最重要的是：

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
<summary><strong>Claude Code</strong> · 走 Anthropic-style endpoint</summary>

```bash
uncommon-route setup claude-code
```

手动配置时，最重要的是：

```bash
export ANTHROPIC_BASE_URL="http://localhost:8403"
export ANTHROPIC_API_KEY="not-needed"
```

然后：

```bash
uncommon-route serve
claude
```

Claude Code 走的是 `/v1/messages`。UncommonRoute 会接 Anthropic 风格请求、完成路由，再把响应转回 Claude Code 习惯的格式。

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

Cursor 的重点就是把 `OpenAI Base URL` 指到 `http://localhost:8403/v1`。

</details>

<details>
<summary><strong>OpenClaw</strong> · 通过插件接入</summary>

```bash
openclaw plugins install @anjieyang/uncommon-route
openclaw gateway restart
```

插件会自动帮你拉起 proxy，并把 provider 注册给 OpenClaw。

示例插件配置：

```yaml
plugins:
  entries:
    "@anjieyang/uncommon-route":
      port: 8403
      upstream: "https://api.commonstack.ai/v1"
      spendLimits:
        hourly: 5.00
        daily: 20.00
```

如果 upstream 需要认证，把 `UNCOMMON_ROUTE_API_KEY` 放到 OpenClaw 运行环境里。

</details>

### 6. 做一次健康检查

```bash
uncommon-route doctor
curl http://127.0.0.1:8403/health
```

只要你觉得哪里不对，第一反应都应该是先跑：

```bash
uncommon-route doctor
```

---

## 路由到底怎么做决定

你不需要了解全部细节才能用它，但知道这几个原则之后，很多行为都会变得非常好理解。

### 1. 每个请求先被分到三个 tier 之一

| Tier | 常见请求 |
| --- | --- |
| `SIMPLE` | 问候、短查询、基础翻译 |
| `MEDIUM` | 代码任务、解释、总结 |
| `COMPLEX` | 多约束设计、实现、系统性工作 |

现在已经没有“每个 tier 固定一个默认主模型”这件事了。默认情况下，selector 会在当前 mode 下对发现到的 model pool 统一打分，所以最终模型会随着价格、可用性、能力推断和反馈变化而变化。

### 2. routing mode 决定风格

| Mode | 适合什么 |
| --- | --- |
| `auto` | 平衡型默认策略 |
| `fast` | 更轻、更快、更省 |
| `best` | 质量优先 |

对应的虚拟 model ID 是：

- `uncommon-route/auto`
- `uncommon-route/fast`
- `uncommon-route/best`

### 3. selector 看的是整个可用 pool，不是死表

它会综合考虑：

- token 成本估算
- 已观测到的延迟和可靠性
- cache affinity
- 显式用户反馈
- BYOK-backed model 优先级
- free / local bias
- 工具调用、vision 等能力要求

如果 upstream 能提供 `/v1/models`，UncommonRoute 就会基于真实上游来构建 live pool，而不是假设模型世界永远不变。

### 4. session ID 还在，但不再做 sticky routing

session ID 现在主要用于：

- 归组 cache key
- 标记 composition checkpoint
- 给 stats 和 debug 提供任务上下文
- 在 `artifact://...` rehydrate 时找对任务

也就是说，它现在更多是“上下文分组键”，而不是“强制同任务一直用同一模型”的机制。

### 5. tool-heavy step 会被区别对待

真实 agent workflow 里，很多步骤根本不需要最强模型。

比如：

- tool selection
- tool-result follow-up
- 普通 chat turn

这些 turn 和真正的重推理 turn 混在一起时，UncommonRoute 会尽量把贵模型留给真正值得的地方。

---

## 看它实际在干什么

启动 proxy 后，打开：

```text
http://127.0.0.1:8403/dashboard/
```

Dashboard 里能看到：

- 请求数量、延迟、成本、节省
- mode、tier、model 分布
- upstream transport 和 cache 行为
- routing config 和 default mode 覆盖
- primary upstream 和 BYOK provider 连接
- 最近流量、spend limit 和使用情况
- feedback 状态和最近提交结果

这几个命令搭配 dashboard 最常用：

```bash
uncommon-route doctor
uncommon-route serve --daemon
uncommon-route stop
uncommon-route logs
uncommon-route logs --follow
uncommon-route config show
uncommon-route stats
uncommon-route stats history
```

后台模式会写：

- `~/.uncommon-route/serve.pid`
- `~/.uncommon-route/serve.log`

---

## 真正会影响行为的配置

### 核心环境变量

| 变量 | 说明 |
| --- | --- |
| `UNCOMMON_ROUTE_UPSTREAM` | 上游 OpenAI-compatible API URL |
| `UNCOMMON_ROUTE_API_KEY` | 上游 provider 的 API key |
| `UNCOMMON_ROUTE_PORT` | 本地 proxy 端口，默认 `8403` |
| `UNCOMMON_ROUTE_COMPOSITION_CONFIG` | composition policy JSON 文件路径 |
| `UNCOMMON_ROUTE_COMPOSITION_CONFIG_JSON` | 内联 composition policy JSON |

### Primary upstream 与实时连接

当前生效的 primary upstream 按这个优先级解析：

1. CLI flag，比如 `uncommon-route serve --upstream ...`
2. 环境变量，比如 `UNCOMMON_ROUTE_UPSTREAM` 和 `UNCOMMON_ROUTE_API_KEY`
3. 通过 dashboard 或 `PUT /v1/connections` 保存的本地文件配置

由 dashboard/API 保存的 primary connection 会写到：

```text
~/.uncommon-route/connections.json
```

### Bring Your Own Key（BYOK）

如果你希望 router 更偏向你自己有 key 的 provider 模型，可以这样注册：

```bash
uncommon-route provider add openai sk-your-openai-key
uncommon-route provider add anthropic sk-ant-your-key
uncommon-route provider list
uncommon-route provider models
```

provider 配置保存在：

```text
~/.uncommon-route/providers.json
```

当前行为里有几个重要事实：

- `provider add` 会写入这个 provider 的已知模型集合
- 如果能访问 `/models`，它会顺手做 key 验证
- `GET /v1/models` 依旧只暴露 UncommonRoute 的虚拟模型，不会直接把你完整 upstream catalog 暴露出来

如果你现在就想强制某个上游模型生效，最直接的方法有三个：

- 直接传那个明确的非虚拟 model ID
- 用 `uncommon-route config set-tier ...` 把它绑到某个 mode/tier
- 用 `uncommon-route provider models` 看当前 BYOK-backed 的模型集合

`--plan` 现在只是元数据。它会显示在 `provider list` 里，但不能替代 API key，也不会单独解锁模型。

### 实时路由配置

```bash
uncommon-route config show
uncommon-route config set-default-mode fast
uncommon-route config set-tier auto SIMPLE moonshot/kimi-k2.5 --fallback google/gemini-2.5-flash-lite,deepseek/deepseek-chat
uncommon-route config set-tier best COMPLEX anthropic/claude-opus-4.6 --fallback anthropic/claude-sonnet-4.6 --strategy hard-pin
uncommon-route config reset-tier auto SIMPLE
```

如果请求没有显式传 `model`，就会使用这个 default mode。

如果你传的是明确的非虚拟 model ID，UncommonRoute 仍然会原样 passthrough。

`--strategy hard-pin` 的意思是：除非主模型真的失败，否则这个 tier 尽量固定走你指定的 primary。

routing override 存在：

```text
~/.uncommon-route/routing_config.json
```

### Spend Control

```bash
uncommon-route spend set per_request 0.10
uncommon-route spend set hourly 5.00
uncommon-route spend set daily 20.00
uncommon-route spend set session 3.00
uncommon-route spend status
uncommon-route spend history
```

触发限制时，proxy 会返回 HTTP `429`，并附带 `reset_in_seconds`。

spending 数据保存在：

```text
~/.uncommon-route/spending.json
```

---

## 集成速查

这一节是给 SDK 作者、agent builder 和脚本接入方看的，一页内快速查完。

### Base URLs

| 客户端类型 | Base URL |
| --- | --- |
| OpenAI-compatible clients | `http://127.0.0.1:8403/v1` |
| Anthropic-style clients | `http://127.0.0.1:8403` |

### 虚拟 model IDs

| Model ID | 含义 |
| --- | --- |
| `uncommon-route/auto` | 平衡型默认策略 |
| `uncommon-route/fast` | 更轻、更快 |
| `uncommon-route/best` | 质量优先 |

### 常用 endpoints

| Endpoint | 用途 |
| --- | --- |
| `GET /health` | 看存活状态、配置状态、model discovery 状态 |
| `GET /v1/models` | 查看 router 暴露的虚拟模型 |
| `GET /v1/models/mapping` | 查看内部模型名到上游模型名映射与 pool |
| `GET /v1/connections` / `PUT /v1/connections` | 查看或更新当前 primary runtime connection |
| `GET /v1/routing-config` / `POST /v1/routing-config` | 查看或更新 mode/tier override |
| `GET /v1/stats` / `POST /v1/stats` | 路由统计摘要或重置 |
| `GET /v1/stats/recent` | 最近路由请求和 feedback 状态 |
| `GET /v1/selector` / `POST /v1/selector` | 查看 selector 状态或预览路由结果 |
| `GET /v1/feedback` / `POST /v1/feedback` | 查看反馈状态、提交反馈、回滚在线更新 |
| `GET /dashboard/` | 面向人类的监控界面 |

### 常用响应头

对于使用虚拟模型触发的 **routed request**，常见响应头包括：

- `x-uncommon-route-model`
- `x-uncommon-route-tier`
- `x-uncommon-route-mode`
- `x-uncommon-route-step`
- `x-uncommon-route-reasoning`

如果你发的是显式非虚拟 model 的 passthrough 请求，不要假设这些 routing headers 一定会全部出现。

### Python SDK 示例

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

---

## 进阶能力

### Model discovery 与 mapping

不同 upstream 的模型 ID 不一样。UncommonRoute 会尝试拉取 `/v1/models`，构建 live model pool，把内部模型名映射到上游真实模型名，并在 fallback 成功时记录 learned alias。

常用命令：

```bash
uncommon-route doctor
curl http://127.0.0.1:8403/v1/models/mapping
```

### Composition pipeline

非常大的工具输出不会总是原样继续往前传。

Proxy 可以：

- 压缩超长文本和 JSON
- 把大工具输出转存成 artifact
- 生成 semantic side-channel summary
- 给长历史打 checkpoint
- 按需 rehydrate `artifact://...`

artifact 存在：

```text
~/.uncommon-route/artifacts/
```

相关 headers 常见有：

- `x-uncommon-route-input-before`
- `x-uncommon-route-input-after`
- `x-uncommon-route-artifacts`
- `x-uncommon-route-semantic-calls`
- `x-uncommon-route-semantic-fallbacks`
- `x-uncommon-route-checkpoints`
- `x-uncommon-route-rehydrated`

### Anthropic-native transport

如果最终路由到了 Anthropic 系模型，而且 upstream 支持，UncommonRoute 可以保留 Anthropic-native transport 与 caching 语义，同时继续对 OpenAI 风格客户端提供正常返回。

### 本地训练

classifier 是本地模型，不是黑盒服务。你可以用自己的 benchmark 数据重新训练。

从 repo 根目录执行：

```bash
python - <<'PY'
from uncommon_route.router.classifier import train_and_save_model
train_and_save_model("bench/data/train.jsonl")
PY
```

在线反馈产生的本地覆盖模型会写到：

```text
~/.uncommon-route/model_online.json
```

---

## 常见问题

### “`route` 能跑，但真实请求还是不通”

`uncommon-route route ...` 只是本地路由决策，不会真的调用 upstream。

如果真实请求失败，先看：

- `UNCOMMON_ROUTE_UPSTREAM`
- provider 需要 key 的话，看 `UNCOMMON_ROUTE_API_KEY`
- `uncommon-route doctor`

### “Codex 或 Cursor 连不上”

对于 OpenAI 风格客户端，`OPENAI_BASE_URL` 必须带 `/v1`：

```bash
export OPENAI_BASE_URL="http://localhost:8403/v1"
```

### “Claude Code 连不上”

对于 Anthropic 风格客户端，`ANTHROPIC_BASE_URL` 要指向 router 根路径，而不是 `/v1`：

```bash
export ANTHROPIC_BASE_URL="http://localhost:8403"
```

### “本地 upstream 能跑，但 discovery 还是失败”

有些本地或实验性 upstream 提供了 `POST /chat/completions`，但没有完整或标准的 `/models`。这种情况下，passthrough 可能依旧可用，但 live discovery 受限。`uncommon-route doctor` 会把这个状态说清楚。

### “我不知道第一条命令该跑什么”

先跑：

```bash
uncommon-route doctor
```

通常这条命令本身就会把缺口指出来。

---

## 详细 Benchmark

最关键的问题其实只有两个：

1. 它能不能正确判断请求难度？
2. 这种判断能不能在真实 coding session 里省下钱？

### 保留集路由 benchmark

评测集包含 **763 条手写 prompt**，覆盖 **15 种语言** 和 **35 个类别**。

| 指标 | UncommonRoute | ClawRouter | NotDiamond (cost) |
| --- | ---: | ---: | ---: |
| Accuracy | **92.3%** | 52.6% | 46.1% |
| Weighted F1 | **92.3%** | 47.0% | 38.0% |
| Latency / request | **0.5ms** | 0.6ms | 37.6ms |
| MEDIUM F1 | **88.7%** | 43.6% | 6.2% |
| COMPLEX F1 | **97.8%** | 61.7% | 0.0% |

### 真实成本模拟

基于一个 **131 请求的 agent coding session**，对比 always send to `anthropic/claude-opus-4.6`：

| 指标 | Always Opus | UncommonRoute |
| --- | ---: | ---: |
| Total cost | $1.7529 | **$0.236** |
| Cost saved | — | **86.4%** |
| Quality retained | 100% | **93.5%** |
| Routing accuracy | — | **90.8%** |

### 复现实验

如果你也把配套的 `router-bench/` 目录放在了当前 repo 同级，可以这样跑：

```bash
cd ../router-bench && python -m router_bench.run
```

---

## 停掉或者卸载

如果你想停止使用 UncommonRoute：

```bash
# 如果你是后台启动
uncommon-route stop

# 卸载 Python 包
pip uninstall uncommon-route
```

如果你是前台直接运行 `uncommon-route serve`，用 `Ctrl+C` 停掉即可。

只停掉 `serve` 不会自动恢复你原来的客户端配置。只要客户端还指向 `http://localhost:8403` 或 `http://localhost:8403/v1`，它就还会继续请求 localhost。

常见回退命令：

```bash
unset UNCOMMON_ROUTE_UPSTREAM
unset UNCOMMON_ROUTE_API_KEY
unset OPENAI_BASE_URL
unset ANTHROPIC_BASE_URL
```

如果你装过 OpenClaw 集成，也别忘了一起移除：

```bash
openclaw plugins uninstall @anjieyang/uncommon-route

# 如果你用的是 config-patch fallback，而不是插件：
uncommon-route openclaw uninstall
```

---

## 开发

```bash
git clone https://github.com/CommonstackAI/UncommonRoute.git
cd UncommonRoute
pip install -e ".[dev]"
python -m pytest tests -v
```

最近一次本地执行结果：`281 passed`。

---

## License

MIT — 见 [LICENSE](LICENSE)。

---

<div align="center">
<sub>Built by <a href="https://github.com/anjieyang">Anjie Yang</a> · <a href="https://commonstack.ai/">Commonstack-compatible</a></sub>
</div>
