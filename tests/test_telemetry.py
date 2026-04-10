"""E2E tests for telemetry: opt-in logic, record lifecycle, buffer, privacy."""

import json
import os
from pathlib import Path
from unittest import mock

import numpy as np

import uncommon_route.telemetry as telem


def _reset(tmp_path):
    """Point telemetry at tmp_path and reset state."""
    telem._config_dir = lambda: tmp_path
    # Clear any env vars
    for key in ("DO_NOT_TRACK", "UNCOMMON_ROUTE_TELEMETRY", "CI"):
        os.environ.pop(key, None)


# ─── Opt-In Logic ───

def test_default_is_disabled(tmp_path):
    _reset(tmp_path)
    assert not telem.is_enabled()


def test_env_var_enables(tmp_path):
    _reset(tmp_path)
    os.environ["UNCOMMON_ROUTE_TELEMETRY"] = "on"
    assert telem.is_enabled()
    os.environ.pop("UNCOMMON_ROUTE_TELEMETRY")


def test_do_not_track_wins(tmp_path):
    _reset(tmp_path)
    os.environ["UNCOMMON_ROUTE_TELEMETRY"] = "on"
    os.environ["DO_NOT_TRACK"] = "1"
    assert not telem.is_enabled()
    os.environ.pop("UNCOMMON_ROUTE_TELEMETRY")
    os.environ.pop("DO_NOT_TRACK")


def test_config_file_persists(tmp_path):
    _reset(tmp_path)
    telem.enable()
    assert telem.is_enabled()
    telem.disable()
    assert not telem.is_enabled()


def test_ci_environment_auto_off_when_no_config(tmp_path):
    """CI=true prevents auto-prompt but does NOT override explicit config."""
    _reset(tmp_path)
    os.environ["CI"] = "true"
    # No config set → CI auto-off
    assert not telem.is_enabled()
    os.environ.pop("CI")


def test_explicit_config_respected_in_ci(tmp_path):
    """User who explicitly enabled should stay enabled even in CI."""
    _reset(tmp_path)
    telem.enable()
    os.environ["CI"] = "true"
    assert telem.is_enabled()  # explicit config wins over CI
    os.environ.pop("CI")


# ─── Record Building ───

def test_record_to_dict():
    rec = telem.TelemetryRecord(
        predicted_tier=0,
        routed_model="test-model",
        confidence=0.85,
        outcome="success",
    )
    d = rec.to_dict()
    assert d["predicted_tier"] == 0
    assert d["routed_model"] == "test-model"
    assert "embedding" not in d  # None embedding removed


def test_record_with_embedding():
    rec = telem.TelemetryRecord(
        predicted_tier=1,
        embedding=[0.1] * 384,
    )
    d = rec.to_dict()
    assert "embedding" in d
    assert len(d["embedding"]) == 384


# ─── Embedding Noise ───

def test_prepare_embedding_adds_noise():
    raw = np.random.randn(384).astype(np.float32)
    raw = raw / np.linalg.norm(raw)
    noised = telem.prepare_embedding(raw, text_token_count=50)
    assert noised is not None
    assert len(noised) == 384
    # Should be close but not identical
    noised_arr = np.array(noised, dtype=np.float32)
    cosine = float(np.dot(raw, noised_arr))
    assert cosine > 0.90  # noise sigma=0.02 on 384-dim
    assert cosine < 1.0   # but not zero


def test_prepare_embedding_skips_short():
    raw = np.random.randn(384).astype(np.float32)
    result = telem.prepare_embedding(raw, text_token_count=5)
    assert result is None  # skip short messages


# ─── Buffer + Transmission ───

def test_buffer_and_status(tmp_path):
    _reset(tmp_path)
    telem.enable()
    rec = telem.TelemetryRecord(predicted_tier=0, outcome="success")
    telem.buffer_record(rec)
    s = telem.status()
    assert s["enabled"]
    assert s["pending_records"] == 1
    assert s["total_sent"] == 0


def test_disable_discards_buffer(tmp_path):
    _reset(tmp_path)
    telem.enable()
    rec = telem.TelemetryRecord(predicted_tier=0, outcome="success")
    telem.buffer_record(rec)
    assert telem.status()["pending_records"] == 1
    telem.disable()
    assert telem.status()["pending_records"] == 0


def test_flush_moves_to_sent_log(tmp_path):
    _reset(tmp_path)
    telem.enable()

    # Buffer a record
    rec = telem.TelemetryRecord(predicted_tier=0, outcome="success")
    telem.buffer_record(rec)
    assert telem.status()["pending_records"] == 1

    # Mock successful send
    with mock.patch.object(telem, "_send_batch", return_value=True):
        count = telem.flush()

    assert count == 1
    assert telem.status()["pending_records"] == 0
    assert telem.status()["total_sent"] == 1

    # Verify sent log
    sent = telem.get_sent_records()
    assert len(sent) == 1
    assert sent[0]["predicted_tier"] == 0


def test_flush_on_failure_keeps_buffer(tmp_path):
    _reset(tmp_path)
    telem.enable()

    rec = telem.TelemetryRecord(predicted_tier=1, outcome="http_error")
    telem.buffer_record(rec)

    # Mock failed send
    with mock.patch.object(telem, "_send_batch", return_value=False):
        count = telem.flush()

    assert count == 0
    assert telem.status()["pending_records"] == 1  # still pending
    assert telem.status()["total_sent"] == 0


def test_disabled_telemetry_does_not_buffer(tmp_path):
    _reset(tmp_path)
    # Don't enable
    rec = telem.TelemetryRecord(predicted_tier=0, outcome="success")
    telem.buffer_record(rec)
    assert telem.status()["pending_records"] == 0


# ─── E2E: Full Record Lifecycle ───

def test_e2e_two_stage_lifecycle(tmp_path):
    """Stage 1: create skeleton → Stage 2: add outcome → buffer → flush."""
    _reset(tmp_path)
    telem.enable()

    # Stage 1: routing decision
    rec = telem.TelemetryRecord(
        schema_version=1,
        client_version="0.5.0",
        platform="darwin",
        timestamp_day="2026-04-10",
        predicted_tier=0,
        routed_model="deepseek-chat",
        confidence=0.85,
        routing_method="direct",
        message_count=4,
        has_tools=False,
        tool_count=0,
    )

    # Stage 2: outcome arrives
    rec.outcome = "success"
    rec.user_feedback = "ok"
    rec.final_tier = 0
    rec.final_model = "deepseek-chat"
    rec.cascaded = False

    # Add noised embedding
    raw_emb = np.random.randn(384).astype(np.float32)
    raw_emb = raw_emb / np.linalg.norm(raw_emb)
    rec.embedding = telem.prepare_embedding(raw_emb, text_token_count=50)

    # Buffer
    telem.buffer_record(rec)
    assert telem.status()["pending_records"] == 1

    # Flush (mock network)
    with mock.patch.object(telem, "_send_batch", return_value=True):
        telem.flush()

    assert telem.status()["pending_records"] == 0
    assert telem.status()["total_sent"] == 1

    # Verify sent record
    sent = telem.get_sent_records()
    assert len(sent) == 1
    assert sent[0]["predicted_tier"] == 0
    assert sent[0]["outcome"] == "success"
    assert sent[0]["user_feedback"] == "ok"
    assert "embedding" in sent[0]
    assert len(sent[0]["embedding"]) == 384
