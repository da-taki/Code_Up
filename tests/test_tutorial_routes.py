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


class TestTutorialCommandRouting:
    def test_start_tutorial(self, client):
        assert _action(client, "start tutorial")["action"] == "start_tutorial"

    def test_bare_tutorial_word_opens(self, client):
        assert _action(client, "tutorial")["action"] == "start_tutorial"

    @pytest.mark.parametrize("text", [
        "start tutorial", "open tutorial", "begin tutorial", "launch tutorial",
        "the tutorial", "go to the tutorial",
        "start the tutorial", "open the tutorial", "start a tutorial",
        "let's start the tutorial", "can you start the tutorial",
        "i want to start the tutorial", "take me to the tutorial",
        "start tutorial please",
    ])
    def test_start_tutorial_natural_phrasings(self, client, text):
        assert _action(client, text)["action"] == "start_tutorial", text

    def test_start_tutorial_again_still_restarts(self, client):
        assert _action(client, "start tutorial again")["action"] == "restart_tutorial"

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
        assert _action(client, "run", code="print(1)")["action"] == "run"


class TestTutorialCoachRoute:

    def _coach(self, client, **body):
        return client.post("/tutorial/coach", json=body).get_json()

    def test_i_dont_understand_returns_coach_response(self, client):
        d = self._coach(client, module="print", text="I don't understand")
        assert d["success"] is True and d["handled"] is True
        assert d["request"] == "dont_understand"
        assert "print" in d["text"].lower()

    def test_explain_simpler_returns_simpler_explanation(self, client):
        d = self._coach(client, module="print", text="explain simpler")
        assert d["handled"] is True
        assert d["request"] == "explain_simpler"
        assert "print" in d["text"].lower()

    def test_why_quotes_works_in_print_module(self, client):
        d = self._coach(client, module="print", text="why do we use quotes")
        assert d["handled"] is True
        assert "text" in d["text"].lower()

    def test_repeated_failure_encouragement_is_supportive(self, client):
        d = self._coach(client, module="print", request="encourage", attempts=2)
        assert d["handled"] is True
        assert "wrong" not in d["text"].lower()
        assert d["text"]

    def test_key2_unavailable_uses_deterministic_fallback(self, client):
        d = self._coach(client, module="print", text="why indentation")
        assert d["handled"] is True
        assert d["source"] == "deterministic"
        assert "indent" in d["text"].lower()

    def test_non_coach_text_is_not_handled(self, client):
        d = self._coach(client, module="print", text="run code")
        assert d["handled"] is False

    def test_unknown_module_still_answers_general_facts(self, client):
        d = self._coach(client, module="", text="why do we use quotes")
        assert d["handled"] is True
        assert "quote" in d["text"].lower()


class TestWhileExecutionBackstop:
    def test_sandbox_wall_clock_timeout_is_configured(self):
        assert 0 < app_module.SUBPROCESS_WALL_TIMEOUT_SECONDS <= 10
