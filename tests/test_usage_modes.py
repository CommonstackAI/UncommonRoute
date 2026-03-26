"""End-to-end tests for the primary usage surfaces.

1. CLI routing   — subprocess: uncommon-route route / debug
2. Python SDK    — import route(), classify(), SpendControl
3. HTTP Proxy    — start ASGI app, hit endpoints with httpx
4. OpenClaw      — install / status / uninstall config patch
5. Spend control — set limits, get blocked at 429, history
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from uncommon_route.proxy import create_app
from uncommon_route.proxy import VERSION as PROXY_VERSION
from uncommon_route.router.config import DEFAULT_MODEL_PRICING
from uncommon_route.router.types import ModelPricing
from uncommon_route.spend_control import InMemorySpendControlStorage, SpendControl

PYTHON = sys.executable
PACKAGE_ROOT = Path(__file__).resolve().parents[1]

# Patched pricing so nvidia/gpt-oss-120b has non-zero cost (for spend-block tests)
_SPEND_TEST_PRICING = dict(DEFAULT_MODEL_PRICING)
_SPEND_TEST_PRICING["nvidia/gpt-oss-120b"] = ModelPricing(0.10, 0.40)
CLI_MODULE = [PYTHON, "-m", "uncommon_route.cli"]


def run_cli(args: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = dict(os.environ)
    merged_env["PYTHONPATH"] = str(PACKAGE_ROOT)
    if env:
        merged_env.update(env)
    return subprocess.run(
        [*CLI_MODULE, *args],
        capture_output=True,
        text=True,
        cwd=PACKAGE_ROOT,
        env=merged_env,
    )


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def proxy_client() -> TestClient:
    """Full proxy with spend control, fake upstream."""
    sc = SpendControl(storage=InMemorySpendControlStorage())
    app = create_app(upstream="http://127.0.0.1:1/fake", spend_control=sc)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _isolate_openclaw(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_dir = tmp_path / ".openclaw"
    monkeypatch.setattr("uncommon_route.openclaw._OPENCLAW_DIR", fake_dir)
    monkeypatch.setattr("uncommon_route.openclaw._CONFIG_FILE", fake_dir / "openclaw.json")
    monkeypatch.setattr("uncommon_route.openclaw._PLUGINS_DIR", fake_dir / "plugins")


# ── Mode 1: CLI ──────────────────────────────────────────────────────

class TestCLI:
    def test_version(self) -> None:
        r = run_cli(["--version"])
        assert r.returncode == 0
        assert PROXY_VERSION in r.stdout

    def test_help(self) -> None:
        r = run_cli(["--help"])
        assert r.returncode == 0
        assert "uncommon-route" in r.stdout
        assert "openclaw" in r.stdout
        assert "spend" in r.stdout

    def test_route_text(self) -> None:
        r = run_cli(["route", "what is 2+2"])
        assert r.returncode == 0
        assert "Model:" in r.stdout
        assert "Tier:" in r.stdout
        assert "SIMPLE" in r.stdout

    def test_route_json(self) -> None:
        r = run_cli(["route", "--json", "explain quicksort in detail"])
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data["mode"] == "auto"
        assert "model" in data
        assert "tier" in data
        assert "confidence" in data
        assert "latency_ms" in data

    def test_route_mode_flag(self) -> None:
        r = run_cli(["route", "--json", "--mode", "fast", "explain quicksort in detail"])
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data["mode"] == "fast"

    def test_route_uses_persisted_default_mode(self, tmp_path: Path) -> None:
        env = {"UNCOMMON_ROUTE_DATA_DIR": str(tmp_path / ".uncommon-route")}

        set_mode = run_cli(["config", "set-default-mode", "best", "--json"], env=env)
        assert set_mode.returncode == 0
        assert json.loads(set_mode.stdout)["default_mode"] == "best"

        routed = run_cli(["route", "--json", "hello"], env=env)
        assert routed.returncode == 0
        assert json.loads(routed.stdout)["mode"] == "best"

    def test_route_complex_prompt(self) -> None:
        r = run_cli([
            "route",
            "--json",
            "Design a distributed consensus algorithm that handles Byzantine faults "
            "with formal correctness proofs and implement it in Rust",
        ])
        data = json.loads(r.stdout)
        assert data["tier"] == "COMPLEX"

    def test_debug(self) -> None:
        r = run_cli(["debug", "prove that sqrt(2) is irrational"])
        assert r.returncode == 0
        assert "Structural Features:" in r.stdout
        assert "Unicode Blocks:" in r.stdout

    def test_route_no_prompt_fails(self) -> None:
        r = run_cli(["route"])
        assert r.returncode != 0

    def test_doctor_local_upstream_without_key(self) -> None:
        env = dict(os.environ)
        env["UNCOMMON_ROUTE_UPSTREAM"] = "http://127.0.0.1:11434/v1"
        env.pop("UNCOMMON_ROUTE_API_KEY", None)
        env.pop("COMMONSTACK_API_KEY", None)

        r = run_cli(["doctor"], env=env)

        assert r.returncode == 0
        assert "✓ API key configured: (not needed for local upstream)" in r.stdout


# ── Mode 2: Python SDK ───────────────────────────────────────────────

class TestSDK:
    def test_route(self) -> None:
        from uncommon_route import route
        d = route("what is 2+2")
        assert d.model is not None
        assert d.tier.value == "SIMPLE"
        assert 0 <= d.confidence <= 1
        assert d.savings >= 0
        assert 0 <= d.complexity <= 1

    def test_classify(self) -> None:
        from uncommon_route import classify
        r = classify("implement a B-tree in C++ with deletion support")
        assert r.tier is not None
        assert r.tier.value in ("MEDIUM", "COMPLEX")
        assert len(r.signals) > 0

    def test_route_with_system_prompt(self) -> None:
        from uncommon_route import route
        d = route(
            "list 3 colors",
            system_prompt="You are a helpful assistant. Respond in JSON format.",
        )
        # structured output → at least MEDIUM
        assert d.tier.value in ("MEDIUM", "COMPLEX")

    def test_select_model_and_fallback(self) -> None:
        from uncommon_route import route
        d = route("hello")
        assert len(d.fallback_chain) > 0
        assert d.fallback_chain[0].cost_estimate >= 0

    def test_spend_control_sdk(self) -> None:
        from uncommon_route import SpendControl, InMemorySpendControlStorage
        sc = SpendControl(storage=InMemorySpendControlStorage())
        sc.set_limit("per_request", 0.05)
        assert sc.check(0.03).allowed is True
        assert sc.check(0.10).allowed is False

    def test_openclaw_sdk(self) -> None:
        from uncommon_route import openclaw_install, openclaw_status, openclaw_uninstall
        openclaw_install(port=9999)
        s = openclaw_status()
        assert s["registered"] is True
        assert s["base_url"] == "http://127.0.0.1:9999/v1"
        openclaw_uninstall()
        assert openclaw_status()["registered"] is False


# ── Mode 3: HTTP Proxy ───────────────────────────────────────────────

class TestHTTPProxy:
    def test_health(self, proxy_client: TestClient) -> None:
        r = proxy_client.get("/health")
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "ok"
        assert d["router"] == "uncommon-route"
        assert "spending" in d

    def test_models(self, proxy_client: TestClient) -> None:
        r = proxy_client.get("/v1/models")
        ids = [m["id"] for m in r.json()["data"]]
        assert "uncommon-route/auto" in ids

    def test_chat_debug(self, proxy_client: TestClient) -> None:
        r = proxy_client.post("/v1/chat/completions", json={
            "model": "uncommon-route/auto",
            "messages": [{"role": "user", "content": "/debug explain recursion"}],
        })
        assert r.status_code == 200
        content = r.json()["choices"][0]["message"]["content"]
        assert "Tier:" in content
        assert "Model:" in content

    def test_chat_routes_to_upstream(self, proxy_client: TestClient) -> None:
        """Virtual model routes and forwards (upstream is fake → 502, but routing works)."""
        r = proxy_client.post("/v1/chat/completions", json={
            "model": "uncommon-route/auto",
            "messages": [{"role": "user", "content": "hello"}],
        })
        assert r.status_code == 502
        assert r.headers["x-uncommon-route-model"] != ""
        assert r.headers["x-uncommon-route-tier"] in ("SIMPLE", "MEDIUM", "COMPLEX")

    def test_passthrough_model(self, proxy_client: TestClient) -> None:
        r = proxy_client.post("/v1/chat/completions", json={
            "model": "openai/gpt-4o",
            "messages": [{"role": "user", "content": "hello"}],
        })
        assert r.status_code == 502
        assert "x-uncommon-route-model" not in r.headers


# ── Mode 4: OpenClaw Integration ─────────────────────────────────────

# Mode 5 (Session Management) removed: SessionConfig, SessionStore, /v1/sessions,
# and route methods session-hold, session-upgrade, step-aware, escalated no longer exist.

class TestOpenClawIntegration:
    def test_cli_openclaw_status(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """CLI `openclaw status` runs without error."""
        r = run_cli(["openclaw", "status"], env={"HOME": str(tmp_path)})
        assert r.returncode == 0
        assert "not installed" in r.stdout or "registered" in r.stdout

    def test_install_uninstall_cycle(self) -> None:
        from uncommon_route.openclaw import install, uninstall, status
        install(port=8403)
        s = status()
        assert s["config_patched"] is True
        assert s["model_count"] > 1

        uninstall()
        s = status()
        assert s["config_patched"] is False


# ── Mode 5: Spend Control ─────────────────────────────────────────────

class TestSpendControlE2E:
    def test_set_limit_via_api(self, proxy_client: TestClient) -> None:
        proxy_client.post("/v1/spend", json={"action": "set", "window": "hourly", "amount": 10.0})
        data = proxy_client.get("/v1/spend").json()
        assert data["limits"]["hourly"] == 10.0
        assert data["remaining"]["hourly"] == 10.0

    def test_spend_blocks_at_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("uncommon_route.proxy._get_pricing", lambda: _SPEND_TEST_PRICING)
        sc = SpendControl(storage=InMemorySpendControlStorage())
        sc.set_limit("per_request", 0.00005)  # Below estimated ~0.0001 for "hello"
        app = create_app(upstream="http://127.0.0.1:1/fake", spend_control=sc)
        client = TestClient(app, raise_server_exceptions=False)

        r = client.post("/v1/chat/completions", json={
            "model": "uncommon-route/auto",
            "messages": [{"role": "user", "content": "hello"}],
        })
        assert r.status_code == 429
        err = r.json()["error"]
        assert err["type"] == "spend_limit_exceeded"
        assert "Per-request limit" in err["message"]

    def test_spend_clear_and_retry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("uncommon_route.proxy._get_pricing", lambda: _SPEND_TEST_PRICING)
        sc = SpendControl(storage=InMemorySpendControlStorage())
        sc.set_limit("per_request", 0.00005)  # Below estimated ~0.0001 for "hello"
        app = create_app(upstream="http://127.0.0.1:1/fake", spend_control=sc)
        client = TestClient(app, raise_server_exceptions=False)

        r1 = client.post("/v1/chat/completions", json={
            "model": "uncommon-route/auto",
            "messages": [{"role": "user", "content": "hello"}],
        })
        assert r1.status_code == 429

        client.post("/v1/spend", json={"action": "clear", "window": "per_request"})

        r2 = client.post("/v1/chat/completions", json={
            "model": "uncommon-route/auto",
            "messages": [{"role": "user", "content": "hello"}],
        })
        assert r2.status_code != 429

    def test_cli_spend_status(self, tmp_path: Path) -> None:
        r = run_cli(["spend", "status"], env={"HOME": str(tmp_path)})
        assert r.returncode == 0
        assert "Spending Limits" in r.stdout or "no limits" in r.stdout

    def test_spend_status_in_health(self, proxy_client: TestClient) -> None:
        proxy_client.post("/v1/spend", json={"action": "set", "window": "daily", "amount": 50.0})
        health = proxy_client.get("/health").json()
        assert health["spending"]["limits"]["daily"] == 50.0
