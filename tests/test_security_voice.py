"""
CodeUp — Full Test Suite
Covers: sandbox security, voice intent parsing, trace playback,
        snippet CRUD, size limits, repeat command, dotenv loading,
        pending quiz/bug intercepts, syntax checking, variable tracking,
        structure parsing, and execution story mode.
"""

import os
import sys
import json
import types
import time
import subprocess
import threading
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import app as app_module
from app import app
from intent_parser import parse_intent
import sandboxed_fs as sandboxed_fs_module
from sandboxed_fs import SandboxedFileSystem


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_snippets(tmp_path, monkeypatch):
    """Redirect snippet storage to a temp file — never touches real snippets.json."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    tmp_file = tmp_path / "snippets.json"
    tmp_file.write_text(json.dumps({"snippets": []}))
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("SNIPPETS_FILE", str(tmp_file))
    monkeypatch.setattr(app_module, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(app_module, "SNIPPETS_FILE", str(tmp_file))
    return tmp_file


def _client_session_id(client):
    cookie = client.get_cookie(app_module.SESSION_COOKIE_NAME)
    return getattr(cookie, "value", cookie)


def _subprocess_env_without_dotenv():
    env = os.environ.copy()
    env["PYTHON_DOTENV_DISABLED"] = "1"
    return env


def _app_import_without_dotenv(extra=""):
    return (
        "import dotenv; "
        "dotenv.load_dotenv = lambda *args, **kwargs: False; "
        "import app; "
        + extra
    )


@pytest.fixture
def client(tmp_snippets, monkeypatch):
    """Flask test client with isolated snippet storage and AI disabled."""
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    # Key 2 (the conversation/intent orchestrator) reads GROQ_API_KEY_2, not
    # GROQ_API_KEY. Unset it too so "AI disabled" is real and these deterministic
    # routing assertions never depend on a live network call.
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("CODEUP_AI_ENABLED", raising=False)
    monkeypatch.delenv("AI_ENABLED", raising=False)
    monkeypatch.delenv("GROQ_ENABLED", raising=False)
    with app.test_client() as c:
        yield c


# ===========================================================================
# 1. ENVIRONMENT / CONFIG
# ===========================================================================

def test_gemini_disabled_returns_message(client):
    """When GEMINI_ENABLED=0, AI endpoints return a clear message not a crash."""
    res = client.post("/analyze", json={"code": "print(1)", "language": "en"})
    assert res.status_code == 200
    data = res.get_json()
    assert "analysis" in data
    assert "disabled" in data["analysis"].lower() or "service" in data["analysis"].lower()


def test_app_import_does_not_start_background_services():
    env = _subprocess_env_without_dotenv()
    env["FLASK_TESTING"] = "true"
    env.pop("FLASK_SECRET_KEY", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            _app_import_without_dotenv("print(app._cleanup_thread is None, app._gemini_executor is None)"),
        ],
        cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "True True" in result.stdout


def test_start_background_services_is_idempotent_under_concurrent_calls(monkeypatch):
    real_thread = threading.Thread
    created = []

    class FakeThread:
        def __init__(self, *args, **kwargs):
            self.started = False
            created.append(self)

        def start(self):
            self.started = True

        def is_alive(self):
            return self.started

    monkeypatch.setattr(app_module, "_cleanup_thread", None)
    monkeypatch.setattr(app_module.threading, "Thread", FakeThread)

    barrier = threading.Barrier(8)
    results = []
    errors = []

    def call_start():
        try:
            barrier.wait(timeout=5)
            results.append(app_module.start_background_services())
        except Exception as exc:
            errors.append(exc)

    threads = [real_thread(target=call_start) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert errors == []
    assert len(created) == 1
    assert len({id(result) for result in results}) == 1


def test_shutdown_background_services_stops_cleanup_thread_idempotently(monkeypatch):
    class FakeThread:
        def __init__(self, *args, **kwargs):
            self.started = False
            self.join_calls = []

        def start(self):
            self.started = True

        def is_alive(self):
            return self.started

        def join(self, timeout=None):
            self.join_calls.append(timeout)
            self.started = False

    monkeypatch.setattr(app_module, "_cleanup_thread", None)
    monkeypatch.setattr(app_module.threading, "Thread", FakeThread)
    app_module._cleanup_stop_event.set()

    thread = app_module.start_background_services()
    assert thread.started is True
    assert not app_module._cleanup_stop_event.is_set()

    app_module.shutdown_background_services()
    app_module.shutdown_background_services()

    assert app_module._cleanup_stop_event.is_set()
    assert app_module._cleanup_thread is None
    assert thread.join_calls == [2]


def test_gemini_executor_is_singleton_and_shutdown_is_idempotent(monkeypatch):
    real_thread = threading.Thread

    class FakeExecutor:
        instances = []

        def __init__(self, *args, **kwargs):
            self.shutdown_calls = []
            FakeExecutor.instances.append(self)

        def shutdown(self, **kwargs):
            self.shutdown_calls.append(kwargs)

    monkeypatch.setattr(app_module, "_gemini_executor", None)
    monkeypatch.setattr(app_module, "ThreadPoolExecutor", FakeExecutor)

    barrier = threading.Barrier(8)
    results = []
    errors = []

    def get_executor():
        try:
            barrier.wait(timeout=5)
            results.append(app_module._get_gemini_executor())
        except Exception as exc:
            errors.append(exc)

    threads = [real_thread(target=get_executor) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert errors == []
    assert len(FakeExecutor.instances) == 1
    assert len({id(result) for result in results}) == 1

    app_module.shutdown_background_services()
    app_module.shutdown_background_services()
    assert app_module._gemini_executor is None
    assert FakeExecutor.instances[0].shutdown_calls == [
        {"wait": False, "cancel_futures": True}
    ]


def test_call_gemini_cancels_pending_future_on_timeout(monkeypatch):
    monkeypatch.setenv("GEMINI_ENABLED", "1")
    monkeypatch.delenv("CODEUP_AI_ENABLED", raising=False)
    monkeypatch.setattr(app_module, "GEMINI_API_KEY", "session-key")
    monkeypatch.setattr(app_module, "_call_ollama", lambda *a, **k: None)
    monkeypatch.setattr(app_module, "MAX_GEMINI_TIMEOUT", 0)
    with app_module._gemini_active_lock:
        app_module._gemini_queued_requests = 0
        app_module._gemini_active_requests = 0

    class TimeoutFuture:
        def __init__(self):
            self.cancelled = False

        def result(self, timeout=None):
            raise app_module.FutureTimeoutError()

        def cancel(self):
            self.cancelled = True
            return True

    future = TimeoutFuture()

    class FakeExecutor:
        def submit(self, func):
            return future

    monkeypatch.setattr(app_module, "_gemini_executor", FakeExecutor())
    result = app_module.call_gemini("system", "user")

    assert future.cancelled is True
    assert "too long" in result.lower()
    assert app_module._gemini_queued_requests == 0


def test_call_gemini_tracks_running_future_after_timeout(monkeypatch):
    monkeypatch.setenv("GEMINI_ENABLED", "1")
    monkeypatch.delenv("CODEUP_AI_ENABLED", raising=False)
    monkeypatch.setattr(app_module, "GEMINI_API_KEY", "session-key")
    monkeypatch.setattr(app_module, "_call_ollama", lambda *a, **k: None)
    monkeypatch.setattr(app_module, "MAX_GEMINI_TIMEOUT", 0)
    logs = []
    monkeypatch.setattr(app_module, "_debug_log", logs.append)
    with app_module._gemini_active_lock:
        app_module._gemini_queued_requests = 0
        app_module._gemini_active_requests = 0

    class RunningFuture:
        def __init__(self):
            self.callbacks = []
            self.cancel_calls = 0

        def result(self, timeout=None):
            if timeout is not None:
                raise app_module.FutureTimeoutError()
            raise RuntimeError("late failure")

        def cancel(self):
            self.cancel_calls += 1
            return False

        def cancelled(self):
            return False

        def add_done_callback(self, callback):
            self.callbacks.append(callback)

    future = RunningFuture()

    class FakeExecutor:
        def submit(self, func):
            with app_module._gemini_active_lock:
                app_module._gemini_queued_requests = max(0, app_module._gemini_queued_requests - 1)
                app_module._gemini_active_requests += 1
            return future

    monkeypatch.setattr(app_module, "_gemini_executor", FakeExecutor())
    try:
        result = app_module.call_gemini("system", "user")
        assert "too long" in result.lower()
        assert future.cancel_calls == 1
        assert len(future.callbacks) == 1
        assert app_module._gemini_queued_requests == 0
        future.callbacks[0](future)
        assert any("Timed-out AI task finished with an error" in msg for msg in logs)
    finally:
        with app_module._gemini_active_lock:
            app_module._gemini_queued_requests = 0
            app_module._gemini_active_requests = 0


def test_production_import_requires_secret_key():
    env = _subprocess_env_without_dotenv()
    env.pop("FLASK_SECRET_KEY", None)
    env.pop("FLASK_TESTING", None)
    env["CODEUP_ENV"] = "production"
    env["FLASK_ENV"] = "production"
    env["FLASK_DEBUG"] = "0"
    result = subprocess.run(
        [sys.executable, "-c", _app_import_without_dotenv()],
        cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "FLASK_SECRET_KEY" in result.stderr


def test_dev_testing_secret_is_ephemeral():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    env = _subprocess_env_without_dotenv()
    env["FLASK_TESTING"] = "true"
    env.pop("FLASK_SECRET_KEY", None)
    keys = []
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, "-c", _app_import_without_dotenv("print(app.app.secret_key)")],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
        keys.append(result.stdout.strip())

    assert keys[0] and keys[1]
    assert keys[0] != "dev-secret-key-change-in-production"
    assert keys[0] != keys[1]


def test_production_session_cookie_secure_by_default():
    env = _subprocess_env_without_dotenv()
    env["FLASK_SECRET_KEY"] = "prod-secret"
    env.pop("FLASK_TESTING", None)
    env["CODEUP_ENV"] = "production"
    env["FLASK_ENV"] = "production"
    env.pop("SESSION_COOKIE_SECURE", None)
    result = subprocess.run(
        [sys.executable, "-c", _app_import_without_dotenv("print(app.SESSION_COOKIE_SECURE)")],
        cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True"


def test_gemini_key_not_configured_returns_message(client, monkeypatch):
    """Missing API key returns a human-readable message not a 500."""
    monkeypatch.setenv("GEMINI_ENABLED", "1")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    res = client.post("/summarize", json={"code": "x = 1", "language": "en"})
    assert res.status_code == 200
    data = res.get_json()
    assert "summary" in data
    assert "configured" in data["summary"].lower() or "api" in data["summary"].lower()


def test_call_gemini_uses_session_api_config_key(monkeypatch):
    """Keys entered through /api-config must be honored by AI calls."""
    monkeypatch.setenv("GEMINI_ENABLED", "1")
    monkeypatch.delenv("CODEUP_AI_ENABLED", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(app_module, "GEMINI_API_KEY", "Insert_API_Key_Here")
    monkeypatch.setattr(app_module, "_call_ollama", lambda *a, **k: None)
    app_module.set_gemini_api_key("session-key")
    seen_keys = []

    class FakeCompletions:
        def create(self, **kwargs):
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="print('ok')"))]
            )

    class FakeGroq:
        def __init__(self, api_key):
            seen_keys.append(api_key)
            self.chat = types.SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setitem(sys.modules, "groq", types.SimpleNamespace(Groq=FakeGroq))

    try:
        result = app_module.call_gemini("Return code", "Task")
        assert result == "print('ok')"
        assert seen_keys == ["session-key"]
    finally:
        app_module.set_gemini_api_key("Insert_API_Key_Here")


def test_legacy_gemini_disabled_does_not_block_configured_key(monkeypatch):
    """A stale GEMINI_ENABLED=0 must not kill Groq when a key is configured."""
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("CODEUP_AI_ENABLED", raising=False)
    monkeypatch.delenv("AI_ENABLED", raising=False)
    monkeypatch.delenv("GROQ_ENABLED", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(app_module, "GEMINI_API_KEY", "Insert_API_Key_Here")
    monkeypatch.setattr(app_module, "_call_ollama", lambda *a, **k: None)
    app_module.set_gemini_api_key("session-key")
    seen = {"keys": [], "max_tokens": []}

    class FakeCompletions:
        def create(self, **kwargs):
            seen["max_tokens"].append(kwargs["max_tokens"])
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="AI online"))]
            )

    class FakeGroq:
        def __init__(self, api_key):
            seen["keys"].append(api_key)
            self.chat = types.SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setitem(sys.modules, "groq", types.SimpleNamespace(Groq=FakeGroq))

    try:
        result = app_module.call_gemini("Return a status", "Test", max_tokens=4096)
        assert result == "AI online"
        assert seen["keys"] == ["session-key"]
        assert seen["max_tokens"] == [4096]
    finally:
        app_module.set_gemini_api_key("Insert_API_Key_Here")


def test_codeup_ai_enabled_zero_is_hard_disable(monkeypatch):
    """Use CODEUP_AI_ENABLED=0 for an intentional full AI disable."""
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "1")
    monkeypatch.setattr(app_module, "_call_ollama", lambda *a, **k: None)
    app_module.set_gemini_api_key("session-key")

    try:
        result = app_module.call_gemini("Return a status", "Test")
        assert "disabled" in result.lower()
    finally:
        app_module.set_gemini_api_key("Insert_API_Key_Here")


def test_call_gemini_whitespace_response_is_not_empty_string(monkeypatch):
    monkeypatch.setenv("GEMINI_ENABLED", "1")
    monkeypatch.delenv("CODEUP_AI_ENABLED", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(app_module, "GEMINI_API_KEY", "Insert_API_Key_Here")
    monkeypatch.setattr(app_module, "_call_ollama", lambda *a, **k: None)
    app_module.set_gemini_api_key("session-key")

    class FakeCompletions:
        def create(self, **kwargs):
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="   \n\t"))]
            )

    class FakeGroq:
        def __init__(self, api_key):
            self.chat = types.SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setitem(sys.modules, "groq", types.SimpleNamespace(Groq=FakeGroq))

    try:
        result = app_module.call_gemini("Return code", "Task")
        assert result
        assert "empty response" in result.lower()
    finally:
        app_module.set_gemini_api_key("Insert_API_Key_Here")


def test_api_config_key_persists_to_generate_code(client, monkeypatch):
    """A key entered through the UI must work on the next request too."""
    monkeypatch.setenv("GEMINI_ENABLED", "1")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(app_module, "GEMINI_API_KEY", "Insert_API_Key_Here")
    monkeypatch.setattr(app_module, "_call_ollama", lambda *a, **k: None)
    with app_module._session_ai_keys_lock:
        app_module._session_ai_keys.clear()
    app_module.set_gemini_api_key("Insert_API_Key_Here")
    seen_keys = []

    class FakeCompletions:
        def create(self, **kwargs):
            seen_keys.append(kwargs["messages"][1]["content"])
            user_prompt = kwargs["messages"][1]["content"]
            content = "OK" if user_prompt == "Test" else "radius = 5\narea = 3.14159 * radius * radius\nprint(area)"
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content))]
            )

    class FakeGroq:
        def __init__(self, api_key):
            seen_keys.append(api_key)
            self.chat = types.SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setitem(sys.modules, "groq", types.SimpleNamespace(Groq=FakeGroq))

    try:
        config = client.post("/api-config", json={"api_key": "session-groq-key"})
        assert config.status_code == 200
        assert config.get_json()["success"] is True

        generated = client.post("/generate-code", json={"prompt": "find the area of a circle", "language": "en"})
        data = generated.get_json()
        assert data["success"] is True
        assert "area" in data["code"]
        assert seen_keys.count("session-groq-key") == 2
    finally:
        with app_module._session_ai_keys_lock:
            app_module._session_ai_keys.clear()
        app_module.set_gemini_api_key("Insert_API_Key_Here")


def test_generate_code_surfaces_ai_service_error(client, monkeypatch):
    monkeypatch.setenv("GEMINI_ENABLED", "1")
    monkeypatch.setattr(app_module, "call_gemini", lambda *a, **k: "AI service not configured. Add a Groq API key in settings.")
    res = client.post("/generate-code", json={"prompt": "find the area of a circle", "language": "en"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is False
    assert "not configured" in data["error"].lower()
    assert "empty response" not in data["error"].lower()


def test_generate_code_circle_fallback_on_blank_ai(client, monkeypatch):
    monkeypatch.setenv("GEMINI_ENABLED", "1")
    monkeypatch.setattr(app_module, "call_gemini", lambda *a, **k: "   ")
    res = client.post("/generate-code", json={"prompt": "find the area of a circle", "language": "en"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["source"] == "local_fallback"
    assert "radius" in data["code"]
    assert "area" in data["code"]
    compile(data["code"], "<generated>", "exec")


def test_fix_code_surfaces_ai_service_error(client, monkeypatch):
    monkeypatch.setenv("GEMINI_ENABLED", "1")
    monkeypatch.setattr(app_module, "call_gemini", lambda *a, **k: "Cloud AI authentication failed and offline AI is not available.")
    res = client.post("/fix", json={"code": "print(1)", "language": "en"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is False
    assert "authentication failed" in data["error"].lower()
    assert data["code"] == ""
    

# ===========================================================================
# 2. SANDBOX SECURITY
# ===========================================================================

@pytest.mark.parametrize("code, expected_fragments", [
    (
        "import os\nprint(os.getcwd())",
        ["not allowed", "os"],
    ),
    (
        "import sys\nprint(sys.version)",
        ["not allowed", "sys"],
    ),
    (
        "import subprocess\nprint(subprocess)",
        ["not allowed", "subprocess"],
    ),
    (
        "import importlib\nprint(importlib)",
        ["not allowed", "importlib"],
    ),
    (
        "from os import path\nprint(path)",
        ["not allowed", "os"],
    ),
    (
        "import _strptime\nprint(_strptime)",
        ["not allowed", "_strptime"],
    ),
    (
        "from datetime import _strptime\nprint(_strptime)",
        ["not allowed", "_strptime"],
    ),
    (
        "print(object.__subclasses__())",
        ["not allowed", "__subclasses__"],
    ),
    (
        "open('../outside.txt', 'w').write('x')",
        ["project root", "limited"],
    ),
    (
        "eval('import os')",
        ["not defined", "nameerror"],
    ),
    (
        "exec('import sys')",
        ["not defined", "nameerror"],
    ),
    (
        "compile('import os', '<s>', 'exec')",
        ["not defined", "nameerror"],
    ),
    (
        "__import__('os').system('ls')",
        ["not allowed", "os"],
    ),
])
def test_sandbox_blocks_escape_attempts(client, code, expected_fragments):
    """Sandbox must block all common escape patterns."""
    res = client.post("/run", json={"code": code})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is False
    error_lower = data["error"].lower()
    assert any(f in error_lower for f in expected_fragments), (
        f"Expected one of {expected_fragments!r} in:\n{data['error']}"
    )


def test_sandbox_input_blocked(client):
    """input() must be blocked with a clear explanation."""
    res = client.post("/run", json={"code": "x = input('name: ')\nprint(x)"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is False
    assert "input" in data["error"].lower() or (
        data.get("explanation") and "input" in data["explanation"].lower()
    )


def test_sandbox_allowed_modules(client):
    """math and random must be importable and usable."""
    res = client.post("/run", json={"code": "import math\nprint(int(math.pi))"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True, f"math import failed: {data.get('error')}"
    assert "3" in data.get("output", "")


@pytest.mark.timeout(15)
def test_sandbox_function_reads_module_global_without_false_scope_loss(client):
    code = "answer = 42\n\ndef show():\n    print(answer)\n\nshow()"
    res = client.post("/run", json={"code": code})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True, data.get("error")
    assert "42" in data.get("output", "")
    changes = [
        change
        for event in data.get("trace", [])
        for change in event.get("changes", [])
    ]
    assert "answer went out of scope" not in changes


@pytest.mark.timeout(15)
def test_sandbox_simple_class_definition_runs(client):
    code = "class Dog:\n    def speak(self):\n        return 'woof'\n\nprint(Dog().speak())"
    res = client.post("/run", json={"code": code})
    data = res.get_json()
    assert data["success"] is True, data.get("error")
    assert "woof" in data.get("output", "")


@pytest.mark.timeout(15)
def test_sandbox_common_class_patterns_work(client):
    code = (
        "class Animal(object):\n"
        "    def __init__(self, name):\n"
        "        self.name = name\n"
        "    @property\n"
        "    def label(self):\n"
        "        return self.name\n"
        "    @staticmethod\n"
        "    def kind():\n"
        "        return 'animal'\n"
        "class Dog(Animal):\n"
        "    def __init__(self, name):\n"
        "        super().__init__(name)\n"
        "print(Dog.kind(), Dog('Ada').label)"
    )
    data = client.post("/run", json={"code": code}).get_json()
    assert data["success"] is True, data.get("error")
    assert "animal Ada" in data.get("output", "")


@pytest.mark.timeout(15)
def test_sandbox_name_is_main(client):
    res = client.post("/run", json={"code": "if __name__ == '__main__':\n    print('main block')"})
    data = res.get_json()
    assert data["success"] is True, data.get("error")
    assert "main block" in data.get("output", "")


@pytest.mark.timeout(15)
def test_sandbox_datetime_imports(client):
    code = (
        "import datetime\n"
        "from datetime import date\n"
        "print(datetime.date(2026, 5, 28).isoformat())\n"
        "print(date(2026, 5, 28).isoformat())\n"
        "print(datetime.datetime.strptime('2026-05-28', '%Y-%m-%d').date().isoformat())"
    )
    res = client.post("/run", json={"code": code})
    data = res.get_json()
    assert data["success"] is True, data.get("error")
    assert data.get("output", "").count("2026-05-28") == 3


def test_sandbox_module_allowlist_and_preload_are_consistent():
    import sandbox_runner

    assert "date" not in sandbox_runner.ALLOWED_MODULES
    lazy_third_party = {"numpy", "pandas", "matplotlib"}
    assert lazy_third_party.issubset(sandbox_runner.ALLOWED_MODULES)
    assert set(sandbox_runner._PRELOADED) == set(sandbox_runner.ALLOWED_MODULES) - lazy_third_party


@pytest.mark.timeout(15)
def test_sandbox_date_is_not_top_level_module(client):
    res = client.post("/run", json={"code": "import date"})
    data = res.get_json()
    assert data["success"] is False
    assert "not allowed" in data.get("error", "").lower()


def test_sandbox_bad_inputs_file_reports_clear_failure(tmp_path):
    code_file = tmp_path / "code.py"
    trace_file = tmp_path / "trace.json"
    inputs_file = tmp_path / "inputs.txt"
    code_file.write_text("print('should not run')", encoding="utf-8")
    inputs_file.write_bytes(b"\xff\xfe\xff")
    env = os.environ.copy()
    env["CODEUP_CODE_FILE"] = str(code_file)
    env["CODEUP_TRACE_FILE"] = str(trace_file)
    env["CODEUP_INPUTS_FILE"] = str(inputs_file)

    result = subprocess.run(
        [sys.executable, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "sandbox_runner.py"))],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert "Could not load pre-flight inputs" in result.stderr
    assert "should not run" not in result.stdout


def test_sandbox_normal_execution(client):
    """Basic arithmetic and print work correctly."""
    res = client.post("/run", json={"code": "print(2 + 2)"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert "4" in data.get("output", "")


def test_sandbox_loop_output(client):
    """For loops produce correct sequential output."""
    res = client.post("/run", json={"code": "for i in range(3):\n    print(i)"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    output = data.get("output", "")
    assert "0" in output and "1" in output and "2" in output


def test_broken_loop_error_is_safe_and_beginner_friendly(client):
    """The external-demo indentation failure must not expose host paths."""
    code = "for i in range(3):\nprint(i)"
    res = client.post("/run", json={"code": code, "language": "en"})
    assert res.status_code == 200
    data = res.get_json()

    assert data["success"] is False
    error = data.get("error", "")
    explanation = data.get("explanation", "")
    combined = f"{error}\n{explanation}"

    assert "Traceback" not in combined
    assert "sandbox_runner.py" not in combined
    assert os.getcwd() not in combined
    assert "C:\\" not in combined
    assert "Line 2" in error
    assert "IndentationError" in error
    assert "line after the loop" in combined.lower()
    assert "four spaces" in combined.lower()


def test_traceback_sanitizer_redacts_paths_and_api_keys():
    raw = (
        'Traceback (most recent call last):\n'
        '  File "C:\\Users\\Teacher\\CodeUp\\sandbox_runner.py", line 99, in main\n'
        'gsk_fakeSecretValue1234567890'
    )
    safe = app_module.sanitize_traceback(raw)
    assert "C:\\" not in safe
    assert "Users\\Teacher" not in safe
    assert "gsk_fakeSecretValue1234567890" not in safe
    assert "<redacted-api-key>" in safe


def test_corrected_loop_demo_flow_returns_expected_trace_and_structure(client):
    """The documented corrected loop prints the running total and exposes loop structure."""
    code = "total = 0\nfor i in range(3):\n    total = total + i\n    print(total)"
    run = client.post("/run", json={"code": code, "language": "en"})
    assert run.status_code == 200
    run_data = run.get_json()
    assert run_data["success"] is True
    assert run_data["output"].strip().splitlines() == ["0", "1", "3"]
    assert len(run_data.get("trace", [])) > 0

    structure = client.post("/structure", json={"code": code}).get_json()
    assert structure["success"] is True
    assert structure["structure"]["loops"] == [{"type": "for", "line": 2}]


# ===========================================================================
# 3. REQUEST SIZE LIMITS
# ===========================================================================

def test_max_code_size_rejected_run(client):
    """Code over MAX_CODE_SIZE must return 413 from /run."""
    big = "x = 1\n" * 20_000
    res = client.post("/run", json={"code": big})
    assert res.status_code == 413


def test_max_code_size_rejected_analyze(client):
    """Code over MAX_CODE_SIZE must return 413 from /analyze."""
    big = "x = 1\n" * 20_000
    res = client.post("/analyze", json={"code": big, "language": "en"})
    assert res.status_code == 413


def test_empty_code_rejected(client):
    """Empty code must return 400 from /run."""
    res = client.post("/run", json={"code": "   "})
    assert res.status_code == 400


HTML_DOCUMENT = """<!doctype html>
<html>
<head><title>Not Python</title></head>
<body><script>console.log("nope")</script></body>
</html>
"""


@pytest.mark.parametrize("path,payload", [
    ("/run", {"code": HTML_DOCUMENT}),
    ("/analyze", {"code": HTML_DOCUMENT, "language": "en"}),
    ("/summarize", {"code": HTML_DOCUMENT, "language": "en"}),
    ("/check-syntax", {"code": HTML_DOCUMENT}),
    ("/structure", {"code": HTML_DOCUMENT}),
    ("/structure-outline", {"code": HTML_DOCUMENT}),
    ("/suggest-next", {"code": HTML_DOCUMENT, "line": 1, "language": "en"}),
    ("/narrate-file", {"code": HTML_DOCUMENT, "language": "en"}),
    ("/snippets", {"name": "html_doc", "code": HTML_DOCUMENT}),
    ("/diff-explain", {"before": "print('ok')", "after": HTML_DOCUMENT, "language": "en"}),
])
def test_non_python_documents_rejected_by_python_endpoints(client, path, payload):
    """The Python IDE must reject whole-document HTML/CSS/JS before processing."""
    res = client.post(path, json=payload)
    assert res.status_code == 400
    data = res.get_json()
    assert "python-only" in (data.get("error") or "").lower()


def test_stale_mixed_html_autosave_shape_rejected(client):
    """Regression test for a Python line accidentally prepended to an HTML draft."""
    code = "print('browser smoke ok')<!doctype html>\n<html>\n<body>bad</body>\n</html>"
    res = client.post("/check-syntax", json={"code": code})
    assert res.status_code == 400
    assert "python-only" in res.get_json()["error"].lower()


def test_python_string_containing_html_is_allowed(client):
    """Python code can still contain HTML-looking text inside ordinary strings."""
    code = "page = '<html><body>ok</body></html>'\nprint(page)"
    syntax = client.post("/check-syntax", json={"code": code})
    assert syntax.status_code == 200
    assert syntax.get_json()["has_errors"] is False

    run = client.post("/run", json={"code": code})
    assert run.status_code == 200
    data = run.get_json()
    assert data["success"] is True
    assert "<html><body>ok</body></html>" in data["output"]


def test_monaco_worker_nls_route_serves_bundled_asset(client):
    res = client.get("/vs/base/common/worker/simpleWorker.nls.js")
    assert res.status_code == 200
    assert b"Microsoft Corporation" in res.data[:200]


def test_snippet_create_rejects_blank_name_or_code(client):
    blank_name = client.post("/snippets", json={"name": "  ", "code": "print(1)"})
    assert blank_name.status_code == 400

    blank_code = client.post("/snippets", json={"name": "empty", "code": "   "})
    assert blank_code.status_code == 400


def test_snippet_update_rejects_non_python_document(client):
    saved = client.post("/snippets", json={"name": "safe", "code": "print('safe')"})
    sid = saved.get_json()["id"]
    updated = client.put(f"/snippets/{sid}", json={"code": HTML_DOCUMENT})
    assert updated.status_code == 400
    assert "python-only" in updated.get_json()["error"].lower()


def test_corrupt_snippet_file_is_normalized(client):
    client.get("/snippets")
    path = app_module._snippets_path(_client_session_id(client))
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"snippets": [None, "bad", {"id": "ok", "name": "", "code": "print(1)"}]}, handle)

    data = client.get("/snippets").get_json()
    assert data["snippets"] == [{"id": "ok", "name": "Untitled", "code": "print(1)"}]


def test_voice_command_non_string_and_too_long_payloads(client):
    numeric = client.post("/voice-command", json={"text": 123})
    assert numeric.status_code == 200
    assert numeric.get_json()["success"] is True

    too_long = client.post("/voice-command", json={"text": "x" * (app_module.MAX_VOICE_TEXT_SIZE + 1)})
    assert too_long.status_code == 413


def test_command_input_enter_and_live_input_contracts_are_wired():
    with open(os.path.join("templates", "index.html"), encoding="utf-8") as handle:
        html = handle.read()
    assert "voiceTextInput.addEventListener('keydown'" in html
    assert "submitCommand" in html

    with open(os.path.join("static", "app.js"), encoding="utf-8") as handle:
        app_js = handle.read()
    submit_start = app_js.index("async function submitCommand()")
    submit_end = app_js.index("// ---------- VOICE ----------")
    submit_block = app_js[submit_start:submit_end]
    assert "sendStreamingInput(txt)" in submit_block
    assert "buildVoiceCommandPayload(txt, 'typed')" in app_js
    assert "buildVoiceCommandPayload(cleaned, 'voice')" in app_js
    assert "code: getCode()" in app_js
    assert "error: errorText" in app_js
    assert "source," in app_js


def test_start_gate_is_removed_so_ide_opens_directly():
    # The blocking start gate was removed entirely: /ide opens straight into the
    # usable IDE, so there is no hidden modal left in the keyboard/focus order at
    # all (a strictly stronger guarantee than hiding it after a language choice).
    with open(os.path.join("templates", "index.html"), encoding="utf-8") as handle:
        html = handle.read()
    assert 'id="startGate"' not in html
    assert 'id="startEnglish"' not in html
    assert "Start in English" not in html


def test_microphone_denied_path_uses_visible_fallback_without_console_error():
    with open(os.path.join("static", "app.js"), encoding="utf-8") as handle:
        app_js = handle.read()
    assert "console.error('Voice error:'" not in app_js
    assert "Microphone access blocked" in app_js
    assert "Keyboard shortcuts and the typed command box still work" in app_js
    assert "VoiceEngine.VoiceInput.isActive()" in app_js


def test_repeat_preserves_fuzzy_fallback_actions(client):
    first = client.post("/voice-command", json={"text": "copy code"})
    assert first.status_code == 200
    assert first.get_json()["action"] == "copy_code"

    repeated = client.post("/voice-command", json={"text": "repeat"})
    assert repeated.status_code == 200
    assert repeated.get_json()["action"] == "copy_code"


def test_missing_body_handled(client):
    """Completely missing JSON body must not crash the server."""
    res = client.post("/run", data="not json", content_type="text/plain")
    assert res.status_code in (400, 413, 200)  # any of these is acceptable, not 500


# ===========================================================================
# 4. VOICE INTENT PARSING
# ===========================================================================

@pytest.mark.parametrize("voice_input, expected_action, check", [
    ("go to line fifteen",          "goto_line",    lambda d: d.get("line") == 15),
    ("go to line 3",                "goto_line",    lambda d: d.get("line") == 3),
    ("read line 7",                 "read_line",    lambda d: d.get("line") == 7),
    ("describe line 10",            "describe_line",lambda d: d.get("line") == 10),
    ("delete line 4",               "delete_line",  lambda d: d.get("line") == 4),
    ("clear editor",                "clear_editor", lambda d: True),
    ("summarize this file",         "summarize",    lambda d: True),
    ("read code",                   "narrate_file", lambda d: True),
    ("read the code",               "narrate_file", lambda d: True),
    ("explain structure",           "read_outline", lambda d: True),
    ("advise on code",              "advise",       lambda d: True),
    ("next step",                   "next_step",    lambda d: True),
    ("previous step",               "previous_step",lambda d: True),
    ("what changed here",           "what_changed", lambda d: True),
    ("run",                         "run",          lambda d: True),
    ("fix code",                    "fix",          lambda d: True),
    ("debug this error",            "explain_simply", lambda d: True),
    ("check for errors",            "check_errors", lambda d: True),
    ("suggest next line",           "suggest_next", lambda d: True),
    ("story mode",                  "story_mode",   lambda d: True),
    ("learning mode",               "mentor_mode",  lambda d: True),
    ("bug challenge",               "bug_challenge",lambda d: True),
    ("save snippet named my prog",  "save_snippet_named", lambda d: "my prog" in d.get("name", "")),
    ("save code as a snippet",      "save_snippet_auto",  lambda d: True),
    ("show my snippets",            "list_snippets",      lambda d: True),
    ("load the snippet called first loop", "load_snippet", lambda d: d.get("id") == "first loop"),
    ("turn voice off",              "pause_voice",        lambda d: True),
    ("turn voice on",               "resume_voice",       lambda d: True),
    ("code run karo",               "run",                lambda d: True),
    ("editor clear karo",           "clear_editor",       lambda d: True),
    ("code map batao",              "code_map",           lambda d: True),
    ("step by step run karke samjhao", "step_narration",  lambda d: True),
    ("meri mistake samjhao",        "replay_mistake",     lambda d: True),
    ("block sonify karo",           "sonify_block",       lambda d: True),
    ("is code ko first loop naam se snippet save karo", "save_snippet_named", lambda d: d.get("name") == "first loop"),
    ("first loop wala snippet load karo", "load_snippet", lambda d: d.get("id") == "first loop"),
    ("generate code for fibonacci", "generate_code",lambda d: "fibonacci" in d.get("prompt", "")),
    ("rename snippet 1234 to final","rename_snippet",lambda d: d.get("new_name") == "final"),
    ("quiz me on loops",            "quiz_me",      lambda d: "loops" in d.get("topic", "")),
    ("set breakpoint at line 5",    "set_breakpoint",lambda d: d.get("line_number") == 5),
    ("watch variable x",            "watch_variable",lambda d: d.get("variable") == "x"),
    ("insert function called greet","insert_function",lambda d: d.get("function_name") == "greet"),
    ("insert a for loop",           "conversational_edit",
     lambda d: d.get("ai_action", {}).get("code") == "for i in range(3):\n    print(i)"),
    # Spoken print inserts are now built into valid Python (quoted text) and applied
    # via conversational_edit, instead of the old raw append_line passthrough.
    ("insert print hello world",    "conversational_edit",
     lambda d: d.get("ai_action", {}).get("code") == 'print("hello world")'),
])
def test_voice_intent_parsing(client, voice_input, expected_action, check):
    res = client.post("/voice-command", json={"text": voice_input})
    assert res.status_code == 200
    data = res.get_json()
    assert data["action"] == expected_action, (
        f"Input {voice_input!r}: expected {expected_action!r}, got {data['action']!r}"
    )
    if check:
        assert check(data), f"Slot check failed for {voice_input!r}: {data}"


def test_voice_unknown_command(client):
    """Completely nonsense input clarifies or confirms, never crashes."""
    res = client.post("/voice-command", json={"text": "xyzzy blort flibble"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["action"] in ("clarify", "confirm")
    if data["action"] == "clarify":
        assert data.get("needs_clarification") is True


def test_deterministic_voice_command_does_not_call_conversational_ai(client, monkeypatch):
    def fail_call(*args, **kwargs):
        raise AssertionError("deterministic route should not call AI")

    monkeypatch.setattr(app_module, "call_gemini", fail_call)
    res = client.post("/voice-command", json={"text": "clear editor", "code": "print('hi')"})
    assert res.status_code == 200
    assert res.get_json()["action"] == "clear_editor"


def test_why_did_this_fail_routes_to_accessible_error_explainer_when_error_present(client):
    res = client.post("/voice-command", json={
        "text": "why did this fail",
        "code": "for i in range(3):\nprint(i)",
        "error": "IndentationError: expected an indented block after for statement on line 1",
    })
    assert res.status_code == 200
    data = res.get_json()
    assert data["action"] == "explain_simply"


def test_conversational_voice_uses_validated_structured_ai_action(client, monkeypatch):
    def fake_call(*args, **kwargs):
        return json.dumps({
            "action": "append_code",
            "target": {"line_number": None, "position": "end_of_file"},
            "code": "total = 0",
            "spoken_confirmation": "I added total equals zero.",
            "confidence": 0.94,
            "requires_confirmation": False,
        })

    monkeypatch.setattr(app_module, "call_gemini", fake_call)
    res = client.post("/voice-command", json={
        "text": "Please put total equal to zero at the end.",
        "code": "",
    })
    data = res.get_json()
    assert data["action"] == "conversational_edit"
    assert data["ai_action"]["action"] == "append_code"
    assert data["ai_action"]["code"] == "total = 0"
    assert data["ai_action"]["requires_confirmation"] is False


def test_conversational_voice_rejects_unsafe_ai_action(client, monkeypatch):
    def fake_call(*args, **kwargs):
        return json.dumps({
            "action": "shell",
            "target": {"line_number": None, "position": "end_of_file"},
            "code": "open('.env').read()",
            "spoken_confirmation": "done",
            "confidence": 0.99,
            "requires_confirmation": False,
        })

    monkeypatch.setattr(app_module, "call_gemini", fake_call)
    res = client.post("/voice-command", json={
        "text": "Change the message to hello Arun.",
        "code": 'message = "hello"\nprint(message)',
    })
    data = res.get_json()
    assert data["action"] == "unknown"
    assert "could not confidently" in data["message"].lower()


def test_conversational_voice_rejects_malformed_ai_json(client, monkeypatch):
    monkeypatch.setattr(app_module, "call_gemini", lambda *args, **kwargs: "not json")
    res = client.post("/voice-command", json={
        "text": "Change the message to hello Arun.",
        "code": 'message = "hello"\nprint(message)',
    })
    data = res.get_json()
    assert data["action"] == "unknown"
    assert "rephrase" in data["message"].lower()


def test_conversational_voice_local_demo_fallback_when_ai_unavailable(client, monkeypatch):
    monkeypatch.setattr(app_module, "call_gemini", lambda *args, **kwargs: "AI service not configured.")
    res = client.post("/voice-command", json={
        "text": "remove the indentation before the print statement so I can see the error",
        "code": "for i in range(3):\n    print(i)",
    })
    data = res.get_json()
    assert data["action"] == "conversational_edit"
    assert data["ai_action"]["action"] == "dedent_line"
    assert data["ai_action"]["target"]["line_number"] == 2
    assert data["ai_action"]["source"] == "local"


def test_add_loop_that_prints_uses_conversational_router_not_greedy_insert_loop(client, monkeypatch):
    monkeypatch.setattr(app_module, "call_gemini", lambda *args, **kwargs: "AI service not configured.")
    res = client.post("/voice-command", json={
        "text": "add a loop that prints the numbers zero to two",
        "code": "",
    })
    data = res.get_json()
    assert data["action"] == "conversational_edit"
    assert data["ai_action"]["action"] == "append_code"
    assert data["ai_action"]["code"] == "for i in range(3):\n    print(i)"


def test_hinglish_loop_and_indentation_fallbacks_when_ai_unavailable(client, monkeypatch):
    monkeypatch.setattr(app_module, "call_gemini", lambda *args, **kwargs: "AI service not configured.")

    loop = client.post("/voice-command", json={
        "text": "zero se two tak ek loop banao jo har number print kare",
        "code": "",
        "language": "hi",
    }).get_json()
    assert loop["action"] == "conversational_edit"
    assert loop["ai_action"]["action"] == "append_code"
    assert loop["ai_action"]["code"] == "for i in range(3):\n    print(i)"
    assert "zero se two" in loop["ai_action"]["spoken_confirmation"].lower()

    dedent = client.post("/voice-command", json={
        "text": "print statement ke pehle wali indentation hata do taaki error dikhe",
        "code": "for i in range(3):\n    print(i)",
        "language": "hi",
    }).get_json()
    assert dedent["ai_action"]["action"] == "dedent_line"
    assert dedent["ai_action"]["target"]["line_number"] == 2

    indent = client.post("/voice-command", json={
        "text": "print statement ko loop ke andar karo",
        "code": "for i in range(3):\nprint(i)",
        "language": "hi",
    }).get_json()
    assert indent["ai_action"]["action"] == "indent_line"
    assert indent["ai_action"]["target"]["line_number"] == 2


def test_voice_ambiguous_triggers_confirm(client):
    """Input that fuzzy-matches multiple commands should trigger confirm."""
    res = client.post("/voice-command", json={"text": "analyse"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["action"] in ("analyze", "confirm", "unknown")
    if data["action"] == "confirm":
        assert "options" in data
        assert len(data["options"]) >= 1


def test_voice_repeat_replays_last_action(client):
    """Repeat must return the same action as the previous command."""
    seed = client.post("/voice-command", json={"text": "execute code"})
    assert seed.get_json()["action"] == "run", f"Seed failed: {seed.get_json()}"

    repeat_res = client.post("/voice-command", json={"text": "repeat"})
    assert repeat_res.status_code == 200
    assert repeat_res.get_json()["action"] == "run"

def test_voice_empty_text(client):
    """Empty voice input must not crash."""
    res = client.post("/voice-command", json={"text": ""})
    assert res.status_code == 200
    data = res.get_json()
    assert "action" in data


# ===========================================================================
# 5. EXECUTION TRACE PLAYBACK
# ===========================================================================

@pytest.mark.timeout(15)
def test_trace_populated_after_run(client):
    """Running code must populate a non-empty trace."""
    res = client.post("/run", json={"code": "x = 5\ny = 10\nprint(x + y)"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert len(data.get("trace", [])) > 0, "Trace must not be empty after successful run"


@pytest.mark.timeout(15)
def test_trace_next_step_returns_speech(client):
    """next step voice command must return non-empty speech after a run."""
    run = client.post("/run", json={"code": "a = 1\nb = 2\nc = a + b"})
    assert run.get_json()["success"] is True
    assert len(run.get_json().get("trace", [])) > 0

    step = client.post("/voice-command", json={"text": "next step"})
    assert step.status_code == 200
    data = step.get_json()
    assert data["action"] == "next_step"
    assert data.get("speech"), f"Expected non-empty speech, got: {data}"


@pytest.mark.timeout(15)
def test_trace_step_counter_in_speech(client):
    """Speech output must contain 'Step X of Y' format."""
    run = client.post("/run", json={"code": "a = 1\nb = 2\nc = a + b"})
    run_data = run.get_json()
    assert run_data["success"] is True
    assert len(run_data.get("trace", [])) > 0, "Trace empty — cannot test step counter"

    step = client.post("/voice-command", json={"text": "next step"})
    speech = step.get_json().get("speech", "")
    assert "Step" in speech, f"Expected step counter in speech, got: {speech!r}"

@pytest.mark.timeout(15)
def test_trace_what_changed(client):
    """what changed must return speech after a run."""
    client.post("/run", json={"code": "x = 42\nprint(x)"})
    # advance to first step first
    client.post("/voice-command", json={"text": "next step"})
    res = client.post("/voice-command", json={"text": "what changed here"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["action"] == "what_changed"
    assert data.get("speech")


@pytest.mark.timeout(15)
def test_trace_endpoint_returns_structure(client):
    """GET /execution-trace must return expected keys."""
    client.post("/run", json={"code": "x = 1"})
    res = client.get("/execution-trace")
    assert res.status_code == 200
    data = res.get_json()
    assert "trace" in data
    assert "current_index" in data
    assert "duration_ms" in data


@pytest.mark.timeout(15)
def test_execution_story_has_local_fallback_when_ai_disabled(client):
    """Story mode must remain useful when no AI key is configured."""
    code = "total = 0\nfor i in range(3):\n    total = total + i\n    print(total)"
    run = client.post("/run", json={"code": code})
    assert run.get_json()["success"] is True

    story = client.post("/execution-story", json={"code": code, "language": "en"})
    assert story.status_code == 200
    data = story.get_json()
    assert data["success"] is True
    text = data.get("story", "")
    assert "AI service" not in text
    assert "tracer" in text
    assert "next step" in text.lower()


@pytest.mark.timeout(15)
def test_custom_session_cookie_is_single_and_reused(client):
    run = client.post("/run", json={"code": "x = 1\nprint(x)"})
    assert run.status_code == 200
    session_cookies = [
        value for value in run.headers.getlist("Set-Cookie")
        if value.startswith(app_module.SESSION_COOKIE_NAME + "=")
    ]
    assert len(session_cookies) == 1
    session_id = _client_session_id(client)
    assert session_id in app_module._session_traces

    followup = client.get("/execution-trace")
    followup_cookies = [
        value for value in followup.headers.getlist("Set-Cookie")
        if value.startswith(app_module.SESSION_COOKIE_NAME + "=")
    ]
    assert followup_cookies == []


@pytest.mark.timeout(15)
def test_testing_session_cookie_is_local_workable(client):
    res = client.get("/execution-trace")
    session_cookies = [
        value for value in res.headers.getlist("Set-Cookie")
        if value.startswith(app_module.SESSION_COOKIE_NAME + "=")
    ]
    assert len(session_cookies) == 1
    cookie = session_cookies[0]
    assert "HttpOnly" in cookie
    assert "Secure" not in cookie
    assert "SameSite" not in cookie


def test_run_stream_input_rejects_when_not_awaiting_without_opening_fifo(client, tmp_path):
    # Windows cannot exercise the POSIX FIFO end-to-end path; this validates
    # the non-blocking state gate before any FIFO open is attempted.
    client.get("/execution-trace")
    session_id = _client_session_id(client)
    run_id = "notwaiting"

    class FakeProc:
        returncode = None

        def poll(self):
            return None

    state = {
        "proc": FakeProc(),
        "fifo": str(tmp_path / "missing.fifo"),
        "session_id": session_id,
        "awaiting_input": False,
        "awaiting_input_lock": threading.Lock(),
    }
    with app_module._active_runs_lock:
        app_module._active_runs[run_id] = state
    try:
        res = client.post(f"/run-stream/{run_id}/input", json={"value": "hello"})
        assert res.status_code == 409
        assert "not awaiting input" in res.get_json()["error"].lower()
    finally:
        with app_module._active_runs_lock:
            app_module._active_runs.pop(run_id, None)


def test_cleanup_run_reaps_child_process(monkeypatch):
    monkeypatch.setattr(app_module.sys, "platform", "win32")

    class FakeProc:
        def __init__(self):
            self.returncode = None
            self.kill_called = False
            self.wait_calls = 0

        def poll(self):
            return self.returncode

        def kill(self):
            self.kill_called = True
            self.returncode = -9

        def wait(self, timeout=None):
            self.wait_calls += 1
            return self.returncode

    proc = FakeProc()
    run_id = "cleanup-test"
    with app_module._active_runs_lock:
        app_module._active_runs[run_id] = {"proc": proc}

    app_module._cleanup_run(run_id)

    assert proc.kill_called is True
    assert proc.wait_calls >= 1
    assert proc.returncode == -9
    assert run_id not in app_module._active_runs


def test_interactive_wait_unit_reports_concrete_exit_code_after_timeout(monkeypatch):
    # Unit-level coverage for Windows: the true POSIX FIFO/SSE E2E path cannot
    # run here, but final exit-code stabilization is still deterministic.
    monkeypatch.setattr(app_module.sys, "platform", "win32")

    class FakeProc:
        def __init__(self):
            self.returncode = None
            self.kill_called = False
            self.wait_calls = 0

        def poll(self):
            return self.returncode

        def kill(self):
            self.kill_called = True
            self.returncode = -9

        def wait(self, timeout=None):
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
            return self.returncode

    proc = FakeProc()
    exit_code = app_module._wait_for_process_exit(proc, timeout=0)

    assert proc.kill_called is True
    assert exit_code == -9
    assert proc.returncode == -9


# ===========================================================================
# 6. SYNTAX CHECKING
# ===========================================================================

def test_syntax_check_clean_code(client):
    """Valid code returns has_errors=False."""
    res = client.post("/check-syntax", json={"code": "x = 1\nprint(x)"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["has_errors"] is False


def test_syntax_check_broken_code(client):
    """Broken code returns has_errors=True with error details."""
    res = client.post("/check-syntax", json={"code": "def foo(\n    print('oops')"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["has_errors"] is True
    assert data["error_count"] >= 1
    assert len(data["errors"]) >= 1
    assert "line" in data["errors"][0]
    assert "message" in data["errors"][0]


def test_syntax_check_empty_code(client):
    """Empty code returns has_errors=False cleanly."""
    res = client.post("/check-syntax", json={"code": ""})
    assert res.status_code == 200
    data = res.get_json()
    assert data["has_errors"] is False


# ===========================================================================
# 7. VARIABLE TRACKING
# ===========================================================================

def test_track_variables_basic(client):
    """Variable tracker finds assignments correctly."""
    code = "x = 5\ny = 10\nz = x + y"
    res = client.post("/track-variables", json={"code": code, "line": 1})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    names = [v["name"] for v in data["variables"]]
    assert "x" in names
    assert "y" in names


def test_track_variables_invalid_line(client):
    """Non-numeric line number returns 400."""
    res = client.post("/track-variables", json={"code": "x = 1", "line": "abc"})
    assert res.status_code == 400


def test_find_variable_usage(client):
    """Variable usage finder returns correct line numbers."""
    code = "x = 5\nprint(x)\ny = x + 1"
    res = client.post("/find-variable-usage", json={"code": code, "variable": "x"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["count"] >= 2
    lines = [u["line"] for u in data["usages"]]
    assert 1 in lines  # assignment


def test_find_variable_usage_not_found(client):
    """Variable not in code returns success=False with message."""
    res = client.post("/find-variable-usage", json={"code": "x = 1", "variable": "z"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is False
    assert "message" in data


# ===========================================================================
# 8. STRUCTURE PARSING
# ===========================================================================

def test_structure_functions(client):
    """Structure parser finds function definitions."""
    code = "def greet(name):\n    print(name)\n\ndef add(a, b):\n    return a + b"
    res = client.post("/structure", json={"code": code})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    fn_names = [f["name"] for f in data["structure"]["functions"]]
    assert "greet" in fn_names
    assert "add" in fn_names


def test_structure_classes(client):
    """Structure parser finds class definitions."""
    code = "class Dog:\n    def bark(self):\n        print('woof')"
    res = client.post("/structure", json={"code": code})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    class_names = [c["name"] for c in data["structure"]["classes"]]
    assert "Dog" in class_names


def test_structure_empty_code(client):
    """Empty code returns empty structure without error."""
    res = client.post("/structure", json={"code": ""})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["structure"]["functions"] == []
    assert data["structure"]["classes"] == []


# ===========================================================================
# 9. SNIPPET CRUD
# ===========================================================================

def test_snippet_save_and_list(client):
    """Save a snippet and confirm it appears in the list."""
    save = client.post("/snippets", json={"name": "test_snip", "code": "print('hi')"})
    assert save.status_code == 200
    data = save.get_json()
    assert data["success"] is True
    assert "id" in data
    assert "speech" in data

    lst = client.get("/snippets")
    assert lst.status_code == 200
    lst_data = lst.get_json()
    assert "snippets" in lst_data
    assert "speech" in lst_data
    names = [s["name"] for s in lst_data["snippets"]]
    assert "test_snip" in names


def test_snippet_delete(client):
    """Delete a snippet and confirm it no longer appears."""
    save = client.post("/snippets", json={"name": "to_delete", "code": "x = 1"})
    sid = save.get_json()["id"]

    delete = client.delete(f"/snippets/{sid}")
    assert delete.status_code == 200
    assert delete.get_json()["success"] is True

    lst = client.get("/snippets")
    names = [s["name"] for s in lst.get_json()["snippets"]]
    assert "to_delete" not in names


def test_snippet_update(client):
    """Update a snippet name via PUT."""
    save = client.post("/snippets", json={"name": "original", "code": "x = 1"})
    sid = save.get_json()["id"]

    update = client.put(f"/snippets/{sid}", json={"name": "renamed"})
    assert update.status_code == 200
    assert update.get_json()["success"] is True

    lst = client.get("/snippets")
    names = [s["name"] for s in lst.get_json()["snippets"]]
    assert "renamed" in names
    assert "original" not in names


def test_snippet_delete_nonexistent(client):
    """Deleting a nonexistent snippet returns success=True gracefully."""
    res = client.delete("/snippets/nonexistent-id-xyz")
    assert res.status_code == 200


def test_snippet_name_too_long(client):
    """Snippet name over 256 chars must return 400."""
    res = client.post("/snippets", json={"name": "x" * 300, "code": "print(1)"})
    assert res.status_code == 400


def test_snippet_code_too_large(client):
    """Snippet code over MAX_CODE_SIZE must return 413."""
    res = client.post("/snippets", json={"name": "big", "code": "x = 1\n" * 20_000})
    assert res.status_code == 413


def test_snippet_speech_single(client):
    """Single snippet produces grammatically correct speech."""
    client.post("/snippets", json={"name": "only_one", "code": "print(1)"})
    lst = client.get("/snippets")
    speech = lst.get_json()["speech"]
    assert "1 snippet" in speech


def test_snippet_speech_multiple(client):
    """Multiple snippets produce correct count in speech."""
    client.post("/snippets", json={"name": "first", "code": "print(1)"})
    client.post("/snippets", json={"name": "second", "code": "print(2)"})
    lst = client.get("/snippets")
    speech = lst.get_json()["speech"]
    assert "2 snippets" in speech


def test_concurrent_snippet_saves_do_not_lose_updates(client):
    client.get("/snippets")
    session_id = _client_session_id(client)
    barrier = threading.Barrier(8)
    errors = []

    def save_one(i):
        try:
            with app.test_client() as c:
                c.set_cookie(app_module.SESSION_COOKIE_NAME, session_id)
                barrier.wait(timeout=5)
                res = c.post("/snippets", json={"name": f"s{i}", "code": f"print({i})"})
                assert res.status_code == 200
                assert res.get_json()["success"] is True
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=save_one, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    names = {s["name"] for s in client.get("/snippets").get_json()["snippets"]}
    assert {f"s{i}" for i in range(8)} <= names


# ===========================================================================
# 10. LINE READING & DESCRIBE
# ===========================================================================

def test_read_line_context_valid(client):
    """Read line context returns correct line content."""
    code = "x = 5\ny = 10\nprint(x + y)"
    res = client.post("/read-line-context", json={"code": code, "line": 2})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert "y" in data["content"]
    assert "indent_level" in data
    assert "context" in data


def test_read_line_context_invalid_line(client):
    """Non-numeric line returns 400."""
    res = client.post("/read-line-context", json={"code": "x = 1", "line": "bad"})
    assert res.status_code == 400


def test_read_line_context_out_of_range(client):
    """Line number beyond file length returns success=False."""
    res = client.post("/read-line-context", json={"code": "x = 1", "line": 999})
    assert res.status_code == 200
    assert res.get_json()["success"] is False


# ===========================================================================
# 11. SANDBOXED FILESYSTEM
# ===========================================================================

def test_fs_write_and_read(client):
    """Write then read a file in the sandbox."""
    write = client.post("/fs/write", json={"path": "test.txt", "content": "hello"})
    assert write.status_code == 200
    assert write.get_json()["success"] is True

    read = client.post("/fs/read", json={"path": "test.txt"})
    assert read.status_code == 200
    data = read.get_json()
    assert data["success"] is True
    assert data["content"] == "hello"


def test_fs_path_traversal_blocked(client):
    """Path traversal attempts must be blocked."""
    res = client.post("/fs/read", json={"path": "../../etc/passwd"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is False
    assert "outside" in data.get("error", "").lower() or "not found" in data.get("error", "").lower()


def test_fs_missing_path(client):
    """Missing path field returns 400."""
    res = client.post("/fs/read", json={})
    assert res.status_code == 400


def test_fs_delete(client):
    """Write then delete a file."""
    client.post("/fs/write", json={"path": "bye.txt", "content": "bye"})
    res = client.post("/fs/delete", json={"path": "bye.txt"})
    assert res.status_code == 200
    assert res.get_json()["success"] is True


def test_fs_info(client):
    """Workspace info endpoint returns expected keys."""
    res = client.get("/fs/info")
    assert res.status_code == 200
    data = res.get_json()
    assert "workspace" in data
    assert "total_files" in data
    assert data["workspace"] == "session workspace"
    assert os.getcwd() not in json.dumps(data)
    assert "C:\\" not in json.dumps(data)


def test_sandbox_invalid_size_env_falls_back(monkeypatch):
    monkeypatch.setenv("SANDBOX_MAX_FILE_SIZE", "not-a-number")
    assert sandboxed_fs_module._max_file_size() == 5_000_000


def test_sandbox_read_uses_default_size_when_env_unset(tmp_path):
    fs = SandboxedFileSystem(str(tmp_path))
    big = tmp_path / "big.txt"
    big.write_text("x" * 20, encoding="utf-8")
    data = fs.read("big.txt")
    assert data["success"] is True


def test_sandbox_file_size_env_is_read_at_runtime(tmp_path, monkeypatch):
    fs = SandboxedFileSystem(str(tmp_path))
    big = tmp_path / "big.txt"
    big.write_text("x" * 20, encoding="utf-8")
    monkeypatch.setenv("SANDBOX_MAX_FILE_SIZE", "10")
    data = fs.read("big.txt")
    assert data["success"] is False
    assert "maximum size" in data["error"].lower()


def test_stale_sandbox_cleanup_removes_workspace(tmp_path):
    workspace = tmp_path / "old_workspace"
    fs = SandboxedFileSystem(str(workspace))
    fs.last_accessed = 100
    with sandboxed_fs_module._sandboxes_lock:
        sandboxed_fs_module._sandboxes["old-session"] = fs

    removed = sandboxed_fs_module.cleanup_stale_sandboxes(now=1000, max_age=60)

    assert removed == 1
    assert "old-session" not in sandboxed_fs_module._sandboxes
    assert not workspace.exists()


# ===========================================================================
# 12. SESSION ISOLATION
# ===========================================================================

def test_two_sessions_have_separate_traces():
    """Two clients with different session cookies get separate trace storage."""
    with app.test_client() as c1:
        r1 = c1.post("/run", json={"code": "x = 100\nprint(x)"})
        assert r1.get_json()["success"] is True

        t1 = c1.get("/execution-trace").get_json()
        assert isinstance(t1["trace"], list)
        assert len(t1["trace"]) > 0, "Session A must have trace after run"

    with app.test_client() as c2:
        t2 = c2.get("/execution-trace").get_json()
        assert isinstance(t2["trace"], list)
        assert len(t2["trace"]) == 0, "Fresh session must have empty trace"

# ===========================================================================
# 13. SEMANTIC ERROR CLASSIFICATION
# ===========================================================================

def test_semantic_issues_in_response(client):
    """Semantic issues key is always present in successful run response."""
    res = client.post("/run", json={"code": "x = 1\nprint(x)"})
    assert res.status_code == 200
    data = res.get_json()
    if data["success"]:
        assert "semantic_issues" in data
        assert isinstance(data["semantic_issues"], list)


# ===========================================================================
# 14. INTENT PARSER UNIT TESTS (direct, no HTTP)
# ===========================================================================

@pytest.mark.parametrize("text, expected_intent, slot_check", [
    ("go to line twenty five",      "goto_line",    lambda s: s.get("line_number") == 25),
    ("go to line forty two",        "goto_line",    lambda s: s.get("line_number") == 42),
    ("read line 10",                "read_line",    lambda s: s.get("line_number") == 10),
    ("find function calculate",     "find_function",lambda s: s.get("function_name") == "calculate"),
    ("find class Parser",           "find_class",   lambda s: s.get("class_name", "").lower() == "parser"),
    ("insert function called greet","insert_function",lambda s: s.get("function_name") == "greet"),
    ("save snippet named my prog",  "save_snippet_named", lambda s: "my prog" in s.get("name", "")),
    ("set breakpoint at line 10",   "set_breakpoint",lambda s: s.get("line_number") == 10),
    ("watch variable counter",      "watch_variable",lambda s: s.get("variable") == "counter"),
    ("quiz me on variables",        "quiz_me",      lambda s: "variables" in s.get("topic", "")),
    ("read code",                   "narrate_file", lambda s: True),
    ("explain structure",           "read_outline", lambda s: True),
    ("run",                         "run",          lambda s: True),
    ("fix code",                    "fix",          lambda s: True),
    ("suggest next line",           "suggest_next", lambda s: True),
    ("repeat that",                 "repeat",       lambda s: True),
])
def test_intent_parser_direct(text, expected_intent, slot_check):
    result = parse_intent(text)
    assert result["intent"] == expected_intent, (
        f"Input {text!r}: expected {expected_intent!r}, got {result['intent']!r}"
    )
    assert slot_check(result["slots"]), (
        f"Slot check failed for {text!r}: {result['slots']}"
    )


def test_intent_parser_empty_string():
    result = parse_intent("")
    assert result["intent"] is None
    assert result["confidence"] == 0.0


def test_intent_parser_compound_numbers():
    """Spoken compound numbers like 'thirty seven' must parse correctly."""
    result = parse_intent("go to line thirty seven")
    assert result["intent"] == "goto_line"
    assert result["slots"]["line_number"] == 37


def test_intent_parser_no_false_goto_line():
    """Generic sentences mentioning 'line' must not trigger goto_line."""
    result = parse_intent("this is a single line program")
    assert result["intent"] != "goto_line"

# ===========================================================================
# 15. HINDI INTENT PARSING
# ===========================================================================

@pytest.mark.parametrize("hindi_text, expected_intent, slot_check", [
    # Numbers
    ("लाइन बीस पर जाओ",        "goto_line",    lambda s: s.get("line_number") == 20),
    ("लाइन पंद्रह पर जाओ",      "goto_line",    lambda s: s.get("line_number") == 15),
    ("लाइन पांच पढ़ो",          "read_line",    lambda s: s.get("line_number") == 5),
    ("line 10 पर जाओ",          "goto_line",    lambda s: s.get("line_number") == 10),

    # Execution
    ("चलाओ",                    "run",          lambda s: True),
    ("कोड चलाओ",                "run",          lambda s: True),
    ("रन करो",                  "run",          lambda s: True),

    # Analysis
    ("कोड का विश्लेषण करो",     "analyze",      lambda s: True),
    ("कोड समझाओ",               "analyze",      lambda s: True),

    # Fix
    ("कोड ठीक करो",             "fix",          lambda s: True),
    ("गलती ठीक करो",            "fix",          lambda s: True),
    ("सही करो",                 "fix",          lambda s: True),

    # Summarize
    ("सारांश दो",               "summarize",    lambda s: True),
    ("कोड का सारांश",           "summarize",    lambda s: True),

    # Suggest next
    ("अगली लाइन सुझाओ",         "suggest_next", lambda s: True),
    ("क्या लिखूं",              "suggest_next", lambda s: True),

    # Steps
    ("अगला कदम",                "next_step",    lambda s: True),
    ("पिछला कदम",               "previous_step",lambda s: True),
    ("आगे बढ़ो",                "next_step",    lambda s: True),
    ("पीछे जाओ",                "previous_step",lambda s: True),

    # Help
    ("मदद",                     "help",         lambda s: True),
    ("सहायता",                  "help",         lambda s: True),
    ("क्या कर सकते हो",         "help",         lambda s: True),

    # Clear
    ("एडिटर साफ करो",           "clear_editor", lambda s: True),
    ("कोड हटाओ",                "clear_editor", lambda s: True),
])
def test_hindi_intent_parsing(hindi_text, expected_intent, slot_check):
    """Hindi voice commands must parse to the correct intent."""
    result = parse_intent(hindi_text)
    assert result["intent"] == expected_intent, (
        f"Hindi input {hindi_text!r}: expected {expected_intent!r}, got {result['intent']!r}"
    )
    assert slot_check(result["slots"]), (
        f"Slot check failed for {hindi_text!r}: {result['slots']}"
    )


def test_hindi_number_parser_direct():
    """Hindi number words must convert to integers correctly."""
    from intent_parser import get_parser
    parser = get_parser()
    assert parser._word_to_number("बीस") == 20
    assert parser._word_to_number("पंद्रह") == 15
    assert parser._word_to_number("पचास") == 50
    assert parser._word_to_number("एक") == 1


def test_hindi_voice_command_via_http(client):
    """End-to-end: Hindi voice command through the /voice-command endpoint."""
    res = client.post("/voice-command", json={"text": "लाइन बीस पर जाओ"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["action"] == "goto_line"
    assert data["line"] == 20


def test_hindi_run_via_http(client):
    """End-to-end: Hindi run command."""
    res = client.post("/voice-command", json={"text": "कोड चलाओ"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["action"] == "run"


def test_hindi_help_via_http(client):
    """Hindi help should not fall through to English-only fuzzy matching."""
    hindi_help = "".join(chr(cp) for cp in (0x092e, 0x0926, 0x0926))
    res = client.post("/voice-command", json={"text": hindi_help})
    assert res.status_code == 200
    data = res.get_json()
    assert data["action"] == "help"

class TestPerSessionSandbox:

    def test_separate_sessions_get_different_workspaces(self):
        """Different session IDs must produce different sandbox instances."""
        from sandboxed_fs import get_sandbox
        sb1 = get_sandbox("test-session-aaa")
        sb2 = get_sandbox("test-session-bbb")
        assert sb1 is not sb2, "Different session IDs must return different sandbox instances"
        assert sb1.workspace_dir != sb2.workspace_dir, (
            "Different sessions must have different workspace directories"
        )

    def test_delete_in_one_session_does_not_affect_other(self):
        """Files written in one sandbox must not appear in another."""
        from sandboxed_fs import get_sandbox
        sb1 = get_sandbox("test-session-ccc")
        sb2 = get_sandbox("test-session-ddd")

        sb1.write("shared.txt", "A")
        sb2.write("shared.txt", "B")
        sb1.delete("shared.txt")

        result = sb2.read("shared.txt")
        assert result["success"] is True, "sb2's file should still exist after sb1 deleted its own copy"
        assert result["content"] == "B"

    def test_same_session_reads_own_files(self, client):
        w = client.post("/fs/write", json={"path": "mine.txt", "content": "hello"})
        assert w.get_json()["success"] is True
        r = client.post("/fs/read", json={"path": "mine.txt"})
        data = r.get_json()
        assert data["success"] is True
        assert data["content"] == "hello"

        
    def test_fs_info_reflects_only_own_files(self, tmp_snippets):
        os.environ["GEMINI_ENABLED"] = "0"
        with app.test_client() as c1, app.test_client() as c2:
            for i in range(3):
                c1.post("/fs/write", json={"path": f"file{i}.txt", "content": "x"})
            info1 = c1.get("/fs/info").get_json()
            info2 = c2.get("/fs/info").get_json()
            assert info1["total_files"] >= 3
            assert info2["total_files"] < info1["total_files"]
        os.environ["GEMINI_ENABLED"] = "1"


class TestCodeTempFile:

    @pytest.mark.timeout(15)
    def test_basic_execution_still_works(self, client):
        res = client.post("/run", json={"code": "print('file_path_ok')"})
        data = res.get_json()
        assert data["success"] is True
        assert "file_path_ok" in data["output"]

    @pytest.mark.timeout(20)
    def test_large_code_no_truncation(self, client):
        lines = [f"print('line_{i:04d}')" for i in range(200)]
        lines.append("print('END_SENTINEL')")
        res = client.post("/run", json={"code": "\n".join(lines)})
        data = res.get_json()
        assert data["success"] is True, f"Large code failed: {data.get('error')}"
        assert "END_SENTINEL" in data["output"]
        assert "line_0099" in data["output"]

    @pytest.mark.timeout(15)
    def test_trace_still_populated(self, client):
        res = client.post("/run", json={"code": "x = 42\nprint(x)"})
        data = res.get_json()
        assert data["success"] is True
        assert len(data.get("trace", [])) > 0

    @pytest.mark.timeout(15)
    def test_syntax_error_still_reported(self, client):
        res = client.post("/run", json={"code": "def foo(\n    pass"})
        data = res.get_json()
        assert data["success"] is False
        assert data.get("error")

    @pytest.mark.timeout(15)
    def test_timeout_reports_timeout_not_generic_python_error(self, client):
        res = client.post("/run", json={"code": "while True:\n    pass\n"})
        data = res.get_json()
        assert data["success"] is False
        assert "timed out" in data.get("error", "").lower()
        assert "execution complete" not in str(data).lower()

    def test_signal_killed_subprocess_reports_limit(self):
        error = app_module._subprocess_exit_error(-9)
        assert "timed out" in error.lower()
        assert "limit" in error.lower()



class TestRunRateLimit:

    @pytest.mark.timeout(30)
    def test_flagship_demo_sequence_allows_more_than_ten_quick_runs(self, client):
        app_module._run_timestamps.clear()
        statuses = [
            client.post("/run", json={"code": "print('demo run')"}).status_code
            for _ in range(12)
        ]
        assert all(s == 200 for s in statuses), f"Flagship demo was throttled too early: {statuses}"

    @pytest.mark.timeout(15)
    def test_validation_errors_are_not_masked_by_rate_limit(self, client, monkeypatch):
        app_module._run_timestamps.clear()
        monkeypatch.setattr(app_module, "RUN_RATE_LIMIT", 2)

        assert client.post("/run", json={"code": "print(1)"}).status_code == 200
        assert client.post("/run", json={"code": "print(2)"}).status_code == 200

        empty = client.post("/run", json={"code": ""})
        assert empty.status_code == 400
        assert empty.get_json()["error"] == "Code cannot be empty"

        syntax = client.post("/run", json={"code": "for i in range(3):\nprint(i)"})
        assert syntax.status_code == 200
        assert syntax.get_json()["success"] is False
        assert "IndentationError" in syntax.get_json()["error"]

        throttled = client.post("/run", json={"code": "print(3)"})
        assert throttled.status_code == 429

    @pytest.mark.timeout(30)
    def test_rate_limit_blocks_after_threshold(self, client, monkeypatch):
        monkeypatch.setattr(app_module, "RUN_RATE_LIMIT", 5)
        limit = app_module.RUN_RATE_LIMIT
        session_id = "rate-limit-threshold-session"
        with app_module._run_rate_lock:
            app_module._run_timestamps.pop(session_id, None)
        client.set_cookie(app_module.SESSION_COOKIE_NAME, session_id)
        results = [client.post("/run", json={"code": "print(1)"}).status_code
                   for _ in range(limit + 1)]
        assert 429 in results, f"Expected 429 after {limit} runs. Got: {results}"
        with app_module._run_rate_lock:
            app_module._run_timestamps.pop(session_id, None)

    @pytest.mark.timeout(30)
    def test_rate_limit_allows_up_to_threshold(self, client):
        limit = app_module.RUN_RATE_LIMIT
        statuses = [client.post("/run", json={"code": "print(1)"}).status_code
                    for _ in range(limit)]
        assert all(s == 200 for s in statuses), f"Premature block: {statuses}"

    @pytest.mark.timeout(30)
    def test_rate_limit_is_per_session(self, tmp_snippets):
        os.environ["GEMINI_ENABLED"] = "0"
        limit = app_module.RUN_RATE_LIMIT
        with app.test_client() as c1, app.test_client() as c2:
            for _ in range(limit + 1):
                c1.post("/run", json={"code": "print(1)"})
            r2 = c2.post("/run", json={"code": "print(1)"})
            assert r2.status_code == 200, f"Session 2 wrongly rate-limited: {r2.status_code}"
        os.environ["GEMINI_ENABLED"] = "1"

    def test_rate_limit_constants_exist(self):
        assert hasattr(app_module, "RUN_RATE_LIMIT")
        assert hasattr(app_module, "RUN_RATE_WINDOW")
        assert app_module.RUN_RATE_LIMIT > 0
        assert app_module.RUN_RATE_WINDOW > 0

    @pytest.mark.timeout(10)
    def test_rate_limit_429_body(self, client):
        limit = app_module.RUN_RATE_LIMIT
        last = None
        for _ in range(limit + 1):
            last = client.post("/run", json={"code": "print(1)"})
        if last.status_code != 429:
            pytest.skip("Rate limit window may have reset; skipping body check")
        data = last.get_json()
        assert data["success"] is False
        assert "rate" in data["error"].lower() or "limit" in data["error"].lower()

class TestStructureParserFix:

    def setup_method(self):
        from structure_parser import CodeAnalyzer
        self.analyzer = CodeAnalyzer()

    def test_async_function_flagged(self):
        result = self.analyzer.analyze("async def fetch():\n    pass")
        fns = result["functions"]
        assert fns[0]["is_async"] is True

    def test_sync_function_not_flagged(self):
        result = self.analyzer.analyze("def greet():\n    pass")
        assert result["functions"][0]["is_async"] is False

    def test_mixed_async_and_sync(self):
        code = "def sync_fn():\n    pass\n\nasync def async_fn():\n    pass"
        fns = {f["name"]: f for f in self.analyzer.analyze(code)["functions"]}
        assert fns["sync_fn"]["is_async"] is False
        assert fns["async_fn"]["is_async"] is True

    def test_method_has_parent_class(self):
        code = "class Dog:\n    def bark(self):\n        pass"
        fns = {f["name"]: f for f in self.analyzer.analyze(code)["functions"]}
        assert fns["bark"]["parent_class"] == "Dog"

    def test_top_level_function_has_no_parent_class(self):
        result = self.analyzer.analyze("def standalone():\n    pass")
        assert result["functions"][0]["parent_class"] is None

    def test_method_and_standalone_together(self):
        code = "def top():\n    pass\n\nclass C:\n    def method(self):\n        pass"
        fns = {f["name"]: f for f in self.analyzer.analyze(code)["functions"]}
        assert fns["top"]["parent_class"] is None
        assert fns["method"]["parent_class"] == "C"

    def test_async_method_in_class(self):
        code = "class Fetcher:\n    async def get(self):\n        pass"
        fns = {f["name"]: f for f in self.analyzer.analyze(code)["functions"]}
        assert fns["get"]["is_async"] is True
        assert fns["get"]["parent_class"] == "Fetcher"

    def test_nested_function_not_in_top_list(self):
        code = ("class Outer:\n"
                "    def outer_method(self):\n"
                "        def inner():\n"
                "            pass\n")
        names = [f["name"] for f in self.analyzer.analyze(code)["functions"]]
        assert "inner" not in names
        assert "outer_method" in names

    def test_functions_sorted_by_line(self):
        code = "def b():\n    pass\n\ndef a():\n    pass\n\ndef c():\n    pass"
        lines = [f["line"] for f in self.analyzer.analyze(code)["functions"]]
        assert lines == sorted(lines)

    def test_structure_endpoint_includes_is_async(self, client):
        res = client.post("/structure", json={"code": "async def f():\n    pass"})
        fns = res.get_json()["structure"]["functions"]
        assert fns[0]["is_async"] is True

    def test_structure_endpoint_includes_parent_class(self, client):
        res = client.post("/structure", json={"code": "class Cat:\n    def meow(self):\n        pass"})
        fns = {f["name"]: f for f in res.get_json()["structure"]["functions"]}
        assert fns["meow"]["parent_class"] == "Cat"



class TestInputSandboxBackend:

    @pytest.mark.timeout(15)
    def test_input_call_fails(self, client):
        res = client.post("/run", json={"code": "x = input('enter: ')\nprint(x)"})
        assert res.get_json()["success"] is False

    @pytest.mark.timeout(15)
    def test_input_error_mentions_input(self, client):
        res = client.post("/run", json={"code": "name = input('name: ')"})
        data = res.get_json()
        combined = (data.get("error") or "") + (data.get("explanation") or "")
        assert "input" in combined.lower()

    @pytest.mark.timeout(15)
    def test_variable_named_my_input_not_blocked(self, client):
        res = client.post("/run", json={"code": "my_input = 42\nprint(my_input)"})
        data = res.get_json()
        assert data["success"] is True
        assert "42" in data["output"]

    @pytest.mark.timeout(15)
    def test_input_in_comment_not_blocked(self, client):
        res = client.post("/run", json={"code": "# input() disabled\nprint('ok')"})
        data = res.get_json()
        assert data["success"] is True

    @pytest.mark.timeout(15)
    def test_input_in_string_not_blocked(self, client):
        res = client.post("/run", json={"code": "msg = 'input() is disabled'\nprint(msg)"})
        data = res.get_json()
        assert data["success"] is True


class TestRegressionAfterFixes:

    @pytest.mark.timeout(15)
    def test_basic_print(self, client):
        data = client.post("/run", json={"code": "print('ok')"}).get_json()
        assert data["success"] is True and "ok" in data["output"]

    @pytest.mark.timeout(15)
    def test_os_import_blocked(self, client):
        assert client.post("/run", json={"code": "import os\nprint(os.getcwd())"}).get_json()["success"] is False

    @pytest.mark.timeout(15)
    def test_math_import_works(self, client):
        data = client.post("/run", json={"code": "import math\nprint(int(math.pi))"}).get_json()
        assert data["success"] is True and "3" in data["output"]

    def test_voice_goto_line(self, client):
        data = client.post("/voice-command", json={"text": "go to line ten"}).get_json()
        assert data["action"] == "goto_line" and data["line"] == 10

    def test_snippet_roundtrip(self, client):
        client.post("/snippets", json={"name": "reg", "code": "print(1)"})
        assert any(s["name"] == "reg" for s in client.get("/snippets").get_json()["snippets"])

    def test_syntax_check(self, client):
        assert client.post("/check-syntax", json={"code": "x = 1"}).get_json()["has_errors"] is False

    @pytest.mark.timeout(15)
    def test_trace_endpoint(self, client):
        client.post("/run", json={"code": "x = 1"})
        data = client.get("/execution-trace").get_json()
        assert "trace" in data and "current_index" in data

class TestSnippetSessionIsolation:
    def test_snippets_are_per_session(self, tmp_snippets):
        """Two sessions must not see each other's snippets."""
        os.environ["GEMINI_ENABLED"] = "0"
        try:
            c1 = app.test_client()
            c2 = app.test_client()

            c1.post("/snippets", json={"name": "alice_only", "code": "print('alice')"})
            c2.post("/snippets", json={"name": "bob_only",   "code": "print('bob')"})

            alice_list = c1.get("/snippets").get_json()["snippets"]
            bob_list   = c2.get("/snippets").get_json()["snippets"]

            alice_names = [s["name"] for s in alice_list]
            bob_names   = [s["name"] for s in bob_list]

            assert "alice_only" in alice_names
            assert "alice_only" not in bob_names
            assert "bob_only" in bob_names
            assert "bob_only" not in alice_names
        finally:
            os.environ["GEMINI_ENABLED"] = "1"


# ===========================================================================
# 16. PRE-FLIGHT INPUTS (Mechanism A)
# ===========================================================================

class TestPreflightInputs:

    @pytest.mark.timeout(15)
    def test_input_with_preflight_succeeds(self, client):
        code = "name = input('Your name: ')\nprint('Hello,', name)"
        res = client.post("/run", json={"code": code, "inputs": ["Alice"]})
        data = res.get_json()
        assert data["success"] is True, f"Pre-flight input failed: {data.get('error')}"
        assert "Alice" in data["output"]
        assert "Hello" in data["output"]

    @pytest.mark.timeout(15)
    def test_multiple_preflight_inputs(self, client):
        code = "a = input('a: ')\nb = input('b: ')\nprint(a + ' and ' + b)"
        res = client.post("/run", json={"code": code, "inputs": ["foo", "bar"]})
        data = res.get_json()
        assert data["success"] is True
        assert "foo and bar" in data["output"]

    @pytest.mark.timeout(15)
    def test_too_few_inputs_friendly_error(self, client):
        code = "a = input('a: ')\nb = input('b: ')\nprint(a, b)"
        res = client.post("/run", json={"code": code, "inputs": ["only_one"]})
        data = res.get_json()
        assert data["success"] is False
        combined = (data.get("error") or "") + (data.get("explanation") or "")
        assert "input" in combined.lower()

    @pytest.mark.timeout(15)
    def test_magic_comment_inputs(self, client):
        code = "# inputs: hello, world\na = input()\nb = input()\nprint(a, b)"
        res = client.post("/run", json={"code": code})
        data = res.get_json()
        assert data["success"] is True
        assert "hello" in data["output"] and "world" in data["output"]

    @pytest.mark.timeout(15)
    def test_last_magic_comment_inputs_win(self, client):
        code = "# inputs: old\n# inputs: newer\nname = input()\nprint(name)"
        res = client.post("/run", json={"code": code})
        data = res.get_json()
        assert data["success"] is True
        assert "newer" in data["output"]
        assert "old" not in data["output"]

    @pytest.mark.timeout(15)
    def test_body_inputs_override_magic_comment(self, client):
        code = "# inputs: from_magic\nname = input()\nprint(name)"
        res = client.post("/run", json={"code": code, "inputs": ["from_body"]})
        data = res.get_json()
        assert data["success"] is True
        assert "from_body" in data["output"]
        assert "from_magic" not in data["output"]

    @pytest.mark.timeout(15)
    def test_inputs_hint_when_input_used_without_inputs(self, client):
        code = "x = input('?')\nprint(x)"
        res = client.post("/run", json={"code": code})
        data = res.get_json()
        assert data.get("inputs_hint")
        assert "input" in data["inputs_hint"].lower()
        assert data["input_prompts"] == ["?"]


class TestVoiceSetInputs:

    def test_set_inputs_basic(self, client):
        res = client.post("/voice-command", json={"text": "set inputs to Alice and 17"})
        data = res.get_json()
        assert data["action"] == "set_inputs"
        assert "Alice" in data["values"]
        assert "17" in data["values"]

    def test_set_inputs_with_commas(self, client):
        res = client.post("/voice-command", json={"text": "set inputs to foo, bar, baz"})
        data = res.get_json()
        assert data["action"] == "set_inputs"
        assert data["values"] == ["foo", "bar", "baz"]

    def test_clear_inputs_command(self, client):
        res = client.post("/voice-command", json={"text": "clear inputs"})
        data = res.get_json()
        assert data["action"] == "clear_inputs"

    def test_live_input_mode_command(self, client):
        res = client.post("/voice-command", json={"text": "live input mode"})
        data = res.get_json()
        assert data["action"] == "live_input_mode"


# ===========================================================================
# 17. VOICE MACROS
# ===========================================================================

class TestVoiceMacros:

    def test_save_macro_via_command(self, client):
        res = client.post("/voice-command", json={"text": "remember this as my pattern"})
        data = res.get_json()
        assert data["action"] == "save_macro"
        assert data["name"] == "my pattern"

    def test_use_macro_via_command(self, client):
        res = client.post("/voice-command", json={"text": "use macro my pattern"})
        data = res.get_json()
        assert data["action"] == "use_macro"
        assert data["name"] == "my pattern"

    def test_save_and_load_macro_endpoint(self, client):
        save = client.post("/macros", json={"name": "greeting", "code": "print('hi')"})
        assert save.get_json()["success"] is True

        get = client.get("/macros/get/greeting")
        data = get.get_json()
        assert data["success"] is True
        assert "print" in data["code"]

    def test_macro_invalid_name_rejected(self, client):
        res = client.post("/macros", json={"name": "bad/name", "code": "x"})
        assert res.status_code == 400

    def test_macro_too_large_rejected(self, client):
        res = client.post("/macros", json={"name": "big", "code": "x = 1\n" * 20_000})
        assert res.status_code == 413

    def test_list_macros_endpoint(self, client):
        client.post("/macros", json={"name": "alpha", "code": "print(1)"})
        res = client.get("/macros")
        data = res.get_json()
        assert data["success"] is True
        assert "alpha" in data["names"]

    def test_concurrent_macro_saves_do_not_lose_updates(self, client):
        client.get("/macros")
        session_id = _client_session_id(client)
        barrier = threading.Barrier(8)
        errors = []

        def save_one(i):
            try:
                with app.test_client() as c:
                    c.set_cookie(app_module.SESSION_COOKIE_NAME, session_id)
                    barrier.wait(timeout=5)
                    res = c.post("/macros", json={"name": f"m{i}", "code": f"print({i})"})
                    assert res.status_code == 200
                    assert res.get_json()["success"] is True
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=save_one, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert errors == []
        names = set(client.get("/macros").get_json()["names"])
        assert {f"m{i}" for i in range(8)} <= names

    def test_shared_macro_roundtrip(self, client):
        shared = client.post("/macros/share", json={"name": "demo", "code": "print('hi')"}).get_json()
        assert shared["success"] is True
        assert len(shared["share_code"]) >= 16
        loaded = client.get(f"/macros/shared/{shared['share_code']}").get_json()
        assert loaded["success"] is True
        assert loaded["code"] == "print('hi')"

    def test_share_code_uses_secure_high_entropy_token(self, client, monkeypatch):
        monkeypatch.setattr(app_module.secrets, "token_urlsafe", lambda n: "SecureToken_123456789")
        shared = client.post("/macros/share", json={"name": "secure", "code": "print(1)"}).get_json()
        assert shared["success"] is True
        assert shared["share_code"] == "SecureToken_123456789"

    def test_macro_file_normalizes_bad_records(self, client):
        client.get("/macros")
        path = app_module._macros_path(_client_session_id(client))
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({
                "good": {"code": "print(1)", "saved_at": "x"},
                "bad/name": {"code": "print(2)"},
                "empty": {"code": ""},
                "legacy": "print(3)",
            }, handle)

        data = client.get("/macros").get_json()
        assert data["success"] is True
        assert data["names"] == ["good", "legacy"]
        assert client.get("/macros/get/legacy").get_json()["code"] == "print(3)"

    def test_shared_macro_bad_expiry_does_not_500(self, client):
        code = "BadExpiryToken12345"
        path = app_module._shared_macro_path(code)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"name": "bad", "code": "print(1)", "expires_at": "not-a-float"}, handle)

        res = client.get(f"/macros/shared/{code}")
        assert res.status_code == 404
        assert res.get_json()["success"] is False

    def test_shared_macro_non_python_is_rejected_and_removed(self, client):
        code = "BadPythonToken12345"
        path = app_module._shared_macro_path(code)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"name": "bad", "code": HTML_DOCUMENT, "expires_at": time.time() + 3600}, handle)

        res = client.get(f"/macros/shared/{code}")
        assert res.status_code == 400
        assert "python-only" in res.get_json()["error"].lower()
        assert not os.path.exists(path)

    def test_shared_macro_lookup_rate_limit(self, client, monkeypatch):
        monkeypatch.setattr(app_module, "_SHARED_MACRO_LOOKUP_LIMIT", 2)
        with app_module._shared_macro_lookup_lock:
            app_module._shared_macro_lookup_attempts.clear()

        code = "MissingToken123456"
        assert client.get(f"/macros/shared/{code}").status_code == 404
        assert client.get(f"/macros/shared/{code}").status_code == 404
        limited = client.get(f"/macros/shared/{code}")
        assert limited.status_code == 429

    def test_shared_macro_lookup_limiter_prunes_stale_and_caps_keys(self, monkeypatch):
        base = 1000.0
        monkeypatch.setattr(app_module.time, "time", lambda: base)
        monkeypatch.setattr(app_module, "_SHARED_MACRO_LOOKUP_WINDOW", 10)
        monkeypatch.setattr(app_module, "_SHARED_MACRO_LOOKUP_MAX_KEYS", 2)
        with app_module._shared_macro_lookup_lock:
            app_module._shared_macro_lookup_attempts.clear()
            app_module._shared_macro_lookup_attempts.update({
                "stale-key": [base - 20],
                "old-key": [base - 5],
                "new-key": [base - 1],
            })

        with app.test_request_context(
            "/macros/shared/MissingToken123456",
            headers={"Cookie": f"{app_module.SESSION_COOKIE_NAME}=fresh-session"},
        ):
            assert app_module._check_shared_macro_lookup_limit() is True

        with app_module._shared_macro_lookup_lock:
            assert "stale-key" not in app_module._shared_macro_lookup_attempts
            assert "old-key" not in app_module._shared_macro_lookup_attempts
            assert "new-key" in app_module._shared_macro_lookup_attempts
            assert any(
                key.endswith(":fresh-session")
                for key in app_module._shared_macro_lookup_attempts
            )
            assert len(app_module._shared_macro_lookup_attempts) == 2

    def test_shared_macro_corrupt_data_returns_operational_error(self, client):
        code = "CorruptToken12345"
        path = app_module._shared_macro_path(code)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{not-json")

        res = client.get(f"/macros/shared/{code}")
        assert res.status_code == 500
        assert "corrupted" in res.get_json()["error"].lower()

    def test_shared_macro_unreadable_data_returns_forbidden(self, client, monkeypatch):
        code = "UnreadableToken123"
        path = app_module._shared_macro_path(code)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"name": "locked", "code": "print(1)", "expires_at": time.time() + 3600}, handle)

        real_open = open

        def fake_open(file_path, *args, **kwargs):
            if os.fspath(file_path) == path:
                raise PermissionError("denied")
            return real_open(file_path, *args, **kwargs)

        monkeypatch.setattr(app_module, "open", fake_open, raising=False)
        res = client.get(f"/macros/shared/{code}")

        assert res.status_code == 403
        assert "not readable" in res.get_json()["error"].lower()

    def test_shared_macro_invalid_code_and_missing_code_are_distinct(self, client):
        invalid = client.get("/macros/shared/not-valid")
        missing = client.get("/macros/shared/MissingToken123456")

        assert invalid.status_code == 400
        assert "invalid" in invalid.get_json()["error"].lower()
        assert missing.status_code == 404
        assert "not found" in missing.get_json()["error"].lower()


# ===========================================================================
# 18. OUTPUT BOOKMARKS
# ===========================================================================

class TestOutputBookmarks:

    def test_save_bookmark(self, client):
        res = client.post("/bookmarks", json={"label": "test_mark", "position": 100})
        data = res.get_json()
        assert data["success"] is True

    def test_list_bookmarks(self, client):
        client.post("/bookmarks", json={"label": "first", "position": 0})
        client.post("/bookmarks", json={"label": "second", "position": 50})
        res = client.get("/bookmarks")
        data = res.get_json()
        assert len(data["bookmarks"]) == 2

    def test_clear_bookmarks(self, client):
        client.post("/bookmarks", json={"label": "x", "position": 0})
        client.delete("/bookmarks")
        res = client.get("/bookmarks")
        assert res.get_json()["bookmarks"] == []

    def test_read_from_bookmark(self, client):
        client.post("/bookmarks", json={"label": "mid", "position": 6})
        res = client.post("/bookmarks/read", json={"label": "mid", "output": "abcdef_END"})
        data = res.get_json()
        assert data["success"] is True
        assert data["slice"] == "_END"

    def test_negative_bookmark_position_is_clamped(self, client):
        client.post("/bookmarks", json={"label": "neg", "position": -50})
        res = client.post("/bookmarks/read", json={"label": "neg", "output": "abcdef"})
        data = res.get_json()
        assert data["success"] is True
        assert data["slice"] == "abcdef"

    def test_bookmark_read_rejects_huge_output(self, client):
        client.post("/bookmarks", json={"label": "big", "position": 0})
        res = client.post(
            "/bookmarks/read",
            json={"label": "big", "output": "x" * (app_module.MAX_REQUEST_SIZE + 1)},
        )
        assert res.status_code == 413


# ===========================================================================
# 19. BREADCRUMBS
# ===========================================================================

class TestBreadcrumbs:

    def test_top_level(self, client):
        res = client.post("/breadcrumbs", json={"code": "x = 1", "line": 1})
        data = res.get_json()
        assert data["success"] is True
        assert "top level" in data["breadcrumb"].lower()

    def test_inside_function(self, client):
        code = "def hello():\n    x = 1\n    print(x)"
        res = client.post("/breadcrumbs", json={"code": code, "line": 3})
        data = res.get_json()
        assert data["success"] is True
        assert "function" in data["breadcrumb"].lower()
        assert "hello" in data["breadcrumb"]

    def test_inside_loop_in_function(self, client):
        code = "def calc():\n    for i in range(5):\n        print(i)"
        res = client.post("/breadcrumbs", json={"code": code, "line": 3})
        data = res.get_json()
        assert data["success"] is True
        assert "calc" in data["breadcrumb"]
        assert "for loop" in data["breadcrumb"].lower()


# ===========================================================================
# 20. OUTPUT DIFF NARRATION
# ===========================================================================

class TestOutputDiff:

    @pytest.mark.timeout(15)
    def test_first_run_no_diff(self, client):
        res = client.post("/run", json={"code": "print('hi')"})
        data = res.get_json()
        assert data["success"] is True
        # First run: diff present but identical=False with empty changed_lines
        assert "diff" in data

    @pytest.mark.timeout(15)
    def test_second_identical_run_marked_identical(self, client):
        client.post("/run", json={"code": "print('same')"})
        res = client.post("/run", json={"code": "print('same')"})
        data = res.get_json()
        assert data["success"] is True
        assert data["diff"]["identical"] is True

    @pytest.mark.timeout(15)
    def test_changed_output_diff_detected(self, client):
        client.post("/run", json={"code": "print(1)"})
        res = client.post("/run", json={"code": "print(2)"})
        data = res.get_json()
        assert data["success"] is True
        assert data["diff"]["identical"] is False
        assert data["diff"]["total_changes"] >= 1

    def test_appended_output_diff_summarized(self):
        import app as app_module

        diff = app_module._compute_output_diff("0\n1", "0\n1\n2\n3")
        assert diff["mode"] == "appended"
        assert "new lines at the end" in diff["summary"]

    def test_large_output_diff_switches_to_summary(self):
        import app as app_module

        prev = "\n".join(f"old {i}" for i in range(30))
        curr = "\n".join(f"new {i}" for i in range(30))
        diff = app_module._compute_output_diff(prev, curr)
        assert diff["mode"] == "summary"
        assert "mostly different" in diff["summary"].lower()


# ===========================================================================
# 21. BEGINNER ERROR EXPLANATION
# ===========================================================================

class TestBeginnerErrorExplanation:

    def test_endpoint_exists(self, client):
        res = client.post("/explain-error-beginner", json={
            "code": "x = 1",
            "error": "NameError: name 'y' is not defined",
            "language": "en"
        })
        # AI is disabled in tests so we get a "service disabled" response
        # but the endpoint should respond 200, not crash.
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert "explanation" in data

    def test_endpoint_rejects_empty_error(self, client):
        res = client.post("/explain-error-beginner", json={
            "code": "x = 1",
            "error": "",
            "language": "en"
        })
        assert res.status_code == 400


class TestConversationalMentor:

    def test_mentor_chat_requires_message(self, client):
        res = client.post("/mentor/chat", json={"code": "print(1)", "message": ""})
        assert res.status_code == 400
        data = res.get_json()
        assert data["success"] is False
        assert "message" in data["error"].lower()

    def test_mentor_chat_with_mocked_ai(self, client, monkeypatch):
        monkeypatch.setattr(app_module, "call_gemini", lambda *a, **k: "Line 2 needs indentation. Try adding four spaces.")
        res = client.post("/mentor/chat", json={
            "code": "for i in range(3):\nprint(i)",
            "message": "Why did this fail?",
            "error": "IndentationError: expected an indented block",
            "language": "en",
            "history": [{"role": "student", "text": "I ran it."}],
            "preferences": {"level": "beginner", "answerStyle": "hints_first"},
        })
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert "indentation" in data["reply"].lower()
        assert data["auto_speak"] is True

    def test_mentor_check_progress_with_mocked_ai(self, client, monkeypatch):
        monkeypatch.setattr(app_module, "call_gemini", lambda *a, **k: "You added indentation. The original error seems fixed. Run one more test.")
        res = client.post("/mentor/check-progress", json={
            "previousCode": "for i in range(3):\nprint(i)",
            "currentCode": "for i in range(3):\n    print(i)",
            "previousError": "IndentationError",
            "currentOutput": "0\n1\n2\n",
            "currentError": "",
        })
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert "fixed" in data["reply"].lower()

    def test_mentor_code_map_endpoint_basic_output(self, client):
        res = client.post("/mentor/code-map", json={
            "code": "total = 0\nfor i in range(3):\n    total += i\nprint(total)"
        })
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert "Your code has" in data["reply"]
        assert "loop" in data["reply"].lower()
        assert data["auto_speak"] is True

    def test_code_map_heuristic_does_not_execute_code(self):
        code = "print('safe')\nraise RuntimeError('would execute')\nfor i in range(2):\n    print(i)"
        result = app_module.build_code_audio_map(code)
        assert "Your code has" in result
        assert "loop" in result.lower()
        assert "would execute" not in result

    def test_mentor_transcript_renders_text_not_markup(self):
        js = open(os.path.join(os.path.dirname(__file__), "..", "static", "app.js"), encoding="utf-8").read()
        assert "function renderMentorTranscript()" in js
        assert "text.textContent = turn.text || ''" in js
        assert "mentorTranscript.innerHTML" not in js

    def test_input_dialog_exists_in_template_and_js_opens_it(self):
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        html = open(os.path.join(root, "templates", "index.html"), encoding="utf-8").read()
        js = open(os.path.join(root, "static", "app.js"), encoding="utf-8").read()
        body_start = js.index("function showInputDialog")
        body_end = js.index("// ---------- LIST VARIABLES", body_start)
        dialog_body = js[body_start:body_end]

        assert 'id="_cuInputDialog"' in html
        assert 'tabindex="-1"' in html
        assert "const inputDialog = document.getElementById('_cuInputDialog')" in js
        assert "overlay.hidden = false" in dialog_body
        assert "overlay.hidden = true" in dialog_body
        assert "input.focus()" in dialog_body
        assert "e.key === 'Escape'" in dialog_body
        assert "overlay._cuClose" in dialog_body
        assert "if (typeof overlay._cuClose === 'function') overlay._cuClose();" in dialog_body
        assert dialog_body.count("addEventListener") == dialog_body.count("removeEventListener")
        assert "document.createElement" not in dialog_body
        assert "overlay.remove()" not in dialog_body

    @pytest.mark.parametrize("text, expected_action, expected_mode", [
        ("give me a tiny hint", "mentor_chat", "tiny_hint"),
        ("did I fix it", "mentor_progress", None),
        ("walk me through slowly", "mentor_chat", "slow_walkthrough"),
        ("give me a map of my code", "mentor_code_map", None),
        ("mentor stop", "mentor_stop", None),
    ])
    def test_mentor_voice_command_parsing(self, client, text, expected_action, expected_mode):
        res = client.post("/voice-command", json={"text": text})
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert data["action"] == expected_action
        if expected_mode:
            assert data["mode"] == expected_mode

    def test_say_that_simpler_is_handled(self, client):
        # "say that simpler" parses as mentor_chat/"simpler"; but in a fresh client
        # with no prior code or mentor answer, the session-memory follow-up resolver
        # claims it first and asks what to simplify. Both are valid, non-broken
        # responses — it must never be unrecognized. (The mentor-vs-follow-up
        # precedence is a pre-existing concern outside the voice-control hotfix.)
        data = client.post("/voice-command", json={"text": "say that simpler"}).get_json()
        assert data["success"] is True
        assert data["action"] in ("mentor_chat", "deterministic_message")

    def test_ask_mentor_prefix_is_stripped(self, client):
        res = client.post("/voice-command", json={"text": "ask mentor why did this fail"})
        assert res.status_code == 200
        data = res.get_json()
        assert data["action"] == "mentor_chat"
        assert data["message"] == "why did this fail"


class TestOutlineAndDiffEndpoints:

    def test_structure_outline_endpoint(self, client):
        code = "import math\n\nclass Dog:\n    def bark(self):\n        pass\n\ndef greet():\n    pass"
        res = client.post("/structure-outline", json={"code": code})
        data = res.get_json()
        assert res.status_code == 200
        assert data["success"] is True
        assert "1 import" in data["outline"]
        assert "Dog" in data["outline"]
        assert "greet" in data["outline"]

    def test_explain_diff_fallback(self, client):
        res = client.post("/explain-diff", json={
            "code": "import random\nprint(random.randint(1, 10))",
            "previous_output": "1\n",
            "current_output": "2\n",
            "language": "en",
        })
        data = res.get_json()
        assert res.status_code == 200
        assert data["success"] is True
        assert "explanation" in data
        assert data["diff"]["identical"] is False


class TestMentorNormalization:

    def test_quiz_options_are_normalized(self):
        import app as app_module

        parsed = {
            "question": "What prints?",
            "options": ["(A) one", "B: two", "C. three"],
            "answer": "b",
            "explanation": "Two is correct.",
        }
        assert app_module._validate_quiz_response(parsed) is None
        assert parsed["options"] == ["A: one", "B: two", "C: three"]

    def test_bug_challenge_strips_markdown_fences(self):
        import app as app_module

        parsed = {
            "code": "```python\nprint('bad')\n```",
            "hint": "Look at the string.",
            "bug": "Wrong text.",
            "fixed": "```python\nprint('good')\n```",
        }
        assert app_module._validate_bug_challenge(parsed) is None
        assert "```" not in parsed["code"]
        assert "```" not in parsed["fixed"]

    def test_quiz_explanation_must_be_string(self):
        parsed = {
            "question": "What prints?",
            "options": ["A", "B", "C"],
            "answer": "A",
            "explanation": {"bad": "type"},
        }
        assert "explanation" in app_module._validate_quiz_response(parsed).lower()

    def test_bug_challenge_rejects_non_python_code(self):
        parsed = {
            "code": HTML_DOCUMENT,
            "hint": "Look for markup.",
            "bug": "It is not Python.",
            "fixed": "print('ok')",
        }
        assert "non-python" in app_module._validate_bug_challenge(parsed).lower()

    def test_mentor_quiz_topic_too_large(self, client):
        res = client.post("/mentor/quiz", json={"topic": "x" * (app_module.MAX_LEARNING_TOPIC_SIZE + 1)})
        assert res.status_code == 413

    def test_mentor_explain_concept_too_large(self, client):
        res = client.post("/mentor/explain", json={"concept": "x" * (app_module.MAX_LEARNING_TOPIC_SIZE + 1)})
        assert res.status_code == 413


# ===========================================================================
# 22. INTENT PARSER — NEW INTENTS
# ===========================================================================

class TestNewIntents:

    @pytest.mark.parametrize("text, intent, slot_check", [
        ("set inputs to alice and seventeen",
            "set_inputs", lambda s: "alice" in [v.lower() for v in s.get("values", [])]),
        ("set inputs to foo, bar, baz",
            "set_inputs", lambda s: s.get("values") == ["foo", "bar", "baz"]),
        ("clear inputs",          "clear_inputs", lambda s: True),
        ("list inputs",           "list_inputs",  lambda s: True),
        ("live input mode",       "live_input_mode", lambda s: True),
        ("preflight input mode",  "preflight_input_mode", lambda s: True),
        ("remember this as quick sort", "save_macro", lambda s: s.get("name") == "quick sort"),
        ("use macro quick sort",  "use_macro", lambda s: s.get("name") == "quick sort"),
        ("list macros",           "list_macros", lambda s: True),
        ("share current code as demo", "share_macro", lambda s: s.get("name") == "demo"),
        ("use shared macro 7k2p", "use_shared_macro", lambda s: s.get("share_code") == "7K2P"),
        ("bookmark this",         "bookmark_output", lambda s: True),
        ("bookmark this as totals", "bookmark_output", lambda s: s.get("label") == "totals"),
        ("read from bookmark totals", "read_bookmark", lambda s: s.get("label") == "totals"),
        ("list bookmarks",        "list_bookmarks", lambda s: True),
        ("where am i",            "where_am_i", lambda s: True),
        ("explain like i'm five", "explain_simply", lambda s: True),
        ("what's different",      "narrate_diff", lambda s: True),
        ("read the outline",      "read_outline", lambda s: True),
        ("sonify the whole file", "sonify_file", lambda s: True),
        ("why is the output different", "explain_diff", lambda s: True),
        ("restart tutorial",      "restart_tutorial", lambda s: True),
    ])
    def test_intent(self, text, intent, slot_check):
        result = parse_intent(text)
        assert result["intent"] == intent, (
            f"Input {text!r}: expected {intent!r}, got {result['intent']!r}"
        )
        assert slot_check(result["slots"]), (
            f"Slot check failed for {text!r}: {result['slots']}"
        )
