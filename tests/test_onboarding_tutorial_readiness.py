import os

import pytest

import app as app_module
from codeup.learning import tutorial_engine
from codeup.accessibility.speech_output import sanitize_speech_text


ROOT = os.path.dirname(os.path.dirname(__file__))


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.setenv("AI_ENABLED", "0")
    monkeypatch.setenv("GROQ_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def no_cloud(monkeypatch):
    def _fail(*a, **k):
        raise AssertionError("onboarding/tutorial routing must not call cloud AI")

    monkeypatch.setattr(app_module, "call_gemini", _fail)


def _vc(client, text, **kw):
    return client.post("/voice-command", json={"text": text, **kw}).get_json()


def _spoken(data):
    return (
        data.get("speech")
        or data.get("message")
        or (data.get("ai_action") or {}).get("spoken_confirmation")
        or ""
    ).strip()


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def test_what_can_i_do_here_speaks_short_beginner_onboarding(client, no_cloud):
    data = _vc(client, "what can I do here")
    assert data["action"] == "deterministic_message"
    assert data["onboarding"] is True
    s = _spoken(data).lower()
    for kw in ("learn python", "speaking or typing", "start tutorial",
               "generate code", "run code", "explain it", "more examples"):
        assert kw in s, kw
    for adv in ("replay mistake", "summarize structure", "make project report"):
        assert adv not in s, adv


def test_onboarding_mentions_typed_fallback_and_microphone(client, no_cloud):
    s = _spoken(_vc(client, "what can I do here")).lower()
    assert "microphone" in s
    assert "type" in s  # typed command fallback explicitly offered


@pytest.mark.parametrize("text", [
    "what should I do first",
    "where do I begin",
    "how do I get started",
    "what is the first step",
    "what should I do to start",
])
def test_first_step_gives_one_clear_beginner_action(client, no_cloud, text):
    data = _vc(client, text)
    assert data["action"] == "deterministic_message"
    assert data["onboarding"] is True
    s = _spoken(data).lower()
    assert "start tutorial" in s          # one concrete first action
    assert "type" in s                    # typed fallback for no-mic learners


@pytest.mark.parametrize("text", ["help", "show commands", "guide me"])
def test_help_routes_to_help_not_a_wall_of_text(client, no_cloud, text):
    assert _vc(client, text)["action"] == "help"


def test_say_more_and_more_examples_expose_the_longer_help(client, no_cloud):
    assert _vc(client, "say more")["action"] == "say_more"
    assert _vc(client, "more examples")["action"] == "more_help"


@pytest.mark.parametrize("text", [
    "start tutorial",
    "start Python tutorial",
    "start the python tutorial",
    "begin the beginner tutorial",
    "open the coding tutorial",
    "python tutorial",
    "beginner lesson",
])
def test_tutorial_entry_phrases_start_the_tutorial(client, no_cloud, text):
    data = _vc(client, text)
    assert data["action"] == "start_tutorial", (text, data.get("action"), _spoken(data))


@pytest.mark.parametrize("text", [
    "teach me Python",
    "teach me python please",
    "teach me to code",
    "teach me coding",
    "teach me programming",
    "i want to learn python",
    "learn to code",
])
def test_teach_me_python_routes_to_tutorial_not_random_chat(client, no_cloud, text):
    data = _vc(client, text)
    assert data["action"] == "start_tutorial", (text, data.get("action"), _spoken(data))


@pytest.mark.parametrize("text", [
    "teach me this code", "teach me this scored", "teach me this court",
    "teach me this cod", "teach me scored",
])
def test_teach_me_this_code_still_analyzes(client, no_cloud, text):
    assert _vc(client, text, code="for i in range(3):\n    print(i)\n")["action"] == "analyze"


def test_teach_me_a_concept_does_not_hijack_to_tutorial(client, no_cloud):
    assert _vc(client, "teach me inheritance")["action"] != "start_tutorial"


def test_make_a_beginner_lesson_stays_a_lesson(client, no_cloud):
    data = _vc(client, "make a beginner lesson on loops", code="")
    assert data.get("lesson_builder") is True
    assert data["action"] != "start_tutorial"


@pytest.mark.parametrize("text,expected", [
    ("exit tutorial", "skip_tutorial"),
    ("stop tutorial", "skip_tutorial"),
    ("stop everything", "stop_everything"),
])
def test_tutorial_exit_and_global_stop_route(client, no_cloud, text, expected):
    assert _vc(client, text)["action"] == expected


@pytest.mark.parametrize("module,code", [
    ("print", 'print("Hello world")'),
    ("variables", 'name = "Taknoor"\nprint(name)'),
    ("if", 'age = 12\nif age > 10:\n    print("you can vote")'),
    ("for", 'for i in range(3):\n    print(i)'),
    ("while", "count = 1\nwhile count <= 3:\n    print(count)\n    count = count + 1"),
])
def test_each_module_validates_a_correct_attempt(module, code):
    res = tutorial_engine.validate_attempt(module, code, ran_ok=True, output="")
    assert res["passed"] is True, (module, res)
    assert res["safe"] is True
    assert res["feedback"].strip()  # spoken success line exists


@pytest.mark.parametrize("module,code", [
    ("print", "x = 1"),                       # no print at all
    ("variables", 'print("hi")'),             # printed, but no variable used
    ("if", "age = 12"),                        # no decision yet
    ("for", "print(1)"),                       # no loop
    ("while", "while True:\n    print(1)"),   # unsafe infinite loop
])
def test_each_module_rejects_an_incomplete_attempt_with_a_hint(module, code):
    res = tutorial_engine.validate_attempt(module, code, ran_ok=True, output="")
    assert res["passed"] is False, (module, res)
    assert res["feedback"].strip()            # kind spoken feedback, not silence


def test_module_pack_covers_all_five_topics_in_order():
    pack = tutorial_engine.module_pack()
    assert pack["order"] == ["print", "variables", "if", "for", "while"]
    assert pack["count"] == 5
    for mid in pack["order"]:
        m = pack["modules"][mid]
        assert m["concept"].strip() and m["task"].strip() and m["success"].strip()
        assert m["hints"]  # beginner hints exist


def test_tutorial_coach_speaks_a_fact_without_ai(client):
    d = client.post("/tutorial/coach",
                    json={"module": "for", "request": "another_hint", "attempts": 0}).get_json()
    assert d["handled"] is True
    assert d["text"].strip()
    assert d["speech"].strip()


def test_tutorial_validate_route_passes_a_real_for_loop(client):
    d = client.post("/tutorial/validate",
                    json={"module": "for", "code": "for i in range(3):\n    print(i)",
                          "ran_ok": True}).get_json()
    assert d["passed"] is True


@pytest.mark.parametrize("text", [
    "what can I do here", "what should I do first", "teach me Python",
    "start tutorial", "beginner lesson",
])
def test_spoken_replies_are_markdown_sanitized(client, no_cloud, text):
    speech = _spoken(_vc(client, text))
    for bad in ("`", "*", "#", "```", "**"):
        assert bad not in speech, (text, bad, speech)
    assert sanitize_speech_text(speech) == speech


def test_engine_success_and_concept_text_is_speech_safe():
    for mid, m in tutorial_engine.MODULES.items():
        for field in ("concept", "task", "success", "recap", "example_spoken"):
            value = m[field]
            assert sanitize_speech_text(value) == value, (mid, field)


def test_ide_start_gate_explains_what_codeup_is_and_how_to_start():
    src = _read("templates/index.html")
    low = src.lower()
    assert "python basics" in low
    assert "nvda" in low and "jaws" in low and "vs" in low  # not-a-replacement note
    assert "headphone" in low
    assert "microphone" in low
    assert "type the command" in low or "type a command" in low or "type the command in the box" in low
    assert 'start tutorial' in low
    assert "what can i do here" in low


def test_landing_bundle_has_a_how_to_start_and_scope_section():
    bundle = _read("static/landing/dist/bundle.js")
    assert "How to start" in bundle
    assert "not trying to replace VS Code" in bundle
    assert "what can I do here" in bundle
    assert "insert a for loop that prints the first 3 whole numbers" in bundle
    assert "Before you begin" in bundle


def test_app_js_help_speech_is_bounded_and_leads_with_learning():
    app_js = _read("static/app.js")
    start = app_js.index("const BEGINNER_COMMAND_GUIDE_SPEECH")
    end = app_js.index(";", start)
    speech = app_js[start:end]
    assert len(speech) < 900, len(speech)
    low = speech.lower()
    assert "speaking or typing" in low
    assert "start tutorial" in low
