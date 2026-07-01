import pytest

from codeup.integrations import groq_key_manager as gkm


class ApiError(RuntimeError):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def test_loads_groq_keys_in_order_and_ignores_missing():
    env = {
        "GROQ_API_KEY": "first",
        "GROQ_API_KEY_2": "",
        "GROQ_API_KEY_3": "third",
        "GROQ_API_KEY_15": "last",
    }
    records = gkm.load_groq_api_keys(env)
    assert [(r.index, r.env_name, r.key) for r in records] == [
        (1, "GROQ_API_KEY", "first"),
        (3, "GROQ_API_KEY_3", "third"),
        (15, "GROQ_API_KEY_15", "last"),
    ]


def test_uses_first_key_when_healthy():
    manager = gkm.GroqKeyManager(cooldown_seconds=10)
    seen = []

    result = manager.call_with_failover(
        lambda key, index: seen.append((key, index)) or "ok",
        env={"GROQ_API_KEY": "first", "GROQ_API_KEY_2": "second"},
    )

    assert result == "ok"
    assert seen == [("first", 1)]


@pytest.mark.parametrize("exc", [
    ApiError("rate limit 429 secret-first", 429),
    ApiError("quota exhausted for secret-first"),
    TimeoutError("request timed out for secret-first"),
    ApiError("server failed secret-first", 503),
    ConnectionError("network down secret-first"),
])
def test_falls_back_on_retryable_failures(exc):
    manager = gkm.GroqKeyManager(cooldown_seconds=10)
    seen = []

    def request(key, index):
        seen.append(key)
        if key == "first":
            raise exc
        return "ok"

    result = manager.call_with_failover(
        request,
        env={"GROQ_API_KEY": "first", "GROQ_API_KEY_2": "second"},
    )

    assert result == "ok"
    assert seen == ["first", "second"]
    status = manager.status(env={"GROQ_API_KEY": "first", "GROQ_API_KEY_2": "second"})
    assert status[0]["failure_count"] == 1
    assert "first" not in str(status)


def test_all_keys_fail_cleanly_without_secret_leakage():
    manager = gkm.GroqKeyManager(cooldown_seconds=10)

    def request(key, index):
        raise ApiError(f"429 quota for {key}", 429)

    with pytest.raises(gkm.GroqFailoverError) as err:
        manager.call_with_failover(
            request,
            env={"GROQ_API_KEY": "secret-one", "GROQ_API_KEY_2": "secret-two"},
        )

    message = err.value.user_message
    assert "busy" in message.lower() or "quota" in message.lower()
    assert "secret-one" not in message
    assert "secret-two" not in message
    assert [a["key_index"] for a in err.value.attempts] == [1, 2]


def test_cooldown_skips_unhealthy_key_until_expiry():
    now = [100.0]
    manager = gkm.GroqKeyManager(cooldown_seconds=10, time_fn=lambda: now[0])
    env = {"GROQ_API_KEY": "first", "GROQ_API_KEY_2": "second"}
    seen = []

    def first_call(key, index):
        seen.append(key)
        if key == "first":
            raise ApiError("429", 429)
        return "ok"

    assert manager.call_with_failover(first_call, env=env) == "ok"
    assert seen == ["first", "second"]

    seen.clear()
    assert manager.call_with_failover(lambda key, index: seen.append(key) or "ok", env=env) == "ok"
    assert seen == ["second"]

    now[0] = 111.0
    seen.clear()
    assert manager.call_with_failover(lambda key, index: seen.append(key) or "ok", env=env) == "ok"
    assert seen == ["first"]


def test_redacts_known_keys():
    manager = gkm.GroqKeyManager()
    text = manager.redact_known_keys(
        "failed with secret-one and secret-two",
        env={"GROQ_API_KEY": "secret-one", "GROQ_API_KEY_2": "secret-two"},
    )
    assert "secret-one" not in text
    assert "secret-two" not in text
    assert text.count("<redacted-api-key>") == 2
