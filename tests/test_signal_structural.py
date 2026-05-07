from uncommon_route.signals.structural import StructuralSignal


def test_simple_prompt_predicts_low():
    sig = StructuralSignal()
    row = {"messages": [{"role": "user", "content": "hello"}]}
    vote = sig.predict(row)
    assert vote.tier_id == 0
    assert vote.confidence > 0.0


def test_system_prompt_does_not_escalate_simple_latest_user_message():
    sig = StructuralSignal()
    row = {"messages": [
        {
            "role": "system",
            "content": "You are Claude Code, an interactive CLI for software engineering. " * 80,
        },
        {"role": "user", "content": "say hello to me"},
    ]}
    vote = sig.predict(row)
    assert vote.tier_id == 0
    assert vote.confidence > 0.0


def test_short_system_prompt_constraints_still_influence_classification():
    sig = StructuralSignal()
    row = {"messages": [
        {"role": "system", "content": "You are helpful. Respond in JSON format."},
        {"role": "user", "content": "list 3 colors"},
    ]}
    vote = sig.predict(row)
    assert vote.tier_id >= 1


def test_short_persona_system_prompt_does_not_escalate_simple_latest_user_message():
    sig = StructuralSignal()
    row = {"messages": [
        {"role": "system", "content": "You are Claude Code."},
        {"role": "user", "content": "hello"},
    ]}
    vote = sig.predict(row)
    assert vote.tier_id == 0


def test_client_wrapper_blocks_do_not_escalate_simple_latest_user_message():
    sig = StructuralSignal()
    row = {"messages": [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "<system-reminder>\nThe following skills are available for use with the Skill tool.\n" * 100
                    + "</system-reminder>",
                },
                {"type": "text", "text": "hello"},
            ],
        },
    ]}
    vote = sig.predict(row)
    assert vote.tier_id == 0
    assert vote.confidence > 0.0


def test_complex_prompt_predicts_high():
    sig = StructuralSignal()
    row = {"messages": [{"role": "user", "content": (
        "Design a distributed rate limiter that works across multiple "
        "microservice instances using Redis. Include the algorithm, "
        "data structures, and handle edge cases like clock skew. "
        "Implement with proper error handling, retry logic, and "
        "monitoring. Support both sliding window and token bucket."
    )}]}
    vote = sig.predict(row)
    assert vote.tier_id >= 2
    assert vote.confidence > 0.0


def test_medium_prompt():
    sig = StructuralSignal()
    row = {"messages": [{"role": "user", "content": "Write a Python function that reverses a string"}]}
    vote = sig.predict(row)
    assert 0 <= vote.tier_id <= 3
    assert 0.0 <= vote.confidence <= 1.0


def test_empty_messages_abstains():
    sig = StructuralSignal()
    row = {"messages": []}
    vote = sig.predict(row)
    assert vote.abstained


def test_uses_last_user_message():
    sig = StructuralSignal()
    row = {"messages": [
        {"role": "user", "content": "Design a distributed system with Kafka, Redis, PostgreSQL..."},
        {"role": "assistant", "content": "Sure, here is the design..."},
        {"role": "user", "content": "hello"},
    ]}
    vote = sig.predict(row)
    assert vote.tier_id <= 1
