<p align="right"><strong>English</strong> | <a href="https://github.com/anjieyang/UncommonRoute/blob/main/README.zh-CN.md">简体中文</a></p>

<div align="center">

<h1>UncommonRoute</h1>

<p><strong>Route prompts by difficulty, not habit.</strong></p>

<p>
UncommonRoute is a local LLM router that sits between your client and your model provider.
It sends easy requests to cheaper models, hard requests to stronger models, and keeps a fallback chain ready when the first choice fails.
</p>

<p>
Built for real tools like <strong>Codex</strong>, <strong>Claude Code</strong>, <strong>Cursor</strong>, the <strong>OpenAI SDK</strong>, and <strong>OpenClaw</strong>.
</p>

<p>
Held-out routing benchmark: <strong>92.3% accuracy</strong> ·
Average routing latency: <strong>~0.5ms</strong> ·
Simulated coding-session savings vs always-Opus: <strong>67%</strong>
</p>

<p>
<a href="#quick-start"><strong>Quick Start</strong></a> ·
<a href="#connect-your-client"><strong>Connect Your Client</strong></a> ·
<a href="#agent-quick-reference"><strong>Agent Quick Reference</strong></a> ·
<a href="#how-routing-works"><strong>How Routing Works</strong></a>
</p>

<a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.11+-3776ab?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11+"></a>&nbsp;
<a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-22c55e?style=for-the-badge" alt="MIT"></a>&nbsp;
<img src="https://img.shields.io/badge/Tests-169_passing-16a34a?style=for-the-badge&logo=pytest&logoColor=white" alt="169 tests">&nbsp;
<a href="#connect-your-client"><img src="https://img.shields.io/badge/Claude_Code-Ready-f97316?style=for-the-badge&logo=anthropic&logoColor=white" alt="Claude Code"></a>&nbsp;
<a href="#connect-your-client"><img src="https://img.shields.io/badge/Codex-Ready-412991?style=for-the-badge&logo=openai&logoColor=white" alt="Codex"></a>&nbsp;
<a href="#connect-your-client"><img src="https://img.shields.io/badge/Cursor-Compatible-007acc?style=for-the-badge&logo=visual-studio-code&logoColor=white" alt="Cursor"></a>&nbsp;
<a href="https://openclaw.ai"><img src="https://img.shields.io/badge/OpenClaw-Plugin-e11d48?style=for-the-badge" alt="OpenClaw"></a>

</div>

---

## Why This Exists

Most AI tools send every request to the same model.

That is simple, but it is usually wasteful:

- "What is 2+2?" does not need the same model as "Design a fault-tolerant distributed database".
- Tool-heavy agent loops often spend most of their time on boring middle steps.
- Switching your whole workflow to the most expensive model is easy, but expensive.

UncommonRoute fixes that by making one local decision per request:

1. Classify how difficult the request is.
2. Pick a model for that difficulty and routing profile.
3. Keep fallbacks ready if the upstream rejects or fails.

You keep one local endpoint. The router handles the model choice.

---

## The 15-Second Mental Model

```text
Your client
  (Codex / Claude Code / Cursor / OpenAI SDK)
            |
            v
     UncommonRoute
   (runs on your machine)
            |
            v
    Your upstream API
 (Parallax / Commonstack / OpenAI / Ollama / vLLM / ...)
```

Examples: [Parallax](https://github.com/GradientHQ/parallax), [Commonstack](https://commonstack.ai/), OpenAI, Ollama, vLLM.

Important terms:

| Term | Plain-English meaning |
|---|---|
| **Client** | The thing you already use, like Codex or Claude Code |
| **Upstream** | The real model API that generates responses |
| **Profile** | A routing strategy like `auto`, `eco`, or `premium` |
| **Tier** | The difficulty bucket: `SIMPLE`, `MEDIUM`, `COMPLEX`, `REASONING` |
| **Virtual model** | A special model name like `uncommon-route/auto` that means "pick for me" |

> **The most important beginner fact:** UncommonRoute does **not** host models. It routes requests to an upstream provider that you choose.

---

## Quick Start

If you are brand new, follow these steps in order.

### 0. What you need

- Python 3.11 or newer
- A terminal
- For real chat responses: one upstream API

Good upstream choices:

- [**Commonstack**](https://commonstack.ai/) if you want one key that can reach multiple providers
- **OpenAI** if you already use OpenAI directly
- [**Parallax**](https://github.com/GradientHQ/parallax) / **Ollama / vLLM** if you want to route to a local OpenAI-compatible server

### 1. Install

```bash
pip install uncommon-route
```

Or use the installer:

```bash
curl -fsSL https://anjieyang.github.io/uncommon-route/install | bash
```

### 2. Try the router locally first

This step does **not** need an API key.

```bash
uncommon-route route "write a Python function that validates email addresses"
uncommon-route debug "prove that sqrt(2) is irrational"
```

What this proves:

- the package is installed
- the local classifier works
- the router can choose a tier and model

What this does **not** prove:

- your upstream is configured
- your client can talk through the proxy

### 3. Configure an upstream

Pick one example and export the environment variables.

```bash
# Commonstack: one key, many providers
export UNCOMMON_ROUTE_UPSTREAM="https://api.commonstack.ai/v1"
export UNCOMMON_ROUTE_API_KEY="csk-..."
```

```bash
# OpenAI direct
export UNCOMMON_ROUTE_UPSTREAM="https://api.openai.com/v1"
export UNCOMMON_ROUTE_API_KEY="sk-..."
```

```bash
# Parallax scheduler endpoint (experimental local OpenAI-style upstream)
export UNCOMMON_ROUTE_UPSTREAM="http://127.0.0.1:3001/v1"
```

```bash
# Other local OpenAI-compatible servers (Ollama, vLLM, etc.)
export UNCOMMON_ROUTE_UPSTREAM="http://127.0.0.1:11434/v1"
```

If your upstream does not need a key, you can skip `UNCOMMON_ROUTE_API_KEY`.

Parallax is listed as experimental here: its public docs and source clearly expose `POST /v1/chat/completions`, but I could not find a public `/v1/models` route, so UncommonRoute model discovery may be limited.

### 4. Start the proxy

```bash
uncommon-route serve
```

If your upstream is configured, you should see a banner with:

- the upstream host
- the local proxy URL
- the dashboard URL
- a quick health-check command

If your upstream is **not** configured yet, the banner tells you exactly which `export` commands to run next.

### 5. Verify that it is healthy

```bash
uncommon-route doctor
curl http://127.0.0.1:8403/health
```

`doctor` is the first command to run when anything feels off.

If you are using a local upstream like Ollama or vLLM, make sure that local server is already running before you expect `doctor` to pass the reachability check.

### 6. Connect your client

Pick the client you already use:

| If you use | Do this |
|---|---|
| **Codex** | `uncommon-route setup codex` |
| **Claude Code** | `uncommon-route setup claude-code` |
| **OpenAI SDK / Cursor** | `uncommon-route setup openai` |
| **OpenClaw** | `openclaw plugins install @anjieyang/uncommon-route` |

Each `setup` command prints the exact next step for your shell or client.

---

## Connect Your Client

You only need one of these sections.

<details>
<summary><strong>Codex</strong> · OpenAI-compatible local routing for Codex</summary>

```bash
uncommon-route setup codex
```

That command prints the exact shell config to add. Manually, the important part is:

```bash
export OPENAI_BASE_URL="http://localhost:8403/v1"
export OPENAI_API_KEY="not-needed"
```

Then:

```bash
uncommon-route serve
codex
```

For smart routing, use:

```text
model = "uncommon-route/auto"
```

</details>

<details>
<summary><strong>Claude Code</strong> · Anthropic-style local routing for Claude Code</summary>

```bash
uncommon-route setup claude-code
```

Manually, the important part is:

```bash
export ANTHROPIC_BASE_URL="http://localhost:8403"
export ANTHROPIC_API_KEY="not-needed"
```

Then:

```bash
uncommon-route serve
claude
```

Claude Code talks to the Anthropic-style `/v1/messages` endpoint. UncommonRoute converts formats and handles smart routing automatically.

</details>

<details>
<summary><strong>OpenAI SDK / Cursor</strong> · One local OpenAI-compatible base URL</summary>

```bash
uncommon-route setup openai
```

Python example:

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

Cursor users can point "OpenAI Base URL" to `http://localhost:8403/v1`.

</details>

<details>
<summary><strong>OpenClaw</strong> · Plugin-based integration</summary>

```bash
openclaw plugins install @anjieyang/uncommon-route
```

The plugin handles dependency installation, proxy startup, and registration.

</details>

---

## Agent Quick Reference

If you are wiring UncommonRoute into another tool, script, or agent loop, this is the minimum contract to know.

### Base URLs

| Client type | Base URL |
|---|---|
| **OpenAI-compatible clients** | `http://127.0.0.1:8403/v1` |
| **Anthropic-style clients** | `http://127.0.0.1:8403` |

### Virtual routing profiles

| Model ID | What it means |
|---|---|
| `uncommon-route/auto` | Balanced default |
| `uncommon-route/eco` | Cheapest capable model first |
| `uncommon-route/premium` | Quality-first routing |
| `uncommon-route/free` | Free-first, then cheapest capable fallback |
| `uncommon-route/agentic` | Tool-heavy workflow routing |

### Useful commands for scripts

```bash
uncommon-route route --json --no-feedback "summarize this log file"
uncommon-route doctor
uncommon-route stats
uncommon-route logs --follow
```

### Useful response headers

- `x-uncommon-route-model`
- `x-uncommon-route-tier`
- `x-uncommon-route-profile`
- `x-uncommon-route-step`
- `x-uncommon-route-reasoning`

### Useful endpoints

| Endpoint | Why you would use it |
|---|---|
| `GET /health` | Basic liveness and config status |
| `GET /v1/models` | Virtual models exposed by the router |
| `GET /v1/models/mapping` | Internal model names mapped to upstream names |
| `GET /v1/stats` | Routing analytics summary |
| `POST /v1/stats` | Reset routing analytics |
| `GET /v1/stats/recent` | Recent routed requests and feedback state |
| `GET /v1/selector` | Inspect selector state and live routing preferences |
| `POST /v1/selector` | Preview routing for a prompt or request body |
| `GET /dashboard/` | Human-friendly monitoring UI |

### Success criteria

Your integration is "live" when all of these are true:

- `uncommon-route doctor` shows the upstream and key are configured
- `GET /health` returns `{"status": "ok", ...}`
- routed requests include `x-uncommon-route-model` and `x-uncommon-route-tier`

---

## Everyday Usage

<details>
<summary><strong>CLI</strong> · Inspect routing locally without sending a real upstream request</summary>

Use the CLI when you want to inspect routing locally without sending a real request upstream.

```bash
uncommon-route route "what is 2+2"
uncommon-route route --json --no-feedback "design a distributed database"
uncommon-route debug "explain quicksort"
```

What each command is for:

- `route`: get the chosen tier, model, savings estimate, and fallback chain
- `route --json`: same information in machine-readable form
- `debug`: see the feature breakdown behind the classification

</details>

<details>
<summary><strong>Python SDK</strong> · Call the router directly inside Python</summary>

Use the SDK when you want routing decisions directly inside Python.

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
<summary><strong>HTTP Proxy</strong> · Put UncommonRoute in front of real clients and apps</summary>

Use the proxy when you want real applications to send requests through UncommonRoute.

```bash
uncommon-route serve --port 8403
```

OpenAI-compatible example:

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

Non-virtual model names are passed through unchanged, so you can still target a specific model when you want to.

</details>

---

## Dashboard And Diagnostics

After starting the proxy, open:

```text
http://127.0.0.1:8403/dashboard/
```

The dashboard shows:

- request counts, latency, cost, and savings
- tier and model distribution
- upstream transport and cache behavior
- live routing configuration
- active sessions
- spend limits and recent usage

Useful local commands:

```bash
uncommon-route doctor
uncommon-route serve --daemon
uncommon-route stop
uncommon-route logs
uncommon-route logs --follow
uncommon-route sessions
uncommon-route stats
```

Background mode writes to:

- PID: `~/.uncommon-route/serve.pid`
- Logs: `~/.uncommon-route/serve.log`

---

## Configuration

### Core Environment Variables

| Variable | Default | Meaning |
|---|---|---|
| `UNCOMMON_ROUTE_UPSTREAM` | — | Upstream OpenAI-compatible API URL |
| `UNCOMMON_ROUTE_API_KEY` | — | API key for the upstream provider |
| `UNCOMMON_ROUTE_PORT` | `8403` | Local proxy port |
| `UNCOMMON_ROUTE_DISABLED` | `false` | Disable routing and act as passthrough |
| `UNCOMMON_ROUTE_COMPOSITION_CONFIG` | — | Path to a composition-policy JSON file |
| `UNCOMMON_ROUTE_COMPOSITION_CONFIG_JSON` | — | Inline composition-policy JSON |

### Bring Your Own Key (BYOK)

If you have direct API keys for providers and want the router to prefer those models, register them:

```bash
uncommon-route provider add openai sk-your-openai-key
uncommon-route provider add anthropic sk-ant-your-key
uncommon-route provider list
```

BYOK keys are verified on add when possible. Provider config is stored at:

```text
~/.uncommon-route/providers.json
```

### Live Routing Config

You can override the default model table per profile and tier:

```bash
uncommon-route config show
uncommon-route config set-tier auto SIMPLE moonshot/kimi-k2.5 --fallback google/gemini-2.5-flash-lite,deepseek/deepseek-chat
uncommon-route config set-tier premium COMPLEX anthropic/claude-opus-4.6 --fallback anthropic/claude-sonnet-4.6 --mode hard-pin
uncommon-route config reset-tier auto SIMPLE
```

Use `--mode hard-pin` when you want a tier to stay on the configured primary model unless that model actually fails upstream.

### Spend Control

Set safety limits to stop runaway cost:

```bash
uncommon-route spend set per_request 0.10
uncommon-route spend set hourly 5.00
uncommon-route spend set daily 20.00
uncommon-route spend set session 3.00
uncommon-route spend status
uncommon-route spend history
```

When a limit is hit, the proxy returns HTTP `429` with `reset_in_seconds`.

Spending data is stored at:

```text
~/.uncommon-route/spending.json
```

---

## How Routing Works

You do not need to understand every internal detail to use the tool, but this mental model helps.

### 1. Each request is placed into one of four tiers

| Tier | Typical requests | Default primary |
|---|---|---|
| `SIMPLE` | greetings, short lookups, basic translation | `moonshot/kimi-k2.5` |
| `MEDIUM` | code tasks, explanations, summaries | `moonshot/kimi-k2.5` |
| `COMPLEX` | multi-constraint design and implementation work | `google/gemini-3.1-pro` |
| `REASONING` | proofs, derivations, hard mathematical reasoning | `xai/grok-4-1-fast-reasoning` |

### 2. The routing profile chooses the style of decision

| Profile | Best for |
|---|---|
| `auto` | balanced default |
| `eco` | lowest expected cost |
| `premium` | quality-first |
| `free` | free-first, then cheapest capable fallback |
| `agentic` | tool-heavy workflows |

### 3. A local selector chooses a model and fallback chain

The selector considers:

- profile preferences
- estimated token cost
- observed latency and reliability
- cache affinity
- explicit user feedback
- BYOK and free/local biases

### 4. Sessions reduce unnecessary switching

By default, sessions:

- hold on to an already-adequate model within a task
- upgrade when a task becomes harder
- avoid needless downgrade churn
- expire after 30 minutes of inactivity

### 5. Agentic steps are treated differently

Tool-heavy workflows often contain cheap middle steps.

UncommonRoute detects cases like:

- tool selection
- tool-result follow-up
- general chat turns

That allows it to use cheaper tool-capable models for boring steps and save stronger reasoning models for the turns that actually need them.

---

## Common Problems

If you are new, these are the mistakes people hit most often.

### "`route` works, but my app still cannot get responses"

`uncommon-route route ...` is a local routing decision. It does not call your upstream.

If real chat requests fail:

- check `UNCOMMON_ROUTE_UPSTREAM`
- check `UNCOMMON_ROUTE_API_KEY` if your provider needs one
- run `uncommon-route doctor`

### "Codex cannot connect"

For OpenAI-style tools, `OPENAI_BASE_URL` must end with `/v1`:

```bash
export OPENAI_BASE_URL="http://localhost:8403/v1"
```

### "Claude Code cannot connect"

For Anthropic-style tools, `ANTHROPIC_BASE_URL` should point at the router root, not `/v1`:

```bash
export ANTHROPIC_BASE_URL="http://localhost:8403"
```

### "I do not know which command to run first"

Start here:

```bash
uncommon-route doctor
```

That one command usually tells you what is missing.

---

## Advanced Features

Once the basics are working, these are the features that make the router more powerful.

### Model Mapping

Different upstreams use different model IDs. UncommonRoute fetches `/v1/models`, maps internal names to upstream names, and retries through the fallback chain if the first model is unavailable.

Useful commands:

```bash
uncommon-route doctor
curl http://127.0.0.1:8403/v1/models/mapping
```

### Composition Pipeline

Very large tool outputs are not always forwarded verbatim.

The proxy can:

- compact oversized text and JSON
- offload large tool results into local artifacts
- create semantic side-channel summaries
- checkpoint long histories
- rehydrate `artifact://...` references on demand

Artifacts are stored under:

```text
~/.uncommon-route/artifacts/
```

Useful response headers:

- `x-uncommon-route-input-before`
- `x-uncommon-route-input-after`
- `x-uncommon-route-artifacts`
- `x-uncommon-route-semantic-calls`
- `x-uncommon-route-semantic-fallbacks`
- `x-uncommon-route-checkpoints`
- `x-uncommon-route-rehydrated`

### Anthropic-Native Transport

When routing lands on an Anthropic-family model and the upstream supports it, UncommonRoute can preserve Anthropic-native transport and caching semantics while still serving OpenAI-style clients normally.

### Local Training

The classifier is local, not a SaaS black box. You can retrain it on your own benchmark data:

```bash
python - <<'PY'
from uncommon_route.router.classifier import train_and_save_model
train_and_save_model("bench/data/train.jsonl")
PY
```

---

## Benchmarks

Two questions matter:

1. Does the router classify difficulty correctly?
2. Does that save real money in a realistic coding session?

### Held-Out Routing Benchmark

Evaluated on **763 hand-written prompts** across **15 languages** and **35 categories**.

| Metric | UncommonRoute | ClawRouter | NotDiamond (cost) |
|---|---|---|---|
| Accuracy | **92.3%** | 52.6% | 46.1% |
| Weighted F1 | **92.3%** | 47.0% | 38.0% |
| Latency / request | **0.5ms** | 0.6ms | 37.6ms |
| MEDIUM F1 | **88.7%** | 43.6% | 6.2% |
| REASONING F1 | **97.8%** | 61.7% | 0.0% |

### Real Cost Simulation

Simulated on a **131-request agent coding session** and compared against always sending every request to `anthropic/claude-opus-4.6`.

| Metric | Always Opus | UncommonRoute |
|---|---|---|
| Total cost | $1.7529 | **$0.5801** |
| Cost saved | — | **67%** |
| Quality retained | 100% | **93.5%** |
| Routing accuracy | — | **90.8%** |

### Reproduce The Benchmarks

```bash
cd ../router-bench && python -m router_bench.run
```

---

## Project Structure

```text
├── uncommon_route/           # Core package
│   ├── router/               # Classifier + selector + model table
│   ├── proxy.py              # ASGI proxy (OpenAI + Anthropic endpoints)
│   ├── session.py            # Session persistence + escalation
│   ├── spend_control.py      # Spending limits
│   ├── providers.py          # BYOK provider management
│   ├── feedback.py           # Online feedback loop
│   ├── composition.py        # Tool-result compaction / checkpointing
│   ├── artifacts.py          # Local artifact storage
│   ├── stats.py              # Routing analytics
│   └── static/               # Built dashboard assets
├── frontend/dashboard/       # Dashboard source
├── openclaw-plugin/          # OpenClaw integration
├── tests/                    # Unit + integration + end-to-end tests
├── bench/                    # Benchmark data and training scripts
├── scripts/install.sh        # Installer
└── pyproject.toml            # Packaging and dependencies
```

---

## Development

```bash
git clone https://github.com/anjieyang/UncommonRoute.git
cd UncommonRoute
pip install -e ".[dev]"
python -m pytest tests/ -v
```

---

## License

MIT — see [LICENSE](LICENSE).

---

<div align="center">
<sub>Built by <a href="https://github.com/anjieyang">Anjie Yang</a> · <a href="https://commonstack.ai/">Commonstack-compatible</a></sub>
</div>
