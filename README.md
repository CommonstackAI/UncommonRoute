<p align="right"><strong>English</strong> | <a href="https://github.com/CommonstackAI/UncommonRoute/blob/main/README.zh-CN.md">简体中文</a></p>

<div align="center">

<h1>UncommonRoute</h1>

<p><strong>Route prompts by difficulty, not habit.</strong></p>

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
<a href="LICENSE"><img src="https://img.shields.io/badge/License-Modified_MIT-22c55e?style=for-the-badge" alt="Modified MIT"></a>&nbsp;
<a href="https://github.com/CommonstackAI/UncommonRoute/actions/workflows/ci.yml"><img src="https://github.com/CommonstackAI/UncommonRoute/actions/workflows/ci.yml/badge.svg" alt="CI"></a>&nbsp;
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

- **~90-95% cost savings** in real Claude Code / OpenClaw sessions versus always using premium models
- **Zero keyword lists** — the classifier uses structural features + n-gram learning, no hardcoded patterns
- **Benchmark-driven quality** — model quality from [PinchBench](https://pinchbench.com) replaces price-based assumptions
- **Thompson Sampling** — natural exploration-exploitation balance across the model pool
- **3 feedback clicks** to change routing — user feedback takes effect immediately
- **341 passing tests**

That is the core story of the project: spend premium-model money where it changes the answer, not where it just burns the budget.

---

## Quick Start

If you are brand new, do these in order.

### 1. Install

```bash
pip install uncommon-route
```

Optional convenience installer. Review the script first if you are security-conscious:

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

The plugin starts the proxy for you, registers a local OpenClaw provider, and syncs the discovered upstream pool into OpenClaw once `/v1/models/mapping` is available.

The config-patch fallback is static by nature, so it only registers the virtual routing IDs.

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

### 1. Continuous difficulty, not discrete tiers

The classifier estimates a continuous difficulty score (0.0–1.0) from structural features and n-gram patterns. No keyword lists, no hardcoded rules. The score drives model selection through a quality prediction formula — there are no fixed tier boundaries in the routing logic.

Tiers (`SIMPLE` / `MEDIUM` / `COMPLEX`) still appear in logs, headers, and the dashboard, but they are display labels derived from the continuous score, not routing decisions.

### 2. Routing mode changes quality-vs-cost preference

| Mode | What it optimizes for |
| --- | --- |
| `auto` | balanced — best quality-per-dollar, adapts with difficulty |
| `fast` | cost-dominant — cheapest acceptable model |
| `best` | quality-dominant — highest quality, cost nearly ignored |

These show up as virtual model IDs:

- `uncommon-route/auto`
- `uncommon-route/fast`
- `uncommon-route/best`

Only these virtual IDs trigger routing. Explicit real model IDs still pass through unchanged.

The quality-vs-cost weight automatically increases with task difficulty: harder tasks prioritize quality more, even in `auto` mode.

### 3. Benchmark-driven quality, not price-based

Model quality comes from real benchmark data ([PinchBench](https://pinchbench.com) agent task scores), not from price assumptions. Quality scores are blended with observed experience through Bayesian updating — the system starts from benchmark data and adapts to real-world performance over time.

The selector uses **Thompson Sampling** (Beta distribution per model) for natural exploration-exploitation balance. Models with fewer observations have wider distributions, giving them chances to prove themselves.

### 4. Three layers of learning

| Layer | Source | What it learns |
| --- | --- | --- |
| **Benchmark prior** | PinchBench API + seed data | Model quality baselines (refreshed periodically) |
| **Implicit feedback** | HTTP failures, retrial detection, logprob confidence | Automatic quality signals from every request |
| **Explicit feedback** | User ok/weak/strong signals | Direct quality corrections (3 clicks to change routing) |

### 5. Agentic steps route correctly

Having tools in the request body does **not** inflate difficulty. A "hello" through Claude Code still routes as SIMPLE. The classifier evaluates the user's prompt on its own structural merits, not on whether tools happen to be available.

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
- selector state, default mode, and stored override rows
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
- inspect the provider-backed set with `uncommon-route provider models`
- preview what the live scorer would pick with `GET /v1/selector` or the dashboard

Only the first option forces the request immediately. The others help you inspect what the live selector can see.

The optional `--plan` field is metadata only. It is shown in `provider list`, but it does not replace an API key or unlock models by itself.

### Default mode and stored override state

```bash
uncommon-route config show
uncommon-route config set-default-mode fast
# Stored override rows for inspection / preview:
uncommon-route config set-tier auto SIMPLE moonshot/kimi-k2.5 --fallback google/gemini-2.5-flash-lite,deepseek/deepseek-chat
uncommon-route config set-tier best COMPLEX anthropic/claude-opus-4.6 --fallback anthropic/claude-sonnet-4.6 --strategy hard-pin
uncommon-route config reset-tier auto SIMPLE
```

The default mode is used when a request omits `model`. Explicit model IDs still pass through unchanged.

Tier overrides are persisted, surfaced in the dashboard/API, and shown in selector previews.

Important current behavior: the live pool-based request path still scores the discovered model pool at request time and does **not** yet enforce `primary`, `fallback`, or `--strategy hard-pin` as request-time routing controls.

If you need to force a model immediately, send that explicit non-virtual model ID directly.

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
| `GET /v1/routing-config` / `POST /v1/routing-config` | inspect or update stored default-mode and mode/tier override rows |
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

The classifier is local and keyword-free — it uses structural features and character n-grams only. You can retrain it on your own benchmark data:

```bash
python -c "from uncommon_route.router.classifier import train_and_save_model; train_and_save_model('bench/data/train.jsonl')"
```

Model experience and feedback data are stored at:

```text
~/.uncommon-route/model-experience.json
~/.uncommon-route/benchmark_cache.json
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

### Classifier accuracy

The v2 classifier uses structural features + n-grams only (no keyword lists). Trained on 1,904 examples, tested on a held-out set of 1,077 prompts:

| Metric | Value |
| --- | ---: |
| Training accuracy | **99.2%** |
| Held-out accuracy | **88.5%** |

The classifier's role in v2 is to provide a continuous difficulty signal, not to make the final routing decision. Benchmark quality data and Thompson Sampling compensate for classification errors.

### Real-world cost savings

In end-to-end testing through Claude Code with Commonstack upstream:

- **~90-95% cost reduction** versus always using premium models
- **28/28 requests successful** with quality maintained across all difficulty levels
- **15 different models** selected via Thompson Sampling
- **0 expensive model waste** on simple tasks
- **3 feedback clicks** sufficient to change routing behavior

Quality is maintained because the system uses [PinchBench](https://pinchbench.com) benchmark data to select models by measured agent-task performance, not by price.

### Reproduce the benchmark run

```bash
python -m bench.run
```

---

## Repo Layout

- `uncommon_route/` is the shipped runtime package: proxy, router, CLI, calibration.
- `bench/` contains offline evaluation datasets and benchmark scripts.
- `demo/` contains local comparison/demo apps. The comparison server now lives in `demo/compare_api.py`.
- `frontend/` contains dashboard/demo frontends.

The root-level `api.py` now exists only as a compatibility shim for the comparison demo, so the package boundary stays clear.

---

## Turn It Off Or Remove It

If you want to stop using UncommonRoute, there are three different levels:

1. stop the local proxy
2. clear all local records and state
3. fully uninstall and restore your client config

### 1. Stop the local proxy

```bash
# If you started it in background mode
uncommon-route stop
```

If you started `uncommon-route serve` in the foreground, stop it with `Ctrl+C`.

### 2. Clear all local records

By default, UncommonRoute stores local state under:

```text
~/.uncommon-route
```

If you set `UNCOMMON_ROUTE_DATA_DIR`, it uses that directory instead.

That local data directory can contain:

- route stats and spending history
- dashboard-saved primary connection and routing overrides
- BYOK provider keys
- online-learning weights and feedback buffers
- learned aliases, model-experience memory, logs, and local artifacts

To clear **all** local records, stop the proxy first and then move or delete the active data directory:

```bash
# Show the active data directory
echo "${UNCOMMON_ROUTE_DATA_DIR:-$HOME/.uncommon-route}"

# Recommended: move it aside as a backup first
mv "${UNCOMMON_ROUTE_DATA_DIR:-$HOME/.uncommon-route}" \
  "${UNCOMMON_ROUTE_DATA_DIR:-$HOME/.uncommon-route}.backup-$(date +%Y%m%d-%H%M%S)"

# Or permanently delete it if you are sure
# rm -rf "${UNCOMMON_ROUTE_DATA_DIR:-$HOME/.uncommon-route}"
```

If you only want to clear routing analytics, `uncommon-route stats reset` resets stats and pending feedback. It does **not** remove the rest of the local state.

### 3. Fully uninstall

If you installed the OpenClaw integration, remove that first:

```bash
openclaw plugins uninstall @anjieyang/uncommon-route

# If you used the config-patch fallback instead of the plugin:
uncommon-route openclaw uninstall
```

Stopping `serve` or uninstalling the package only stops the local proxy layer. It does **not** automatically restore your previous client config. If your client still points at `http://localhost:8403` or `http://localhost:8403/v1`, it will keep trying localhost until you restore the original settings.

Typical client rollback commands:

```bash
unset UNCOMMON_ROUTE_UPSTREAM
unset UNCOMMON_ROUTE_API_KEY
unset OPENAI_BASE_URL
unset ANTHROPIC_BASE_URL
```

Then remove the Python package with the same tool you used to install it:

```bash
pipx uninstall uncommon-route
# or
python -m pip uninstall uncommon-route
# or
pip uninstall uncommon-route
```

---

## Development

```bash
git clone https://github.com/CommonstackAI/UncommonRoute.git
cd UncommonRoute
pip install -e ".[dev]"
python -m pytest tests -v
```

The current test suite is `341 passed` on the latest local run.

---

## License

MIT — see [LICENSE](LICENSE).

---

<div align="center">
<sub>Built by <a href="https://github.com/anjieyang">Anjie Yang</a> · <a href="https://commonstack.ai/">Commonstack-compatible</a></sub>
</div>
