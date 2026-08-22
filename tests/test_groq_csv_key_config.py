"""Regression coverage for the audit pass: a deployment that ONLY sets
GROQ_API_KEYS (comma-separated), with no GROQ_API_KEY / GROQ_API_KEY_2..15
at all, must work through every real app.py gating path - not just
groq_pool directly. Several of these functions used to read
os.environ["GROQ_API_KEY"] straight, which made a CSV-only config look
unconfigured and short-circuit before ever reaching the pool."""

import pytest

import app as app_module
from codeup.providers import groq_pool


@pytest.fixture(autouse=True)
def _csv_only_env(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    for i in range(2, 16):
        monkeypatch.delenv(f"GROQ_API_KEY_{i}", raising=False)
    monkeypatch.setenv("GROQ_API_KEYS", "csv-key-one,csv-key-two,csv-key-three")
    monkeypatch.setenv("GEMINI_ENABLED", "1")
    monkeypatch.delenv("CODEUP_AI_ENABLED", raising=False)
    monkeypatch.delenv("AI_ENABLED", raising=False)
    monkeypatch.delenv("GROQ_ENABLED", raising=False)
    # app.py's _groq_failover_env() deliberately returns {} (ignoring real
    # env vars) under test mode unless this is set, so tests can never
    # accidentally read a real developer-configured key. Opt back in here
    # since these tests are specifically about env-var wiring.
    monkeypatch.setenv("CODEUP_ALLOW_TEST_AI", "1")
    # No session-pasted key for this check - purely env-sourced.
    monkeypatch.setattr(app_module, "GEMINI_API_KEY", "Insert_API_Key_Here")


def test_csv_only_keys_load_through_the_pool():
    values = groq_pool.load_pool_key_values()
    assert values == ["csv-key-one", "csv-key-two", "csv-key-three"]


def test_configured_cloud_api_key_sees_csv_only_config():
    assert app_module._configured_cloud_api_key() == "csv-key-one"


def test_cloud_ai_not_disabled_when_only_csv_keys_configured(monkeypatch):
    # GEMINI_ENABLED=0 previously meant "disabled" whenever the raw
    # GROQ_API_KEY var was empty, even with real CSV keys configured.
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    key = app_module._configured_cloud_api_key()
    assert key == "csv-key-one"
    assert app_module._cloud_ai_disabled_for_request(key) is False


def test_cloud_ai_disabled_with_truly_no_keys_at_all(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEYS", raising=False)
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    assert app_module._configured_cloud_api_key() == ""
    assert app_module._cloud_ai_disabled_for_request("") is True


def test_structured_ai_available_sees_csv_only_config():
    assert app_module._structured_ai_available() is True


def test_structured_ai_unavailable_with_no_keys(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEYS", raising=False)
    assert app_module._structured_ai_available() is False


def test_call_conversation_orchestrator_ai_reaches_the_pool_with_csv_only_keys(monkeypatch):
    """Doesn't need a real network call to prove the point: if the old
    buggy early-exit fired, call_with_pool would never even be invoked."""
    calls = []
    real = groq_pool.call_with_pool

    def spy(*args, **kwargs):
        calls.append(kwargs.get("env"))
        raise groq_pool.GroqPoolExhausted("no keys", reason="not_configured")

    monkeypatch.setattr(groq_pool, "call_with_pool", spy)
    monkeypatch.setattr(app_module, "groq_pool", groq_pool)
    result = app_module.call_conversation_orchestrator_ai("system", "user")
    assert result == ""  # this function always fails silently to ""
    assert len(calls) == 1  # but it DID reach the pool, proving the gate passed
    monkeypatch.setattr(groq_pool, "call_with_pool", real)


def test_redact_known_keys_covers_csv_only_keys():
    text = "error contained csv-key-two right here"
    redacted = groq_pool.redact_known_keys(text)
    assert "csv-key-two" not in redacted
    assert "<redacted-api-key>" in redacted


def test_sanitize_traceback_redacts_csv_only_key():
    text = app_module.sanitize_traceback("Auth failed for csv-key-three during request")
    assert "csv-key-three" not in text


# ---- executor sizing (the pre-pool concurrency bottleneck) ---------------------------

def test_gemini_executor_size_scales_with_configured_keys(monkeypatch):
    monkeypatch.setenv("GROQ_MAX_CONCURRENCY_PER_KEY", "1")
    size_three_keys = app_module._gemini_executor_size()
    assert size_three_keys >= 3  # 3 CSV keys configured by the fixture

    monkeypatch.setenv("GROQ_API_KEYS", ",".join(f"key{i}" for i in range(15)))
    size_fifteen_keys = app_module._gemini_executor_size()
    assert size_fifteen_keys >= 15
    assert size_fifteen_keys > size_three_keys


def test_gemini_executor_size_has_a_floor_with_no_keys(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEYS", raising=False)
    assert app_module._gemini_executor_size() >= app_module._GEMINI_EXECUTOR_MIN_WORKERS


def test_gemini_executor_size_is_capped(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEYS", ",".join(f"key{i}" for i in range(200)))
    monkeypatch.setenv("GROQ_MAX_CONCURRENCY_PER_KEY", "5")
    assert app_module._gemini_executor_size() <= app_module._GEMINI_EXECUTOR_MAX_WORKERS
