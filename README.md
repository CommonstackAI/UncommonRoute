<p align="right"><strong>English</strong> | <a href="https://github.com/CommonstackAI/UncommonRoute/blob/main/README.zh-CN.md">简体中文</a></p>

<div align="center">

<h1>UncommonRoute</h1>

**Automatic model routing for lower LLM spend.**

Most of your LLM budget goes to simple tasks that don't need a premium model.
UncommonRoute picks the cheapest model that still gets the job done — automatically.

Current held-out eval: **91.8% task pass rate** with an **81.9 cost-savings score** on CommonRouterBench.

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

### 1. Install

```bash
pipx install uncommon-route
```

`pipx` is the best default for most CLI users: it installs UncommonRoute into its own isolated environment, keeps your system Python clean, and gives you a clean uninstall path.

A normal install includes the trained v2 runtime assets and embedding dependencies. You do not need a separate `[v2]` install for production routing.

If you do not have `pipx` yet, prefer your OS package manager when it is available (`brew install pipx` on macOS, `sudo apt install pipx` on recent Ubuntu, `sudo dnf install pipx` on Fedora), then run `pipx ensurepath`.

If that is not available, see the [pipx installation guide](https://pipx.pypa.io/stable/installation/) or install it with:

```bash
python3 -m pip install --user pipx
python3 -m pipx ensurepath
```

If you already work inside a virtual environment, `pip` is still fine:

```bash
python3 -m pip install uncommon-route
```

<details>
<summary><strong>Install troubleshooting: pip vs. pipx</strong></summary>

- If you are installing a command-line app for everyday use, prefer `pipx install uncommon-route`.
- If you are already inside a project virtual environment, use `python -m pip install uncommon-route` inside that environment.
- Prefer `python3 -m pip ...` over bare `pip ...` when you are unsure which Python interpreter `pip` points at.
- If your OS Python reports an "externally managed environment" error, use `pipx` or a virtual environment instead of forcing a system-wide install.
- If you need a specific interpreter, `pipx` can target it directly, for example: `pipx install --python python3.12 uncommon-route`.

</details>

### 2. Run the guided setup

```bash
uncommon-route init
```

The wizard walks you through:

- choosing a connection path: Commonstack, local/custom upstream, or BYOK
- saving upstream credentials locally
- configuring Claude Code, Codex, or OpenAI SDK / Cursor
- optionally starting the proxy in background

If you prefer to sanity-check before starting the proxy:

```bash
uncommon-route doctor
```

### 3. Point your client at the proxy

| Client | Change |
|---|---|
| Claude Code | `export ANTHROPIC_BASE_URL="http://localhost:8403"` and `export ANTHROPIC_AUTH_TOKEN="not-needed"` |
| Codex / Cursor / OpenAI SDK | `export OPENAI_BASE_URL="http://localhost:8403/v1"` |
| OpenClaw | Plugin — see [openclaw.ai](https://openclaw.ai) |

Then use `uncommon-route/auto` as the model ID:

```python
client = OpenAI(base_url="http://localhost:8403/v1")
resp = client.chat.completions.create(model="uncommon-route/auto", messages=msgs)
# → simple tasks → cheap model, complex tasks → premium model
```

Works with **Claude Code**, **Codex**, **Cursor**, the **OpenAI SDK**, and **OpenClaw**.

<details>
<summary><strong>Manual setup (advanced)</strong></summary>

**Commonstack managed upstream**

```bash
export UNCOMMON_ROUTE_UPSTREAM="https://api.commonstack.ai/v1"
export UNCOMMON_ROUTE_API_KEY="csk-your-key"
uncommon-route serve
```

One key gives you OpenAI, Anthropic, Google, xAI, MiniMax, Moonshot, DeepSeek, and more — consolidated billing, no per-provider setup.

**Bring your own keys (BYOK)**

```bash
uncommon-route provider add openai     sk-...
uncommon-route provider add anthropic  sk-ant-...
uncommon-route provider add google     AIza...
# also supported: xai, minimax, moonshot, deepseek
uncommon-route serve
```

Auto-routing will only consider models backed by a registered provider.

> **Note:** UncommonRoute does **not** auto-read `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`. Use `uncommon-route init`, a saved connection, or one of the manual paths above.

</details>

---

## How It Works

Every request is analyzed by multiple local signals, then routed to the cheapest capable model available from your configured upstream:

```
"hello"                              → economy tier
"fix the typo on line 3"             → economy / balanced tier
"refactor this 500-line module"      → balanced / premium tier
"design a distributed scheduler"     → premium tier
```

Actual model IDs and prices come from the live upstream model catalog plus your local overrides. UncommonRoute does not rely on a single hardcoded model list.

| Signal | What it does | Speed (CPU, warm) |
|---|---|---|
| **Metadata** | Conversation structure, tool usage, depth | <1ms |
| **Embedding** | Trained BGE classifier over the user request, recent agent state, and metadata; KNN fallback when uncertain | ~25–35ms end-to-end warm route overhead |
| **Structural** | Text and conversation complexity; active on selected requests, shadow-tracked otherwise | <1ms |

End-to-end `route()` overhead on a warm process is typically **~25–35ms** on CPU and is dominated by the embedding signal. Cold start includes loading the embedding model and can take seconds on a fresh process or machine; after warmup, routing stays local.

Signals vote. The ensemble picks the tier. The router then selects the cheapest model that satisfies tier, capability, transport, and upstream availability constraints. Unknown or dynamic upstream pricing is treated conservatively instead of being interpreted as a real negative price.

Routing is **per request / per agent step**, not sticky for an entire session. Protocol-level constraints still apply when the request requires them, for example Anthropic thinking continuations.

**It gets smarter over time.** Local feedback can adjust signal weights, high-confidence agreement can grow the embedding index, and low-confidence predictions escalate instead of silently under-routing.

---

## Why v2

Our v1 classifier hit 88.5% accuracy on clean benchmark data. We shipped it.

Then we tested on real agent conversations — multi-turn, tool-calling, messy context — and accuracy dropped to 43%. More than half the routing decisions were wrong.

We didn't patch it. We rebuilt from scratch.

| | v1 | v2 |
|---|---|---|
| **Tier match accuracy** | 43% | **74.0%** held-out |
| **Task pass rate** | 100% (cheated — always chose most expensive) | **91.8%** with real routing |
| **Cost-savings score** | 0% | **81.9** |

We're telling you this because we'd rather you trust our numbers than be impressed by them.

---

## Benchmarks

Tested on [CommonRouterBench](https://github.com/CommonstackAI/CommonRouterBench) — 970 real agent task traces across SWE-Bench, BFCL, MT-RAG, QMSum, and PinchBench. The public numbers below use the 196-row held-out split, not the training or calibration rows.

| Metric | Value |
|---|---|
| **Task pass rate** | **91.8%** |
| **Tier match accuracy** | **74.0%** |
| **Cost-savings score** | **81.9** vs always-premium baseline |
| **Overall score** | **76.7** |
| **Warm routing overhead** | **p50 25.6ms / p90 32.1ms** on a local CPU run |

```bash
python -m pip install -e ".[dev]"
python -m pip install "git+https://github.com/CommonstackAI/CommonRouterBench.git"
python scripts/eval_v2.py --split holdout
python scripts/bench_overhead.py --iterations 50 --json
```

---

## Dashboard

```bash
uncommon-route serve
# → http://localhost:8403/dashboard/
```

Real-time monitoring, interactive playground, cost tracking, and model routing configuration — all in a Nothing Design-inspired interface.

---

## Diagnostics

When a user hits a routing or upstream issue, you can export a local support bundle without guessing which logs to collect:

```bash
uncommon-route support bundle
uncommon-route support request <request_id>
```

The bundle includes recent request traces, recent errors, stats summaries, provider/config snapshots, and redacted local state. It stays on your machine until you choose to share it.

---

## Stopping and Uninstalling

To stop the proxy:

- foreground run: press `Ctrl+C` in the terminal running `uncommon-route serve`
- background daemon: run `uncommon-route stop`
- background logs: run `uncommon-route logs --follow`

To stop routing your clients through UncommonRoute, remove or comment out the shell block that `uncommon-route init` added to your shell rc file (`~/.zshrc`, `~/.bashrc`, or `~/.config/fish/config.fish`), then restart your terminal. For the current shell only, you can also unset the proxy variables:

```bash
unset OPENAI_BASE_URL OPENAI_API_KEY ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN ANTHROPIC_API_KEY
```

To uninstall the package:

```bash
pipx uninstall uncommon-route
# or, if you installed it with pip in a specific environment:
python3 -m pip uninstall uncommon-route
```

If you also want to remove local state, delete `~/.uncommon-route/`. That directory contains saved connections, provider keys, logs, traces, and support bundles.

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

### Managing providers

```bash
uncommon-route provider list
uncommon-route provider add <name> <api-key>
uncommon-route provider remove <name>
```

Supported names: `commonstack`, `openai`, `anthropic`, `google`, `xai`, `minimax`, `moonshot`, `deepseek`. See [Quick Start](#quick-start) for the two setup paths (managed upstream vs. BYOK).

<details>
<summary><strong>All environment variables</strong></summary>

| Variable | Meaning |
|---|---|
| `UNCOMMON_ROUTE_UPSTREAM` | Upstream base URL for the managed path (e.g. `https://api.commonstack.ai/v1`). Ignored in BYOK mode. |
| `UNCOMMON_ROUTE_API_KEY` | API key paired with `UNCOMMON_ROUTE_UPSTREAM`. Not a fallback for per-provider keys. |
| `UNCOMMON_ROUTE_PORT` | Local proxy port (default 8403) |

</details>

---

## Privacy

Runs entirely on your machine. No data leaves unless you opt in.

```bash
uncommon-route telemetry status
```

Diagnostics exports are also local-first: `uncommon-route support bundle` writes a redacted zip under `~/.uncommon-route/support/` by default.

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
