"""BYOK (Bring Your Own Key) provider management.

Users configure their own API keys and subscription plans.
When routing, models backed by a user-provided key are prioritized
since the user already pays for them.

Config stored at ~/.uncommon-route/providers.json

Example (providers.json):
    {
      "providers": {
        "minimax": {
          "api_key": "eyJ...",
          "base_url": "https://api.minimax.io/v1",
          "models": ["minimax/minimax-m2.5"],
          "plan": "coding-plan"
        },
        "deepseek": {
          "api_key": "sk-...",
          "base_url": "https://api.deepseek.com/v1",
          "models": ["deepseek/deepseek-chat", "deepseek/deepseek-reasoner"]
        }
      }
    }
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from uncommon_route.paths import data_dir

_DATA_DIR = data_dir()
_PROVIDERS_FILE = _DATA_DIR / "providers.json"

KNOWN_BASE_URLS: dict[str, str] = {
    "commonstack": "https://api.commonstack.ai/v1",
    "minimax": "https://api.minimax.io/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta",
    "xai": "https://api.x.ai/v1",
    "moonshot": "https://api.moonshot.cn/v1",
}

PROVIDER_MODELS: dict[str, list[str]] = {
    "minimax": ["minimax/minimax-m2.5"],
    "deepseek": ["deepseek/deepseek-chat", "deepseek/deepseek-reasoner"],
    "openai": ["openai/gpt-4o-mini", "openai/gpt-4o", "openai/gpt-5.2", "openai/gpt-5.2-codex", "openai/o1-mini", "openai/o3", "openai/o4-mini"],
    "anthropic": ["anthropic/claude-haiku-4.5", "anthropic/claude-sonnet-4.6", "anthropic/claude-opus-4.6"],
    "google": ["google/gemini-2.5-flash-lite", "google/gemini-2.5-flash", "google/gemini-2.5-pro", "google/gemini-3-pro-preview", "google/gemini-3.1-pro"],
    "xai": ["xai/grok-4-1-fast-reasoning", "xai/grok-4-1-fast-non-reasoning", "xai/grok-4-0709", "xai/grok-code-fast-1"],
    "moonshot": ["moonshot/kimi-k2.5"],
}


@dataclass
class ProviderEntry:
    name: str
    api_key: str
    base_url: str
    models: list[str] = field(default_factory=list)
    plan: str = ""


@dataclass
class ProvidersConfig:
    providers: dict[str, ProviderEntry] = field(default_factory=dict)

    def keyed_models(self) -> set[str]:
        """All model IDs backed by a user-provided key."""
        result: set[str] = set()
        for entry in self.providers.values():
            result.update(entry.models)
        return result

    def get_for_model(self, model_id: str) -> ProviderEntry | None:
        """Find the provider entry that covers a given model."""
        for entry in self.providers.values():
            if model_id in entry.models:
                return entry
        return None

    def provider_names(self) -> list[str]:
        return list(self.providers.keys())


def load_providers(path: Path | None = None) -> ProvidersConfig:
    """Load provider config from disk."""
    filepath = path or _PROVIDERS_FILE
    if not filepath.exists():
        return ProvidersConfig()
    try:
        raw = json.loads(filepath.read_text())
        config = ProvidersConfig()
        for name, data in raw.get("providers", {}).items():
            if not isinstance(data, dict) or not data.get("api_key"):
                continue
            config.providers[name] = ProviderEntry(
                name=name,
                api_key=data["api_key"],
                base_url=data.get("base_url", KNOWN_BASE_URLS.get(name, "")),
                models=data.get("models", PROVIDER_MODELS.get(name, [])),
                plan=data.get("plan", ""),
            )
        return config
    except Exception as exc:
        print(
            f"[UncommonRoute] warning: failed to load providers from {filepath}: {exc}. "
            "BYOK routing disabled until resolved.",
            file=sys.stderr,
        )
        return ProvidersConfig()


def save_providers(config: ProvidersConfig, path: Path | None = None) -> None:
    """Save provider config to disk."""
    filepath = path or _PROVIDERS_FILE
    filepath.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = {
        "providers": {
            name: {
                "api_key": entry.api_key,
                "base_url": entry.base_url,
                "models": entry.models,
                **({"plan": entry.plan} if entry.plan else {}),
            }
            for name, entry in config.providers.items()
        }
    }
    filepath.write_text(json.dumps(data, indent=2))
    filepath.chmod(0o600)


def add_provider(
    name: str,
    api_key: str,
    base_url: str | None = None,
    models: list[str] | None = None,
    plan: str = "",
    config: ProvidersConfig | None = None,
) -> ProvidersConfig:
    """Add or update a provider. Returns updated config (also saves to disk)."""
    cfg = config or load_providers()
    resolved_url = base_url or KNOWN_BASE_URLS.get(name, "")
    resolved_models = models or PROVIDER_MODELS.get(name, [])

    cfg.providers[name] = ProviderEntry(
        name=name,
        api_key=api_key,
        base_url=resolved_url,
        models=resolved_models,
        plan=plan,
    )
    save_providers(cfg)
    return cfg


def remove_provider(name: str, config: ProvidersConfig | None = None) -> bool:
    """Remove a provider. Returns True if removed."""
    cfg = config or load_providers()
    if name not in cfg.providers:
        return False
    del cfg.providers[name]
    save_providers(cfg)
    return True


def select_preferred_model(
    tier_models: list[str],
    user_config: ProvidersConfig,
) -> tuple[str | None, ProviderEntry | None]:
    """Given a list of candidate models for a tier, return the first one backed by a user key.

    Returns (model_id, provider_entry) or (None, None) if no match.
    """
    keyed = user_config.keyed_models()
    for model in tier_models:
        if model in keyed:
            return model, user_config.get_for_model(model)
    return None, None


def verify_key(base_url: str, api_key: str) -> tuple[bool, str]:
    """Ping ``/v1/models`` to check if an API key is valid.

    Returns ``(ok, detail)`` — *detail* is a human-readable status string.
    """
    import httpx

    url = f"{base_url.rstrip('/')}/models"
    try:
        resp = httpx.get(
            url,
            headers={"authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(8.0, connect=4.0),
        )
        if resp.status_code == 200:
            data = resp.json()
            count = len(data.get("data", []))
            return True, f"{count} models available"
        if resp.status_code == 401:
            return False, "invalid or expired API key (HTTP 401)"
        if resp.status_code == 403:
            return False, "access denied (HTTP 403)"
        return False, f"unexpected HTTP {resp.status_code}"
    except httpx.ConnectError:
        return False, f"cannot connect to {url}"
    except httpx.TimeoutException:
        return False, f"timeout connecting to {url}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def cmd_provider(args: list[str]) -> None:
    """Handle `uncommon-route provider <subcommand>`."""
    if not args:
        args = ["list"]

    sub = args[0]

    if sub == "list":
        cfg = load_providers()
        if not cfg.providers:
            print("  No providers configured")
            print()
            print("  Add one:  uncommon-route provider add <name> <api_key>")
            print("  Example:  uncommon-route provider add deepseek sk-...")
            return
        print(f"  Configured providers ({len(cfg.providers)}):")
        for name, entry in cfg.providers.items():
            key_preview = entry.api_key[:8] + "..." if len(entry.api_key) > 8 else "***"
            plan_str = f"  [{entry.plan}]" if entry.plan else ""
            print(f"    {name}: {key_preview}{plan_str}")
            print(f"      URL:    {entry.base_url}")
            print(f"      Models: {', '.join(entry.models)}")

    elif sub == "add":
        if len(args) < 3:
            print("Usage: uncommon-route provider add <name> <api_key> [--plan <plan>]", file=sys.stderr)
            print(f"  Known providers: {', '.join(KNOWN_BASE_URLS.keys())}", file=sys.stderr)
            sys.exit(1)
        name = args[1]
        api_key = args[2]
        plan = ""
        if "--plan" in args:
            idx = args.index("--plan")
            if idx + 1 < len(args):
                plan = args[idx + 1]
        base_url = None
        if "--url" in args:
            idx = args.index("--url")
            if idx + 1 < len(args):
                base_url = args[idx + 1]

        add_provider(name, api_key, base_url=base_url, plan=plan)
        models = PROVIDER_MODELS.get(name, [])
        print(f"  Added provider: {name}")
        if models:
            print(f"  Models: {', '.join(models)}")

        resolved_url = base_url or KNOWN_BASE_URLS.get(name, "")
        if resolved_url:
            ok, detail = verify_key(resolved_url, api_key)
            if ok:
                print(f"  Key verified: {detail}")
            else:
                print(f"  Warning: key verification failed — {detail}")
                print("  The key was saved but may not work. Check with: uncommon-route doctor")
        print("  These models will be prioritized when routing to their tier")

    elif sub == "remove":
        if len(args) < 2:
            print("Usage: uncommon-route provider remove <name>", file=sys.stderr)
            sys.exit(1)
        removed = remove_provider(args[1])
        print(f"  {'Removed' if removed else 'Not found'}: {args[1]}")

    elif sub == "models":
        cfg = load_providers()
        keyed = cfg.keyed_models()
        if not keyed:
            print("  No user-keyed models")
            return
        print(f"  User-keyed models ({len(keyed)}):")
        for m in sorted(keyed):
            provider = cfg.get_for_model(m)
            print(f"    {m}  (via {provider.name})" if provider else f"    {m}")

    else:
        print(f"Unknown provider subcommand: {sub}", file=sys.stderr)
        print("  Available: list, add, remove, models", file=sys.stderr)
        sys.exit(1)
