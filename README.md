<p align="right"><strong>English</strong> | <a href="https://github.com/CommonstackAI/UncommonRoute/blob/main/README.zh-CN.md">简体中文</a></p>

<div align="center">

<h1>UncommonRoute</h1>

<p><strong>Stop overpaying for every prompt.</strong></p>

<p>
A lightweight local LLM router that automatically balances cost and quality, saving you 90%+ on every session.
</p>

</div>
 
<table align="center">
<tr>
<td align="center"><h3>~90–95%</h3><sub>real-world cost reduction</sub></td>
<td align="center"><h3>88.5%</h3><sub>classifier accuracy</sub></td>
<td align="center"><h3>100%</h3><sub>request success rate</sub></td>
<td align="center"><h3>1,077</h3><sub>tests passing</sub></td>
</tr>
</table>

<div align="center">
  
<p>
Built for <strong>Codex</strong>, <strong>Claude Code</strong>, <strong>Cursor</strong>, the <strong>OpenAI SDK</strong>, and <strong>OpenClaw</strong>.
</p>

<p>
<a href="#2-minute-setup"><strong>Quick Start</strong></a> ·
<a href="#how-it-works"><strong>How It Works</strong></a> ·
<a href="#configuration"><strong>Configuration</strong></a> ·
<a href="#real-numbers"><strong>Benchmarks</strong></a>
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

## See It In Action
 
<img width="947" height="450" alt="image" src="https://github.com/user-attachments/assets/9df2066f-f0fc-4ace-99cd-3a56f08cb52e" />
 
```text
"hello"                              → 🟢 gpt-4.1-nano       ($0.0008)
"fix the typo on line 3"             → 🟢 deepseek-chat       ($0.0012)
"summarize this log in one sentence" → 🟢 minimax-m2.7        ($0.0097)
"refactor this 500-line module"      → 🟠 claude-sonnet-4.6   ($0.0337)
"design a distributed scheduler"     → 🔴 claude-opus-4.6     ($0.0562)
```
 
One endpoint. The router picks the model. You just write code.
 
## One URL Solves Your Biggest Pain Point
 
**Always on premium = burning money on simple tasks.**
You're on Claude Opus. Every tool call, status check, and log summary in your agent loop bills at Opus prices. One session: $50+. But most of those requests? A $0.001 model handles them fine.
 
**Daily driver on cheap models = stuck when it gets hard.**
You're on MiniMax or DeepSeek's coding plan. Good enough for everyday work. But when you hit complex architecture or large-scale refactoring, you know Opus should take over — there's just no automatic way to switch.
 
**UncommonRoute fixes exactly this: it automatically picks the right model for every request.**
 
Simple requests go down. Complex requests go up. You don't think about it.
 
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
 
---
 
## $50 → $3, Same Results
 
| | Without UncommonRoute | With UncommonRoute |
|---|---|---|
| **Cost per session** | $50+ | ~$3–5 |
| **Hard tasks** | ✅ Premium model | ✅ Same premium model |
| **Simple tasks** | ✅ Overkill | ✅ Right-sized |
| **Models used** | 1 | 15 (auto-selected) |
| **Success rate** | 28/28 | 28/28 |
 
Measured in a real Claude Code session.
 
## 2-Minute Setup
 
### Install
 
```bash
pip install uncommon-route
```
 
### Verify (no API key needed)
 
```bash
uncommon-route route "write a Python function that validates email addresses"
# → shows difficulty tier, model selection, fallback chain
```
 
### Configure upstream
 
```bash
# Pick one:
export UNCOMMON_ROUTE_UPSTREAM="https://api.commonstack.ai/v1"  # Commonstack
export UNCOMMON_ROUTE_UPSTREAM="https://api.openai.com/v1"       # OpenAI
export UNCOMMON_ROUTE_UPSTREAM="http://127.0.0.1:11434/v1"       # Ollama / vLLM
export UNCOMMON_ROUTE_API_KEY="your-key-here"                    # if needed
```
 
### Start
 
```bash
uncommon-route serve
```
 
Then point your client at the proxy — one line change:
 
| Client | Change this |
|---|---|
| **Codex / Cursor / OpenAI SDK** | `export OPENAI_BASE_URL="http://localhost:8403/v1"` |
| **Claude Code** | `export ANTHROPIC_BASE_URL="http://localhost:8403"` |
| **OpenClaw** | Plugin — see [OpenClaw integration](#openclaw-integration) |
 
Done. Your existing workflow is already saving money.
 
### Not sure what's wrong?
 
```bash
uncommon-route doctor  # one command checks everything
```
 
---
 
## How It Works
 
### Continuous difficulty scoring
 
The classifier estimates a difficulty score (0.0–1.0) from structural features and character n-grams. No keyword lists, no hardcoded rules. SIMPLE / MEDIUM / COMPLEX appear in logs and the dashboard, but they're display labels, not decision boundaries.
 
### Three routing modes
 
| Mode | Virtual model ID | Behavior |
|---|---|---|
| **auto** | `uncommon-route/auto` | Balanced — best quality-per-dollar, adapts with difficulty |
| **fast** | `uncommon-route/fast` | Cost-first — cheapest acceptable model |
| **best** | `uncommon-route/best` | Quality-first — highest quality, cost nearly ignored |
 
Only virtual model IDs trigger routing. Explicit real model IDs pass through unchanged.
 
### Quality from benchmarks, not guesswork
 
Model quality comes from [PinchBench](https://pinchbench.com) agent task scores, not price assumptions. The selector uses Thompson Sampling (one Beta distribution per model) — models with fewer observations get wider distributions and chances to prove themselves. Quality scores improve over time through Bayesian updating.
 
### Three layers of learning
 
| Layer | Source | What it learns |
|---|---|---|
| Benchmark prior | PinchBench API + seed data | Model quality baselines |
| Implicit feedback | HTTP failures, retrial detection, logprob confidence | Automatic quality signals per request |
| Explicit feedback | User ok / weak / strong signals | Direct corrections — 3 clicks to change routing |
 
### Agentic steps route correctly
 
Tools in the request body don't inflate difficulty. A "hello" through Claude Code still routes as SIMPLE. The classifier evaluates the user's prompt on its own structural merits.
 
---
 
## Dashboard
 
```bash
uncommon-route serve
# open http://127.0.0.1:8403/dashboard/
```
 
See request counts, latency, cost savings, model distribution, selector state, spend limits, and recent feedback in real time.
 
```bash
uncommon-route serve --daemon  # background mode
uncommon-route stop
uncommon-route logs --follow
uncommon-route stats
```
 
---
 
## Configuration
 
### Core environment variables
 
| Variable | Meaning |
|---|---|
| `UNCOMMON_ROUTE_UPSTREAM` | Upstream OpenAI-compatible API URL |
| `UNCOMMON_ROUTE_API_KEY` | API key for the upstream provider |
| `UNCOMMON_ROUTE_PORT` | Local proxy port (default 8403) |
 
### Bring Your Own Key (BYOK)
 
```bash
uncommon-route provider add openai sk-your-openai-key
uncommon-route provider add anthropic sk-ant-your-key
uncommon-route provider list
uncommon-route provider models
```
 
### Routing overrides
 
```bash
uncommon-route config set-default-mode fast
uncommon-route config set-tier auto SIMPLE moonshot/kimi-k2.5 \
  --fallback google/gemini-2.5-flash-lite,deepseek/deepseek-chat
uncommon-route config set-tier best COMPLEX anthropic/claude-opus-4.6 \
  --fallback anthropic/claude-sonnet-4.6 --strategy hard-pin
```
 
> **Note:** The live pool scorer runs at request time and does **not** yet enforce `--strategy hard-pin` at the request level. To force a specific model immediately, send that non-virtual model ID directly.
 
### Spend control
 
```bash
uncommon-route spend set per_request 0.10
uncommon-route spend set hourly 5.00
uncommon-route spend set daily 20.00
uncommon-route spend status
```
 
Returns HTTP 429 with `reset_in_seconds` when a limit is hit.
 
---
 
## Integration Reference
 
### Base URLs
 
| Client type | Base URL |
|---|---|
| OpenAI-compatible | `http://127.0.0.1:8403/v1` |
| Anthropic-style | `http://127.0.0.1:8403` |
 
### Key endpoints
 
| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness + config status |
| `GET /v1/models` | Virtual models exposed by the router |
| `GET /v1/models/mapping` | Internal-to-upstream model mapping |
| `GET /v1/selector` | Inspect or preview routing decisions |
| `POST /v1/feedback` | Submit quality feedback |
| `GET /dashboard/` | Monitoring UI |
 
### Response headers (routed requests)
 
`x-uncommon-route-model` · `x-uncommon-route-tier` · `x-uncommon-route-mode` · `x-uncommon-route-reasoning`
 
### Python SDK
 
```python
from uncommon_route import classify, route
 
decision = route("explain the Byzantine Generals Problem")
print(decision.model)       # "anthropic/claude-sonnet-4.6"
print(decision.tier)        # "COMPLEX"
print(decision.confidence)  # 0.87
```
 
Full API reference: [docs/api.md](docs/api.md)
 
---
 
## Advanced Features
 
<details>
<summary><strong>Composition pipeline</strong> — handling oversized tool outputs</summary>
 
The proxy can compact oversized text/JSON, offload large tool results to local artifacts, create semantic side-channel summaries, and checkpoint long histories. Artifacts are stored in `~/.uncommon-route/artifacts/`.
 
</details>
 
<details>
<summary><strong>Anthropic-native transport</strong></summary>
 
When routing lands on an Anthropic-family model, UncommonRoute can preserve Anthropic-native transport and caching semantics while serving OpenAI-style clients normally.
 
</details>
 
<details>
<summary><strong>Local classifier retraining</strong></summary>
 
The classifier uses structural features and character n-grams only — no keyword lists. Retrain on your own data:
 
```bash
python -c "from uncommon_route.router.classifier import train_and_save_model; train_and_save_model('bench/data/train.jsonl')"
```
 
</details>
 
<details>
<summary><strong>Model discovery and mapping</strong></summary>
 
UncommonRoute fetches `/v1/models` from your upstream, builds a live model pool, maps internal IDs to what the upstream actually serves, and records learned aliases when fallbacks find a better match.
 
```bash
uncommon-route doctor
curl http://127.0.0.1:8403/v1/models/mapping
```
 
</details>
 
---
 
## Real Numbers
 
### Classifier accuracy
 
1,904 training samples, 1,077 held-out test samples:
 
| Metric | Value |
|---|---|
| Training accuracy | 99.2% |
| Held-out accuracy | 88.5% |
 
The classifier provides a continuous difficulty signal. Benchmark quality data and Thompson Sampling compensate for classification noise.
 
### Real-world cost savings
 
End-to-end testing through Claude Code with Commonstack upstream:
 
| Metric | Value |
|---|---|
| Cost reduction | ~90–95% (vs always-premium) |
| Request success rate | 28/28 |
| Models auto-selected | 15 |
| Expensive model waste on simple tasks | 0 |
| Clicks to change routing | 3 |
 
```bash
python -m bench.run  # reproduce it yourself
```
 
---
 
## Troubleshooting
 
| Symptom | Fix |
|---|---|
| `route` works but real requests fail | Check `UNCOMMON_ROUTE_UPSTREAM` and `UNCOMMON_ROUTE_API_KEY`, run `uncommon-route doctor` |
| Codex / Cursor can't connect | `OPENAI_BASE_URL` must end with `/v1` |
| Claude Code can't connect | `ANTHROPIC_BASE_URL` should point to the router root, **not** `/v1` |
| Local upstream discovery fails | Some servers have `/chat/completions` but no `/models`; passthrough may work; `doctor` will tell you |
| Don't know what to run first | `uncommon-route doctor` |
 
---
 
## Uninstall
 
```bash
# Stop the proxy
uncommon-route stop
 
# Remove local state (stats, feedback, learning weights)
rm -rf "${UNCOMMON_ROUTE_DATA_DIR:-$HOME/.uncommon-route}"
 
# Restore client config
unset OPENAI_BASE_URL ANTHROPIC_BASE_URL UNCOMMON_ROUTE_UPSTREAM UNCOMMON_ROUTE_API_KEY
 
# Uninstall
pip uninstall uncommon-route
```
 
If you installed the OpenClaw plugin: `openclaw plugins uninstall @anjieyang/uncommon-route`
 
---
 
## Repo Layout
 
| Directory | Contents |
|---|---|
| `uncommon_route/` | Shipped runtime: proxy, router, CLI, calibration |
| `bench/` | Offline evaluation datasets and benchmark scripts |
| `demo/` | Local comparison / demo apps |
| `frontend/` | Dashboard and demo frontends |
 
---
 
## Development
 
```bash
git clone https://github.com/CommonstackAI/UncommonRoute.git
cd UncommonRoute
pip install -e ".[dev]"
python -m pytest tests -v  # 341 tests passing
```
 
---
 
## License
 
MIT — see [LICENSE](LICENSE).
