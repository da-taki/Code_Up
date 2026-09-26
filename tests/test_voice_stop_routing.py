"""Routing for the three distinct "stop" commands (exact phrases only).

  speech interruption -> stop_speaking   (CodeUp stops talking, voice stays on)
  microphone off      -> pause_voice     (recognition off, remembered)
  stop everything     -> stop_everything (both)

Behaviour in the browser is covered end to end by test_voice_stop_browser.py.
"""

import pytest

import app as app_module

LOOP = "count = 0\nwhile count < 3:\n    count = count + 1  # stop at 3\n"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _vc(client, text, code=LOOP):
    return client.post("/voice-command", json={"text": text, "code": code}).get_json()


@pytest.mark.parametrize("text", [
    "stop", "Stop.", "stop it", "be quiet", "stop talking", "stop speaking", "shut up", "silence", "quiet",
    "ruko", "chup", "bas",
])
def test_speech_interruption_never_turns_the_microphone_off(client, text):
    assert _vc(client, text)["action"] == "stop_speaking"


@pytest.mark.parametrize("text", [
    "stop listening", "turn voice off", "turn off voice", "voice off", "disable microphone", "mute microphone",
    "stop voice", "pause voice", "pause listening", "top listening", "stop listing",
])
def test_explicit_microphone_off(client, text):
    assert _vc(client, text)["action"] == "pause_voice"


@pytest.mark.parametrize("text", ["stop everything", "cancel everything", "stop all", "top everything"])
def test_stop_everything_is_the_explicit_full_shutdown(client, text):
    assert _vc(client, text)["action"] == "stop_everything"


@pytest.mark.parametrize("text", ["stop the loop", "how do I stop this loop", "make the loop stop at 5",
                                  "what does stop at 3 mean"])
def test_sentences_containing_stop_are_not_control_commands(client, text):
    action = _vc(client, text)["action"]
    assert action not in {"stop_speaking", "pause_voice", "stop_everything"}


def test_stop_typed_as_a_program_answer_is_still_program_input(client):
    client.post("/run", json={"code": 'answer = input("Say something: ")\nprint(answer)\n'})
    data = _vc(client, "stop", code='answer = input("Say something: ")\nprint(answer)\n')
    assert data["action"] != "stop_everything"


def test_semantic_ai_speech_stop_maps_to_speech_only():
    from codeup.commands import semantic_intent
    assert semantic_intent.canonical_command("STOP_SPEECH") == "stop speaking"
    assert semantic_intent.canonical_command("PAUSE_LISTENING") == "pause voice"


def test_frontend_stop_speaking_never_touches_recognition():
    src = open("static/app.js", encoding="utf-8").read()
    start = src.index("else if (action === 'stop_speaking') {")
    block = src[start:src.index("\n  }", start)]
    assert "SpeechManager.cancelAll()" in block
    for forbidden in ("stopListeningNow", "VoiceInput", "markVoiceListeningOff", "pauseVoiceRecognition"):
        assert forbidden not in block
    everything = src[src.index("else if (action === 'stop_everything') {"):]
    assert "stopListeningNow()" in everything[:400]


def test_help_copy_explains_the_three_commands():
    src = open("static/app.js", encoding="utf-8").read()
    assert '"stop" — stop CodeUp speaking (voice keeps listening)' in src
    assert '"stop listening" / "turn voice off" — turn voice control off' in src
    assert '"stop everything" — stop speech and listening' in src
    assert "keep mic open but ignore commands" not in src

    landing = open("static/landing/features.jsx", encoding="utf-8").read()
    assert 'Say "stop" to stop CodeUp speaking.' in landing
    assert 'Say "stop listening" to turn voice control off' in landing
    assert '"stop everything" to stop both' in landing
