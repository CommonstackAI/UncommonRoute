"""Tests for the one-time legacy traces.json migration."""

from __future__ import annotations

import json
from pathlib import Path

from uncommon_route.traces import migrate_legacy_json


class TestMigrateLegacyJson:
    def test_splits_into_daily_jsonl(self, tmp_path: Path) -> None:
        legacy = tmp_path / "traces.json"
        new_dir = tmp_path / "traces"
        legacy.write_text(
            json.dumps(
                [
                    {"request_id": "a", "timestamp": 1777249800.0},  # 2026-04-27 00:30 UTC
                    {"request_id": "b", "timestamp": 1777292400.0},  # same day, 12:20 UTC
                    {"request_id": "c", "timestamp": 1777336200.0},  # 2026-04-28 00:30 UTC
                ]
            )
        )

        migrated = migrate_legacy_json(legacy, new_dir)

        assert migrated is True
        assert (new_dir / "2026-04-27.jsonl").exists()
        assert (new_dir / "2026-04-28.jsonl").exists()
        a_day = (new_dir / "2026-04-27.jsonl").read_text().splitlines()
        assert len(a_day) == 2
        # Legacy file is renamed, not deleted.
        assert not legacy.exists()
        assert (legacy.parent / "traces.json.bak").exists()

    def test_idempotent_when_traces_dir_already_exists(self, tmp_path: Path) -> None:
        legacy = tmp_path / "traces.json"
        new_dir = tmp_path / "traces"
        new_dir.mkdir()
        (new_dir / "2026-04-27.jsonl").write_text("")
        legacy.write_text("[]")

        migrated = migrate_legacy_json(legacy, new_dir)

        # Already migrated → no-op.
        assert migrated is False
        assert legacy.exists()  # untouched

    def test_no_legacy_file_is_no_op(self, tmp_path: Path) -> None:
        new_dir = tmp_path / "traces"
        migrated = migrate_legacy_json(tmp_path / "traces.json", new_dir)
        assert migrated is False
        assert not new_dir.exists() or list(new_dir.glob("*")) == []

    def test_corrupt_legacy_file_does_not_crash(self, tmp_path: Path) -> None:
        legacy = tmp_path / "traces.json"
        legacy.write_text("not valid json")
        new_dir = tmp_path / "traces"
        migrated = migrate_legacy_json(legacy, new_dir)
        assert migrated is False
        # Original left in place for human inspection.
        assert legacy.exists()
