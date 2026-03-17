<p align="right"><strong>English</strong> | <a href="https://github.com/anjieyang/UncommonRoute/blob/main/README.zh-CN.md">简体中文</a></p>

# @anjieyang/uncommon-route

OpenClaw plugin for [UncommonRoute](https://github.com/anjieyang/UncommonRoute), the local LLM router that sends easy requests to cheaper models and saves stronger models for harder work.

If you use OpenClaw and want one local endpoint with smart routing behind it, this plugin is the shortest path.

## Mental Model

```text
OpenClaw -> UncommonRoute -> your upstream API
```

This plugin:

- installs the Python `uncommon-route` package if needed
- starts `uncommon-route serve`
- registers the local provider with OpenClaw
- exposes the virtual routing modes like `uncommon-route/auto`

## Install

```bash
openclaw plugins install @anjieyang/uncommon-route
openclaw gateway restart
```

That is enough to install the plugin.

For real responses, you still need to configure an upstream model API.

## Configure An Upstream

UncommonRoute does not host models. It routes to an upstream OpenAI-compatible API.

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

Common upstream choices:

| Provider | URL |
| --- | --- |
| [Parallax](https://github.com/GradientHQ/parallax) | `http://127.0.0.1:3001/v1` |
| [Commonstack](https://commonstack.ai) | `https://api.commonstack.ai/v1` |
| OpenAI | `https://api.openai.com/v1` |
| Local Ollama / vLLM | `http://127.0.0.1:11434/v1` |

If your upstream needs a key, set `UNCOMMON_ROUTE_API_KEY` in the environment where OpenClaw runs.

Parallax is best treated as an experimental local upstream for now: its public docs show `POST /v1/chat/completions`, but UncommonRoute model discovery may be limited because a public `/v1/models` route was not obvious in the repo.

## What You Get

- a local OpenClaw provider backed by `http://127.0.0.1:8403/v1`
- `uncommon-route/auto` for balanced smart routing
- hardcoded additional virtual modes: `uncommon-route/fast` and `uncommon-route/best`

The router also keeps a fallback chain, records local feedback, and exposes a local dashboard at `http://127.0.0.1:8403/dashboard/`.

## OpenClaw Commands

| Command | Description |
| --- | --- |
| `/route <prompt>` | Preview which model the router would pick |
| `/spend status` | Show current spending and limits |
| `/spend set hourly 5.00` | Set an hourly spend limit |
| `/feedback <signal>` | Use `ok`, `weak`, `strong`, `status`, or `rollback` to rate the last routing decision or inspect feedback state |

## Troubleshooting

If the plugin is installed but responses are failing:

1. Make sure your upstream URL is configured.
2. Make sure `UNCOMMON_ROUTE_API_KEY` is set if your provider requires one.
3. Open `http://127.0.0.1:8403/health`.
4. Open `http://127.0.0.1:8403/dashboard/`.

## Benchmarks

Current repo benchmarks:

- 92.3% held-out routing accuracy
- ~0.5ms average routing latency
- 67% lower simulated cost than always using Claude Opus in a coding session

## Links

- [GitHub](https://github.com/anjieyang/UncommonRoute)
- [PyPI](https://pypi.org/project/uncommon-route/)
- [Full README](https://github.com/anjieyang/UncommonRoute#readme)

## License

MIT
