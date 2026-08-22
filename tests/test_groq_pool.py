"""codeup.providers.groq_pool - unit tests. No real Groq calls: every
request_fn here is a fake that raises/returns synthetically. Mirrors the
required checklist for the Groq reliability pass."""

import threading
import time

import pytest

from codeup.providers import groq_pool as gp


def _fake_exc(status_code=None, headers=None, message="error"):
    class FakeResponse:
        def __init__(self, headers):
            self.headers = headers or {}
            self.status_code = status_code

    class FakeExc(Exception):
        pass

    exc = FakeExc(message)
    exc.status_code = status_code
    if headers is not None:
        exc.response = FakeResponse(headers)
    return exc


@pytest.fixture
def pool():
    return gp.GroqKeyPool()


# ---- 1-4: key loading ----------------------------------------------------------

def test_legacy_single_key_loads():
    values = gp.load_pool_key_values({"GROQ_API_KEY": "k1"})
    assert values == ["k1"]


def test_groq_api_keys_csv_loads_many():
    values = gp.load_pool_key_values({"GROQ_API_KEYS": "k1,k2,k3"})
    assert values == ["k1", "k2", "k3"]


def test_blank_entries_removed():
    values = gp.load_pool_key_values({"GROQ_API_KEYS": "k1,, ,k2,"})
    assert values == ["k1", "k2"]


def test_duplicate_keys_deduplicated_across_both_forms():
    values = gp.load_pool_key_values({"GROQ_API_KEYS": "k1,k2", "GROQ_API_KEY": "k1", "GROQ_API_KEY_2": "k3"})
    assert values == ["k1", "k2", "k3"]


def test_numbered_scheme_still_works_alongside_csv():
    env = {"GROQ_API_KEYS": "kA,kB", "GROQ_API_KEY": "kC", "GROQ_API_KEY_2": "kD"}
    assert gp.load_pool_key_values(env) == ["kA", "kB", "kC", "kD"]


# ---- 5: raw keys never exposed in diagnostics -----------------------------------

def test_status_never_contains_raw_key_values(pool):
    env = {"GROQ_API_KEY": "super-secret-value-1", "GROQ_API_KEY_2": "super-secret-value-2"}
    st = pool.status(env=env)
    import json
    blob = json.dumps(st)
    assert "super-secret-value-1" not in blob
    assert "super-secret-value-2" not in blob
    assert st["key_rows"][0]["id"] == "groq-key-01"


def test_key_state_repr_never_contains_secret():
    state = gp.KeyState(internal_id="groq-key-01", key="super-secret")
    assert "super-secret" not in repr(state)


# ---- 6-12: health-check classification -----------------------------------------

def test_health_check_success_marks_healthy(pool, monkeypatch):
    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = self

        @property
        def completions(self):
            return self

        def create(self, **kwargs):
            return object()

    monkeypatch.setattr("groq.Groq", FakeClient)
    result = pool.health_check_one("k1")
    assert result == "healthy"
    assert pool.status(env={"GROQ_API_KEY": "k1"})["key_rows"][0]["state"] == "HEALTHY"


def test_health_check_uses_short_timeout_and_no_retries(pool, monkeypatch):
    """The health checker must make EXACTLY one bounded-time request per
    key - the SDK's own default timeout/retry behavior would silently
    violate that."""
    seen_kwargs = {}

    class FakeClient:
        def __init__(self, **kwargs):
            seen_kwargs.update(kwargs)
            self.chat = self

        @property
        def completions(self):
            return self

        def create(self, **kwargs):
            return object()

    monkeypatch.setattr("groq.Groq", FakeClient)
    pool.health_check_one("k1")
    assert seen_kwargs.get("max_retries") == 0
    assert seen_kwargs.get("timeout") == gp.HEALTH_CHECK_TIMEOUT_SECONDS
    assert seen_kwargs["timeout"] <= 15  # explicitly short, not the SDK default


def test_fresh_key_is_unverified_not_healthy(pool):
    """A key that has never been used or checked must not be reported as
    HEALTHY just because it hasn't failed yet - that's an unverified
    assumption, not a fact."""
    with pool._cv:
        pool._sync_and_get_locked(["k1"])
    st = pool.status(env={"GROQ_API_KEY": "k1"})
    assert st["key_rows"][0]["state"] == "UNVERIFIED"
    assert st["counts"]["unverified"] == 1
    assert st["counts"]["healthy"] == 0


def test_key_becomes_healthy_only_after_a_real_success(pool):
    env = {"GROQ_API_KEY": "k1"}
    assert pool.status(env=env)["key_rows"][0]["state"] == "UNVERIFIED"
    with pool.acquire(timeout=1, env=env):
        pass  # normal completion = a real success
    assert pool.status(env=env)["key_rows"][0]["state"] == "HEALTHY"


def test_key_becomes_healthy_after_passing_health_check(pool, monkeypatch):
    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = self

        @property
        def completions(self):
            return self

        def create(self, **kwargs):
            return object()

    monkeypatch.setattr("groq.Groq", FakeClient)
    env = {"GROQ_API_KEY": "k1"}
    assert pool.status(env=env)["key_rows"][0]["state"] == "UNVERIFIED"
    pool.health_check_one("k1")
    assert pool.status(env=env)["key_rows"][0]["state"] == "HEALTHY"


def test_health_check_401_disables_key(pool, monkeypatch):
    class FakeClient:
        def __init__(self, **kwargs):
            pass

        @property
        def chat(self):
            return self

        @property
        def completions(self):
            return self

        def create(self, **kwargs):
            raise _fake_exc(status_code=401, message="invalid api key")

    monkeypatch.setattr("groq.Groq", FakeClient)
    result = pool.health_check_one("k1")
    assert result == "invalid"
    st = pool.status(env={"GROQ_API_KEY": "k1"})
    assert st["key_rows"][0]["state"] == "DISABLED"


def test_health_check_403_disables_key(pool, monkeypatch):
    class FakeClient:
        def __init__(self, **kwargs):
            pass

        @property
        def chat(self):
            return self

        @property
        def completions(self):
            return self

        def create(self, **kwargs):
            raise _fake_exc(status_code=403, message="permission denied")

    monkeypatch.setattr("groq.Groq", FakeClient)
    result = pool.health_check_one("k1")
    assert result == "invalid"


def test_health_check_429_sets_cooldown(pool, monkeypatch):
    class FakeClient:
        def __init__(self, **kwargs):
            pass

        @property
        def chat(self):
            return self

        @property
        def completions(self):
            return self

        def create(self, **kwargs):
            raise _fake_exc(status_code=429, headers={"retry-after": "5"}, message="rate limited")

    monkeypatch.setattr("groq.Groq", FakeClient)
    result = pool.health_check_one("k1")
    assert result == "rate_limited"
    st = pool.status(env={"GROQ_API_KEY": "k1"})
    assert st["key_rows"][0]["state"] == "COOLDOWN"
    assert st["key_rows"][0]["cooldown_remaining_seconds"] > 0


def test_health_check_retry_after_respected(pool, monkeypatch):
    class FakeClient:
        def __init__(self, **kwargs):
            pass

        @property
        def chat(self):
            return self

        @property
        def completions(self):
            return self

        def create(self, **kwargs):
            raise _fake_exc(status_code=429, headers={"retry-after": "123"})

    monkeypatch.setattr("groq.Groq", FakeClient)
    pool.health_check_one("k1")
    st = pool.status(env={"GROQ_API_KEY": "k1"})
    # cooldown should reflect the Retry-After value (bounded by MAX), not the smaller default
    assert st["key_rows"][0]["cooldown_remaining_seconds"] > 60


def test_health_check_timeout_is_temporary_cooldown(pool, monkeypatch):
    class FakeClient:
        def __init__(self, **kwargs):
            pass

        @property
        def chat(self):
            return self

        @property
        def completions(self):
            return self

        def create(self, **kwargs):
            raise TimeoutError("timed out")

    monkeypatch.setattr("groq.Groq", FakeClient)
    result = pool.health_check_one("k1")
    assert result == "unhealthy"
    st = pool.status(env={"GROQ_API_KEY": "k1"})
    assert st["key_rows"][0]["state"] == "UNHEALTHY"
    assert not st["key_rows"][0]["state"] == "DISABLED"


def test_health_check_5xx_is_temporary_cooldown(pool, monkeypatch):
    class FakeClient:
        def __init__(self, **kwargs):
            pass

        @property
        def chat(self):
            return self

        @property
        def completions(self):
            return self

        def create(self, **kwargs):
            raise _fake_exc(status_code=503, message="service unavailable")

    monkeypatch.setattr("groq.Groq", FakeClient)
    result = pool.health_check_one("k1")
    assert result == "unhealthy"


# ---- 13-17: selection ---------------------------------------------------------

def test_healthy_key_selected(pool):
    with pool.acquire(timeout=1, env={"GROQ_API_KEY": "k1"}) as lease:
        assert lease.internal_id == "groq-key-01"


def test_disabled_key_skipped(pool):
    env = {"GROQ_API_KEY": "k1", "GROQ_API_KEY_2": "k2"}
    with pool._cv:
        states = pool._sync_and_get_locked(["k1", "k2"])
        states[0].disabled = True
    with pool.acquire(timeout=1, env=env) as lease:
        assert lease.internal_id == "groq-key-02"


def test_cooldown_key_skipped(pool):
    env = {"GROQ_API_KEY": "k1", "GROQ_API_KEY_2": "k2"}
    with pool._cv:
        states = pool._sync_and_get_locked(["k1", "k2"])
        states[0].cooldown_until = time.time() + 300
    with pool.acquire(timeout=1, env=env) as lease:
        assert lease.internal_id == "groq-key-02"


def test_least_loaded_key_selected(pool, monkeypatch):
    monkeypatch.setenv("GROQ_MAX_CONCURRENCY_PER_KEY", "5")
    env = {"GROQ_API_KEY": "k1", "GROQ_API_KEY_2": "k2"}
    with pool._cv:
        states = pool._sync_and_get_locked(["k1", "k2"])
        states[0].active = 3
        states[1].active = 1
    with pool.acquire(timeout=1, env=env) as lease:
        assert lease.internal_id == "groq-key-02"  # fewer active wins


def test_round_robin_fairness_on_ties(pool):
    env = {"GROQ_API_KEY": "k1", "GROQ_API_KEY_2": "k2", "GROQ_API_KEY_3": "k3"}
    seen = []
    for _ in range(6):
        with pool.acquire(timeout=1, env=env) as lease:
            seen.append(lease.internal_id)
    assert seen == ["groq-key-01", "groq-key-02", "groq-key-03"] * 2


# ---- 18-19: concurrency accounting -----------------------------------------------

def test_active_count_increments_while_held(pool):
    env = {"GROQ_API_KEY": "k1"}
    with pool.acquire(timeout=1, env=env):
        assert pool.status(env=env)["key_rows"][0]["active"] == 1
    assert pool.status(env=env)["key_rows"][0]["active"] == 0


def test_normal_completion_releases_capacity(pool):
    env = {"GROQ_API_KEY": "k1"}
    with pool.acquire(timeout=1, env=env):
        pass
    st = pool.status(env=env)
    assert st["key_rows"][0]["active"] == 0
    assert st["key_rows"][0]["successful_requests"] == 1


def test_exception_releases_capacity(pool):
    env = {"GROQ_API_KEY": "k1"}
    with pytest.raises(ValueError):
        with pool.acquire(timeout=1, env=env):
            raise ValueError("boom")
    st = pool.status(env=env)
    assert st["key_rows"][0]["active"] == 0
    assert st["key_rows"][0]["transient_failures"] == 1


def test_concurrent_requests_distribute_across_keys(pool, monkeypatch):
    monkeypatch.setenv("GROQ_MAX_CONCURRENCY_PER_KEY", "1")
    env = {"GROQ_API_KEY": "k1", "GROQ_API_KEY_2": "k2", "GROQ_API_KEY_3": "k3"}
    seen = []
    lock = threading.Lock()
    barrier = threading.Barrier(3)

    def hold():
        with pool.acquire(timeout=2, env=env) as lease:
            with lock:
                seen.append(lease.internal_id)
            barrier.wait(timeout=2)

    threads = [threading.Thread(target=hold) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=3)
    assert sorted(seen) == ["groq-key-01", "groq-key-02", "groq-key-03"]


# ---- 22-26: bounded FIFO queue -----------------------------------------------

def test_all_keys_busy_request_queues_and_is_served(pool, monkeypatch):
    monkeypatch.setenv("GROQ_MAX_CONCURRENCY_PER_KEY", "1")
    env = {"GROQ_API_KEY": "k1"}
    release_evt = threading.Event()
    got_second = threading.Event()

    def holder():
        with pool.acquire(timeout=3, env=env):
            release_evt.wait(2)

    def waiter():
        with pool.acquire(timeout=3, env=env):
            got_second.set()

    t1 = threading.Thread(target=holder)
    t1.start()
    time.sleep(0.1)
    t2 = threading.Thread(target=waiter)
    t2.start()
    time.sleep(0.1)
    assert pool.status(env=env)["queued_requests"] == 1
    release_evt.set()
    t1.join(timeout=3)
    t2.join(timeout=3)
    assert got_second.is_set()


def test_fifo_order_preserved(pool, monkeypatch):
    monkeypatch.setenv("GROQ_MAX_CONCURRENCY_PER_KEY", "1")
    env = {"GROQ_API_KEY": "k1"}
    order = []
    lock = threading.Lock()

    def worker(idx, hold_seconds):
        with pool.acquire(timeout=3, env=env):
            with lock:
                order.append(idx)
            time.sleep(hold_seconds)

    t1 = threading.Thread(target=worker, args=(1, 0.3))
    t1.start()
    time.sleep(0.05)
    t2 = threading.Thread(target=worker, args=(2, 0.05))
    t2.start()
    time.sleep(0.05)
    t3 = threading.Thread(target=worker, args=(3, 0.05))
    t3.start()
    for t in (t1, t2, t3):
        t.join(timeout=3)
    assert order == [1, 2, 3]


def test_released_capacity_serves_queued_request(pool, monkeypatch):
    monkeypatch.setenv("GROQ_MAX_CONCURRENCY_PER_KEY", "1")
    env = {"GROQ_API_KEY": "k1"}
    results = {}

    def holder():
        with pool.acquire(timeout=2, env=env):
            time.sleep(0.2)

    def waiter():
        with pool.acquire(timeout=2, env=env) as lease:
            results["got"] = lease.internal_id

    t1 = threading.Thread(target=holder)
    t1.start()
    time.sleep(0.05)
    t2 = threading.Thread(target=waiter)
    t2.start()
    t1.join(timeout=3)
    t2.join(timeout=3)
    assert results.get("got") == "groq-key-01"


def test_queue_timeout(pool, monkeypatch):
    monkeypatch.setenv("GROQ_MAX_CONCURRENCY_PER_KEY", "1")
    env = {"GROQ_API_KEY": "k1"}
    release_evt = threading.Event()

    def holder():
        with pool.acquire(timeout=3, env=env):
            release_evt.wait(2)

    t1 = threading.Thread(target=holder)
    t1.start()
    time.sleep(0.1)
    with pytest.raises(gp.GroqPoolTimeout):
        with pool.acquire(timeout=0.3, env=env):
            pass
    release_evt.set()
    t1.join(timeout=3)
    # timing out must not leak a queue slot or an active slot
    assert pool.status(env=env)["queued_requests"] == 0


def test_queue_overflow_raises_queue_full(pool, monkeypatch):
    monkeypatch.setenv("GROQ_MAX_CONCURRENCY_PER_KEY", "1")
    monkeypatch.setenv("GROQ_QUEUE_MAX_SIZE", "1")
    env = {"GROQ_API_KEY": "k1"}
    release_evt = threading.Event()

    def holder():
        with pool.acquire(timeout=3, env=env):
            release_evt.wait(2)

    def parked_waiter():
        with pool.acquire(timeout=3, env=env):
            pass

    t1 = threading.Thread(target=holder)
    t1.start()
    time.sleep(0.1)
    t2 = threading.Thread(target=parked_waiter)  # fills the 1-slot queue
    t2.start()
    time.sleep(0.1)

    with pytest.raises(gp.GroqPoolQueueFull):
        with pool.acquire(timeout=1, env=env):
            pass

    release_evt.set()
    t1.join(timeout=3)
    t2.join(timeout=3)


# ---- 27-29: safe retry / failover ------------------------------------------------

def test_safe_retry_uses_another_key_once(pool):
    env = {"GROQ_API_KEY": "k1", "GROQ_API_KEY_2": "k2"}
    calls = []

    def flaky(api_key, internal_id):
        calls.append(internal_id)
        if len(calls) == 1:
            raise _fake_exc(status_code=429)
        return "ok"

    result = pool.call_with_pool(flaky, env=env, timeout=2, max_attempts=2)
    assert result == "ok"
    assert len(calls) == 2
    assert calls[0] != calls[1]


def test_authentication_failure_does_not_retry_endlessly(pool):
    env = {"GROQ_API_KEY": "k1", "GROQ_API_KEY_2": "k2"}
    calls = []

    def bad(api_key, internal_id):
        calls.append(internal_id)
        raise _fake_exc(status_code=401)

    with pytest.raises(gp.GroqAllKeysFailedError):
        pool.call_with_pool(bad, env=env, timeout=2, max_attempts=2)
    assert len(calls) == 1  # non-retryable -> stop immediately, no second attempt
    st = pool.status(env=env)
    assert st["key_rows"][0]["state"] == "DISABLED"


def test_provider_level_cooldown_after_correlated_429s(pool):
    env = {f"GROQ_API_KEY_{i}" if i > 1 else "GROQ_API_KEY": f"k{i}" for i in range(1, 6)}

    def rl(api_key, internal_id):
        raise _fake_exc(status_code=429)

    for _ in range(3):
        try:
            pool.call_with_pool(rl, env=env, timeout=1, max_attempts=1)
        except gp.GroqPoolError:
            pass

    st = pool.status(env=env)
    assert st["shared_limit_suspected"] is True
    assert st["pool_cooldown_remaining_seconds"] > 0


# ---- 30-31: streaming lifecycle -----------------------------------------------

def test_streaming_keeps_slot_until_generator_completes(pool):
    env = {"GROQ_API_KEY": "k1"}

    def gen():
        with pool.acquire(timeout=1, env=env):
            yield 1
            yield 2

    g = gen()
    next(g)
    assert pool.status(env=env)["key_rows"][0]["active"] == 1
    list(g)  # exhaust
    assert pool.status(env=env)["key_rows"][0]["active"] == 0
    assert pool.status(env=env)["key_rows"][0]["successful_requests"] == 1


def test_streaming_generator_close_releases_without_penalty(pool):
    env = {"GROQ_API_KEY": "k1"}

    def gen():
        with pool.acquire(timeout=1, env=env):
            yield 1
            yield 2

    g = gen()
    next(g)
    g.close()
    st = pool.status(env=env)
    assert st["key_rows"][0]["active"] == 0
    # A generator close is neither a proven success nor a failure - not
    # penalized (no cooldown/failure), but also not promoted to HEALTHY,
    # since nothing ever actually completed on this key.
    assert st["key_rows"][0]["state"] == "UNVERIFIED"
    assert st["key_rows"][0]["transient_failures"] == 0


def test_streaming_exception_releases_slot_and_cools_down(pool):
    env = {"GROQ_API_KEY": "k1"}

    def gen():
        with pool.acquire(timeout=1, env=env):
            yield 1
            raise ConnectionError("dropped")

    g = gen()
    next(g)
    with pytest.raises(ConnectionError):
        next(g)
    st = pool.status(env=env)
    assert st["key_rows"][0]["active"] == 0
    assert st["key_rows"][0]["state"] in ("UNHEALTHY", "COOLDOWN")


# ---- 33: zero keys never breaks the caller --------------------------------------

def test_no_keys_configured_raises_friendly_error_not_hang(pool):
    with pytest.raises(gp.GroqPoolExhausted) as excinfo:
        with pool.acquire(timeout=1, env={}):
            pass
    assert "still work" in excinfo.value.user_message.lower() or "not configured" in excinfo.value.user_message.lower()


def test_has_configured_keys_false_when_empty(pool):
    assert gp.has_configured_keys(env={}) is False


# ---- misc safety ---------------------------------------------------------------

def test_active_count_never_goes_negative(pool):
    with pool._cv:
        states = pool._sync_and_get_locked(["k1"])
        key = states[0]
    pool._release(gp._Lease(key), success=True, exc=None)
    pool._release(gp._Lease(key), success=True, exc=None)
    assert key.active == 0


def test_extra_keys_take_priority_over_env_pool(pool):
    values = pool._resolve_values(env={"GROQ_API_KEY": "envkey"}, extra_keys=["sessionkey"])
    assert values[0] == "sessionkey"
    assert "envkey" in values
