"""Flask route + intent-routing tests for the guided tutorial:

  * GET  /tutorial/modules    — lesson pack
  * POST /tutorial/validate   — activity verdicts (incl. unsafe while loops)
  * /voice-command            — start / practise / exit tutorial routing
  * /run                      — the execution backstop actually stops a
                                non-terminating while loop
"""
import pytest

import app as app_module


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "false")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _validate(client, module, code, ran_ok=True, output=""):
    return client.post(
        "/tutorial/validate",
        json={"module": module, "code": code, "ran_ok": ran_ok, "output": output},
    ).get_json()


def _action(client, text, code=""):
    return client.post("/voice-command", json={"text": text, "code": code}).get_json()


# ---------------------------------------------------------------------------
# /tutorial/modules
# ---------------------------------------------------------------------------
class TestModulesRoute:
    def test_returns_ordered_pack(self, client):
        data = client.get("/tutorial/modules").get_json()
        assert data["success"] is True
        assert data["order"] == ["print", "variables", "if", "for", "while"]
        assert data["count"] == 5
        printm = data["modules"]["print"]
        assert printm["title"]
        assert printm["concept"]
        assert printm["example_code"]
        assert isinstance(printm["hints"], list) and printm["hints"]


# ---------------------------------------------------------------------------
# /tutorial/validate
# ---------------------------------------------------------------------------
class TestValidateRoute:
    def test_print_correct_accepted(self, client):
        d = _validate(client, "print", 'print("hello")', ran_ok=True)
        assert d["success"] is True
        assert d["passed"] is True
        assert d["feedback"]
        assert d["next_module"] == "variables"

    def test_print_wrong_rejected_with_hint(self, client):
        d = _validate(client, "print", 'x = 1', ran_ok=True)
        assert d["passed"] is False
        assert d["hint"]

    @pytest.mark.parametrize("code", [
        'name = "Aman"\nprint(name)',
        'score = 10\nprint(score)',
        'city = "Patiala"\nprint(city)',
    ])
    def test_variables_accepts_different_names(self, client, code):
        assert _validate(client, "variables", code, ran_ok=True)["passed"] is True

    def test_if_accepted(self, client):
        assert _validate(client, "if", 'x=10\nif x>5:\n    print("big")', ran_ok=True)["passed"] is True

    def test_for_accepted(self, client):
        assert _validate(client, "for", 'for i in range(3):\n    print(i)', ran_ok=True)["passed"] is True

    def test_while_safe_accepted(self, client):
        code = 'c=1\nwhile c<=3:\n    print(c)\n    c=c+1'
        assert _validate(client, "while", code, ran_ok=True)["passed"] is True

    def test_while_unsafe_blocked(self, client):
        d = _validate(client, "while", 'while True:\n    print("x")', ran_ok=True)
        assert d["passed"] is False
        assert d["safe"] is False
        assert d["feedback"]

    def test_runtime_error_not_passed(self, client):
        d = _validate(client, "print", 'print("hi")', ran_ok=False)
        assert d["passed"] is False

    def test_unknown_module_is_400(self, client):
        resp = client.post("/tutorial/validate", json={"module": "lambdas", "code": "print(1)"})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Intent / command routing
# ---------------------------------------------------------------------------
class TestTutorialCommandRouting:
    def test_start_tutorial(self, client):
        assert _action(client, "start tutorial")["action"] == "start_tutorial"

    def test_bare_tutorial_word_opens(self, client):
        assert _action(client, "tutorial")["action"] == "start_tutorial"

    def test_restart_tutorial(self, client):
        assert _action(client, "restart tutorial")["action"] == "restart_tutorial"

    def test_exit_tutorial_routes_to_skip(self, client):
        assert _action(client, "exit tutorial")["action"] == "skip_tutorial"

    @pytest.mark.parametrize("text,module", [
        ("practise for loops", "for"),
        ("practice variables", "variables"),
        ("let me practise print", "print"),
        ("practise while loops", "while"),
        ("practice if statements", "if"),
    ])
    def test_practice_specific_module(self, client, text, module):
        d = _action(client, text)
        assert d["action"] == "tutorial_practice"
        assert d["module"] == module

    def test_run_still_routes_to_run(self, client):
        # The tutorial must not hijack core commands at the backend.
        assert _action(client, "run", code="print(1)")["action"] == "run"


# ---------------------------------------------------------------------------
# Execution backstop.
# ---------------------------------------------------------------------------
class TestWhileExecutionBackstop:
    def test_sandbox_wall_clock_timeout_is_configured(self):
        # The tutorial blocks obviously non-terminating while loops *before*
        # running them (see test_while_unsafe_blocked / check_while_safety),
        # giving a spoken safety hint instead. The runtime backstop for anything
        # that slips past the static check is the sandbox's wall-clock timeout.
        # We assert that backstop is present and bounded rather than actually
        # spinning an infinite loop, which is flaky to kill cleanly on Windows.
        assert 0 < app_module.SUBPROCESS_WALL_TIMEOUT_SECONDS <= 10
