import os

import pytest

import app as app_module
from codeup.learning import concept_tutor
from codeup.learning import lesson_builder
from codeup.reports import report_support
from codeup.accessibility import screen_reader_bridge
from codeup.runtime import session_memory
from codeup.commands.command_normalization import normalize_command_transcript

LOOP = "for i in range(3):\n    print(i)\n"
ROOT = os.path.dirname(os.path.dirname(__file__))


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _vc(client, text, **kw):
    return client.post("/voice-command", json={"text": text, **kw}).get_json()


def _spoken(d):
    return (d.get("speech") or d.get("message")
            or (d.get("ai_action") or {}).get("spoken_confirmation") or "").strip()


def _app_js():
    with open(os.path.join(ROOT, "static", "app.js"), encoding="utf-8") as fh:
        return fh.read()


class TestCountingLoopGeneration:
    EXPLICIT = [
        "insert a for loop that prints the first 3 whole numbers",
        "make a loop that prints three numbers",
        "generate a loop that prints 0 to 2",
        "insert loop printing first three whole numbers",
    ]
    BARE = [
        "insert a for loop",
        "put a for loop in the editor",
        "add a for loop",
    ]
    NOISY_ASR = [
        "of for loop the Trends the first 3 whole numbers",
        "a for loop the prints the first three whole numbers",
        "insert a loop that prince 3 whole numbers",
        "make loop friends zero to two",
        "put for loop numbers 0 to 2",
        "for loop first 3 whole numbers",
        "loop the first three numbers",
        "insert for loop trends first 3 numbers",
    ]

    @pytest.mark.parametrize("text", EXPLICIT + BARE)
    def test_inserts_a_real_beginner_loop_not_a_stub(self, client, text):
        d = _vc(client, text)
        assert d["action"] == "conversational_edit", text
        assert d["ai_action"]["code"] == "for i in range(3):\n    print(i)", text
        assert "range(0)" not in d["ai_action"]["code"]
        assert "pass" not in d["ai_action"]["code"]

    @pytest.mark.parametrize("text", EXPLICIT)
    def test_explicit_loop_speaks_inserted_for_loop(self, client, text):
        assert _spoken(_vc(client, text)).startswith("Inserted a for loop that prints 0, 1, and 2.")

    @pytest.mark.parametrize("text", BARE)
    def test_bare_loop_speaks_simple_for_loop(self, client, text):
        assert _spoken(_vc(client, text)).startswith("Inserted a simple for loop that prints 0, 1, and 2.")

    @pytest.mark.parametrize("text", NOISY_ASR)
    def test_noisy_asr_loop_commands_repair_before_raw_insert(self, client, text):
        d = _vc(client, text)
        assert d["action"] == "conversational_edit", (text, d)
        assert d["ai_action"]["action"] == "append_code", (text, d)
        assert d["ai_action"]["code"] == "for i in range(3):\n    print(i)"
        assert text not in d["ai_action"]["code"]
        spoken = _spoken(d)
        assert "loop" in spoken.lower()
        assert "prints 0, 1, and 2" in spoken

    def test_reported_asr_transcript_speaks_simple_loop_confirmation(self, client):
        d = _vc(client, "of for loop the Trends the first 3 whole numbers")
        assert _spoken(d).startswith("Inserted a simple for loop that prints 0, 1, and 2.")

    def test_unclear_loop_command_clarifies_instead_of_raw_insert(self, client):
        d = _vc(client, "insert a loop that blargs the thing")
        assert d["action"] == "clarify"
        assert d["reason"] == "unclear_loop_command"
        assert "loop command" in _spoken(d).lower()
        assert d.get("text") != "insert a loop that blargs the thing"
        assert "ai_action" not in d

    def test_loop_phrasings_do_not_shadow_lessons_or_reports(self, client):
        assert _vc(client, "make a beginner lesson on loops", code=LOOP).get("lesson_builder") is True
        assert _vc(client, "make a project report", code=LOOP)["action"] == "project_report"

    def test_value_loop_is_not_hijacked(self, client):
        d = _vc(client, "insert a for loop that prints hello 3 times")
        code = (d.get("ai_action") or {}).get("code", "")
        if code:
            assert "hello" in code


class TestTeachCode:
    def test_explanation_matches_actual_loop(self):
        speech = concept_tutor.build_concept_lesson(LOOP, {})["speech"]
        assert speech.startswith("This program uses a for loop.")
        assert "range(3) gives 0, 1, and 2." in speech
        assert "i changes each time through the loop." in speech
        assert "print(i) displays the current value." in speech
        assert "Expected output is 0, 1, and 2 on separate lines." in speech

    def test_does_not_hallucinate_a_variable_name(self):
        speech = concept_tutor.build_concept_lesson("for count in range(2):\n    print(count)\n", {})["speech"]
        assert "count changes each time through the loop." in speech
        assert "range(2) gives 0 and 1." in speech

    def test_speech_does_not_open_on_practice_idea(self):
        speech = concept_tutor.build_concept_lesson(LOOP, {})["speech"]
        assert not speech.startswith("Practice idea")
        assert speech.index("This program") < speech.index("Practice idea")


class TestLessonTrainerNote:
    @pytest.mark.parametrize("verbosity", ["concise", "normal", "detailed", "beginner", "expert"])
    def test_trainer_note_never_spoken(self, verbosity):
        r = lesson_builder.build_accessible_lesson("loops", verbosity)
        assert "Trainer note" not in r["speech"]

    def test_trainer_note_still_shown_for_the_teacher(self):
        r = lesson_builder.build_accessible_lesson("loops")
        assert "Trainer note:" in r["message"]  # display-only, teacher-facing

    def test_spoken_lesson_starts_at_the_title(self, client):
        speech = _spoken(_vc(client, "make a beginner lesson on loops"))
        assert speech.startswith("Lesson: For loops")
        assert "Trainer note" not in speech


class TestScreenReaderHandoff:
    OPENING = "Screen reader handoff notes ready."

    def test_prepare_for_nvda_opening(self, client):
        speech = _spoken(_vc(client, "prepare this for NVDA", code=LOOP))
        assert speech.startswith(self.OPENING)
        assert "easier to read with NVDA or another screen reader" in speech

    def test_make_handoff_notes_is_the_clear_alias(self, client):
        d = _vc(client, "make screen reader handoff notes", code=LOOP)
        assert d.get("screen_reader_bridge") is True
        assert _spoken(d).startswith(self.OPENING)

    def test_never_implies_codeup_configures_nvda(self):
        speech = screen_reader_bridge.build_screen_reader_bridge(LOOP, {}, "prepare this for nvda")["speech"]
        low = speech.lower()
        assert "not a replacement" in low
        for bad in ("configure nvda", "set up nvda", "configures nvda", "turn on nvda"):
            assert bad not in low

    def test_help_text_surfaces_the_clear_command_first(self):
        src = _app_js()
        assert "make screen reader handoff notes" in src
        assert src.index("make screen reader handoff notes") < src.index("prepare this for NVDA")


class TestStopAndRecovery:
    @pytest.mark.parametrize("raw,expected", [
        ("top listening", "stop listening"),
        ("stop listing", "stop listening"),
        ("stop listen", "stop listening"),
        ("top everything", "stop everything"),
    ])
    def test_misheard_stop_is_normalized(self, raw, expected):
        assert normalize_command_transcript(raw)["normalized_text"] == expected

    def test_stop_listening_is_not_corrupted(self):
        assert normalize_command_transcript("stop listening")["normalized_text"] == "stop listening"
        assert normalize_command_transcript("go to top")["normalized_text"] == "go to top"

    @pytest.mark.parametrize("text,action", [
        ("stop listening", "pause_voice"),
        ("top listening", "pause_voice"),
        ("stop everything", "stop_everything"),
        ("top everything", "stop_everything"),
    ])
    def test_stop_routes(self, client, text, action):
        assert _vc(client, text)["action"] == action

    def test_key_2_is_a_recovery_key(self):
        src = _app_js()
        assert "e.key === '2'" in src
        assert "Stopped listening and speech. Editor unchanged." in src
        assert "Key 2 repeats the current context" not in src

    def test_recovery_stops_listening_and_speech_but_not_editor(self):
        src = _app_js()
        start = src.index("e.key === '2'")
        block = src[start:start + 900]
        assert "stopListeningNow()" in block
        assert "SpeechManager.cancelAll()" in block
        assert "setCode" not in block

    def test_stop_everything_also_stops_listening(self):
        src = _app_js()
        start = src.index("action === 'stop_everything'")
        block = src[start:start + 500]
        assert "stopListeningNow()" in block
        assert "SpeechManager.cancelAll()" in block


class TestProjectReportSpeech:
    def _mem(self):
        mem = session_memory.new_memory()
        session_memory.record_run(mem, output="0\n1\n2\n", ran_ok=True)
        mem["features_used"] = ["generated code", "ran code"]
        return mem

    def test_summary_starts_at_the_beginning_not_history(self):
        rep = report_support.build_project_report({"is_project": False, "code": LOOP}, self._mem())
        speech = rep["speech"]
        assert speech.startswith("Project report ready.")
        assert not speech.lstrip().lower().startswith("session history")
        assert "Commands and session history" not in speech
        assert "##" not in speech

    def test_summary_invites_say_more_and_defers_next_steps(self):
        rep = report_support.build_project_report({"is_project": False, "code": LOOP}, self._mem())
        assert rep["speech"].rstrip().endswith("Say more to hear next steps.")
        assert "Next step:" in rep["speech_more"]
        assert "Session history" not in rep["speech"]
        assert "Session history" in rep["speech_more"]

    def test_summary_still_explains_behaviour_and_last_output(self):
        rep = report_support.build_project_report({"is_project": False, "code": LOOP}, self._mem())
        low = rep["speech"].lower()
        assert "for loop" in low and "range(3)" in rep["speech"] and "0, 1, and 2" in rep["speech"]
        assert "last successful output" in low

    def test_route_speaks_summary_and_frontend_wires_say_more(self, client):
        d = _vc(client, "make a project report", code=LOOP)
        assert d["action"] == "project_report"
        src = _app_js()
        assert "setSayMoreContinuation(data.speech_more" in src
        assert "action === 'say_more'" in src
        assert "function sayMore(" in src

    def test_say_more_routes_distinctly_from_more_help(self, client):
        assert _vc(client, "say more")["action"] == "say_more"
        assert _vc(client, "say more to hear next steps")["action"] == "say_more"
        assert _vc(client, "more examples")["action"] == "more_help"
