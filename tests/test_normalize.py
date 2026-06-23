"""Tests for message normalization helpers."""

from __future__ import annotations

from uncommon_route.normalize import (
    hash16,
    normalize_message_text,
    normalize_messages_to_hashes,
)


class TestNormalizeMessageText:
    def test_string_content(self) -> None:
        msg = {"role": "user", "content": "  hello   world  "}
        assert normalize_message_text(msg) == "hello world"

    def test_text_blocks(self) -> None:
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "first"},
                {"type": "text", "text": "second"},
            ],
        }
        assert normalize_message_text(msg) == "first second"

    def test_tool_use_block(self) -> None:
        msg = {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "name": "Read", "input": {"path": "/tmp/x"}}
            ],
        }
        out = normalize_message_text(msg)
        assert "[tool_use:Read(" in out
        # arguments are canonical JSON (sorted keys, no whitespace)
        assert '{"path":"/tmp/x"}' in out

    def test_tool_result_block(self) -> None:
        msg = {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "abc", "content": "ok"}
            ],
        }
        assert normalize_message_text(msg) == "ok"

    def test_tool_result_with_blocks(self) -> None:
        msg = {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "abc",
                    "content": [{"type": "text", "text": "nested"}],
                }
            ],
        }
        assert normalize_message_text(msg) == "nested"

    def test_image_block_placeholder(self) -> None:
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "see"},
                {"type": "image", "source": {"data": "base64..."}},
            ],
        }
        assert normalize_message_text(msg) == "see [image]"

    def test_whitespace_collapse(self) -> None:
        msg = {"role": "user", "content": "a\n\nb\t  c\r\nd"}
        assert normalize_message_text(msg) == "a b c d"

    def test_empty_content(self) -> None:
        assert normalize_message_text({"role": "user", "content": ""}) == ""
        assert normalize_message_text({"role": "user"}) == ""


class TestHash16:
    def test_returns_16_hex_chars(self) -> None:
        h = hash16("hello")
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic(self) -> None:
        assert hash16("x") == hash16("x")

    def test_different_inputs(self) -> None:
        assert hash16("a") != hash16("b")


class TestNormalizeMessagesToHashes:
    def test_one_hash_per_message(self) -> None:
        messages = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
        ]
        hashes = normalize_messages_to_hashes(messages)
        assert len(hashes) == 2
        assert all(len(h) == 16 for h in hashes)

    def test_same_messages_same_hashes(self) -> None:
        messages = [{"role": "user", "content": "x"}]
        assert normalize_messages_to_hashes(messages) == normalize_messages_to_hashes(messages)

    def test_role_independent_only_content_matters(self) -> None:
        # Same content text → same hash regardless of role label.
        # (Role is captured by message position in the list, not in the hash.)
        a = normalize_messages_to_hashes([{"role": "user", "content": "x"}])
        b = normalize_messages_to_hashes([{"role": "assistant", "content": "x"}])
        assert a == b


class TestPinnedStability:
    """Tripwires guarding the on-disk hash contract.

    These hashes are persisted as session_id components. If the algorithm
    (UTF-8 SHA-256, first 16 hex chars) or the normalization pipeline
    changes, all already-persisted sessions become unreachable. Don't
    update these expected values lightly — a change here is a database
    migration.
    """

    def test_hash16_pinned(self) -> None:
        # SHA-256("hello").hexdigest()[:16]
        assert hash16("hello") == "2cf24dba5fb0a30e"
        # Empty string is a meaningful input — pin it explicitly.
        assert hash16("") == "e3b0c44298fc1c14"

    def test_normalize_messages_to_hashes_pinned(self) -> None:
        messages = [{"role": "user", "content": "hello"}]
        # hash16("hello") — confirms normalize→hash16 pipeline produces the
        # same value as raw hash16 for plain string content.
        assert normalize_messages_to_hashes(messages) == ["2cf24dba5fb0a30e"]


class TestEdgeCases:
    def test_tool_result_none_content(self) -> None:
        msg = {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "x", "content": None}
            ],
        }
        assert normalize_message_text(msg) == ""

    def test_unknown_block_type_emits_nothing(self) -> None:
        msg = {
            "role": "user",
            "content": [
                {"type": "audio", "data": "..."},
                {"type": "text", "text": "after"},
            ],
        }
        assert normalize_message_text(msg) == "after"

    def test_non_dict_block_skipped(self) -> None:
        msg = {
            "role": "user",
            "content": [
                "stray string",
                {"type": "text", "text": "real"},
            ],
        }
        assert normalize_message_text(msg) == "real"
