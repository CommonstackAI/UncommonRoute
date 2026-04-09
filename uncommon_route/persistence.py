"""Schema-versioned atomic persistence for v2 learned state."""

from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("uncommon-route.persistence")

SCHEMA_VERSION = 1
STATE_FILENAME = "learned_state.json"


@dataclass
class LearnedState:
    signal_weights: list[float] = field(default_factory=lambda: [0.55, 0.45])
    calibration_temperature: float = 1.0
    shadow_consecutive_wins: int = 0
    shadow_promoted: bool = False
    embedding_index_size: int = 0
    model_priors: dict[str, Any] = field(default_factory=dict)


def save_state(state: LearnedState, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    data = asdict(state)
    data["schema_version"] = SCHEMA_VERSION
    target = directory / STATE_FILENAME
    fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        Path(tmp_path).replace(target)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def load_state(directory: Path) -> LearnedState:
    path = directory / STATE_FILENAME
    if not path.exists():
        return LearnedState()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("schema_version") != SCHEMA_VERSION:
            logger.warning("Schema version mismatch, using defaults")
            return LearnedState()
        data.pop("schema_version", None)
        return LearnedState(**{k: v for k, v in data.items() if k in LearnedState.__dataclass_fields__})
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning(f"Corrupt state file: {e}, using defaults")
        return LearnedState()


def reset_state(directory: Path) -> None:
    save_state(LearnedState(), directory)
