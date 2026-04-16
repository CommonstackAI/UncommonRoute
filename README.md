<p align="right"><strong>English</strong> | <a href="https://github.com/CommonstackAI/UncommonRoute/blob/main/README.zh-CN.md">简体中文</a></p>

<div align="center">

<h1>UncommonRoute</h1>

**Cut your LLM costs by 82% with automatic model routing.**

Most of your LLM budget goes to simple tasks that don't need a premium model.
UncommonRoute picks the cheapest model that still gets the job done — automatically.

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

**[Quick Start](#quick-start)** · **[How It Works](#how-it-works)** · **[Benchmarks](#benchmarks)** · **[Dashboard](#dashboard)** · **[Configuration](#configuration)**

</div>

---

## Quick Start

```bash
pip install uncommon-route
```

```bash
export UNCOMMON_ROUTE_UPSTREAM="https://api.openai.com/v1"  # or any OpenAI-compatible API
export UNCOMMON_ROUTE_API_KEY="your-key"
uncommon-route serve
```

Point your client at the proxy — one line change:

```python
client = OpenAI(base_url="http://localhost:8403/v1")
resp = client.chat.completions.create(model="uncommon-route/auto", messages=msgs)
# → simple tasks → cheap model, complex tasks → premium model
```

Works with **Codex**, **Claude Code**, **Cursor**, the **OpenAI SDK**, and **OpenClaw**.

<details>
<summary><strong>Client-specific setup</strong></summary>

| Client | Change |
|---|---|
| Codex / Cursor / OpenAI SDK | `export OPENAI_BASE_URL="http://localhost:8403/v1"` |
| Claude Code | `export ANTHROPIC_BASE_URL="http://localhost:8403"` |
| OpenClaw | Plugin — see [openclaw.ai](https://openclaw.ai) |

</details>

---

## How It Works

Every request is analyzed by three independent signals, then routed to the cheapest capable model:

```
"hello"                              → 🟢 nano         $0.0008
"fix the typo on line 3"             → 🟢 deepseek     $0.0012
"refactor this 500-line module"      → 🟠 sonnet       $0.0337
"design a distributed scheduler"     → 🔴 opus         $0.0562
```

| Signal | What it does | Speed |
|---|---|---|
| **Metadata** | Conversation structure, tool usage, depth | <1ms |
| **Embedding** | Semantic similarity to known task patterns | ~10ms |
| **Structural** | Text complexity features (shadow mode) | <1ms |

Signals vote. The ensemble picks the tier. The router selects the cheapest model in that tier. If uncertain, it leans conservative — better to spend a little more than to fail the task.

**It gets smarter over time.** Signal weights adjust from routing outcomes. The embedding index grows with usage. Low-confidence predictions automatically escalate.

---

## Why v2

Our v1 classifier hit 88.5% accuracy on clean benchmark data. We shipped it.

Then we tested on real agent conversations — multi-turn, tool-calling, messy context — and accuracy dropped to 43%. More than half the routing decisions were wrong.

We didn't patch it. We rebuilt from scratch.

| | v1 | v2 |
|---|---|---|
| **Accuracy** | 43% | **78%** |
| **Task pass rate** | 100% (cheated — always chose most expensive) | **93.4%** (real routing) |
| **Cost savings** | 0% | **82%** |

We're telling you this because we'd rather you trust our numbers than be impressed by them.

---

## Benchmarks

Tested on [CommonRouterBench](https://github.com/CommonstackAI/CommonRouterBench) — 970 real agent task traces across SWE-Bench, BFCL, MT-RAG, QMSum, and PinchBench. All numbers measured end-to-end through the production code path.

| Metric | Value |
|---|---|
| **Cost savings** | **82%** vs always-premium |
| **Task pass rate** | **93.4%** |
| **Routing overhead** | **<10ms** |
| **Accuracy** | **78%** tier match |

```bash
python scripts/eval_v2.py  # reproduce it yourself
```

---

## Dashboard

```bash
uncommon-route serve
# → http://localhost:8403/dashboard/
```

Real-time monitoring, interactive playground, cost tracking, and model routing configuration — all in a Nothing Design-inspired interface.

---

## Configuration

### Routing modes

| Mode | Model ID | Behavior |
|---|---|---|
| **auto** | `uncommon-route/auto` | Balanced — best quality-per-dollar |
| **fast** | `uncommon-route/fast` | Cost-first — cheapest acceptable |
| **best** | `uncommon-route/best` | Quality-first — strongest available |

### Spend limits

```bash
uncommon-route spend set daily 20.00
uncommon-route spend status
```

### BYOK (Bring Your Own Key)

```bash
uncommon-route provider add openai sk-your-key
uncommon-route provider add anthropic sk-ant-your-key
```

<details>
<summary><strong>All environment variables</strong></summary>

| Variable | Meaning |
|---|---|
| `UNCOMMON_ROUTE_UPSTREAM` | Upstream OpenAI-compatible API URL |
| `UNCOMMON_ROUTE_API_KEY` | API key for the upstream |
| `UNCOMMON_ROUTE_PORT` | Local proxy port (default 8403) |

</details>

---

## Privacy

Runs entirely on your machine. No data leaves unless you opt in.

```bash
uncommon-route telemetry status
```

---

## Development

```bash
git clone https://github.com/CommonstackAI/UncommonRoute.git
cd UncommonRoute && pip install -e ".[dev]"
python -m pytest tests -v
```

---

## License

MIT — see [LICENSE](LICENSE).
