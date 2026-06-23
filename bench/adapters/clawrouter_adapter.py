"""Python-side wrapper: pipe question-bank rows to the Node ClawRouter process.

We keep a single Node child warm for the whole eval so each row is just one
stdin write + one stdout readline — no per-row startup cost.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

ADAPTER_JS = Path(__file__).resolve().parent / "clawrouter_node.mjs"


def _row_to_prompt(row: dict[str, Any]) -> tuple[str, str | None]:
    """Flatten the question-bank messages into a single user prompt + optional system."""
    messages = row.get("messages") or []
    system_prompt: str | None = None
    user_parts: list[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "\n".join(
                str(part.get("text", "")) for part in content
                if isinstance(part, dict) and part.get("type") in {"text", "input_text"}
            )
        if not text.strip():
            continue
        if role == "system":
            system_prompt = text if system_prompt is None else f"{system_prompt}\n{text}"
        else:
            user_parts.append(text)
    return "\n".join(user_parts), system_prompt


class ClawRouterPredictor:
    def __init__(self) -> None:
        self._proc = subprocess.Popen(
            ["node", str(ADAPTER_JS)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

    def predict(self, row: dict[str, Any]) -> int:
        prompt, system_prompt = _row_to_prompt(row)
        req = {"prompt": prompt, "system_prompt": system_prompt}
        assert self._proc.stdin is not None and self._proc.stdout is not None
        self._proc.stdin.write(json.dumps(req) + "\n")
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            err = self._proc.stderr.read() if self._proc.stderr else ""
            raise RuntimeError(f"ClawRouter adapter died: {err}")
        res = json.loads(line)
        return int(res.get("tier_id", 1))

    def close(self) -> None:
        if self._proc.poll() is None:
            try:
                if self._proc.stdin is not None:
                    self._proc.stdin.close()
            except Exception:
                pass
            self._proc.wait(timeout=5)

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
