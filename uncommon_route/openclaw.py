"""OpenClaw integration — register UncommonRoute as an LLM provider.

Two integration modes:
  1. Plugin (recommended): `openclaw plugins install @anjieyang/uncommon-route`
     The JS bridge plugin spawns the Python proxy and registers everything automatically.

  2. Config patch (fallback): `uncommon-route openclaw install`
     Patches ~/.openclaw/openclaw.json directly. Works without the npm plugin but
     requires manually starting the proxy (`uncommon-route serve`).

Commands:
  uncommon-route openclaw install [--port N]   Config-patch registration
  uncommon-route openclaw uninstall            Remove config-patch registration
  uncommon-route openclaw status               Check registration (plugin or config)
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

from uncommon_route.router.config import DEFAULT_MODEL_PRICING, VIRTUAL_MODEL_IDS
from uncommon_route.router.types import RoutingMode

_OPENCLAW_DIR = Path.home() / ".openclaw"
_CONFIG_FILE = _OPENCLAW_DIR / "openclaw.json"
_PLUGINS_DIR = _OPENCLAW_DIR / "plugins"
_PROVIDER_ID = "uncommon-route"
_NPM_PACKAGE = "@anjieyang/uncommon-route"
_DEFAULT_PROXY_PORT = 8403


def _load_config() -> dict[str, Any]:
    if _CONFIG_FILE.exists():
        return json.loads(_CONFIG_FILE.read_text())
    return {}


def _save_config(config: dict[str, Any]) -> None:
    _OPENCLAW_DIR.mkdir(parents=True, exist_ok=True)
    _CONFIG_FILE.write_text(json.dumps(config, indent=2))


def _build_provider_block(port: int) -> dict[str, Any]:
    """Build the provider config block pointing at our local proxy."""
    base_url = f"http://127.0.0.1:{port}/v1"

    models: list[dict[str, Any]] = [
        {
            "id": VIRTUAL_MODEL_IDS[RoutingMode.AUTO],
            "name": "UncommonRoute Auto (smart routing)",
            "api": "openai-completions",
            "reasoning": False,
            "input": ["text"],
            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
            "contextWindow": 200000,
            "maxTokens": 16384,
        },
        {
            "id": VIRTUAL_MODEL_IDS[RoutingMode.FAST],
            "name": "UncommonRoute Fast (lighter and faster)",
            "api": "openai-completions",
            "reasoning": False,
            "input": ["text"],
            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
            "contextWindow": 200000,
            "maxTokens": 16384,
        },
        {
            "id": VIRTUAL_MODEL_IDS[RoutingMode.BEST],
            "name": "UncommonRoute Best (highest quality)",
            "api": "openai-completions",
            "reasoning": True,
            "input": ["text"],
            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
            "contextWindow": 200000,
            "maxTokens": 16384,
        },
    ]

    for model_id, pricing in DEFAULT_MODEL_PRICING.items():
        models.append({
            "id": model_id,
            "name": model_id.split("/")[-1],
            "api": "openai-completions",
            "reasoning": "reason" in model_id or "o3" in model_id or "o4" in model_id,
            "input": ["text"],
            "cost": {
                "input": pricing.input_price,
                "output": pricing.output_price,
                "cacheRead": 0,
                "cacheWrite": 0,
            },
            "contextWindow": 200000,
            "maxTokens": 16384,
        })

    return {
        "baseUrl": base_url,
        "api": "openai-completions",
        "apiKey": "uncommon-route-local-proxy",
        "models": models,
    }


def _is_plugin_installed() -> bool:
    """Check if the JS bridge plugin is installed via `openclaw plugins install`."""
    if _PLUGINS_DIR.exists():
        for p in _PLUGINS_DIR.iterdir():
            if "uncommon-route" in p.name or "anjieyang" in p.name:
                return True
    npm = shutil.which("openclaw")
    if npm:
        config = _load_config()
        plugins = config.get("plugins", {}).get("entries", {})
        return any("uncommon-route" in k or "anjieyang" in k for k in plugins)
    return False


def _is_config_patched() -> bool:
    """Check if config-patch registration exists."""
    config = _load_config()
    providers = config.get("models", {}).get("providers", {})
    return _PROVIDER_ID in providers


def install(port: int = _DEFAULT_PROXY_PORT) -> bool:
    """Register UncommonRoute via config-patch. Returns True if newly installed."""
    config = _load_config()
    models_cfg = config.setdefault("models", {})
    providers = models_cfg.setdefault("providers", {})

    already = _PROVIDER_ID in providers
    providers[_PROVIDER_ID] = _build_provider_block(port)

    if not config.get("models", {}).get("defaultModel"):
        config.setdefault("models", {})["defaultModel"] = "uncommon-route/auto"

    _save_config(config)
    return not already


def uninstall() -> bool:
    """Remove config-patch registration. Returns True if removed."""
    config = _load_config()
    providers = config.get("models", {}).get("providers", {})
    if _PROVIDER_ID not in providers:
        return False

    del providers[_PROVIDER_ID]

    if config.get("models", {}).get("defaultModel", "").startswith("uncommon-route/"):
        del config["models"]["defaultModel"]

    _save_config(config)
    return True


def status() -> dict[str, Any]:
    """Full status: plugin installed? config patched? proxy reachable?"""
    plugin_installed = _is_plugin_installed()
    config_patched = _is_config_patched()

    config = _load_config()
    ur_config = config.get("models", {}).get("providers", {}).get(_PROVIDER_ID)

    result: dict[str, Any] = {
        "plugin_installed": plugin_installed,
        "config_patched": config_patched,
        "registered": plugin_installed or config_patched,
        "config_path": str(_CONFIG_FILE),
        "provider_id": _PROVIDER_ID,
        "npm_package": _NPM_PACKAGE,
        "base_url": ur_config.get("baseUrl") if ur_config else None,
        "model_count": len(ur_config.get("models", [])) if ur_config else 0,
        "default_model": config.get("models", {}).get("defaultModel"),
    }
    return result


def print_status() -> None:
    """Print OpenClaw integration status to stdout."""
    s = status()

    if s["plugin_installed"]:
        print("  Plugin:        installed (JS bridge)")
        print(f"  Package:       {s['npm_package']}")
    elif s["config_patched"]:
        print("  Plugin:        not installed (using config-patch fallback)")
    else:
        print("  Plugin:        not installed")

    if s["registered"]:
        print(f"  Provider ID:   {s['provider_id']}")
        print(f"  Base URL:      {s['base_url']}")
        print(f"  Models:        {s['model_count']}")
        print(f"  Default model: {s['default_model'] or '(not set)'}")
    print(f"  Config:        {s['config_path']}")

    if not s["registered"]:
        print()
        print("  To integrate with OpenClaw:")
        print(f"    Option A (recommended): openclaw plugins install {_NPM_PACKAGE}")
        print("    Option B (fallback):    uncommon-route openclaw install")


def cmd_openclaw(args: list[str]) -> None:
    """Handle `uncommon-route openclaw <subcommand>`."""
    if not args:
        args = ["status"]

    sub = args[0]

    if sub == "install":
        port = _DEFAULT_PROXY_PORT
        if "--port" in args:
            idx = args.index("--port")
            if idx + 1 < len(args):
                port = int(args[idx + 1])

        if _is_plugin_installed():
            print("[UncommonRoute] JS bridge plugin is already installed.")
            print("  Config-patch not needed — the plugin handles registration automatically.")
            print("  To reconfigure: edit ~/.openclaw/openclaw.yaml plugins section")
            return

        is_new = install(port=port)
        if is_new:
            print("[UncommonRoute] Registered as OpenClaw provider (config-patch)")
            print(f"  Proxy URL: http://127.0.0.1:{port}/v1")
            print("  Default model set to: uncommon-route/auto")
            print()
            print("  Next steps:")
            print(f"    1. Start proxy:  uncommon-route serve --port {port}")
            print("    2. Restart:      openclaw gateway restart")
            print()
            print("  For automatic lifecycle management, install the JS bridge instead:")
            print(f"    openclaw plugins install {_NPM_PACKAGE}")
        else:
            print(f"[UncommonRoute] Updated existing config-patch (port {port})")

    elif sub == "uninstall":
        if _is_plugin_installed():
            print("[UncommonRoute] JS bridge plugin is installed.")
            print(f"  To uninstall: openclaw plugins uninstall {_NPM_PACKAGE}")
            print("  Also removing config-patch if present...")

        removed = uninstall()
        if removed:
            print("[UncommonRoute] Removed config-patch registration")
        elif not _is_plugin_installed():
            print("[UncommonRoute] Not registered — nothing to remove")

    elif sub == "status":
        print_status()

    else:
        print(f"Unknown openclaw subcommand: {sub}", file=sys.stderr)
        print("  Available: install, uninstall, status", file=sys.stderr)
        sys.exit(1)
