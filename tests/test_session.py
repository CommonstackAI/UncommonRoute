"""Tests for session identity utilities."""

from __future__ import annotations


from uncommon_route.session import derive_session_id


class TestDeriveSessionId:
    def test_returns_8_char_hash_from_first_user_message(self) -> None:
        messages = [
            {"role": "system", "content": "you are helpful"},
            {"role": "user", "content": "hello world"},
        ]
        sid = derive_session_id(messages)
        assert sid is not None
        assert len(sid) == 8

    def test_returns_none_when_no_user_message(self) -> None:
        messages = [{"role": "system", "content": "you are helpful"}]
        assert derive_session_id(messages) is None

    def test_deterministic(self) -> None:
        messages = [{"role": "user", "content": "test prompt"}]
        assert derive_session_id(messages) == derive_session_id(messages)
