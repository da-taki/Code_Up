import os

import pytest

import app as app_module
import report_support
import session_memory

_STATIC = os.path.join(os.path.dirname(__file__), "..", "static")

LOOP = "for i in range(3):\n    print(i)\n"
BAD = "for i in range(3):\nprint(i)\n"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _vc(client, text, code="", error=""):
    return client.post("/voice-command", json={"text": text, "code": code, "error": error}).get_json()


SUBSTANTIAL = [
    ("what can I do here", "", "", 80),
    ("summarize structure", LOOP, "", 60),
    ("debug this like a teacher", BAD, "IndentationError: expected an indented block on line 2", 120),
    ("make trainer notes", LOOP, "", 80),
    ("make a beginner lesson on loops", "", "", 120),
    ("prepare this for NVDA", LOOP, "", 120),
    ("what did I learn today", "", "", 40),
]

SHORT_OK = [
    ("go to the loop", LOOP, ""),
]

FRONTEND_SPEECH = [
    ("teach me this code", "analyze", "analyzeCode()"),
    ("teach me this scored", "analyze", "analyzeCode()"),
    ("read output", "read_output", "speakOutput()"),
    ("read my code", "read_code", "readMyCodeAloud()"),
]


@pytest.mark.parametrize("text,code,error,min_len", SUBSTANTIAL)
def test_teaching_actions_speak_the_substance(client, text, code, error, min_len):
    d = _vc(client, text, code, error)
    speech = (d.get("speech") or "").strip()
    message = (d.get("message") or "").strip()
    action_speech = ((d.get("ai_action") or {}).get("spoken_confirmation") or "").strip()
    assert speech or message or action_speech, (text, d.get("action"))
    spoken = speech or message or action_speech
    assert len(spoken) >= min_len, (text, len(spoken))
    if speech:
        assert len(speech) >= 40, (text, speech)


@pytest.mark.parametrize("text,code,error", SHORT_OK)
def test_every_action_returns_some_spoken_payload(client, text, code, error):
    d = _vc(client, text, code, error)
    spoken = (d.get("speech") or d.get("message") or "").strip()
    assert spoken, (text, d.get("action"))
    assert len(spoken) >= 15, (text, spoken)


@pytest.mark.parametrize("text,action,fn", FRONTEND_SPEECH)
def test_frontend_speech_actions_route_and_are_wired_to_speak(client, text, action, fn):
    assert _vc(client, text, LOOP, "")["action"] == action
    with open(os.path.join(_STATIC, "app.js"), encoding="utf-8") as fh:
        src = fh.read()
    assert f"action === '{action}'" in src
    assert fn in src


def test_project_report_route_has_meaningful_speech():
    mem = session_memory.new_memory()
    session_memory.record_run(mem, output="0\n1\n2\n", ran_ok=True)
    rep = report_support.build_project_report({"is_project": False, "code": LOOP}, mem)
    assert rep["speech"] and len(rep["speech"]) >= 120
    low = rep["speech"].lower()
    assert "for loop" in low and "last successful output" in low


def test_deterministic_message_dispatch_speaks(app_js_text=None):
    with open(os.path.join(_STATIC, "app.js"), encoding="utf-8") as fh:
        src = fh.read()
    idx = src.index("action === 'deterministic_message'")
    block = src[idx:idx + 400]
    assert "speak(" in block


def test_run_and_report_dispatch_speak():
    with open(os.path.join(_STATIC, "app.js"), encoding="utf-8") as fh:
        src = fh.read()
    assert "speak(formatRunOutputSpeech(data.output))" in src
    start = src.index("async function requestProjectReport(")
    assert "speak(" in src[start:start + 1200]
