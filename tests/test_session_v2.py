"""Tests for derive_session_id_v2 — prefix-trie session matching."""

from __future__ import annotations

from uncommon_route.session import RecentSessions, derive_session_id_v2


class TestDeriveSessionIdV2:
    def test_fresh_conversation_creates_new_id(self) -> None:
        registry = RecentSessions()
        sid = derive_session_id_v2(
            msg_hashes=["aaaaaaaaaaaaaaaa"],
            first_user_v2="aaaaaaaaaaaaaaaa",
            system_hash="",
            metadata_uid="",
            prev_response="",
            registry=registry,
            now=1_000.0,
        )
        assert isinstance(sid, str)
        assert len(sid) == 8

    def test_identical_msg_hashes_reuses_id(self) -> None:
        """Single-shot prompt repeated with same first-user content should
        collapse into the same session, not fragment."""
        registry = RecentSessions()
        sid1 = derive_session_id_v2(
            msg_hashes=["h"],
            first_user_v2="h",
            system_hash="",
            metadata_uid="",
            prev_response="",
            registry=registry,
            now=1_000.0,
        )
        sid2 = derive_session_id_v2(
            msg_hashes=["h"],
            first_user_v2="h",
            system_hash="",
            metadata_uid="",
            prev_response="",
            registry=registry,
            now=1_001.0,
        )
        assert sid1 == sid2

    def test_strict_prefix_extension_reuses_id(self) -> None:
        registry = RecentSessions()
        sid1 = derive_session_id_v2(
            msg_hashes=["a", "b"],
            first_user_v2="a",
            system_hash="",
            metadata_uid="",
            prev_response="",
            registry=registry,
            now=1_000.0,
        )
        sid2 = derive_session_id_v2(
            msg_hashes=["a", "b", "c", "d"],
            first_user_v2="a",
            system_hash="",
            metadata_uid="",
            prev_response="",
            registry=registry,
            now=1_001.0,
        )
        assert sid1 == sid2

    def test_non_prefix_creates_new_id(self) -> None:
        registry = RecentSessions()
        sid1 = derive_session_id_v2(
            msg_hashes=["a", "b"],
            first_user_v2="a",
            system_hash="",
            metadata_uid="",
            prev_response="",
            registry=registry,
            now=1_000.0,
        )
        sid2 = derive_session_id_v2(
            msg_hashes=["x", "y"],  # different first hash → not a prefix
            first_user_v2="x",
            system_hash="",
            metadata_uid="",
            prev_response="",
            registry=registry,
            now=1_001.0,
        )
        assert sid1 != sid2

    def test_compact_simulates_prefix_break(self) -> None:
        """When /compact rewrites history, the new prefix doesn't match."""
        registry = RecentSessions()
        sid1 = derive_session_id_v2(
            msg_hashes=["a", "b", "c"],
            first_user_v2="a",
            system_hash="s",
            metadata_uid="",
            prev_response="",
            registry=registry,
            now=1_000.0,
        )
        sid2 = derive_session_id_v2(
            msg_hashes=["summary"],
            first_user_v2="summary",
            system_hash="s",
            metadata_uid="",
            prev_response="",
            registry=registry,
            now=1_010.0,
        )
        assert sid1 != sid2

    def test_previous_response_id_rescues_session(self) -> None:
        registry = RecentSessions()
        sid1 = derive_session_id_v2(
            msg_hashes=["a"],
            first_user_v2="a",
            system_hash="",
            metadata_uid="",
            prev_response="",
            registry=registry,
            now=1_000.0,
        )
        registry.record_response_id(sid1, "resp_xyz")

        sid2 = derive_session_id_v2(
            msg_hashes=["different"],  # would otherwise create a new session
            first_user_v2="different",
            system_hash="",
            metadata_uid="",
            prev_response="resp_xyz",  # but the chain claims it's the same one
            registry=registry,
            now=1_005.0,
        )
        assert sid1 == sid2

    def test_ttl_eviction(self) -> None:
        registry = RecentSessions(ttl_seconds=60.0)
        sid1 = derive_session_id_v2(
            msg_hashes=["a"],
            first_user_v2="a",
            system_hash="",
            metadata_uid="",
            prev_response="",
            registry=registry,
            now=0.0,
        )
        # 120 s later: extension that was a prefix should NOT match (sid1 evicted)
        sid2 = derive_session_id_v2(
            msg_hashes=["a", "b"],
            first_user_v2="a",
            system_hash="",
            metadata_uid="",
            prev_response="",
            registry=registry,
            now=120.0,
        )
        assert sid1 != sid2

    def test_capacity_lru_eviction(self) -> None:
        registry = RecentSessions(capacity=3, ttl_seconds=10_000.0)
        # Fill capacity.
        sids = []
        for i in range(3):
            sids.append(
                derive_session_id_v2(
                    msg_hashes=[f"msg_{i}"],
                    first_user_v2=f"msg_{i}",
                    system_hash="",
                    metadata_uid="",
                    prev_response="",
                    registry=registry,
                    now=float(i),
                )
            )
        # Touch sid 1 and 2 to make 0 the LRU.
        derive_session_id_v2(
            msg_hashes=["msg_1", "extra"],
            first_user_v2="msg_1",
            system_hash="",
            metadata_uid="",
            prev_response="",
            registry=registry,
            now=10.0,
        )
        derive_session_id_v2(
            msg_hashes=["msg_2", "extra"],
            first_user_v2="msg_2",
            system_hash="",
            metadata_uid="",
            prev_response="",
            registry=registry,
            now=11.0,
        )
        # Insert a 4th: pushes capacity over → evict LRU (sid 0).
        derive_session_id_v2(
            msg_hashes=["msg_NEW"],
            first_user_v2="msg_NEW",
            system_hash="",
            metadata_uid="",
            prev_response="",
            registry=registry,
            now=12.0,
        )
        # Now sid 0's prefix should miss (it was evicted).
        sid_zero_again = derive_session_id_v2(
            msg_hashes=["msg_0", "extra"],
            first_user_v2="msg_0",
            system_hash="",
            metadata_uid="",
            prev_response="",
            registry=registry,
            now=13.0,
        )
        assert sid_zero_again != sids[0]


class TestRecentSessionsRecordResponseId:
    def test_lookup_after_record(self) -> None:
        registry = RecentSessions()
        registry.record_response_id("sess_abc", "resp_001")
        assert registry.find_by_response_id("resp_001") == "sess_abc"

    def test_lookup_miss_returns_none(self) -> None:
        registry = RecentSessions()
        assert registry.find_by_response_id("resp_unknown") is None
