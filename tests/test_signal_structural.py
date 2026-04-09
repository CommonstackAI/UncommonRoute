from uncommon_route.signals.structural import StructuralSignal


def test_simple_prompt_predicts_low():
    sig = StructuralSignal()
    row = {"messages": [{"role": "user", "content": "hello"}]}
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
