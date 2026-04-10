<p align="right"><strong>English</strong> | <a href="https://github.com/CommonstackAI/UncommonRoute/blob/main/README.zh-CN.md">简体中文</a></p>

<div align="center">

<h1>UncommonRoute</h1>

<p><strong>Route every LLM call to the cheapest model that actually works.</strong></p>

<p>
77% of your LLM budget is wasted on simple tasks that don't need a premium model.<br>
UncommonRoute measures this, then fixes it — automatically.
</p>

</div>
 
<table align="center">
<tr>
<td align="center"><h3>77%</h3><sub>cost savings (benchmarked)</sub></td>
<td align="center"><h3>90.3%</h3><sub>tasks completed successfully</sub></td>
<td align="center"><h3><10ms</h3><sub>routing overhead</sub></td>
<td align="center"><h3>407</h3><sub>tests passing</sub></td>
</tr>
</table>

<div align="center">
  
<p>
Built for <strong>Codex</strong>, <strong>Claude Code</strong>, <strong>Cursor</strong>, the <strong>OpenAI SDK</strong>, and <strong>OpenClaw</strong>.
</p>

<p>
<a href="#2-minute-setup"><strong>Quick Start</strong></a> ·
<a href="#how-it-works"><strong>How It Works</strong></a> ·
<a href="#try-it-yourself"><strong>Playground</strong></a> ·
<a href="#benchmarks"><strong>Benchmarks</strong></a>
</p>

<a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.11+-3776ab?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11+"></a>&nbsp;
<a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-22c55e?style=for-the-badge" alt="Modified MIT"></a>&nbsp;
<a href="#2-minute-setup"><img src="https://img.shields.io/badge/Claude_Code-Ready-f97316?style=for-the-badge&logo=anthropic&logoColor=white" alt="Claude Code"></a>&nbsp;
<a href="#2-minute-setup"><img src="https://img.shields.io/badge/Codex-Ready-412991?style=for-the-badge&logo=openai&logoColor=white" alt="Codex"></a>&nbsp;
<a href="#2-minute-setup"><img src="https://img.shields.io/badge/Cursor-Compatible-007acc?style=for-the-badge&logo=visual-studio-code&logoColor=white" alt="Cursor"></a>&nbsp;
<a href="https://openclaw.ai"><img src="https://img.shields.io/badge/OpenClaw-Plugin-e11d48?style=for-the-badge" alt="OpenClaw"></a>

</div>

---

## See It In Action
 
```text
"hello"                              → 🟢 gpt-4.1-nano       ($0.0008)
"fix the typo on line 3"             → 🟢 deepseek-chat       ($0.0012)
"summarize this log in one sentence" → 🟢 minimax-m2.7        ($0.0097)
"refactor this 500-line module"      → 🟠 claude-sonnet-4.6   ($0.0337)
"design a distributed scheduler"     → 🔴 claude-opus-4.6     ($0.0562)
```
 
One endpoint. The router picks the model. You just write code.

---

## The Problem

**Always on premium = burning money on simple tasks.**
You're on Claude Opus. Every tool call, status check, and log summary in your agent loop bills at Opus prices. One session: $50+. But most of those requests? A $0.001 model handles them fine.

**Daily driver on cheap models = stuck when it gets hard.**
You're on DeepSeek's coding plan. Good enough for everyday work. But when you hit complex architecture or large-scale refactoring, you know Opus should take over — there's just no automatic way to switch.

**Every $100 you spend on LLM calls, $77 was wasted.** We benchmarked it.

UncommonRoute fixes exactly this: simple requests go down, complex requests go up. You don't think about it.

```text
Your client
  (Codex / Claude Code / Cursor / OpenAI SDK / OpenClaw)
            |
            v
     UncommonRoute
   (runs on your machine)
            |
            v
    Your upstream API
 (Commonstack / OpenAI / Ollama / vLLM / ...)
```

---

## Why v2

Our v1 classifier had 88.5% accuracy on clean benchmark data. We shipped it.

Then we tested it on real agent conversations — multi-turn, tool-calling, messy context — and accuracy dropped to 43%. Over half the routing decisions were wrong.

We didn't patch it. We rebuilt from scratch.

v2 uses a multi-signal ensemble that analyzes conversation structure, not just text surface features. On 762 real agent task traces from [LLMRouterBench](../LLMRouterBench), tested end-to-end through the production code path:

| | v1 | v2 |
|---|---|---|
| **Accuracy** | 43% | **73.6%** |
| **Task pass rate** | 100% (cheated — always chose most expensive) | **90.3%** (real routing decisions) |
| **Cost savings** | 0% (no savings when you always pick premium) | **77%** |

We're telling you this because we'd rather you trust our numbers than be impressed by them.

---

## How It Works

Every request gets analyzed by three independent signals:

1. **Conversation structure** — How deep is the conversation? Are tools being used? How many rounds? (<1ms)
2. **Semantic matching** — How similar is this request to known tasks we've seen before? (~10ms)
3. **Text analysis** — Structural features of the prompt itself (runs in shadow mode, auto-activates when helpful)

The signals vote. The router picks the cheapest model whose capability matches the task. If signals disagree, it leans conservative — better to spend a little more than to fail the task.

**It gets smarter over time.** Signal weights adjust based on routing outcomes. The embedding index grows with usage. Bad signals get downweighted automatically.

---

## Try It Yourself

No API key needed — see how the router would classify any prompt:

```bash
uncommon-route explain "write a Python function that validates email addresses"

  Tier: mid (id=1)
  Confidence: 85.0%
  Method: direct
  Complexity: 0.40

  Signals:
    metadata      tier=1  confidence=0.50
    structural    tier=1  confidence=0.92 [shadow]
    embedding     tier=1  confidence=0.78
```

Or open the interactive Playground in your browser:

```bash
uncommon-route serve
# → http://localhost:8403/dashboard/playground
```

Type a prompt, watch the routing decision update in real time.

---

## 2-Minute Setup

### Install

```bash
pip install uncommon-route
```

### Configure upstream

```bash
export UNCOMMON_ROUTE_UPSTREAM="https://api.commonstack.ai/v1"  # or OpenAI, Ollama, etc.
export UNCOMMON_ROUTE_API_KEY="your-key-here"
```

### Start

```bash
uncommon-route serve
```

Point your client at the proxy — one line change:

| Client | Change this |
|---|---|
| **Codex / Cursor / OpenAI SDK** | `export OPENAI_BASE_URL="http://localhost:8403/v1"` |
| **Claude Code** | `export ANTHROPIC_BASE_URL="http://localhost:8403"` |
| **OpenClaw** | Plugin — see [openclaw.ai](https://openclaw.ai) |

Done. Your existing workflow is already saving money.

```bash
uncommon-route doctor  # not sure what's wrong? one command checks everything
```

---

## When NOT to Use This

UncommonRoute helps when your traffic is **mixed** — some easy, some hard. If every single request you send truly requires the strongest model (legal analysis, novel research, complex proofs), a router won't save you much.

The sweet spot: **agent workflows** where 60-80% of requests are status checks, file reads, simple edits, and summaries. That's where the 77% savings comes from.

---

## Dashboard

```bash
uncommon-route serve
# → http://127.0.0.1:8403/dashboard/
```

Real-time monitoring: request counts, latency, cost savings, model distribution, signal votes, and spend limits.

**Playground** — type a prompt, see the routing decision live.
**Route Explainer** — click any past request to see why it was routed where it was.

---

## Configuration

### Core environment variables

| Variable | Meaning |
|---|---|
| `UNCOMMON_ROUTE_UPSTREAM` | Upstream OpenAI-compatible API URL |
| `UNCOMMON_ROUTE_API_KEY` | API key for the upstream provider |
| `UNCOMMON_ROUTE_PORT` | Local proxy port (default 8403) |

### Three routing modes

| Mode | Virtual model ID | Behavior |
|---|---|---|
| **auto** | `uncommon-route/auto` | Balanced — best quality-per-dollar |
| **fast** | `uncommon-route/fast` | Cost-first — cheapest acceptable model |
| **best** | `uncommon-route/best` | Quality-first — highest quality, cost nearly ignored |

### Spend control

```bash
uncommon-route spend set per_request 0.10
uncommon-route spend set daily 20.00
uncommon-route spend status
```

### BYOK (Bring Your Own Key)

```bash
uncommon-route provider add openai sk-your-openai-key
uncommon-route provider add anthropic sk-ant-your-key
uncommon-route provider list
```

---

## Benchmarks

Tested on [LLMRouterBench](../LLMRouterBench) — 762 real agent task traces across SWE-Bench, MT-RAG, QMSum, and PinchBench. All numbers measured end-to-end through the production code path (no eval shortcuts).

| Metric | Value |
|---|---|
| Cost savings vs always-premium | **77%** |
| Task pass rate | **90.3%** |
| Routing overhead | **<10ms** |

```bash
python scripts/eval_v2.py --split holdout  # reproduce it yourself
```

---

## Privacy

UncommonRoute runs entirely on your machine. No data leaves your computer unless you explicitly opt in.

```bash
uncommon-route telemetry status  # check current state (default: off)
```

See [TELEMETRY.md](TELEMETRY.md) for full details on our opt-in data sharing program.

---

## Integration Reference

### Base URLs

| Client type | Base URL |
|---|---|
| OpenAI-compatible | `http://127.0.0.1:8403/v1` |
| Anthropic-style | `http://127.0.0.1:8403` |

### Python SDK

```python
from uncommon_route import route

decision = route("explain the Byzantine Generals Problem")
print(decision.model)       # "anthropic/claude-sonnet-4.6"
print(decision.tier)        # "COMPLEX"
print(decision.confidence)  # 0.87
```

### Response headers

`x-uncommon-route-model` · `x-uncommon-route-tier` · `x-uncommon-route-mode` · `x-uncommon-route-reasoning`

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `route` works but real requests fail | Check `UNCOMMON_ROUTE_UPSTREAM` and `UNCOMMON_ROUTE_API_KEY`, run `uncommon-route doctor` |
| Codex / Cursor can't connect | `OPENAI_BASE_URL` must end with `/v1` |
| Claude Code can't connect | `ANTHROPIC_BASE_URL` → router root, **not** `/v1` |
| Don't know what to run first | `uncommon-route doctor` |

---

## Development

```bash
git clone https://github.com/CommonstackAI/UncommonRoute.git
cd UncommonRoute
pip install -e ".[dev]"
python -m pytest tests -v  # 407 tests passing
```

---

## License

MIT — see [LICENSE](LICENSE).
