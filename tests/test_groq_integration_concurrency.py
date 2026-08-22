"""End-to-end concurrency proof: real Flask routes, real call_gemini /
call_gemini_capability / executor / groq_pool call chain - only the
`groq.Groq` SDK client itself is faked (no real network calls). Proves,
through the ACTUAL app request path (not by calling groq_pool directly):

  - concurrent requests distribute across multiple configured keys
  - the bounded queue engages under real contention and drains cleanly
  - classroom AI policy is enforced BEFORE any pool capacity is touched,
    even under concurrent load
  - no active/queued slot is ever leaked
  - overload degrades gracefully (every request gets a normal HTTP 200
    with SOME text back - never a 500, never an unbounded hang)
"""

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import app as app_module
from codeup.providers import groq_pool


def _extract(pattern, data):
    match = re.search(pattern, data)
    assert match, f"pattern not found: {pattern}"
    return match.group(1).decode()


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


def _make_fake_groq_module(call_log, lock, sleep_seconds=0.15, content="Fake analysis from the model."):
    """A fake `groq` module whose Groq(...).chat.completions.create(...)
    records (api_key, thread_name, start, end) for every real call and
    sleeps briefly so concurrent requests genuinely overlap in time,
    instead of completing so fast that "concurrency" would be untestable."""
    import types

    class FakeCompletions:
        def create(self, **kwargs):
            start = time.time()
            time.sleep(sleep_seconds)
            end = time.time()
            with lock:
                call_log.append({
                    "api_key": self._api_key,
                    "thread": threading.current_thread().name,
                    "start": start,
                    "end": end,
                })
            return _FakeResponse(content)

    class FakeChat:
        def __init__(self, api_key):
            self.completions = FakeCompletions()
            self.completions._api_key = api_key

    class FakeGroq:
        def __init__(self, api_key, **kwargs):
            self.api_key = api_key
            self.chat = FakeChat(api_key)

    return types.SimpleNamespace(Groq=FakeGroq)


@pytest.fixture
def fake_groq_calls(monkeypatch):
    call_log = []
    lock = threading.Lock()
    fake_module = _make_fake_groq_module(call_log, lock)
    monkeypatch.setitem(__import__("sys").modules, "groq", fake_module)
    return call_log, lock


def _configure_keys(monkeypatch, n, *, max_concurrency=1, queue_max=50, queue_timeout=10):
    monkeypatch.setenv("GROQ_API_KEYS", ",".join(f"itest-key-{i}" for i in range(n)))
    for i in range(2, 16):
        monkeypatch.delenv(f"GROQ_API_KEY_{i}", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("CODEUP_ALLOW_TEST_AI", "1")
    monkeypatch.setenv("GEMINI_ENABLED", "1")
    monkeypatch.delenv("CODEUP_AI_ENABLED", raising=False)
    monkeypatch.delenv("AI_ENABLED", raising=False)
    monkeypatch.delenv("GROQ_ENABLED", raising=False)
    monkeypatch.setenv("OLLAMA_ENABLED", "0")
    monkeypatch.setenv("GROQ_MAX_CONCURRENCY_PER_KEY", str(max_concurrency))
    monkeypatch.setenv("GROQ_QUEUE_MAX_SIZE", str(queue_max))
    monkeypatch.setenv("GROQ_QUEUE_WAIT_TIMEOUT", str(queue_timeout))
    monkeypatch.setattr(app_module, "GEMINI_API_KEY", "Insert_API_Key_Here")


def test_concurrent_real_route_requests_distribute_across_keys(monkeypatch, fake_groq_calls):
    call_log, lock = fake_groq_calls
    _configure_keys(monkeypatch, 4, max_concurrency=1, queue_max=50, queue_timeout=10)
    groq_pool.get_pool().reset()

    client = app_module.app.test_client()

    def fire(i):
        r = client.post("/analyze", json={"code": f"x = {i}\nprint(x)", "language": "en"})
        return r.status_code, r.get_json()

    started = time.time()
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(fire, range(8)))
    elapsed = time.time() - started

    assert all(status == 200 for status, _ in results)
    assert all("Fake analysis" in (data.get("analysis") or "") for _, data in results)
    assert len(call_log) == 8

    keys_used = {entry["api_key"] for entry in call_log}
    assert len(keys_used) >= 2, f"expected requests spread across multiple keys, only used {keys_used}"

    # 8 requests at 0.15s each, only ever 1-at-a-time per key across 4 keys,
    # would take >= 2 * 0.15s if genuinely parallel; a fully serialized
    # implementation (the old hardcoded 3-worker executor bottleneck, or no
    # pool at all) would take close to 8 * 0.15s = 1.2s. Generous margin for
    # scheduling jitter in CI.
    assert elapsed < 1.0, f"requests did not appear to run concurrently (took {elapsed:.2f}s)"

    status = groq_pool.status()
    assert status["active_requests"] == 0, "leaked active slot(s) after all requests completed"
    assert status["queued_requests"] == 0


def test_policy_blocked_concurrent_requests_never_touch_the_pool(monkeypatch, fake_groq_calls):
    call_log, lock = fake_groq_calls
    _configure_keys(monkeypatch, 3, max_concurrency=1, queue_max=50, queue_timeout=10)
    groq_pool.get_pool().reset()

    instructor = app_module.app.test_client()
    learner = app_module.app.test_client()

    instructor.post(
        "/classroom/instructor/register",
        data={"username": "itest_policy", "password": "correct-horse-1", "display_name": "T"},
    )
    r = instructor.post("/classroom/cohorts", data={"name": "C"}, follow_redirects=True)
    join_code = _extract(rb'cu-join-code">([A-Z0-9]+)<', r.data)
    cohort_id = _extract(rb'cohorts/(\d+)"', r.data)
    r = instructor.post(
        f"/classroom/cohorts/{cohort_id}/assignments",
        data={"title": "A", "instructions": "i", "starter_code": "", "ai_policy": "OFF"},
        follow_redirects=True,
    )
    assignment_id = _extract(rb"assignments/(\d+)/publish", r.data)
    instructor.post(f"/classroom/assignments/{assignment_id}/publish")

    learner.post("/classroom/join", data={"join_code": join_code, "display_name": "Amir"}, follow_redirects=True)
    learner.get(f"/classroom/assignments/{assignment_id}/open")

    def fire(i):
        r1 = learner.post("/analyze", json={"code": f"x = {i}", "language": "en"})
        r2 = learner.post("/generate-code", json={"prompt": "write it", "language": "en"})
        return r1.status_code, r1.get_json(), r2.status_code, r2.get_json()

    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(fire, range(6)))

    for status1, data1, status2, data2 in results:
        assert status1 == 200
        assert status2 == 200
        assert data2["success"] is False  # generate is OFF

    assert call_log == []  # the fake Groq SDK was NEVER invoked
    status = groq_pool.status()
    assert status["active_requests"] == 0
    assert status["queued_requests"] == 0
    for key_row in status["key_rows"]:
        assert key_row["total_requests"] == 0  # not one attempt reached any key


def test_overload_degrades_gracefully_with_no_leaks(monkeypatch, fake_groq_calls):
    call_log, lock = fake_groq_calls
    fake_module = _make_fake_groq_module(call_log, lock, sleep_seconds=0.4)
    monkeypatch.setitem(__import__("sys").modules, "groq", fake_module)
    # Deliberately tiny capacity: 1 key, concurrency 1, queue room for only 1
    # more, short queue timeout - so with 6 simultaneous requests, most must
    # be rejected or time out, not silently hang or crash.
    _configure_keys(monkeypatch, 1, max_concurrency=1, queue_max=1, queue_timeout=1)
    groq_pool.get_pool().reset()

    client = app_module.app.test_client()

    def fire(i):
        r = client.post("/analyze", json={"code": f"x = {i}", "language": "en"})
        return r.status_code, r.get_json()

    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(fire, range(6)))

    # Every request gets a normal, accessible HTTP 200 - never a 500, never
    # an exception escaping the route - whether it was actually served or
    # gracefully told the AI is busy.
    assert all(status == 200 for status, _ in results)
    texts = [data.get("analysis") or "" for _, data in results]
    served = [t for t in texts if "Fake analysis" in t]
    busy = [t for t in texts if "safe" in t.lower() or "busy" in t.lower() or "try again" in t.lower()]
    assert served, "expected at least one request to actually get served"
    assert busy, "expected at least one request to be gracefully told the AI is busy, not silently dropped"
    assert len(served) + len(busy) == len(texts)

    status = groq_pool.status()
    assert status["active_requests"] == 0, "leaked active slot(s) under overload"
    assert status["queued_requests"] == 0, "leaked queue slot(s) under overload"
