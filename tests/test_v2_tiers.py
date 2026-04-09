import pytest

from uncommon_route.v2_tiers import (
    TIER_LOW, TIER_MID, TIER_MID_HIGH, TIER_HIGH,
    TIER_TO_ID, ID_TO_TIER, V1_TO_V2, V2_TO_V1,
    tier_id_from_name, tier_name_from_id,
)


def test_tier_constants():
    assert TIER_LOW == "low"
    assert TIER_MID == "mid"
    assert TIER_MID_HIGH == "mid_high"
    assert TIER_HIGH == "high"


def test_tier_to_id():
    assert TIER_TO_ID == {"low": 0, "mid": 1, "mid_high": 2, "high": 3}


def test_id_to_tier():
    assert ID_TO_TIER == {0: "low", 1: "mid", 2: "mid_high", 3: "high"}


def test_v1_to_v2_mapping():
    assert V1_TO_V2 == {"SIMPLE": "low", "MEDIUM": "mid", "COMPLEX": "high"}


def test_v2_to_v1_mapping():
    assert V2_TO_V1["low"] == "SIMPLE"
    assert V2_TO_V1["mid"] == "MEDIUM"
    assert V2_TO_V1["mid_high"] == "COMPLEX"
    assert V2_TO_V1["high"] == "COMPLEX"


def test_tier_id_from_name():
    assert tier_id_from_name("low") == 0
    assert tier_id_from_name("high") == 3


def test_tier_name_from_id():
    assert tier_name_from_id(0) == "low"
    assert tier_name_from_id(3) == "high"


def test_tier_id_from_name_invalid():
    with pytest.raises(ValueError):
        tier_id_from_name("nonexistent")


def test_tier_name_from_id_invalid():
    with pytest.raises(ValueError):
        tier_name_from_id(99)
