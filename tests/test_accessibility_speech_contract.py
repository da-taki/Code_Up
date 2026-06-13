"""
Cross-cutting accessibility speech contract.

The core rule for a blind beginner: every meaningful system response must be
understandable through speech, not only through visible text. This module guards
that contract across the major learning actions — each must return a spoken
payload (a `speech` field, or a `message` the frontend speaks), and the teaching
actions must carry the *core* explanation in speech, not just a tiny final
sentence. Deterministic only; no cloud AI.
"""
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


# Every major action that returns visible text, with the minimum spoken length we
# expect. Teaching actions must speak the substance; control/navigation actions
# may be short but must still speak *something* meaningful.
SUBSTANTIAL = [
    ("what can I do here", "", "", 80),
    ("summarize structure", LOOP, "", 60),
    ("teach me this code", LOOP, "", 120),
    ("debug this like a teacher", BAD, "IndentationError: expected an indented block on line 2", 120),
    ("make trainer notes", LOOP, "", 80),
    ("make a beginner lesson on loops", "", "", 120),
    ("prepare this for NVDA", LOOP, "", 120),
    ("what did I learn today", "", "", 40),
]

# Actions that carry a short-but-meaningful backend spoken payload.
SHORT_OK = [
    ("go to the loop", LOOP, ""),
]

# Actions whose speech is produced entirely on the frontend (the browser reads
# the editor / output box aloud). The backend intentionally returns no payload;
# the contract is that the dispatcher wires them to a speaking function.
FRONTEND_SPEECH = [
    ("read output", "read_output", "speakOutput()"),
    ("read my code", "read_code", "readMyCodeAloud()"),
]


@pytest.mark.parametrize("text,code,error,min_len", SUBSTANTIAL)
def test_teaching_actions_speak_the_substance(client, text, code, error, min_len):
    d = _vc(client, text, code, error)
    speech = (d.get("speech") or "").strip()
    message = (d.get("message") or "").strip()
    # A spoken payload must exist (speech, or a message the frontend speaks).
    assert speech or message, (text, d.get("action"))
    # And it must carry the core explanation, not a one-line stub.
    assert len(speech or message) >= min_len, (text, len(speech or message))
    # When a `speech` field is present it must itself be meaningful.
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
    # The backend routes the command...
    assert _vc(client, text, LOOP, "")["action"] == action
    # ...and the frontend dispatcher hands it to a speaking function.
    with open(os.path.join(_STATIC, "app.js"), encoding="utf-8") as fh:
        src = fh.read()
    assert f"action === '{action}'" in src
    assert fn in src


def test_project_report_route_has_meaningful_speech():
    mem = session_memory.new_memory()
    session_memory.record_run(mem, output="0\n1\n2\n", ran_ok=True)
    rep = report_support.build_project_report({"is_project": False, "code": LOOP}, mem)
    assert rep["speech"] and len(rep["speech"]) >= 120
    # The spoken report explains behaviour + the last output, not just file type.
    low = rep["speech"].lower()
    assert "for loop" in low and "last successful output" in low


def test_deterministic_message_dispatch_speaks(app_js_text=None):
    # Structural guard: the frontend's deterministic_message handler speaks the
    # payload (so no learning response updates only the visible UI).
    with open(os.path.join(_STATIC, "app.js"), encoding="utf-8") as fh:
        src = fh.read()
    idx = src.index("action === 'deterministic_message'")
    block = src[idx:idx + 400]
    assert "speak(" in block


def test_run_and_report_dispatch_speak():
    with open(os.path.join(_STATIC, "app.js"), encoding="utf-8") as fh:
        src = fh.read()
    # Run speaks the formatted output; project report speaks its speech field.
    assert "speak(formatRunOutputSpeech(data.output))" in src
    start = src.index("async function requestProjectReport(")
    assert "speak(" in src[start:start + 1200]
