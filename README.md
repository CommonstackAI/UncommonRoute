<p align="right"><strong>English</strong> | <a href="https://github.com/CommonstackAI/UncommonRoute/blob/main/README.zh-CN.md">简体中文</a></p>

<div align="center">

<h1>UncommonRoute</h1>

<p><strong>Stop overpaying for every prompt.</strong></p>

<p>
UncommonRoute is a local LLM router that sits between your client and your upstream API.
Easy turns go cheap, hard turns go strong, and fallback chains are ready when the first choice fails.
</p>

<p>
Built for <strong>Codex</strong>, <strong>Claude Code</strong>, <strong>Cursor</strong>, the <strong>OpenAI SDK</strong>, and <strong>OpenClaw</strong>.
</p>

<p>
<a href="#quick-start"><strong>Quick Start</strong></a> ·
<a href="#how-routing-works"><strong>How It Works</strong></a> ·
<a href="#configuration-that-actually-matters"><strong>Configuration</strong></a> ·
<a href="#detailed-benchmarks"><strong>Benchmarks</strong></a>
</p>

<a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.11+-3776ab?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11+"></a>&nbsp;
<a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-22c55e?style=for-the-badge" alt="MIT"></a>&nbsp;
<img src="https://img.shields.io/badge/Tests-281_passing-16a34a?style=for-the-badge&logo=pytest&logoColor=white" alt="281 passing tests">&nbsp;
<a href="#quick-start"><img src="https://img.shields.io/badge/Claude_Code-Ready-f97316?style=for-the-badge&logo=anthropic&logoColor=white" alt="Claude Code"></a>&nbsp;
<a href="#quick-start"><img src="https://img.shields.io/badge/Codex-Ready-412991?style=for-the-badge&logo=openai&logoColor=white" alt="Codex"></a>&nbsp;
<a href="#quick-start"><img src="https://img.shields.io/badge/Cursor-Compatible-007acc?style=for-the-badge&logo=visual-studio-code&logoColor=white" alt="Cursor"></a>&nbsp;
<a href="https://openclaw.ai"><img src="https://img.shields.io/badge/OpenClaw-Plugin-e11d48?style=for-the-badge" alt="OpenClaw"></a>

</div>

---

## The Expensive Default

Most AI tools make one bad assumption: every request deserves the same model.

That works until your workflow starts spending premium-model money on:

- "what is 2+2?"
- tool selection
- log summarization
- boring middle turns in an agent loop

UncommonRoute is the small local layer that changes that default.

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
 (Commonstack / OpenAI / Ollama / vLLM / Parallax / ...)
```

It does not host models. It makes a fast local routing decision, forwards the request to your chosen upstream, and keeps enough fallback logic around to recover when upstream model names or availability do not line up cleanly.

---

## Why It Is Worth Trying

The pitch is simple: keep one local endpoint, let the router decide when a strong model is actually worth paying for.

- **92.3% held-out routing accuracy** on 763 hand-written prompts across 15 languages and 35 categories
- **67% lower simulated cost** on a 131-request coding session versus always using `anthropic/claude-opus-4.6`
- **~0.5ms average routing latency**
- **281 passing tests**

One benchmark snapshot:

| Scenario | Total cost |
| --- | ---: |
| Always `anthropic/claude-opus-4.6` | `$1.7529` |
| UncommonRoute | `$0.5801` |

That is the core story of the project: spend premium-model money where it changes the answer, not where it just burns the budget.

---

## Quick Start

If you are brand new, do these in order.

### 1. Install

```bash
pip install uncommon-route
```

Or use the installer:

```bash
curl -fsSL https://anjieyang.github.io/uncommon-route/install | bash
```

### 2. Prove the router works locally first

This step does **not** need a real upstream or API key.

```bash
uncommon-route route "write a Python function that validates email addresses"
uncommon-route debug "prove that sqrt(2) is irrational"
```

What this proves:

- the package is installed
- the local classifier works
- the router can produce a tier, model choice, and fallback chain

What this does **not** prove:

- your upstream is configured
- your client is connected through the proxy

### 3. Point it at a real upstream

Pick one example and export the variables.

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
# Local OpenAI-compatible servers (Ollama, vLLM, etc.)
export UNCOMMON_ROUTE_UPSTREAM="http://127.0.0.1:11434/v1"
```

```bash
# Parallax scheduler endpoint (experimental)
export UNCOMMON_ROUTE_UPSTREAM="http://127.0.0.1:3001/v1"
```

If your upstream does not need a key, you can skip `UNCOMMON_ROUTE_API_KEY`.

Parallax is still best treated as experimental here: public docs clearly expose `POST /v1/chat/completions`, but public `/v1/models` support is less obvious, so discovery-driven routing may be limited.

### 4. Start the proxy

```bash
uncommon-route serve
```

If the upstream is configured, the startup banner shows:

- the upstream host
- the local proxy URL
- the dashboard URL
- a quick health-check command

If the upstream is missing, the banner tells you exactly which environment variables to set next.

### 5. Connect the client you already use

Pick the path that matches your workflow.

<details>
<summary><strong>Codex</strong> · OpenAI-compatible local routing</summary>

```bash
uncommon-route setup codex
```

Manual version:

```bash
export OPENAI_BASE_URL="http://localhost:8403/v1"
export OPENAI_API_KEY="not-needed"
```

Then:

```bash
uncommon-route serve
codex
```

For smart routing, set:

```text
model = "uncommon-route/auto"
```

</details>

<details>
<summary><strong>Claude Code</strong> · Anthropic-style local routing</summary>

```bash
uncommon-route setup claude-code
```

Manual version:

```bash
export ANTHROPIC_BASE_URL="http://localhost:8403"
export ANTHROPIC_API_KEY="not-needed"
```

Then:

```bash
uncommon-route serve
claude
```

Claude Code talks to `/v1/messages`. UncommonRoute accepts Anthropic-style requests, routes them, and converts the response shape back transparently.

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

For Cursor, point "OpenAI Base URL" to `http://localhost:8403/v1`.

</details>

<details>
<summary><strong>OpenClaw</strong> · Plugin-based integration</summary>

```bash
openclaw plugins install @anjieyang/uncommon-route
openclaw gateway restart
```

The plugin starts the proxy for you and registers a local OpenClaw provider.

Example plugin config:

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

If your upstream needs authentication, set `UNCOMMON_ROUTE_API_KEY` in the environment where OpenClaw runs.

</details>

### 6. Verify end to end

```bash
uncommon-route doctor
curl http://127.0.0.1:8403/health
```

When something feels off, `uncommon-route doctor` should almost always be the first command you run.

---

## How Routing Works

You do not need to understand every internal detail to use the project, but the mental model matters.

### 1. Every request is classified into one of three tiers

| Tier | Typical requests |
| --- | --- |
| `SIMPLE` | greetings, short lookups, basic translation |
| `MEDIUM` | code tasks, explanations, summaries |
| `COMPLEX` | multi-constraint design and implementation work |

There is no fixed per-tier default model anymore. By default, the selector scores the discovered model pool for the active mode, so the chosen model can change as pricing, availability, capabilities, and feedback change.

### 2. Routing mode changes the style of decision

| Mode | What it optimizes for |
| --- | --- |
| `auto` | balanced default |
| `fast` | lighter, faster, and more cost-aware |
| `best` | highest quality |

These show up as virtual model IDs:

- `uncommon-route/auto`
- `uncommon-route/fast`
- `uncommon-route/best`

### 3. The selector scores the real pool, not a static shortlist

The router considers:

- estimated token cost
- observed latency and reliability
- cache affinity
- explicit user feedback
- BYOK-backed model preference
- free/local biases
- capability requirements like tool use or vision

If the upstream exposes `/v1/models`, UncommonRoute builds a live model pool and pricing map from that reality instead of pretending the world is static.

### 4. Session IDs still exist, but routing is no longer sticky

Session IDs are still derived per task, but they do **not** pin model selection anymore.

Today they mainly help:

- group cache keys
- scope composition checkpoints
- tag stats and debug traces
- rehydrate `artifact://...` context for the right task

### 5. Tool-heavy steps are cheaper than they look

A real agent workflow is not one giant reasoning turn.

There are many low-value middle steps:

- tool selection
- tool-result follow-up
- ordinary chat turns between heavier steps

UncommonRoute can detect those patterns and avoid spending the strongest reasoning model on turns that do not need it.

---

## Watch It Work

After starting the proxy, open:

```text
http://127.0.0.1:8403/dashboard/
```

The dashboard shows:

- request counts, latency, cost, and savings
- mode, tier, and model distribution
- upstream transport and cache behavior
- live routing configuration and default-mode overrides
- primary upstream and BYOK provider connections
- recent traffic, spend limits, and usage
- recent feedback state and submitted feedback results

Useful commands around the dashboard:

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

Background mode writes to:

- `~/.uncommon-route/serve.pid`
- `~/.uncommon-route/serve.log`

---

## Configuration That Actually Matters

### Core environment variables

| Variable | Meaning |
| --- | --- |
| `UNCOMMON_ROUTE_UPSTREAM` | Upstream OpenAI-compatible API URL |
| `UNCOMMON_ROUTE_API_KEY` | API key for the upstream provider |
| `UNCOMMON_ROUTE_PORT` | Local proxy port (`8403` by default) |
| `UNCOMMON_ROUTE_COMPOSITION_CONFIG` | Path to a composition-policy JSON file |
| `UNCOMMON_ROUTE_COMPOSITION_CONFIG_JSON` | Inline composition-policy JSON |

### Primary upstream and live connections

The effective primary upstream is resolved in this order:

1. CLI flags like `uncommon-route serve --upstream ...`
2. Environment variables like `UNCOMMON_ROUTE_UPSTREAM` and `UNCOMMON_ROUTE_API_KEY`
3. File-backed settings saved from the dashboard or `PUT /v1/connections`

Dashboard/API-managed primary connection values are stored at:

```text
~/.uncommon-route/connections.json
```

### Bring Your Own Key (BYOK)

If you want the router to prefer models backed by your own provider keys:

```bash
uncommon-route provider add openai sk-your-openai-key
uncommon-route provider add anthropic sk-ant-your-key
uncommon-route provider list
uncommon-route provider models
```

Provider config is stored at:

```text
~/.uncommon-route/providers.json
```

Important behavior today:

- `provider add` stores a known model set for that provider
- key verification uses `/models` when possible
- `GET /v1/models` still exposes only UncommonRoute virtual models, not your full upstream catalog

If you need a specific upstream model right now, do one of these:

- send that explicit non-virtual model ID directly
- pin it with `uncommon-route config set-tier ...`
- inspect the provider-backed set with `uncommon-route provider models`

The optional `--plan` field is metadata only. It is shown in `provider list`, but it does not replace an API key or unlock models by itself.

### Live routing config

```bash
uncommon-route config show
uncommon-route config set-default-mode fast
uncommon-route config set-tier auto SIMPLE moonshot/kimi-k2.5 --fallback google/gemini-2.5-flash-lite,deepseek/deepseek-chat
uncommon-route config set-tier best COMPLEX anthropic/claude-opus-4.6 --fallback anthropic/claude-sonnet-4.6 --strategy hard-pin
uncommon-route config reset-tier auto SIMPLE
```

The default mode is used when a request omits `model`. Explicit model IDs still pass through unchanged.

Use `--strategy hard-pin` when you want a tier to stay on the configured primary model unless that model actually fails upstream.

Routing overrides are stored at:

```text
~/.uncommon-route/routing_config.json
```

### Spend control

```bash
uncommon-route spend set per_request 0.10
uncommon-route spend set hourly 5.00
uncommon-route spend set daily 20.00
uncommon-route spend set session 3.00
uncommon-route spend status
uncommon-route spend history
```

When a limit is hit, the proxy returns HTTP `429` with `reset_in_seconds`.

Spend data is stored at:

```text
~/.uncommon-route/spending.json
```

---

## Integration Reference

This is the compact lookup section for SDK authors, agent builders, and people wiring UncommonRoute into other tools.

### Base URLs

| Client type | Base URL |
| --- | --- |
| OpenAI-compatible clients | `http://127.0.0.1:8403/v1` |
| Anthropic-style clients | `http://127.0.0.1:8403` |

### Virtual model IDs

| Model ID | Meaning |
| --- | --- |
| `uncommon-route/auto` | balanced default |
| `uncommon-route/fast` | lighter and faster |
| `uncommon-route/best` | highest quality |

### Useful endpoints

| Endpoint | Why you would use it |
| --- | --- |
| `GET /health` | liveness, config status, model-discovery status |
| `GET /v1/models` | virtual models exposed by the router |
| `GET /v1/models/mapping` | internal-to-upstream model mapping and pool view |
| `GET /v1/connections` / `PUT /v1/connections` | inspect or update the primary runtime connection |
| `GET /v1/routing-config` / `POST /v1/routing-config` | inspect or change routing mode/tier overrides |
| `GET /v1/stats` / `POST /v1/stats` | routing summary or reset |
| `GET /v1/stats/recent` | recent routed requests with feedback state |
| `GET /v1/selector` / `POST /v1/selector` | inspect selector state or preview a routing decision |
| `GET /v1/feedback` / `POST /v1/feedback` | inspect feedback state, submit signals, or rollback |
| `GET /dashboard/` | human-friendly monitoring UI |

### Useful response headers

On **routed** requests that use a virtual model, headers can include:

- `x-uncommon-route-model`
- `x-uncommon-route-tier`
- `x-uncommon-route-mode`
- `x-uncommon-route-step`
- `x-uncommon-route-reasoning`

On passthrough requests with explicit non-virtual model IDs, do not assume all of those routing headers will exist.

### Python SDK example

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

## Advanced Features

### Model discovery and mapping

Different upstreams use different model IDs. UncommonRoute fetches `/v1/models`, builds a live pool when possible, maps internal IDs to what the upstream actually serves, and records learned aliases when fallbacks prove a better match.

Useful commands:

```bash
uncommon-route doctor
curl http://127.0.0.1:8403/v1/models/mapping
```

### Composition pipeline

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

Useful headers for these flows:

- `x-uncommon-route-input-before`
- `x-uncommon-route-input-after`
- `x-uncommon-route-artifacts`
- `x-uncommon-route-semantic-calls`
- `x-uncommon-route-semantic-fallbacks`
- `x-uncommon-route-checkpoints`
- `x-uncommon-route-rehydrated`

### Anthropic-native transport

When routing lands on an Anthropic-family model and the upstream supports it, UncommonRoute can preserve Anthropic-native transport and caching semantics while still serving OpenAI-style clients normally.

### Local training

The classifier is local. You can retrain it on your own benchmark data.

From the repo root:

```bash
python - <<'PY'
from uncommon_route.router.classifier import train_and_save_model
train_and_save_model("bench/data/train.jsonl")
PY
```

Online feedback updates are stored separately at:

```text
~/.uncommon-route/model_online.json
```

---

## Troubleshooting

### "`route` works, but my app still cannot get responses"

`uncommon-route route ...` is a local routing decision. It does **not** call your upstream.

If real requests fail, check:

- `UNCOMMON_ROUTE_UPSTREAM`
- `UNCOMMON_ROUTE_API_KEY` if your provider needs one
- `uncommon-route doctor`

### "Codex or Cursor cannot connect"

For OpenAI-style tools, `OPENAI_BASE_URL` must end with `/v1`:

```bash
export OPENAI_BASE_URL="http://localhost:8403/v1"
```

### "Claude Code cannot connect"

For Anthropic-style tools, `ANTHROPIC_BASE_URL` should point at the router root, not `/v1`:

```bash
export ANTHROPIC_BASE_URL="http://localhost:8403"
```

### "My local upstream still fails discovery"

Some local or experimental servers expose `POST /chat/completions` but not a clean `/models` endpoint. In that case, passthrough may still work while live discovery stays limited. `uncommon-route doctor` will tell you whether discovery succeeded.

### "I do not know what to run first"

Run:

```bash
uncommon-route doctor
```

That one command usually tells you what is missing.

---

## Detailed Benchmarks

Two questions matter:

1. Does the router classify difficulty correctly?
2. Does that save real money in a realistic coding session?

### Held-out routing benchmark

Evaluated on **763 hand-written prompts** across **15 languages** and **35 categories**.

| Metric | UncommonRoute | ClawRouter | NotDiamond (cost) |
| --- | ---: | ---: | ---: |
| Accuracy | **92.3%** | 52.6% | 46.1% |
| Weighted F1 | **92.3%** | 47.0% | 38.0% |
| Latency / request | **0.5ms** | 0.6ms | 37.6ms |
| MEDIUM F1 | **88.7%** | 43.6% | 6.2% |
| COMPLEX F1 | **97.8%** | 61.7% | 0.0% |

### Real cost simulation

Simulated on a **131-request agent coding session** and compared against always sending every request to `anthropic/claude-opus-4.6`.

| Metric | Always Opus | UncommonRoute |
| --- | ---: | ---: |
| Total cost | $1.7529 | **$0.5801** |
| Cost saved | — | **67%** |
| Quality retained | 100% | **93.5%** |
| Routing accuracy | — | **90.8%** |

### Reproduce the benchmark run

If you also have the companion `router-bench/` directory checked out next to this repo, run:

```bash
cd ../router-bench && python -m router_bench.run
```

---

## Turn It Off Or Remove It

If you want to stop using UncommonRoute:

```bash
# If you started it in background mode
uncommon-route stop

# Remove the Python package
pip uninstall uncommon-route
```

If you started `uncommon-route serve` in the foreground, stop it with `Ctrl+C`.

Stopping `serve` only stops the local proxy. It does **not** automatically restore your previous client config. If your client still points at `http://localhost:8403` or `http://localhost:8403/v1`, it will keep trying localhost until you restore the original settings.

Typical rollback commands:

```bash
unset UNCOMMON_ROUTE_UPSTREAM
unset UNCOMMON_ROUTE_API_KEY
unset OPENAI_BASE_URL
unset ANTHROPIC_BASE_URL
```

If you installed the OpenClaw integration, also remove that registration:

```bash
openclaw plugins uninstall @anjieyang/uncommon-route

# Or, if you used the config-patch fallback instead of the plugin:
uncommon-route openclaw uninstall
```

---

## Development

```bash
git clone https://github.com/CommonstackAI/UncommonRoute.git
cd UncommonRoute
pip install -e ".[dev]"
python -m pytest tests -v
```

The current test suite is `281 passed` on the latest local run.

---

## License

MIT — see [LICENSE](LICENSE).

---

<div align="center">
<sub>Built by <a href="https://github.com/anjieyang">Anjie Yang</a> · <a href="https://commonstack.ai/">Commonstack-compatible</a></sub>
</div>
