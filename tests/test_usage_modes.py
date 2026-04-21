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
import time
import zipfile
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from uncommon_route.proxy import create_app
from uncommon_route.proxy import VERSION as PROXY_VERSION
from uncommon_route.router.config import DEFAULT_MODEL_PRICING
from uncommon_route.router.types import ModelPricing
from uncommon_route.spend_control import InMemorySpendControlStorage, SpendControl
from uncommon_route.traces import FileTraceStorage, RequestTrace, TraceStore

PYTHON = sys.executable
PACKAGE_ROOT = Path(__file__).resolve().parents[1]

# Patched pricing so nvidia/gpt-oss-120b has non-zero cost (for spend-block tests)
_SPEND_TEST_PRICING = dict(DEFAULT_MODEL_PRICING)
_SPEND_TEST_PRICING["nvidia/gpt-oss-120b"] = ModelPricing(0.10, 0.40)
CLI_MODULE = [PYTHON, "-m", "uncommon_route.cli"]


def run_cli(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    merged_env = dict(os.environ)
    merged_env["PYTHONPATH"] = str(PACKAGE_ROOT)
    if env:
        merged_env.update(env)
    return subprocess.run(
        [*CLI_MODULE, *args],
        capture_output=True,
        text=True,
        input=input_text,
        cwd=PACKAGE_ROOT,
        env=merged_env,
    )


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def proxy_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Full proxy with spend control, fake upstream."""
    monkeypatch.setattr(
        "uncommon_route.proxy._LOCAL_CLIENT_HOSTS",
        {"127.0.0.1", "::1", "localhost", "testclient"},
    )
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
        assert "init" in r.stdout
        assert "openclaw" in r.stdout
        assert "spend" in r.stdout

    def test_serve_subcommand_help(self) -> None:
        r = run_cli(["serve", "--help"])
        assert r.returncode == 0
        assert "Usage: uncommon-route serve" in r.stdout
        assert "Start the local proxy server." in r.stdout

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

    def test_doctor_local_upstream_without_key(self, tmp_path: Path) -> None:
        env = {
            "HOME": str(tmp_path),
            "UNCOMMON_ROUTE_DATA_DIR": str(tmp_path / ".uncommon-route"),
            "UNCOMMON_ROUTE_UPSTREAM": "http://127.0.0.1:11434/v1",
        }

        r = run_cli(["doctor"], env=env)

        assert r.returncode == 0
        assert "✓ API key configured: (not needed for local upstream)" in r.stdout

    def test_doctor_fails_when_not_configured(self, tmp_path: Path) -> None:
        r = run_cli(
            ["doctor"],
            env={
                "HOME": str(tmp_path),
                "UNCOMMON_ROUTE_DATA_DIR": str(tmp_path / ".uncommon-route"),
            },
        )

        assert r.returncode == 1
        assert "Setup is incomplete" in r.stdout

    def test_doctor_byok_only_is_ready(self, tmp_path: Path) -> None:
        env = {
            "HOME": str(tmp_path),
            "UNCOMMON_ROUTE_DATA_DIR": str(tmp_path / ".uncommon-route"),
            "SHELL": "/bin/zsh",
        }
        init = run_cli(["init"], env=env, input_text="3\n1\nsk-test\nn\n4\nn\n")
        assert init.returncode == 0

        doctor = run_cli(["doctor"], env=env)
        assert doctor.returncode == 0
        assert "Primary connection: not set (BYOK mode is available)" in doctor.stdout

    def test_init_commonstack_writes_connection_and_client_shell_block(self, tmp_path: Path) -> None:
        env = {
            "HOME": str(tmp_path),
            "UNCOMMON_ROUTE_DATA_DIR": str(tmp_path / ".uncommon-route"),
            "SHELL": "/bin/zsh",
        }
        r = run_cli(
            ["init"],
            env=env,
            input_text="1\n\ncsk-test-key\n2\ny\nn\n",
        )

        assert r.returncode == 0
        assert "Setup summary" in r.stdout

        connections_path = tmp_path / ".uncommon-route" / "connections.json"
        payload = json.loads(connections_path.read_text())
        assert payload["primary"]["upstream"] == "https://api.commonstack.ai/v1"
        assert payload["primary"]["api_key"] == "csk-test-key"

        rc_path = tmp_path / ".zshrc"
        rc_contents = rc_path.read_text()
        assert "OPENAI_BASE_URL" in rc_contents
        assert "OPENAI_API_KEY" in rc_contents

    def test_init_commonstack_writes_claude_code_auth_token_block(self, tmp_path: Path) -> None:
        env = {
            "HOME": str(tmp_path),
            "UNCOMMON_ROUTE_DATA_DIR": str(tmp_path / ".uncommon-route"),
            "SHELL": "/bin/zsh",
        }
        r = run_cli(
            ["init"],
            env=env,
            input_text="1\n\ncsk-test-key\n1\ny\nn\n",
        )

        assert r.returncode == 0
        rc_path = tmp_path / ".zshrc"
        rc_contents = rc_path.read_text()
        assert 'ANTHROPIC_BASE_URL="http://localhost:8403"' in rc_contents
        assert 'ANTHROPIC_AUTH_TOKEN="not-needed"' in rc_contents
        assert 'ANTHROPIC_API_KEY="not-needed"' not in rc_contents

    def test_init_byok_writes_provider_config(self, tmp_path: Path) -> None:
        env = {
            "HOME": str(tmp_path),
            "UNCOMMON_ROUTE_DATA_DIR": str(tmp_path / ".uncommon-route"),
            "SHELL": "/bin/zsh",
        }
        r = run_cli(
            ["init"],
            env=env,
            input_text="3\n1\nsk-openai\nn\n4\nn\n",
        )

        assert r.returncode == 0
        providers_path = tmp_path / ".uncommon-route" / "providers.json"
        payload = json.loads(providers_path.read_text())
        assert payload["providers"]["openai"]["api_key"] == "sk-openai"

    def test_support_bundle_exports_recent_traces(self, tmp_path: Path) -> None:
        data_dir = tmp_path / ".uncommon-route"
        traces = TraceStore(storage=FileTraceStorage(path=data_dir / "traces.json"))
        traces.record(RequestTrace(
            timestamp=time.time(),
            request_id="reqsupport01",
            requested_model="uncommon-route/auto",
            model="moonshot/kimi-k2.5",
            status_code=502,
            mode="auto",
            tier="SIMPLE",
            decision_tier="SIMPLE",
            confidence=0.91,
            method="pool",
            estimated_cost=0.002,
            prompt_preview="hello world",
            prompt_hash="abc123",
            endpoint="chat_completions",
            is_virtual=True,
            streaming=False,
            step_type="general",
            route_reasoning="selected for low latency",
            routing_features_payload={"step_type": "general"},
            fallback_chain_payload=[{"model": "fallback/model", "reason": "cost"}],
            candidate_scores_payload=[{"model": "moonshot/kimi-k2.5", "score": 0.91}],
            selection_weights_payload={"editorial": 0.55, "cost": 0.45},
            attempts_payload=[{"selected_model": "moonshot/kimi-k2.5", "status_code": 502}],
            error_code="connect_error",
            error_stage="upstream_request",
            error_message="Upstream unreachable: http://127.0.0.1:1/fake",
        ))

        r = run_cli(
            ["support", "bundle", "--limit", "5"],
            env={
                "UNCOMMON_ROUTE_DATA_DIR": str(data_dir),
                "HOME": str(tmp_path),
            },
        )

        assert r.returncode == 0
        output_line = r.stdout.strip().splitlines()[-1]
        assert output_line.startswith("Support bundle written:")

        bundle_path = Path(output_line.split(": ", 1)[1])
        assert bundle_path.exists()

        with zipfile.ZipFile(bundle_path) as bundle:
            assert "manifest.json" in bundle.namelist()
            assert "diagnostics/recent_traces.json" in bundle.namelist()
            assert "diagnostics/recent_errors.json" in bundle.namelist()
            assert "diagnostics/trace_summary.json" in bundle.namelist()

            traces = json.loads(bundle.read("diagnostics/recent_traces.json"))
            assert len(traces) == 1
            assert traces[0]["request_id"] == "reqsupport01"
            assert traces[0]["route_reasoning"] == "selected for low latency"
            assert traces[0]["error_code"] == "connect_error"
            assert traces[0]["routing_features_payload"] == {"step_type": "general"}
            assert traces[0]["attempts_payload"][0]["status_code"] == 502

    def test_support_request_prints_trace(self, tmp_path: Path) -> None:
        data_dir = tmp_path / ".uncommon-route"
        traces = TraceStore(storage=FileTraceStorage(path=data_dir / "traces.json"))
        traces.record(RequestTrace(
            timestamp=time.time(),
            request_id="reqlookup001",
            requested_model="uncommon-route/auto",
            model="moonshot/kimi-k2.5",
            status_code=502,
            mode="auto",
            tier="SIMPLE",
            decision_tier="SIMPLE",
            confidence=0.91,
            method="pool",
            estimated_cost=0.002,
            prompt_preview="debug me",
            prompt_hash="def456",
            endpoint="chat_completions",
            is_virtual=True,
            streaming=False,
            route_reasoning="selected for low latency",
            error_code="connect_error",
            error_stage="upstream_request",
            error_message="Upstream unreachable",
        ))

        r = run_cli(
            ["support", "request", "reqlookup001"],
            env={
                "UNCOMMON_ROUTE_DATA_DIR": str(data_dir),
                "HOME": str(tmp_path),
            },
        )

        assert r.returncode == 0
        payload = json.loads(r.stdout)
        assert payload["request_id"] == "reqlookup001"
        assert payload["status_code"] == 502
        assert payload["error_code"] == "connect_error"
        assert payload["route_reasoning"] == "selected for low latency"


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
        assert r.headers["x-uncommon-route-mode"] == "passthrough"
        assert r.headers["x-uncommon-route-model"] == "openai/gpt-4o"


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
        monkeypatch.setattr(
            "uncommon_route.proxy._LOCAL_CLIENT_HOSTS",
            {"127.0.0.1", "::1", "localhost", "testclient"},
        )
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
