import json

import pytest

import app as app_module
import natural_command_mapper


LOOP_CODE = "for i in range(3):\n    print(i)"
BROKEN_CODE = "for i in range(3):\nprint(i)"
INDENT_ERROR = "IndentationError: expected an indented block after for statement on line 1"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as flask_client:
        yield flask_client


def _vc(client, text, **kw):
    return client.post("/voice-command", json={"text": text, **kw}).get_json()


def _mapped(intent, confidence=0.9, slots=None):
    return {
        "status": "mapped",
        "band": natural_command_mapper.confidence_band(confidence),
        "mapping": {
            "intent": intent,
            "confidence": confidence,
            "slots": slots or {},
            "reason": "test",
        },
    }


def test_mapper_prompt_requests_json_only_and_structured_intent():
    def fake_ai(system, user):
        assert "You are not writing code" in system
        assert "Return JSON only" in system
        assert "Never put code in the response" in system
        assert "Student command: please do the beginner counter thing" in user
        return json.dumps({
            "intent": "insert_beginner_loop",
            "confidence": 0.91,
            "slots": {"start": 0, "stop": 3, "output": "print_numbers"},
            "reason": "loop wording",
        })

    result = natural_command_mapper.map_command(
        "please do the beginner counter thing",
        ai_fn=fake_ai,
        has_code=False,
    )

    assert result["status"] == "mapped"
    assert result["band"] == "high"
    assert result["mapping"]["intent"] == "insert_beginner_loop"


@pytest.mark.parametrize("raw", [
    "not json",
    json.dumps({"intent": "shell", "confidence": 0.99, "slots": {}, "reason": "bad"}),
    json.dumps({
        "intent": "insert_beginner_loop",
        "confidence": 0.99,
        "slots": {"start": 0, "stop": 3, "output": "print_numbers", "code": "print(1)"},
        "reason": "bad",
    }),
    json.dumps({
        "intent": "run_code",
        "confidence": 1.5,
        "slots": {},
        "reason": "bad",
    }),
])
def test_mapper_rejects_invalid_json_intents_payloads_and_confidence(raw):
    result = natural_command_mapper.map_command(
        "please do the thing",
        ai_fn=lambda _system, _user: raw,
        has_code=False,
    )

    assert result["status"] == "invalid"


def test_mapper_redacts_api_keys_from_failures(monkeypatch):
    secret = "gsk_live_test_secret_123"
    monkeypatch.setenv("GROQ_API_KEY", secret)

    def boom(_system, _user):
        raise RuntimeError(f"network failed for {secret}")

    result = natural_command_mapper.map_command("unclear command", ai_fn=boom)

    assert result["status"] == "failed"
    assert secret not in result["reason"]
    assert "<redacted-api-key>" in result["reason"]


def test_ai_mapper_runs_only_after_deterministic_routes_decline(client, monkeypatch):
    calls = []

    def fake_mapper(text, code):
        calls.append((text, code))
        return _mapped("run_code", 0.92)

    monkeypatch.setattr(app_module, "_call_ai_natural_command_mapper", fake_mapper)

    assert _vc(client, "run this", code="print('ok')")["action"] == "run"
    assert calls == []

    data = _vc(client, "program please now", code="print('ok')")
    assert data["action"] == "run"
    assert calls == [("program please now", "print('ok')")]
    assert data["ai_mapper"] is True


def test_high_confidence_mapper_loop_executes_safe_existing_handler(client, monkeypatch):
    monkeypatch.setattr(
        app_module,
        "_call_ai_natural_command_mapper",
        lambda _text, _code: _mapped(
            "insert_beginner_loop",
            0.93,
            {"start": 0, "stop": 3, "output": "print_numbers"},
        ),
    )

    transcript = "please do the beginner counter thing"
    data = _vc(client, transcript, code="")

    assert data["action"] == "conversational_edit"
    assert data["ai_action"]["action"] == "append_code"
    assert data["ai_action"]["code"] == LOOP_CODE
    assert transcript not in data["ai_action"]["code"]
    assert data["ai_action"]["source"] == "natural_command_mapper"


@pytest.mark.parametrize("confidence,reason", [(0.70, "medium_confidence"), (0.40, "low_confidence")])
def test_medium_and_low_confidence_mapper_asks_clarification(client, monkeypatch, confidence, reason):
    monkeypatch.setattr(
        app_module,
        "_call_ai_natural_command_mapper",
        lambda _text, _code: _mapped(
            "insert_beginner_loop",
            confidence,
            {"start": 0, "stop": 3, "output": "print_numbers"},
        ),
    )

    data = _vc(client, "please do the beginner counter thing", code="")

    assert data["action"] == "clarify"
    assert data["needs_clarification"] is True
    assert data["reason"] == reason
    assert "ai_action" not in data


@pytest.mark.parametrize("mapper_result", [
    {"status": "invalid", "reason": "invalid_json"},
    {"status": "invalid", "reason": "intent_not_allowed"},
    {"status": "failed", "reason": "network_error"},
])
def test_invalid_disallowed_or_failed_mapper_falls_back_cleanly(client, monkeypatch, mapper_result):
    monkeypatch.setattr(
        app_module,
        "_call_ai_natural_command_mapper",
        lambda _text, _code: mapper_result,
    )

    data = _vc(client, "program please now", code="print('ok')")

    assert data["action"] == "clarify"
    assert data["needs_clarification"] is True
    assert "insert a loop" in data["speech"]
    assert "run" in data["speech"]


def test_route_does_not_leak_api_key_in_mapper_failure(client, monkeypatch):
    secret = "gsk_live_test_secret_456"
    monkeypatch.setenv("GROQ_API_KEY", secret)
    monkeypatch.setattr(
        app_module,
        "_call_ai_natural_command_mapper",
        lambda _text, _code: {"status": "failed", "reason": f"bad key {secret}"},
    )

    data = _vc(client, "program please now", code="print('ok')")

    assert secret not in json.dumps(data)
    assert "<redacted-api-key>" in data["reason"]


@pytest.mark.parametrize("phrase", [
    "insert a for loop that prints the first 3 whole numbers",
    "make a loop that prints first three numbers",
    "create loop 0 to 2",
    "print numbers zero to two using loop",
    "add a loop for first 3 numbers",
    "loop for 3 numbers",
    "for loop three numbers",
    "make loop that shows 0 1 2",
    "make it print 0 1 2",
    "put loop for first three whole numbers",
    "loop print three whole numbers",
    "of for loop the Trends the first 3 whole numbers",
    "make loop friends zero to two",
    "insert a loop that prince 3 whole numbers",
])
def test_requested_loop_phrases_insert_exact_beginner_loop(client, phrase):
    data = _vc(client, phrase, code="")

    assert data["action"] == "conversational_edit", (phrase, data)
    assert data["ai_action"]["code"] == LOOP_CODE
    assert phrase.lower() not in data["ai_action"]["code"].lower()


def test_make_it_print_sequence_with_existing_code_clarifies_not_raw_insert(client):
    data = _vc(client, "make it print 0 1 2", code='print("old")')

    assert data["action"] == "clarify"
    assert data["needs_clarification"] is True
    assert "ai_action" not in data


def test_nonsense_loop_command_clarifies_not_raw_insert(client):
    data = _vc(client, "banana loop potato window", code="")

    assert data["action"] == "clarify"
    assert data["needs_clarification"] is True
    assert "ai_action" not in data


@pytest.mark.parametrize("text,expected", [
    ("run this", "run"),
    ("execute", "run"),
    ("start learning", "start_tutorial"),
    ("begin tutorial", "start_tutorial"),
    ("teach me python", "start_tutorial"),
    ("explain", "walk_through"),
    ("fix", "fix"),
    ("debug", "deterministic_message"),
    ("stop", "stop_everything"),
    ("what can I do", "deterministic_message"),
])
def test_other_simple_commands_still_route_without_ai_mapper(client, monkeypatch, text, expected):
    def fail_mapper(_text, _code):
        raise AssertionError("deterministic simple command should not need the AI mapper")

    monkeypatch.setattr(app_module, "_call_ai_natural_command_mapper", fail_mapper)
    code = BROKEN_CODE if text in {"explain", "fix", "debug"} else ""
    error = INDENT_ERROR if text in {"fix", "debug"} else ""

    data = _vc(client, text, code=code, error=error)

    assert data["action"] == expected


def test_more_after_onboarding_routes_to_help_more_without_ai_mapper(client, monkeypatch):
    def fail_mapper(_text, _code):
        raise AssertionError("contextual more should not need the AI mapper")

    monkeypatch.setattr(app_module, "_call_ai_natural_command_mapper", fail_mapper)

    assert _vc(client, "what can I do here", code="")["action"] == "deterministic_message"
    data = _vc(client, "more", code="")

    assert data["action"] == "more_help"
