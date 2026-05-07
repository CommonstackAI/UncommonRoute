<p align="right"><strong>English</strong> | <a href="README.zh-CN.md">简体中文</a></p>

<div align="center">

<h1>UncommonRoute</h1>

**Cut your API bill in half without giving up performance.**

UncommonRoute plugs into Claude Code, Cursor, Codex, and the OpenAI SDK. It runs locally, analyzes task complexity, conversation structure, tool use, available models, and budget constraints, then routes each request to the right model for the job.

<strong>On a held-out 100-case SWE-bench Verified split, UncommonRoute solved 75/100 tasks vs 74/100 for Opus-only. With task quality matched, API cost dropped by 53%.</strong>

<a href="https://pypi.org/project/uncommon-route/"><img src="https://img.shields.io/pypi/v/uncommon-route?style=flat-square&logo=pypi&logoColor=white&label=PyPI" alt="PyPI"></a>
<a href="https://www.npmjs.com/package/@anjieyang/uncommon-route"><img src="https://img.shields.io/npm/v/@anjieyang/uncommon-route?style=flat-square&logo=npm&logoColor=white&label=npm" alt="npm"></a>
<a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.11+-3776ab?style=flat-square&logo=python&logoColor=white" alt="Python 3.11+"></a>
<a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-22c55e?style=flat-square" alt="MIT"></a>

<br><br>

<a href="#quick-start">Quick Start</a> ·
<a href="#how-uncommonroute-saves-money">Savings</a> ·
<a href="#why-uncommonroute">Why</a> ·
<a href="#visual-routing">Dashboard</a> ·
<a href="#benchmark">Benchmark</a> ·
<a href="#privacy">Privacy</a>

| | Opus-only | UncommonRoute | Saved |
|---|:---:|:---:|:---:|
| Tasks solved | 74 / 100 | **75 / 100** | Matched |
| API cost | $54.73 | **$25.66** | **-53%** |

<sub>Numbers from a held-out 100-case SWE-bench Verified split in <a href="https://github.com/CommonstackAI/TwinRouterBench">TwinRouterBench</a>. Reproduction commands below.</sub>

</div>

<br>

<p align="center">
  <img src="docs/assets/hero-home.png" alt="UncommonRoute Dashboard" width="800">
</p>

---

## Quick Start

```bash
pipx install uncommon-route
uncommon-route init
```

`init` walks you through connection setup, saves credentials, and configures Claude Code, Codex, Cursor, or the OpenAI SDK. After setup, run a health check anytime:

```bash
uncommon-route doctor
```

<details>
<summary>No <code>pipx</code>? Inside a venv?</summary>

- **macOS**: `brew install pipx && pipx ensurepath`
- **Ubuntu**: `sudo apt install pipx && pipx ensurepath`
- **Fedora**: `sudo dnf install pipx && pipx ensurepath`
- **Already inside a virtualenv**: `python3 -m pip install uncommon-route`
- **Seeing an "externally managed environment" error**: use `pipx` or a venv instead of forcing a system install.
- **Need a specific Python version**: `pipx install --python python3.12 uncommon-route`

</details>

---

## How UncommonRoute Saves Money

The savings don't come from using less AI. They come from not sending easy requests to frontier models.

```text
"hello"                         -> simple
"fix a typo in the README"       -> simple
"find and fix this failing test" -> medium
"refactor this 500-line module"  -> medium / complex
"design a distributed scheduler" -> complex
```

Simple requests go to lightweight models. Medium requests go to capable mid-tier models. Complex requests escalate to the strongest model you've configured. Each decision is made per request, so a single conversation isn't tied to one model.

---

## Why UncommonRoute

If you use AI agents for coding every day, a lot of that spend goes toward work that doesn't need the most expensive model: typo fixes, small edits, simple test runs, short explanations.

UncommonRoute does one thing. It doesn't replace Claude Code, Cursor, or Codex, and doesn't try to make cheaper models smarter. It focuses on one decision:

> Which model is the right fit for this request?

Routing happens locally and independently for each agent step. You can inspect every decision in the Dashboard instead of trusting a black-box proxy.

---

## Visual Routing

UncommonRoute isn't just a pass-through proxy. The Dashboard records and explains every routing decision: whether the request was classified as simple, medium, or complex, which model was selected, what it cost, and what you can tune next.

```bash
uncommon-route serve
# -> http://localhost:8403/dashboard/
```

With the Dashboard, you can:

- Preview how a prompt will be classified before sending it.
- Inspect each routed request by session, including model, latency, cost, and signal readout.
- See which complexity classes and models are driving your spend.
- Tune routing policy, fallbacks, budgets, provider keys, and model pools.
- Rate decisions as `too strong`, `just right`, or `too weak`; those labels train a local model overlay without touching the base model.

That Feedback loop is the part that matters after day one. If UncommonRoute routes something too aggressively or too conservatively, you can correct it in the Dashboard. Training happens locally, the base model stays intact, and you can roll back the overlay anytime.

---

## Supported Clients

| Client | Minimal setup | Notes |
|---|---|---|
| Claude Code | `export ANTHROPIC_BASE_URL="http://localhost:8403"` | Uses the Anthropic-compatible proxy |
| OpenAI SDK | `export OPENAI_BASE_URL="http://localhost:8403/v1"` | Use `uncommon-route/auto` as the model ID |
| Codex | `export OPENAI_BASE_URL="http://localhost:8403/v1"` | Uses the OpenAI-compatible API |
| Cursor | `export OPENAI_BASE_URL="http://localhost:8403/v1"` | No application code changes |
| OpenClaw | Install the plugin | See [openclaw.ai](https://openclaw.ai) |

Claude Code also needs a placeholder token:

```bash
export ANTHROPIC_AUTH_TOKEN="not-needed"
```

OpenAI SDK example:

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8403/v1")
resp = client.chat.completions.create(
    model="uncommon-route/auto",
    messages=msgs,
)
```

---

## Highlights

| Capability | Result |
|---|---|
| Local routing | The router runs locally; no extra hop through a cloud routing service |
| Per-request routing | Each agent step is routed independently instead of pinning the whole session to one model tier |
| Automatic model selection | Routes based on task difficulty, conversation structure, tool use, and provider availability |
| Explainable decisions | See complexity, confidence, signal readout, selected model, and cost for each route |
| Adjustable policy | Use `auto` / `fast` / `best`, or override simple / medium / complex with primary and fallback models |
| Spend caps | Set per-request, hourly, or daily API spend limits |
| Local training | Feedback updates a local model overlay; the base model is never overwritten and can be restored anytime |
| Drop-in integration | Claude Code, Cursor, Codex, OpenAI SDK, and OpenClaw work without application code changes |

---

## Benchmark

UncommonRoute is evaluated on [TwinRouterBench](https://github.com/CommonstackAI/TwinRouterBench): 970 router-visible prefixes from 520 instances across SWE-Bench, BFCL, mtRAG, QMSum, and PinchBench, with execution-verified target tier labels. The end-to-end validation below uses a 100-case held-out SWE-bench Verified split.

### Matched task quality, 53% lower API cost

| Policy | Tasks solved | API cost | vs Opus-only |
|---|:---:|:---:|:---:|
| Opus 4.6 only | 74 / 100 | $54.73 | — |
| **UncommonRoute** | **75 / 100** | **$25.66** | **-53%** |

Put another way: this isn't a "spend less, solve fewer tasks" trade-off. On this split, UncommonRoute matched Opus-only on tasks solved while cutting realized API spend by 53%.

"Tasks solved" means the number of successfully resolved tasks out of 100 held-out SWE-bench Verified cases. "API cost" is realized model-call spend and doesn't include the penalty cost reported in Table 4.

### Reproduce

```bash
python -m pip install -e ".[dev]"
python -m pip install "git+https://github.com/CommonstackAI/TwinRouterBench.git"
python scripts/eval_v2.py --split holdout
python scripts/bench_overhead.py --iterations 50 --json
```

### Routing Overhead

Local CPU, warm process:

| Metric | Latency |
|---|:---:|
| p50 | 25.6ms |
| p90 | 32.1ms |

Cold start loads the embedding model and can take a few seconds. After warm-up, a single `route()` call typically takes tens of milliseconds.

---

## Privacy

Routing runs on your machine. **Your prompts don't go through a separate routing service; they're sent only to the upstream provider you configure.**

```bash
uncommon-route telemetry status
```

Diagnostic exports are local by default:

```bash
uncommon-route support bundle
```

The redacted support bundle is written to `~/.uncommon-route/support/`. It leaves your machine only if you choose to share it.

---

## Spend Caps

Set a hard ceiling on API spend:

```bash
uncommon-route spend set daily 20.00
uncommon-route spend status
```

You can also configure per-request, hourly, or daily limits in the Dashboard. Once a limit is reached, requests fall back to the lowest-cost available tier instead of failing outright.

---

## How It Works

Each request runs through three local signals. The router first classifies task complexity, then picks the best model from your configured upstream.

| Signal | What it looks at | Typical overhead |
|---|---|---:|
| Metadata | Conversation structure, tool use, context depth | <1ms |
| Embedding | BGE classifier over the request, recent agent state, and metadata; KNN fallback when uncertain | ~25-35ms |
| Structural | Text and conversation complexity; active only when needed, shadow-tracked otherwise | <1ms |

The signals vote, and the ensemble decides the complexity class. The router then weighs capabilities, transport, upstream availability, and price. From the matching candidates, it picks the lowest-cost option. Unknown upstream pricing is handled conservatively.

Routing is **per request / per agent step**. The session isn't pinned to one model. Protocol constraints, such as Anthropic thinking continuations, are still respected.

UncommonRoute also learns from local feedback: high-confidence agreement grows the embedding index, while low-confidence predictions escalate instead of silently sending complex work to an underpowered model.

---

## Who It's For

- You use Claude Code, Cursor, Codex, or another coding agent every day.
- Most of your spend goes to frontier models, but many requests don't need that tier.
- You want lower API cost without sending prompts to an extra hosted router.
- You need routing at request granularity, not one model choice for the entire session.
- You want routing that is explainable, adjustable, and feedback-driven.

## Who It's Not For

- You only call LLMs occasionally and your bill is already small.
- You expect a router to make low-cost models fundamentally more capable. UncommonRoute doesn't make that claim.
- You want every request to use the strongest model, no matter what. You can use `uncommon-route/best`, but the savings will be smaller.

---

## FAQ

**Will this hurt quality?**

UncommonRoute doesn't blindly chase the cheapest model. Uncertain or high-risk requests escalate to stronger models, and the held-out SWE-bench Verified result above shows matched task quality on that split.

**Where do my prompts go?**

Routing runs locally. Your prompt is sent to the upstream provider you configure, not to a separate hosted routing service.

**What happens when the router is unsure?**

It falls back conservatively: low-confidence decisions escalate instead of quietly sending complex work to an underpowered model.

**Can I override the routing?**

Yes. Use `auto`, `fast`, or `best`, or configure primary and fallback models for simple / medium / complex requests.

**Can I use my own API keys?**

Yes. You can use Commonstack as a managed upstream or register your own provider keys with BYOK.

**Does feedback train anything?**

Yes. Feedback updates a local model overlay and labeled traces can calibrate runtime confidence. The base model is never overwritten, and you can roll back the overlay.

---

## Advanced Configuration

### Connect Providers

**Commonstack (managed)**: one key gets you OpenAI, Anthropic, Google, xAI, MiniMax, Moonshot, and DeepSeek.

```bash
export UNCOMMON_ROUTE_UPSTREAM="https://api.commonstack.ai/v1"
export UNCOMMON_ROUTE_API_KEY="csk-your-key"
uncommon-route serve
```

**BYOK provider keys**: auto-routing only considers providers you've registered.

```bash
uncommon-route provider add openai     sk-...
uncommon-route provider add anthropic  sk-ant-...
uncommon-route provider add google     AIza...
uncommon-route serve
```

> UncommonRoute doesn't automatically read `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`. Use `init`, a saved connection, or one of the manual setup paths above.

### Routing Modes

| Mode | Model ID | Behavior |
|---|---|---|
| auto | `uncommon-route/auto` | Default mode; optimizes for quality per dollar |
| fast | `uncommon-route/fast` | Cost-first; prefers lower-cost models when quality is acceptable |
| best | `uncommon-route/best` | Quality-first; prefers the strongest available model |

### Provider Management

```bash
uncommon-route provider list
uncommon-route provider add <name> <api-key>
uncommon-route provider remove <name>
```

Supported providers: `commonstack`, `openai`, `anthropic`, `google`, `xai`, `minimax`, `moonshot`, `deepseek`.

<details>
<summary><strong>Environment variables</strong></summary>

| Variable | Meaning |
|---|---|
| `UNCOMMON_ROUTE_UPSTREAM` | Upstream URL for the managed path, e.g. `https://api.commonstack.ai/v1`; ignored in BYOK mode |
| `UNCOMMON_ROUTE_API_KEY` | API key used with `UNCOMMON_ROUTE_UPSTREAM`; not a fallback for per-provider keys |
| `UNCOMMON_ROUTE_PORT` | Local proxy port, default 8403 |

</details>

---

## Diagnostics

If you hit routing errors, upstream failures, or need to file an issue, export a redacted diagnostics bundle:

```bash
uncommon-route support bundle
uncommon-route support request <request_id>
```

The bundle includes recent traces, errors, stats, provider/config snapshots, and redacted local state. It's saved locally by default.

---

## Stop and Uninstall

If it's running in the foreground, press `Ctrl+C`. If it's running as a daemon:

```bash
uncommon-route stop
uncommon-route logs --follow
```

To stop routing clients through UncommonRoute, remove the shell block added by `init`, then restart your terminal. Common locations include `~/.zshrc`, `~/.bashrc`, and `~/.config/fish/config.fish`.

For the current shell only:

```bash
unset OPENAI_BASE_URL OPENAI_API_KEY ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN ANTHROPIC_API_KEY
```

Uninstall:

```bash
pipx uninstall uncommon-route
# If installed inside a venv:
python3 -m pip uninstall uncommon-route
```

Remove local state, including connections, provider keys, logs, and traces:

```bash
rm -rf ~/.uncommon-route/
```

---

## Development

```bash
git clone https://github.com/CommonstackAI/UncommonRoute.git
cd UncommonRoute
pip install -e ".[dev]"
python -m pytest tests -v
```

---

## License

MIT. See [LICENSE](LICENSE).
