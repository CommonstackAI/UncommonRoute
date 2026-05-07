from uncommon_route.router.signal_tuning import (
    contextual_followup_floor_from_text,
    system_prompt_has_structured_output_constraint,
    text_high_substance_score,
    text_substance_score,
    vision_prompt_needs_medium_floor,
)
from uncommon_route.router.types import Tier


def test_text_substance_score_separates_shape_from_domain_words() -> None:
    simple = text_substance_score("Write one concise subject line for a meeting reminder.")
    compound = text_substance_score(
        "Design a privacy-preserving analytics pipeline with consent tracking and auditability."
    )
    multi_constraint = text_substance_score(
        "Implement a Python CLI app with tests, packaging, error handling, and documentation."
    )

    assert simple < 0.20
    assert compound >= 0.32
    assert multi_constraint >= 0.32
    assert text_high_substance_score(
        "Design a privacy-preserving analytics pipeline with consent tracking and auditability."
    ) >= 0.46
    assert text_high_substance_score(
        "Implement a Python CLI app with tests, packaging, error handling, and documentation."
    ) < 0.46


def test_contextual_followup_uses_prior_and_latest_substance_scores() -> None:
    floor = contextual_followup_floor_from_text(
        prior_text=(
            "Design a real-time collaboration backend with CRDTs and websocket fanout.\n"
            "The design uses documents, sessions, snapshots, and append-only operation logs."
        ),
        latest_text="What are the top three operational risks?",
    )

    assert floor is Tier.MEDIUM


def test_contextual_followup_can_escalate_dense_latest_request() -> None:
    floor = contextual_followup_floor_from_text(
        prior_text=(
            "We need to migrate billing to usage events.\n"
            "Build event ingestion, aggregation, invoice generation, and reconciliation."
        ),
        latest_text="Now break this into a safe rollout plan with backfills, audits, and rollback criteria.",
    )

    assert floor is Tier.COMPLEX


def test_protocol_structured_system_prompt_is_not_a_domain_whitelist() -> None:
    assert system_prompt_has_structured_output_constraint("Respond in JSON format.")
    assert not system_prompt_has_structured_output_constraint("You are Claude Code.")


def test_vision_floor_depends_on_modality_and_prompt_shape() -> None:
    assert vision_prompt_needs_medium_floor(has_vision=True, prompt="What is this?")
    assert not vision_prompt_needs_medium_floor(has_vision=False, prompt="What is this?")
