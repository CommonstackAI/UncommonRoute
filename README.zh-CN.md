<p align="right"><a href="README.md">English</a> | <strong>简体中文</strong></p>

<div align="center">

<h1>UncommonRoute</h1>

**任务完成质量不变，API 花费减少一半。**

UncommonRoute 接入 Claude Code、Cursor、Codex 和 OpenAI SDK，本地分析任务复杂度、上下文结构、工具调用、可用模型池和预算限制，再把请求路由到最合适的模型。

<strong>在 100 个 held-out SWE-bench Verified 任务上，UncommonRoute 任务通过数 75/100，对比全程 Opus 74/100。任务完成质量不变的同时，API 成本下降 53%。</strong>

<a href="https://pypi.org/project/uncommon-route/"><img src="https://img.shields.io/pypi/v/uncommon-route?style=flat-square&logo=pypi&logoColor=white&label=PyPI" alt="PyPI"></a>
<a href="https://www.npmjs.com/package/@anjieyang/uncommon-route"><img src="https://img.shields.io/npm/v/@anjieyang/uncommon-route?style=flat-square&logo=npm&logoColor=white&label=npm" alt="npm"></a>
<a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.11+-3776ab?style=flat-square&logo=python&logoColor=white" alt="Python 3.11+"></a>
<a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-22c55e?style=flat-square" alt="MIT"></a>

<br><br>

<a href="#30-秒跑起来">Quickstart</a> ·
<a href="#uncommonroute-如何帮你省钱">省钱逻辑</a> ·
<a href="#为什么需要-uncommonroute">为什么需要</a> ·
<a href="#可视化路由">Dashboard</a> ·
<a href="#benchmark">Benchmark</a> ·
<a href="#隐私">Privacy</a>

| 全程 Opus | UncommonRoute | 省下 |
|---:|---:|---:|
| 74 / 100 任务通过 | **75 / 100 任务通过** | 质量持平 |
| $54.73 API 成本 | **$25.66 API 成本** | **−53%** |

<sub>数据来自 <a href="https://github.com/CommonstackAI/TwinRouterBench">TwinRouterBench</a> 的 100 个 held-out SWE-bench Verified case。复现命令见下文。</sub>

</div>

<br>

<p align="center">
  <img src="docs/assets/hero-home.png" alt="UncommonRoute Dashboard" width="800">
</p>

---

## 30 秒跑起来

```bash
pipx install uncommon-route
uncommon-route init
```

`init` 会引导你选择连接方式、保存凭证，并自动配置 Claude Code、Codex、Cursor 或 OpenAI SDK。配置完成后可以随时做健康检查：

```bash
uncommon-route doctor
```

<details>
<summary>没有 <code>pipx</code>？在 venv 里？</summary>

- **macOS**：`brew install pipx && pipx ensurepath`
- **Ubuntu**：`sudo apt install pipx && pipx ensurepath`
- **Fedora**：`sudo dnf install pipx && pipx ensurepath`
- **已经在 virtualenv 里**：`python3 -m pip install uncommon-route`
- **遇到 "externally managed environment"**：用 `pipx` 或 venv，不要强装到系统 Python。
- **指定 Python 版本**：`pipx install --python python3.12 uncommon-route`

</details>

---

## UncommonRoute 如何帮你省钱

省钱不是靠少用 AI，而是避免把简单请求默认交给最强模型。

```text
"hello"                         -> simple
"修一下 README 里的错字"          -> simple
"定位这个失败测试并修复"            -> medium
"重构这个 500 行模块"             -> medium / complex
"设计一个分布式调度器"             -> complex
```

simple 请求优先走轻量模型，medium 请求走能力和成本更匹配的模型，complex 请求再调用你配置里的最强模型。所有判断都按请求独立完成，不会把整个会话固定在同一个模型上。

---

## 为什么需要 UncommonRoute

如果你每天用 AI agent 写代码，很容易遇到一种浪费：改错字、补注释、运行简单测试，也默认调用最贵的模型。

UncommonRoute 的边界很清楚：它不替代 Claude Code、Cursor 或 Codex，也不改变模型本身的能力。它专注于一件事：为每次请求选择最匹配的模型。

> 这个请求，最适合交给哪个模型？

路由在本地完成，请求按 agent step 独立判断，不会把整个会话固定在同一个复杂度分类或模型档位上。你也可以在 Dashboard 里看到每一步决策，而不是只能相信一个黑盒结果。

---

## 可视化路由

UncommonRoute 不只是转发请求的代理。Dashboard 会记录并解释每次路由决策：请求被判定为 simple、medium 还是 complex，最终选择哪个模型，实际花了多少钱，以及后续可以怎么调。

```bash
uncommon-route serve
# -> http://localhost:8403/dashboard/
```

Dashboard 主要解决五件事：

- 发送前预览 prompt 会被判定为 simple / medium / complex。
- 按会话查看每一步路由决策，包括模型、延迟、成本和信号读数。
- 看清楚哪些复杂度分类、哪些模型真正消耗了预算。
- 调整路由策略、fallback、预算上限、provider key 和模型池。
- 把路由结果标成 `too strong` / `just right` / `too weak`；这些标注会训练本地模型覆盖层，不会覆盖基础模型。

Feedback 是长期使用里很有价值的一块。如果某次路由太激进或太保守，你可以直接在 Dashboard 里纠正。训练在本地完成，基础模型保持不变，模型覆盖层也可以随时回滚。

---

## 支持哪些客户端

| 你在用 | 最短配置 | 说明 |
|---|---|---|
| Claude Code | `export ANTHROPIC_BASE_URL="http://localhost:8403"` | 通过 Anthropic-compatible proxy 接入 |
| OpenAI SDK | `export OPENAI_BASE_URL="http://localhost:8403/v1"` | 模型 ID 用 `uncommon-route/auto` |
| Codex | `export OPENAI_BASE_URL="http://localhost:8403/v1"` | 通过 OpenAI-compatible 接口接入 |
| Cursor | `export OPENAI_BASE_URL="http://localhost:8403/v1"` | 不需要改业务代码 |
| OpenClaw | 安装插件 | 见 [openclaw.ai](https://openclaw.ai) |

Claude Code 还需要设置一个占位 token：

```bash
export ANTHROPIC_AUTH_TOKEN="not-needed"
```

OpenAI SDK 示例：

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8403/v1")
resp = client.chat.completions.create(
    model="uncommon-route/auto",
    messages=msgs,
)
```

---

## 为什么选 UncommonRoute

| 能力 | 结果 |
|---|---|
| 本地路由 | 路由器在本地运行，不会在你和 provider 之间额外插入一个云端路由服务 |
| 按请求路由 | 每个 agent step 独立判断，不把整个会话固定在同一个复杂度分类或模型档位上 |
| 自动选择模型 | 根据任务难度、上下文结构、工具调用和 provider 可用性选择路由结果 |
| 可解释决策 | 每次路由都能看到复杂度分类、置信度、信号读数、模型和成本 |
| 可调整策略 | 支持 `auto` / `fast` / `best`，也支持按 simple / medium / complex 配置 override 和 fallback |
| 预算封顶 | 可以设置请求级、小时级或每日 API 花费上限 |
| 本地训练 | Feedback 会更新本地模型覆盖层；基础模型不会被覆盖，也可以随时回滚 |
| 即插即用 | Claude Code、Cursor、Codex、OpenAI SDK、OpenClaw 都不用改业务代码 |

---

## Benchmark

UncommonRoute 在 [TwinRouterBench](https://github.com/CommonstackAI/TwinRouterBench) 上评测。该评测包含 SWE-Bench、BFCL、mtRAG、QMSum、PinchBench 的 520 个实例，共 970 条路由器可见的任务前缀，并带有执行验证过的目标档位标签。端到端验证使用一组 100 个 held-out SWE-bench Verified case。

### 任务通过率持平，API 成本少 53%

| 策略 | 任务通过 | API 成本 | vs 全程 Opus |
|---|:---:|:---:|:---:|
| 全程 Opus 4.6 | 74 / 100 | $54.73 | — |
| **UncommonRoute** | **75 / 100** | **$25.66** | **−53%** |

也就是说，不是“少花钱但少做成任务”。在这组任务里，UncommonRoute 的任务通过数与全程 Opus 持平，实际 API 调用成本下降 53%。

这里的“任务通过”表示 100 个 held-out SWE-bench Verified case 中成功解决的任务数；“API 成本”表示实际模型调用成本，不包含 Table 4 里的 penalty cost。

### 复现

```bash
python -m pip install -e ".[dev]"
python -m pip install "git+https://github.com/CommonstackAI/TwinRouterBench.git"
python scripts/eval_v2.py --split holdout
python scripts/bench_overhead.py --iterations 50 --json
```

### 路由开销

本地 CPU，热进程：

| 指标 | 延迟 |
|---|:---:|
| p50 | 25.6ms |
| p90 | 32.1ms |

冷启动需要加载 embedding 模型，可能要几秒；进程预热后，单次 `route()` 通常是几十毫秒。

---

## 隐私

路由在本地完成。**prompt 不会经过额外的云端路由器；它只会发给你配置的 upstream provider。**

```bash
uncommon-route telemetry status
```

诊断文件也默认保存在本机：

```bash
uncommon-route support bundle
```

脱敏后的 support bundle 会写到 `~/.uncommon-route/support/`。只有你主动分享时，它才会离开本机。

---

## 预算封顶

为 API 花费设置硬上限：

```bash
uncommon-route spend set daily 20.00
uncommon-route spend status
```

Dashboard 里也可以设置请求级、小时级或每日预算。达到上限后，请求会回退到当前可用的最低成本档位，而不是直接失败。

---

## 工作原理

每个请求会经过三个本地 signal，先判断任务复杂度，再从你配置的 upstream 里选择最匹配的模型。

| Signal | 看什么 | 典型开销 |
|---|---|---:|
| Metadata | 对话结构、工具调用、上下文深度 | <1ms |
| Embedding | 用户请求、最近 agent 状态和元数据上的 BGE 分类器；不确定时退到 KNN | ~25–35ms |
| Structural | 文本复杂度、对话复杂度；只在需要时激活，其余时候 shadow 跟踪 | <1ms |

三个 signal 投票后，由 ensemble 决定复杂度分类。Router 再根据分类、能力、transport、upstream 可用性和价格，在匹配的候选里选择成本更低的可用模型。上游价格未知时按保守估计处理。

路由是**按请求 / 按 agent step**做的，不绑定整个会话。协议层限制仍然会遵守，例如 Anthropic thinking continuation 这类场景不会被随意打断。

UncommonRoute 也会从本地反馈里学习：高置信、一致的样本会进入 embedding 索引；低置信预测会向上升档，避免把复杂任务路由到能力不足的模型。

---

## 适合谁

- 你每天都在用 Claude Code、Cursor、Codex 或类似 agent 写代码。
- 你的账单主要花在最强模型上，但很多请求并不需要同一档模型。
- 你想省 API 成本，但不想把 prompt 发给一个额外的云端路由器。
- 你需要按请求粒度路由，而不是整个会话只选一次模型。
- 你希望有可解释、可调整、可反馈的路由，而不只是一个黑盒代理。

## 不适合谁

- 你只偶尔调用 LLM，账单本来就很低。
- 你希望路由器提升低成本模型本身的能力。UncommonRoute 不做这个承诺。
- 你所有任务都必须无条件使用最强模型。那可以直接用 `uncommon-route/best`，但省钱空间会小很多。

---

## FAQ

**会影响任务质量吗？**

UncommonRoute 不是一味追求便宜。不确定或风险高的请求会向更强模型升档，上面的 held-out SWE-bench Verified 结果也显示，在那组任务上任务完成质量持平。

**我的 prompt 会发到哪里？**

路由在本地完成。prompt 会发给你配置的 upstream provider，不会额外经过一个托管的云端路由服务。

**路由器不确定时怎么办？**

默认保守处理：低置信度请求会升档，而不是悄悄把复杂任务发给能力不足的模型。

**我可以手动覆盖路由吗？**

可以。你可以用 `auto`、`fast`、`best`，也可以按 simple / medium / complex 配置 primary 和 fallback model。

**可以用自己的 API key 吗？**

可以。你可以用 Commonstack 托管 upstream，也可以用 BYOK 注册自己的 provider key。

**Feedback 真的会训练东西吗？**

会。Feedback 会更新本地模型覆盖层，带标注的本地 trace 也可以用于校准运行时置信度。基础模型不会被覆盖，模型覆盖层可以随时回滚。

---

## 高级配置

### 连接 provider

**Commonstack 托管 upstream**：一把 key 接入 OpenAI、Anthropic、Google、xAI、MiniMax、Moonshot、DeepSeek。

```bash
export UNCOMMON_ROUTE_UPSTREAM="https://api.commonstack.ai/v1"
export UNCOMMON_ROUTE_API_KEY="csk-your-key"
uncommon-route serve
```

**BYOK，自带 provider key**：自动路由只会在你注册过的 provider 里选模型。

```bash
uncommon-route provider add openai     sk-...
uncommon-route provider add anthropic  sk-ant-...
uncommon-route provider add google     AIza...
uncommon-route serve
```

> UncommonRoute **不会**自动读取 `OPENAI_API_KEY` 或 `ANTHROPIC_API_KEY`。请用 `init`、已保存连接，或上面的手动配置方式。

### 路由模式

| Mode | Model ID | 行为 |
|---|---|---|
| auto | `uncommon-route/auto` | 默认模式，优先质量 / 成本比 |
| fast | `uncommon-route/fast` | 成本优先，在可接受质量下优先低成本模型 |
| best | `uncommon-route/best` | 质量优先，尽量用最强可用模型 |

### Provider 管理

```bash
uncommon-route provider list
uncommon-route provider add <name> <api-key>
uncommon-route provider remove <name>
```

支持：`commonstack`、`openai`、`anthropic`、`google`、`xai`、`minimax`、`moonshot`、`deepseek`。

<details>
<summary><strong>环境变量</strong></summary>

| 变量 | 含义 |
|---|---|
| `UNCOMMON_ROUTE_UPSTREAM` | 托管路径的 upstream 地址，例如 `https://api.commonstack.ai/v1`；BYOK 模式下忽略 |
| `UNCOMMON_ROUTE_API_KEY` | 配合 `UNCOMMON_ROUTE_UPSTREAM` 使用的 key，不是 per-provider key 的兜底 |
| `UNCOMMON_ROUTE_PORT` | 本地 proxy 端口，默认 8403 |

</details>

---

## 诊断

遇到路由异常、上游报错，或者准备提交 issue 时，可以一条命令导出脱敏诊断包：

```bash
uncommon-route support bundle
uncommon-route support request <request_id>
```

诊断包包含近期 traces、errors、stats、provider/config 快照和脱敏后的本地状态，默认保存在本机。

---

## 停止与卸载

前台运行时按 `Ctrl+C`。如果是后台 daemon：

```bash
uncommon-route stop
uncommon-route logs --follow
```

如果你想让客户端停止走 UncommonRoute，删除 `init` 写入 shell rc 的那段配置，然后重启终端。常见位置包括 `~/.zshrc`、`~/.bashrc`、`~/.config/fish/config.fish`。

只对当前 shell 临时恢复：

```bash
unset OPENAI_BASE_URL OPENAI_API_KEY ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN ANTHROPIC_API_KEY
```

卸载：

```bash
pipx uninstall uncommon-route
# 如果你装在 venv 里：
python3 -m pip uninstall uncommon-route
```

删除本地状态，包括连接、provider key、日志和 trace：

```bash
rm -rf ~/.uncommon-route/
```

---

## 开发

```bash
git clone https://github.com/CommonstackAI/UncommonRoute.git
cd UncommonRoute
pip install -e ".[dev]"
python -m pytest tests -v
```

---

## 许可证

MIT，见 [LICENSE](LICENSE)。
