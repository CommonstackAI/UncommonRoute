from uncommon_route.router.signal_tuning import (
    contextual_followup_floor_from_text,
    strip_client_wrapper_blocks,
    system_prompt_is_title_generation_sidechannel,
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


def test_contextual_followup_ignores_client_wrapper_blocks() -> None:
    floor = contextual_followup_floor_from_text(
        prior_text=(
            "<system-reminder>\n"
            "Design a distributed platform with CRDTs, websocket fanout, "
            "permission boundaries, audit logging, and rollout plans.\n"
            "</system-reminder>\n"
            "hello"
        ),
        latest_text="帮我创建一个新的 Python 项目目录，叫 weather-cli",
    )

    assert strip_client_wrapper_blocks("<system-reminder>hidden</system-reminder> hello") == "hello"
    assert floor is None


def test_short_run_followup_does_not_inherit_complex_floor() -> None:
    floor = contextual_followup_floor_from_text(
        prior_text="就按这个方案，帮我把所有代码写出来",
        latest_text="跑一下试试，查一下北京的天气",
    )

    assert floor is None


def test_short_commit_followup_does_not_inherit_context_floor() -> None:
    floor = contextual_followup_floor_from_text(
        prior_text="跑一下试试，查一下北京的天气\n北京天气查询正常。",
        latest_text="没问题的话帮我创建 git 并 commit",
    )

    assert floor is None


def test_short_dense_build_followup_can_inherit_complex_floor() -> None:
    floor = contextual_followup_floor_from_text(
        prior_text=(
            "我想做一个 免费的无需额外配置的命令行天气查询工具，"
            "输入城市名就能显示当前天气、温度、湿度。帮我规划一下："
            "用什么天气 API、项目结构怎么组织、需要处理哪些边界情况"
        ),
        latest_text="就按这个方案，帮我把所有代码写出来",
    )

    assert floor is Tier.COMPLEX


def test_protocol_structured_system_prompt_is_not_a_domain_whitelist() -> None:
    assert system_prompt_has_structured_output_constraint("Respond in JSON format.")
    assert not system_prompt_has_structured_output_constraint("You are Claude Code.")
    title_prompt = (
        'Generate a concise, sentence-case title. Return JSON with a single "title" field.'
    )
    assert system_prompt_is_title_generation_sidechannel(title_prompt)
    assert not system_prompt_has_structured_output_constraint(title_prompt)


def test_vision_floor_depends_on_modality_and_prompt_shape() -> None:
    assert vision_prompt_needs_medium_floor(has_vision=True, prompt="What is this?")
    assert not vision_prompt_needs_medium_floor(has_vision=False, prompt="What is this?")
