<p align="right"><a href="https://github.com/CommonstackAI/UncommonRoute/blob/main/README.md">English</a> | <strong>简体中文</strong></p>

<div align="center">

<h1>UncommonRoute</h1>

**自动模型路由，节省 82% 的 LLM 开销。**

大部分 LLM 预算都花在了不需要顶配模型的简单任务上。
UncommonRoute 自动选最便宜、又能完成任务的模型。

<br>

<a href="https://pypi.org/project/uncommon-route/"><img src="https://img.shields.io/pypi/v/uncommon-route?style=flat-square&logo=pypi&logoColor=white&label=PyPI" alt="PyPI"></a>
<a href="https://www.npmjs.com/package/@anjieyang/uncommon-route"><img src="https://img.shields.io/npm/v/@anjieyang/uncommon-route?style=flat-square&logo=npm&logoColor=white&label=npm" alt="npm"></a>
<a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.11+-3776ab?style=flat-square&logo=python&logoColor=white" alt="Python 3.11+"></a>
<a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-22c55e?style=flat-square" alt="MIT"></a>

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

### 1. 安装

```bash
pipx install uncommon-route
```

对大多数 CLI 用户来说，`pipx` 是更好的默认方案：它会把 UncommonRoute 安装到独立环境里，不污染系统 Python，卸载也更干净。

如果你还没有安装 `pipx`，优先使用系统包管理器安装会更稳妥，比如 macOS 上用 `brew install pipx`，较新的 Ubuntu 用 `sudo apt install pipx`，Fedora 用 `sudo dnf install pipx`，然后执行 `pipx ensurepath`。

如果系统里没有现成的 `pipx` 包，再参考 [pipx 官方安装文档](https://pipx.pypa.io/stable/installation/) 或直接运行：

```bash
python3 -m pip install --user pipx
python3 -m pipx ensurepath
```

如果你本来就在虚拟环境里工作，用 `pip` 也完全可以：

```bash
python3 -m pip install uncommon-route
```

<details>
<summary><strong>安装排障：什么时候用 pip，什么时候用 pipx</strong></summary>

- 如果你是在安装一个平时直接在终端里用的 CLI 工具，优先用 `pipx install uncommon-route`。
- 如果你已经在某个项目的虚拟环境里，直接在那个环境里运行 `python -m pip install uncommon-route`。
- 如果你不确定 `pip` 指向哪个 Python，优先写成 `python3 -m pip ...`，这样不会装到错误的解释器里。
- 如果你的系统 Python 报了 “externally managed environment” 之类的错误，不要强行往系统环境里装，改用 `pipx` 或虚拟环境。
- 如果你必须绑定某个 Python 版本，`pipx` 也可以直接指定，比如：`pipx install --python python3.12 uncommon-route`。

</details>

### 2. 运行引导式配置

```bash
uncommon-route init
```

这个向导会一步步帮你：

- 选择接入方式：Commonstack、BYOK，或者本地 / 自定义 upstream
- 把 upstream 凭据保存到本地
- 配置 Claude Code、Codex、OpenAI SDK / Cursor
- 可选地直接把代理以后台方式启动起来

如果你想在启动前先检查一遍：

```bash
uncommon-route doctor
```

### 3. 把客户端指向代理

| 客户端 | 改动 |
|---|---|
| Claude Code | `export ANTHROPIC_BASE_URL="http://localhost:8403"` |
| Codex / Cursor / OpenAI SDK | `export OPENAI_BASE_URL="http://localhost:8403/v1"` |
| OpenClaw | 插件接入——详见 [openclaw.ai](https://openclaw.ai) |

然后把模型 ID 设为 `uncommon-route/auto`：

```python
client = OpenAI(base_url="http://localhost:8403/v1")
resp = client.chat.completions.create(model="uncommon-route/auto", messages=msgs)
# → 简单任务走便宜模型，复杂任务走顶配模型
```

支持 **Claude Code**、**Codex**、**Cursor**、**OpenAI SDK** 和 **OpenClaw**。

<details>
<summary><strong>手动配置（进阶）</strong></summary>

**方式 A — Commonstack 托管（推荐，一把 key 覆盖所有 provider）**

```bash
export UNCOMMON_ROUTE_UPSTREAM="https://api.commonstack.ai/v1"
export UNCOMMON_ROUTE_API_KEY="csk-your-key"
uncommon-route serve
```

一把 key 打通 OpenAI、Anthropic、Google、xAI、MiniMax、Moonshot、DeepSeek 等所有主流 provider——统一账单，无需逐家申请。

**方式 B — 自带 key（BYOK）**

```bash
uncommon-route provider add openai     sk-...
uncommon-route provider add anthropic  sk-ant-...
uncommon-route provider add google     AIza...
# 同样支持：xai、minimax、moonshot、deepseek
uncommon-route serve
```

Auto 路由只会在已注册的 provider 范围内选模型。

> **注意：** UncommonRoute **不会**自动读取 `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` 环境变量。请使用 `uncommon-route init`、本地保存的连接配置，或上面的手动方式。

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
| **语义信号** | 与已知任务模式的相似度（embedding） | ~20ms |
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
| **准确率** | 43% | **78%** |
| **任务完成率** | 100%（作弊——永远选最贵的） | **93.4%**（真正的路由决策） |
| **成本节省** | 0% | **82%** |

我们把这些数据告诉你，是因为比起让你被数字震撼到，我们更希望你能信任它。

---

## 性能数据

基于 [CommonRouterBench](https://github.com/CommonstackAI/CommonRouterBench) 测试——970 条真实 Agent 任务轨迹，覆盖 SWE-Bench、BFCL、MT-RAG、QMSum 和 PinchBench。所有数据通过生产代码端到端测量。

| 指标 | 数值 |
|---|---|
| **成本节省** | **82%**（对比全程顶配） |
| **任务完成率** | **93.4%** |
| **路由延迟** | **~20–25ms**（warm process, CPU, bge-small embedding） |
| **准确率** | **78%** tier 匹配 |

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

## 诊断与排障

如果用户遇到路由异常、上游报错，或者某次请求行为不对，现在可以直接导出本地诊断包，不需要先猜该收集哪些日志：

```bash
uncommon-route support bundle
uncommon-route support request <request_id>
```

诊断包会包含最近请求 trace、最近错误、统计摘要、provider / 配置快照，以及脱敏后的本地状态。默认只写到你的本机，只有你主动分享时才会离开电脑。

---

## 停用与卸载

停止代理服务：

- 前台运行时：在运行 `uncommon-route serve` 的终端里按 `Ctrl+C`
- 后台 daemon：运行 `uncommon-route stop`
- 查看后台日志：运行 `uncommon-route logs --follow`

如果你不想让客户端继续走 UncommonRoute，把 `uncommon-route init` 写进 shell rc 的那段配置删掉或注释掉即可。这个文件通常是 `~/.zshrc`、`~/.bashrc` 或 `~/.config/fish/config.fish`。改完后重开终端；如果只想让当前 shell 立刻失效，也可以直接执行：

```bash
unset OPENAI_BASE_URL OPENAI_API_KEY ANTHROPIC_BASE_URL ANTHROPIC_API_KEY
```

卸载包本身：

```bash
pipx uninstall uncommon-route
# 如果你是用 pip 装到某个特定环境里的：
python3 -m pip uninstall uncommon-route
```

如果你还想把本地状态一起删掉，再移除 `~/.uncommon-route/` 即可。这个目录里包含保存的连接信息、provider key、日志、trace 和诊断包。

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

### Provider 管理

```bash
uncommon-route provider list
uncommon-route provider add <name> <api-key>
uncommon-route provider remove <name>
```

支持的 provider 名：`commonstack`、`openai`、`anthropic`、`google`、`xai`、`minimax`、`moonshot`、`deepseek`。两种接入模式（托管上游 vs. BYOK）详见 [快速开始](#快速开始)。

<details>
<summary><strong>所有环境变量</strong></summary>

| 变量 | 含义 |
|---|---|
| `UNCOMMON_ROUTE_UPSTREAM` | 托管上游模式的 base URL（例如 `https://api.commonstack.ai/v1`）。BYOK 模式下忽略。 |
| `UNCOMMON_ROUTE_API_KEY` | 与 `UNCOMMON_ROUTE_UPSTREAM` 配对的 API key。不会作为各 provider key 的兜底。 |
| `UNCOMMON_ROUTE_PORT` | 本地代理端口（默认 8403） |

</details>

---

## 隐私

完全在本地运行，除非你主动选择分享，否则不会有任何数据离开你的电脑。

```bash
uncommon-route telemetry status
```

`uncommon-route support bundle` 生成的诊断包默认也只会保存在 `~/.uncommon-route/support/`。

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
