#!/usr/bin/env python3
"""Run a 50-case routing matrix against a local UncommonRoute daemon.

This is an operator validation tool, not a unit test. It intentionally calls
the local HTTP proxy so the dynamic model pool, request feature extraction, and
selector all run together.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from typing import Any


BASE_URL = "http://localhost:8403"

CHAT_TOOL = {
    "type": "function",
    "function": {
        "name": "Bash",
        "description": "Run shell commands",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
    },
}
READ_TOOL = {
    "type": "function",
    "function": {
        "name": "Read",
        "description": "Read a file",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
    },
}
WRITE_TOOL = {
    "type": "function",
    "function": {
        "name": "Write",
        "description": "Write a file",
        "parameters": {"type": "object", "properties": {}},
    },
}
WEB_TOOL = {
    "type": "function",
    "function": {
        "name": "WebSearch",
        "description": "Search the web",
        "parameters": {"type": "object", "properties": {}},
    },
}
TASK_TOOL = {
    "type": "function",
    "function": {
        "name": "Task",
        "description": "Run a subtask",
        "parameters": {"type": "object", "properties": {}},
    },
}

ANTHROPIC_TOOL = {
    "name": "Bash",
    "description": "Run shell commands",
    "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}},
}

CHART_IMAGE_URL = (
    "https://quickchart.io/chart?width=500&height=300&c="
    "%7Btype%3A%27line%27%2Cdata%3A%7Blabels%3A%5B%27Jan%27%2C%27Feb%27%2C%27Mar%27%2C%27Apr%27%5D%2C"
    "datasets%3A%5B%7Blabel%3A%27Revenue%27%2Cdata%3A%5B3%2C5%2C8%2C13%5D%7D%5D%7D%7D"
)
WRAPPER = (
    "<system-reminder>\nThe following skills are available for use with the Skill tool.\n"
    + ("Skill A\n" * 200)
    + "</system-reminder>"
)


@dataclass(frozen=True)
class Case:
    name: str
    expected_tier: str
    body: dict[str, Any]
    endpoint: str = "/v1/chat/completions"
    acceptable_qualities: tuple[str, ...] = ("economy", "balanced", "premium")
    expected_lane: str | None = None


def user(prompt: str, **extra: Any) -> dict[str, Any]:
    body = {
        "model": "uncommon-route/auto",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": extra.pop("max_tokens", 96),
    }
    body.update(extra)
    return body


def cases() -> list[Case]:
    many_tools = [CHAT_TOOL, READ_TOOL, WRITE_TOOL, WEB_TOOL, TASK_TOOL]
    simple = ("economy", "balanced")
    medium = ("balanced", "economy")
    complex_quality = ("balanced", "premium")

    return [
        Case("S01 hello", "SIMPLE", user("hello"), acceptable_qualities=simple),
        Case("S02 thanks", "SIMPLE", user("thanks!"), acceptable_qualities=simple),
        Case("S03 arithmetic", "SIMPLE", user("What is 17 * 23?"), acceptable_qualities=simple),
        Case("S04 translate", "SIMPLE", user('Translate "good morning" to Spanish.'), acceptable_qualities=simple),
        Case(
            "S05 subject line",
            "SIMPLE",
            user("Write one concise subject line for a meeting reminder."),
            acceptable_qualities=simple,
        ),
        Case("S06 cjk hello", "SIMPLE", user("你好"), acceptable_qualities=simple),
        Case("S07 linear equation", "SIMPLE", user("Solve 2x + 5 = 17."), acceptable_qualities=simple),
        Case(
            "S08 json colors",
            "MEDIUM",
            {
                "model": "uncommon-route/auto",
                "messages": [
                    {"role": "system", "content": "Respond in JSON format."},
                    {"role": "user", "content": "list 3 colors"},
                ],
                "max_tokens": 96,
            },
            acceptable_qualities=medium,
        ),
        Case(
            "S09 wrapper hello tools",
            "SIMPLE",
            {
                "model": "uncommon-route/auto",
                "messages": [
                    {"role": "system", "content": "You are Claude Code."},
                    {"role": "user", "content": [{"type": "text", "text": WRAPPER}, {"type": "text", "text": "hello"}]},
                ],
                "tools": many_tools,
                "max_tokens": 48,
            },
            acceptable_qualities=simple,
        ),
        Case(
            "S10 shell one-liner",
            "SIMPLE",
            user("Give me a shell one-liner to count lines in all Python files under src."),
            acceptable_qualities=simple,
        ),
        Case(
            "M01 photosynthesis",
            "MEDIUM",
            user("Explain photosynthesis to a high school student in two paragraphs."),
            acceptable_qualities=medium,
        ),
        Case(
            "M02 email draft",
            "MEDIUM",
            user("Draft a polite email asking a vendor to resend an invoice and include our PO number."),
            acceptable_qualities=medium,
        ),
        Case(
            "M03 REST vs GraphQL",
            "MEDIUM",
            user("Compare REST and GraphQL for a small mobile app backend."),
            acceptable_qualities=medium,
        ),
        Case(
            "M04 python function",
            "MEDIUM",
            user("Write a Python function that parses a CSV string and returns grouped totals."),
            acceptable_qualities=medium,
        ),
        Case(
            "M05 SQL join",
            "MEDIUM",
            user("Write a SQL query joining orders, customers, and refunds to calculate net revenue by month."),
            acceptable_qualities=medium,
        ),
        Case(
            "M06 pandas analysis",
            "MEDIUM",
            user(
                "Given a pandas DataFrame with user_id, country, revenue, and timestamp, "
                "show code to compute weekly ARPU by country."
            ),
            acceptable_qualities=medium,
        ),
        Case(
            "M07 launch emails",
            "MEDIUM",
            user("Create a 5-part launch email sequence for a developer SaaS product."),
            acceptable_qualities=medium,
        ),
        Case(
            "M08 Tokyo itinerary",
            "MEDIUM",
            user("Plan a 3-day Tokyo itinerary optimized for first-time visitors who like food and design."),
            acceptable_qualities=medium,
        ),
        Case(
            "M09 API retry",
            "MEDIUM",
            user("Show how to add retry with exponential backoff around an HTTP API call in TypeScript."),
            acceptable_qualities=medium,
        ),
        Case(
            "M10 structured extraction",
            "MEDIUM",
            {
                "model": "uncommon-route/auto",
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Extract people, dates, and action items from this note and return JSON: "
                            "Sam will send the deck Friday; Priya owns QA next Tuesday."
                        ),
                    }
                ],
                "response_format": {"type": "json_object"},
                "max_tokens": 128,
            },
            acceptable_qualities=medium,
        ),
        Case(
            "C01 rate limiter",
            "COMPLEX",
            user(
                "Design a distributed rate limiter across many microservices using Redis. Include data structures, "
                "consistency tradeoffs, retry behavior, monitoring, and edge cases.",
                max_tokens=160,
            ),
            acceptable_qualities=complex_quality,
        ),
        Case(
            "C02 postgres migration",
            "COMPLEX",
            user(
                "Design a zero-downtime PostgreSQL migration plan for splitting a billion-row table while keeping "
                "dual writes correct.",
                max_tokens=160,
            ),
            acceptable_qualities=complex_quality,
        ),
        Case(
            "C03 OAuth security",
            "COMPLEX",
            user(
                "Review an OAuth architecture for a multi-tenant SaaS app and identify token storage, replay, "
                "privilege escalation, and audit risks.",
                max_tokens=160,
            ),
            acceptable_qualities=complex_quality,
        ),
        Case(
            "C04 algorithm proof",
            "COMPLEX",
            user("Prove correctness and analyze complexity for a lock-free work-stealing scheduler under contention."),
            acceptable_qualities=complex_quality,
        ),
        Case(
            "C05 finance constraints",
            "COMPLEX",
            user(
                "Build a tax-aware portfolio rebalancing strategy across taxable and retirement accounts with "
                "wash-sale constraints and risk bands.",
                max_tokens=160,
            ),
            acceptable_qualities=complex_quality,
        ),
        Case(
            "C06 legal analysis",
            "COMPLEX",
            user(
                "Analyze the legal and operational risks in a data processing agreement for cross-border customer analytics.",
                max_tokens=160,
            ),
            acceptable_qualities=complex_quality,
        ),
        Case(
            "C07 usage billing",
            "COMPLEX",
            user(
                "Design multi-tenant usage-based billing with idempotent metering, late event correction, invoice "
                "reconciliation, and abuse controls.",
                max_tokens=160,
            ),
            acceptable_qualities=complex_quality,
        ),
        Case(
            "C08 incident response",
            "COMPLEX",
            user(
                "Create an incident response plan for suspected credential exfiltration in Kubernetes, including "
                "containment, forensics, rotation, and postmortem steps.",
                max_tokens=160,
            ),
            acceptable_qualities=complex_quality,
        ),
        Case(
            "C09 chinese architecture",
            "COMPLEX",
            user("设计一个支持千万级用户的实时协同文档系统，说明冲突解决、存储、同步协议和灾备。", max_tokens=160),
            acceptable_qualities=complex_quality,
        ),
        Case(
            "C10 theorem reasoning",
            "COMPLEX",
            {
                "model": "uncommon-route/auto",
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Solve this carefully: derive the spectral theorem for real symmetric matrices and explain "
                            "why the eigenvectors form an orthonormal basis."
                        ),
                    }
                ],
                "reasoning_effort": "high",
                "max_tokens": 160,
            },
            acceptable_qualities=complex_quality,
            expected_lane="reasoning",
        ),
        Case(
            "T01 list files tool",
            "MEDIUM",
            {
                "model": "uncommon-route/auto",
                "messages": [{"role": "user", "content": "List the files changed in this repo and pick the best tool."}],
                "tools": [CHAT_TOOL],
                "max_tokens": 96,
            },
            acceptable_qualities=medium,
        ),
        Case(
            "T02 run tests fix",
            "MEDIUM",
            {
                "model": "uncommon-route/auto",
                "messages": [{"role": "user", "content": "Run the test suite and fix any failures you find."}],
                "tools": many_tools,
                "max_tokens": 96,
            },
            acceptable_qualities=medium,
        ),
        Case(
            "T03 inspect repo patch",
            "COMPLEX",
            {
                "model": "uncommon-route/auto",
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Inspect the routing code, identify why agent loops over-escalate trivial prompts, "
                            "implement the fix, and verify with tests."
                        ),
                    }
                ],
                "tools": many_tools,
                "max_tokens": 128,
            },
            acceptable_qualities=complex_quality,
        ),
        Case(
            "T04 tool success",
            "SIMPLE",
            {
                "model": "uncommon-route/auto",
                "messages": [
                    {"role": "user", "content": "Run pytest."},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {"id": "call_1", "type": "function", "function": {"name": "Bash", "arguments": "{\"command\":\"pytest\"}"}}
                        ],
                    },
                    {"role": "tool", "tool_call_id": "call_1", "content": "<returncode>0</returncode>\n<output>678 passed</output>"},
                ],
                "tools": [CHAT_TOOL],
                "max_tokens": 64,
            },
            acceptable_qualities=simple,
        ),
        Case(
            "T05 tool error",
            "MEDIUM",
            {
                "model": "uncommon-route/auto",
                "messages": [
                    {"role": "user", "content": "Run the parser tests."},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "Bash", "arguments": "{\"command\":\"pytest tests/test_parser.py\"}"},
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call_1",
                        "content": "<returncode>1</returncode>\n<output>AssertionError: expected token count 4 got 3</output>",
                    },
                ],
                "tools": [CHAT_TOOL],
                "max_tokens": 64,
            },
            acceptable_qualities=medium,
        ),
        Case(
            "T06 verification fail",
            "COMPLEX",
            {
                "model": "uncommon-route/auto",
                "messages": [
                    {"role": "user", "content": "Verify the serialization fix."},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {"id": "call_1", "type": "function", "function": {"name": "Bash", "arguments": "{\"command\":\"pytest\"}"}}
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call_1",
                        "content": "<returncode>0</returncode>\n<output>Final verification:\ncase 66: FAIL - parsed output differs\ncase 67: OK</output>",
                    },
                ],
                "tools": [CHAT_TOOL],
                "max_tokens": 64,
            },
            acceptable_qualities=complex_quality,
        ),
        Case(
            "F01 make shorter",
            "SIMPLE",
            {
                "model": "uncommon-route/auto",
                "messages": [
                    {"role": "user", "content": "Explain OAuth refresh tokens."},
                    {
                        "role": "assistant",
                        "content": "OAuth refresh tokens let clients obtain new access tokens without asking the user to log in again.",
                    },
                    {"role": "user", "content": "Make it shorter."},
                ],
                "max_tokens": 48,
            },
            acceptable_qualities=simple,
        ),
        Case(
            "F02 turn into JSON",
            "MEDIUM",
            {
                "model": "uncommon-route/auto",
                "messages": [
                    {"role": "user", "content": "Summarize these project milestones."},
                    {"role": "assistant", "content": "Alpha ships June 1, beta July 10, GA September 3."},
                    {"role": "user", "content": "Turn that into JSON with date and milestone fields."},
                ],
                "max_tokens": 96,
            },
            acceptable_qualities=medium,
        ),
        Case(
            "F03 top risks",
            "MEDIUM",
            {
                "model": "uncommon-route/auto",
                "messages": [
                    {"role": "user", "content": "Design a real-time collaboration backend with CRDTs and websocket fanout."},
                    {"role": "assistant", "content": "The design uses CRDT documents, websocket sessions, snapshots, and append-only operation logs."},
                    {"role": "user", "content": "What are the top three operational risks?"},
                ],
                "max_tokens": 96,
            },
            acceptable_qualities=medium,
        ),
        Case(
            "F04 rollout follow-up",
            "COMPLEX",
            {
                "model": "uncommon-route/auto",
                "messages": [
                    {"role": "user", "content": "We need to migrate billing to usage events."},
                    {"role": "assistant", "content": "We should build event ingestion, aggregation, invoice generation, and reconciliation."},
                    {"role": "user", "content": "Now break this into a safe rollout plan with backfills, audits, and rollback criteria."},
                ],
                "max_tokens": 128,
            },
            acceptable_qualities=complex_quality,
        ),
        Case(
            "F05 prior thinking hello",
            "SIMPLE",
            {
                "model": "uncommon-route/auto",
                "messages": [
                    {"role": "user", "content": "hello"},
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "thinking", "thinking": "This was prior reasoning.", "signature": "test"},
                            {"type": "text", "text": "Hello."},
                        ],
                    },
                    {"role": "user", "content": "hello"},
                ],
                "tools": [ANTHROPIC_TOOL],
                "max_tokens": 48,
            },
            endpoint="/v1/messages",
            acceptable_qualities=simple,
        ),
        Case(
            "D01 ibuprofen",
            "MEDIUM",
            user("Explain common side effects of ibuprofen and when someone should consult a clinician."),
            acceptable_qualities=medium,
        ),
        Case(
            "D02 chest pain",
            "COMPLEX",
            user(
                "A patient has crushing chest pain, sweating, and shortness of breath. Create a safe triage response "
                "with emergency guidance and differential risks.",
                max_tokens=128,
            ),
            acceptable_qualities=complex_quality,
        ),
        Case(
            "D03 PCR controls",
            "MEDIUM",
            user("Summarize the purpose of PCR controls and how to interpret a failed negative control."),
            acceptable_qualities=medium,
        ),
        Case(
            "D04 vorticity derivation",
            "COMPLEX",
            user("Derive the Navier-Stokes vorticity equation and explain each term for incompressible flow."),
            acceptable_qualities=complex_quality,
        ),
        Case(
            "D05 product priority",
            "MEDIUM",
            user("Prioritize a backlog for a small B2B onboarding product using impact, effort, risk, and learning value."),
            acceptable_qualities=medium,
        ),
        Case(
            "D06 privacy pipeline",
            "COMPLEX",
            user(
                "Design a privacy-preserving analytics pipeline with consent tracking, deletion propagation, aggregation, "
                "and auditability.",
                max_tokens=160,
            ),
            acceptable_qualities=complex_quality,
        ),
        Case(
            "D07 regex parser",
            "MEDIUM",
            user("Write and explain a regex that extracts ISO dates and dollar amounts from invoice text."),
            acceptable_qualities=medium,
        ),
        Case(
            "D08 vision chart",
            "MEDIUM",
            {
                "model": "uncommon-route/auto",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Describe this chart and extract the trend."},
                            {"type": "image_url", "image_url": {"url": CHART_IMAGE_URL}},
                        ],
                    }
                ],
                "max_tokens": 96,
            },
            acceptable_qualities=("balanced", "premium"),
            expected_lane="vision",
        ),
        Case(
            "D09 best eval framework",
            "COMPLEX",
            {
                "model": "uncommon-route/best",
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Design an evaluation framework for ranking LLM routers across cost, latency, quality, "
                            "safety, and long-horizon agent reliability."
                        ),
                    }
                ],
                "max_tokens": 160,
            },
            acceptable_qualities=("premium", "balanced"),
        ),
    ]


def post_json(path: str, body: dict[str, Any], timeout: int) -> tuple[int | str, dict[str, Any], dict[str, str]]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        BASE_URL + path,
        data=data,
        headers={"content-type": "application/json", "authorization": "Bearer local-e2e"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, json.loads(raw), {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"error": raw}
        return exc.code, payload, {k.lower(): v for k, v in exc.headers.items()}
    except Exception as exc:  # pragma: no cover - operator script
        return "ERR", {"error": f"{type(exc).__name__}: {exc}"}, {}


def route_preview(case: Case, timeout: int) -> dict[str, Any]:
    status, payload, _headers = post_json("/v1/route-preview", case.body, timeout)
    return {
        "status": status,
        "tier": payload.get("served_tier"),
        "model": payload.get("served_model"),
        "quality": payload.get("served_quality"),
        "lane": payload.get("capability_lane"),
        "reasoning": payload.get("reasoning", ""),
        "error": payload.get("error"),
    }


def live_request(case: Case, timeout: int) -> dict[str, Any]:
    status, payload, headers = post_json(case.endpoint, case.body, timeout)
    return {
        "status": status,
        "tier": headers.get("x-uncommon-route-tier") or payload.get("served_tier"),
        "model": headers.get("x-uncommon-route-model") or payload.get("model"),
        "quality": headers.get("x-uncommon-route-served-quality") or payload.get("served_quality"),
        "lane": (
            headers.get("x-uncommon-route-lane")
            or headers.get("x-uncommon-route-capability-lane")
            or payload.get("capability_lane")
        ),
        "reasoning": headers.get("x-uncommon-route-reasoning") or "",
        "error": payload.get("error") or payload.get("message"),
    }


def evaluate(case: Case, result: dict[str, Any]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    if result["status"] != 200:
        failures.append(f"status={result['status']}")
    if result["tier"] != case.expected_tier:
        failures.append(f"tier expected {case.expected_tier} got {result['tier']}")
    quality = str(result.get("quality") or "").lower()
    if quality and quality not in case.acceptable_qualities:
        failures.append(f"quality expected one of {case.acceptable_qualities} got {quality}")
    if case.expected_lane is not None and result.get("lane") != case.expected_lane:
        failures.append(f"lane expected {case.expected_lane} got {result.get('lane')}")
    return not failures, failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("preview", "live"), default="preview")
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--case", action="append", default=[], help="Run cases whose name contains this text.")
    parser.add_argument("--sleep", type=float, default=0.0)
    args = parser.parse_args()

    selected = cases()
    if len(selected) != 50:
        raise SystemExit(f"expected 50 cases, got {len(selected)}")
    if args.case:
        wanted = [part.lower() for part in args.case]
        selected = [case for case in selected if any(part in case.name.lower() for part in wanted)]
    if args.limit:
        selected = selected[: args.limit]

    runner = route_preview if args.mode == "preview" else live_request
    rows: list[tuple[Case, dict[str, Any], bool, list[str]]] = []
    for case in selected:
        result = runner(case, args.timeout)
        ok, failures = evaluate(case, result)
        rows.append((case, result, ok, failures))
        if args.sleep:
            time.sleep(args.sleep)

    print(f"mode={args.mode} total={len(rows)} ok={sum(1 for _, _, ok, _ in rows if ok)}")
    for case, result, ok, failures in rows:
        marker = "OK" if ok else "!!"
        print(
            f"{marker} {case.name:24} exp={case.expected_tier:7} got={str(result.get('tier')):7} "
            f"q={str(result.get('quality')):8} lane={str(result.get('lane')):20} "
            f"model={result.get('model')} status={result.get('status')}"
        )
        if failures:
            print("   failures:", "; ".join(failures))
            reasoning = str(result.get("reasoning") or result.get("error") or "")
            if reasoning:
                print("   detail:", reasoning[:700])

    print("tiers", dict(Counter(result.get("tier") for _, result, _, _ in rows)))
    print("qualities", dict(Counter(result.get("quality") for _, result, _, _ in rows)))
    print("models", Counter(result.get("model") for _, result, _, _ in rows).most_common(12))

    return 0 if all(ok for _, _, ok, _ in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
