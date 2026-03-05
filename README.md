<div align="center">

<h1>UncommonRoute</h1>

<p><strong>SOTA LLM Router — 98% accuracy, &lt;1ms local routing</strong></p>

<p>
Route every LLM request to the optimal model.<br>
39-feature cascade classifier, session persistence, spend control.<br>
Pure local — no external API calls for routing decisions.
</p>

<img src="https://img.shields.io/badge/98%25_Accuracy-success?style=for-the-badge" alt="98% accuracy">&nbsp;
<img src="https://img.shields.io/badge/<1ms_Latency-blue?style=for-the-badge" alt="<1ms">&nbsp;
<img src="https://img.shields.io/badge/Zero_External_Calls-purple?style=for-the-badge" alt="Local">&nbsp;
<img src="https://img.shields.io/badge/OpenAI_Compatible-orange?style=for-the-badge" alt="OpenAI compatible">

<br><br>

[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776ab?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-98_passing-success?style=flat-square)]()
[![OpenClaw Plugin](https://img.shields.io/badge/OpenClaw-Plugin-orange?style=flat-square)](https://openclaw.ai)

</div>

---

## Quick Navigation

| Section | Description |
|---|---|
| [Quick Start](#quick-start) | Install in 30 seconds |
| [Usage Modes](#usage-modes) | CLI, SDK, Proxy, OpenClaw |
| [How It Works](#how-it-works) | Cascade classifier architecture |
| [Routing Tiers](#routing-tiers) | SIMPLE → MEDIUM → COMPLEX → REASONING |
| [Session Management](#session-management) | Sticky routing, auto-escalation |
| [Spend Control](#spend-control) | Per-request, hourly, daily limits |
| [Models & Pricing](#models--pricing) | Supported models and costs |
| [Configuration](#configuration) | Environment variables |
| [Benchmarks](#benchmarks) | Accuracy & latency results |

---

## Quick Start

**One-line install:**

```bash
curl -fsSL https://anjieyang.github.io/uncommon-route/install | bash
```

**Or with pip:**

```bash
pip install uncommon-route
```

**Or as an OpenClaw plugin:**

```bash
openclaw plugins install @anjieyang/uncommon-route
openclaw gateway restart
# Done — smart routing is automatic
```

---

## Usage Modes

### 1. CLI

```bash
uncommon-route route "what is 2+2"
# Model: moonshot/kimi-k2.5  Tier: SIMPLE  Savings: 97%

uncommon-route route --json "design a distributed database"
# Full JSON with model, tier, confidence, cost, fallback chain

uncommon-route debug "explain quicksort"
# Per-dimension scoring breakdown (structural + keyword + unicode)
```

### 2. Python SDK

```python
from uncommon_route import route, classify

decision = route("explain the Byzantine Generals Problem")
print(decision.model)       # google/gemini-3.1-pro
print(decision.tier)        # COMPLEX
print(decision.confidence)  # 0.87
print(decision.savings)     # 0.76

# Classification only (no model selection)
result = classify("hello")
print(result.tier)          # SIMPLE
print(result.signals)       # ['short_prompt', 'greeting_pattern']
```

### 3. HTTP Proxy (OpenAI-compatible)

```bash
uncommon-route serve --port 8403
```

Works with any OpenAI SDK client:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8403/v1",
    api_key="your-upstream-key",
)

response = client.chat.completions.create(
    model="uncommon-route/auto",   # smart routing
    messages=[{"role": "user", "content": "hello"}],
)
```

| Endpoint | Method | Description |
|---|---|---|
| `/v1/chat/completions` | POST | Chat with smart routing |
| `/v1/models` | GET | Available models |
| `/v1/spend` | GET/POST | Spend control |
| `/v1/sessions` | GET | Active sessions |
| `/health` | GET | Health + status |

### 4. OpenClaw Plugin

```bash
openclaw plugins install @anjieyang/uncommon-route
```

The plugin auto-installs Python dependencies, starts the proxy, and registers everything. Available commands in OpenClaw:

| Command | Description |
|---|---|
| `/route <prompt>` | Preview routing decision |
| `/spend status` | View spending limits |
| `/spend set hourly 5.00` | Set hourly limit |
| `/sessions` | View active sessions |

---

## How It Works

UncommonRoute uses a **cascade classifier** with three levels:

```
Input Prompt
     │
     ▼
┌─────────────────────┐
│ 1. Trivial Override  │  greeting / empty / very long → instant decision
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ 2. Learned Model    │  Averaged Perceptron on 39 features
│    (356µs avg)      │  12 structural + 15 unicode + 12 keyword
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ 3. Rule Fallback    │  hand-tuned weights when model unavailable
└─────────┬───────────┘
          │
          ▼
    Tier + Model + Cost
```

### Feature Groups (39 total)

**Structural (12):** normalized_length, enumeration_density, sentence_count, code_markers, math_symbols, nesting_depth, vocabulary_diversity, avg_word_length, alphabetic_ratio, functional_intent, unique_concept_density, requirement_phrases

**Unicode (15):** basic_latin, latin_ext, cyrillic, arabic, devanagari, thai, hangul_jamo, cjk_unified, hiragana, katakana, hangul_syllables, punctuation, digits, symbols_math

**Keyword (12):** code_presence, reasoning_markers, technical_terms, creative_markers, simple_indicators, imperative_verbs, constraint_count, output_format, domain_specificity, agentic_task, analytical_verbs, multi_step_patterns

---

## Routing Tiers

| Tier | When | Default Model | Example |
|---|---|---|---|
| **SIMPLE** | Greetings, lookups, translations | moonshot/kimi-k2.5 | "what is 2+2" |
| **MEDIUM** | Code tasks, explanations, summaries | moonshot/kimi-k2.5 | "explain quicksort" |
| **COMPLEX** | Multi-requirement system design | google/gemini-3.1-pro | "design a distributed DB with these 5 requirements..." |
| **REASONING** | Formal proofs, mathematical derivations | xai/grok-4-1-fast-reasoning | "prove sqrt(2) is irrational" |

---

## Session Management

Sessions prevent model switching mid-task. When enabled (default):

- **Sticky routing** — same session ID → same model across requests
- **30-minute timeout** — sessions auto-expire after inactivity
- **Three-strike escalation** — 3 identical failed requests → auto-upgrade to next tier

```python
# Sessions work via header
headers = {"X-Session-ID": "my-task-123"}

# Or auto-derived from first user message
```

---

## Spend Control

Set spending limits to prevent runaway costs:

```bash
uncommon-route spend set per_request 0.10   # max $0.10 per call
uncommon-route spend set hourly 5.00        # max $5/hour
uncommon-route spend set daily 20.00        # max $20/day
uncommon-route spend set session 3.00       # max $3/session
uncommon-route spend status                 # view current spending
uncommon-route spend history                # recent records
```

When a limit is hit, the proxy returns HTTP 429 with `reset_in_seconds`.

Data persists at `~/.uncommon-route/spending.json`.

---

## Models & Pricing

| Model | Input ($/1M) | Output ($/1M) | Tier |
|---|---|---|---|
| nvidia/gpt-oss-120b | $0.00 | $0.00 | SIMPLE fallback |
| google/gemini-2.5-flash-lite | $0.10 | $0.40 | SIMPLE fallback |
| deepseek/deepseek-chat | $0.28 | $0.42 | MEDIUM fallback |
| xai/grok-4-1-fast-reasoning | $0.20 | $0.50 | REASONING primary |
| moonshot/kimi-k2.5 | $0.60 | $3.00 | SIMPLE/MEDIUM primary |
| google/gemini-3.1-pro | $2.00 | $12.00 | COMPLEX primary |
| openai/gpt-5.2 | $1.75 | $14.00 | COMPLEX fallback |
| anthropic/claude-sonnet-4.6 | $3.00 | $15.00 | COMPLEX fallback |

Baseline comparison: anthropic/claude-opus-4.6 at $5.00/$25.00 per 1M tokens.

---

## Configuration

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `UNCOMMON_ROUTE_UPSTREAM` | `https://openrouter.ai/api/v1` | Upstream API URL |
| `UNCOMMON_ROUTE_PORT` | `8403` | Proxy port |
| `UNCOMMON_ROUTE_DISABLED` | `false` | Disable routing (passthrough) |
| `ANTHROPIC_API_KEY` | — | For comparison API only |

### OpenClaw Plugin Config

```yaml
plugins:
  entries:
    "@anjieyang/uncommon-route":
      port: 8403
      upstream: "https://openrouter.ai/api/v1"
      spendLimits:
        hourly: 5.00
        daily: 20.00
```

---

## Benchmarks

Evaluated on 2000+ multilingual prompts across 10 languages (EN, ZH, KO, JA, ES, PT, AR, RU, DE, HI):

| Metric | Value |
|---|---|
| **Overall Accuracy** | 98.4% |
| **Average Latency** | 356µs |
| **Features** | 39 (structural + unicode + keyword) |
| **Learning** | Averaged Perceptron |
| **External API Calls** | None (pure local) |

### Per-Tier F1 Scores

| Tier | F1 |
|---|---|
| SIMPLE | 0.988 |
| MEDIUM | 0.968 |
| COMPLEX | 0.987 |
| REASONING | 1.000 |

Run the benchmark suite yourself:

```bash
cd bench && python run.py
```

---

## Project Structure

```
├── uncommon_route/           # Core package
│   ├── router/               # Cascade classifier + model selection
│   │   ├── classifier.py     # Three-level cascade
│   │   ├── learned.py        # Averaged Perceptron (ScriptAgnosticClassifier)
│   │   ├── structural.py     # 12 structural + 15 unicode features
│   │   ├── keywords.py       # 12 keyword features
│   │   ├── selector.py       # Tier → model + fallback chain
│   │   └── model.json        # Trained weights
│   ├── proxy.py              # OpenAI-compatible ASGI proxy
│   ├── session.py            # Session persistence + escalation
│   ├── spend_control.py      # Time-windowed spending limits
│   ├── openclaw.py           # OpenClaw config integration
│   └── cli.py                # CLI entry point
├── openclaw-plugin/          # JS bridge for OpenClaw
│   ├── src/index.js          # Auto-install + lifecycle management
│   ├── package.json          # @anjieyang/uncommon-route
│   └── openclaw.plugin.json  # Plugin manifest
├── tests/                    # 98 tests (unit + integration + E2E)
├── bench/                    # Benchmarking suite + datasets
├── scripts/install.sh        # One-line installer
└── pyproject.toml            # PyPI-ready packaging
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
<sub>Built by <a href="https://github.com/anjieyang">Anjie Yang</a></sub>
</div>
