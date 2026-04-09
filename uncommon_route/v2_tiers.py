"""4-tier constants for UncommonRoute v2 (aligned with LLMRouterBench)."""

TIER_LOW = "low"
TIER_MID = "mid"
TIER_MID_HIGH = "mid_high"
TIER_HIGH = "high"

TIER_TO_ID: dict[str, int] = {
    TIER_LOW: 0,
    TIER_MID: 1,
    TIER_MID_HIGH: 2,
    TIER_HIGH: 3,
}
ID_TO_TIER: dict[int, str] = {v: k for k, v in TIER_TO_ID.items()}

V1_TO_V2: dict[str, str] = {"SIMPLE": "low", "MEDIUM": "mid", "COMPLEX": "high"}
V2_TO_V1: dict[str, str] = {"low": "SIMPLE", "mid": "MEDIUM", "mid_high": "COMPLEX", "high": "COMPLEX"}


def tier_id_from_name(name: str) -> int:
    if name not in TIER_TO_ID:
        raise ValueError(f"Unknown tier: {name!r}")
    return TIER_TO_ID[name]


def tier_name_from_id(tier_id: int) -> str:
    if tier_id not in ID_TO_TIER:
        raise ValueError(f"Unknown tier_id: {tier_id!r}")
    return ID_TO_TIER[tier_id]
