import os

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


LOOP = "for i in range(3):\n    print(i)\n"
BROKEN = "for i in range(3):\nprint(i)\n"


def _action(client, text, code=LOOP):
    return client.post("/voice-command", json={"text": text, "code": code}).get_json()



class TestStepNarrationSelfEchoGuard:

    @pytest.fixture(scope="class")
    def handle_voice_src(self):
        path = os.path.join(os.path.dirname(__file__), "..", "static", "app.js")
        with open(path, encoding="utf-8") as fh:
            app_js = fh.read()
        start = app_js.index("async function handleVoiceCommand(")
        return app_js[start:start + 2500]

    def test_guard_runs_before_barge_in_cancel(self, handle_voice_src):
        src = handle_voice_src
        guard = src.index("_stepNarrationJob")
        barge = src.index("BARGE-IN")
        assert guard < barge

    def test_non_stop_input_is_ignored_during_narration(self, handle_voice_src):
        src = handle_voice_src
        block = src[src.index("_stepNarrationJob"):src.index("BARGE-IN")]
        assert "stopWords" in block
        assert "_stepNarrationJob.cancelled = true" in block
        assert "return;" in block

    def test_speech_path_is_shared_voice_engine(self):
        path = os.path.join(os.path.dirname(__file__), "..", "static", "app.js")
        with open(path, encoding="utf-8") as fh:
            app_js = fh.read()
        start = app_js.index("async function requestStepNarration()")
        end = app_js.index("// ---------- MISTAKE REPLAY ----------")
        block = app_js[start:end]
        assert "SpeechManager.enqueue(steps[i])" in block
        assert "new SpeechSynthesisUtterance" not in block



class TestHinglishCoreFlow:

    @pytest.mark.parametrize("text", ["editor clear karo", "code clear karo", "naya code shuru karo"])
    def test_clear(self, client, text):
        assert _action(client, text, code="")["action"] == "clear_editor"

    @pytest.mark.parametrize("text", ["code run karo", "program chalao"])
    def test_run(self, client, text):
        assert _action(client, text)["action"] == "run"

    @pytest.mark.parametrize("text", [
        "zero se two tak ek loop banao jo har number print kare",
        "editor mein zero se two tak loop add karo aur har value print karo",
    ])
    def test_create_loop_is_deterministic_and_exact(self, client, text):
        d = _action(client, text, code="")
        assert d["action"] == "conversational_edit"
        aa = d["ai_action"]
        assert aa["action"] == "append_code"
        assert aa["code"] == "for i in range(3):\n    print(i)"

    @pytest.mark.parametrize("text", [
        "print statement ke pehle wali indentation hata do taaki error dikhe",
        "print ko loop ke bahar kar do taaki error aaye",
    ])
    def test_introduce_error_dedents(self, client, text):
        d = _action(client, text, code=LOOP)
        assert d["action"] == "conversational_edit"
        assert d["ai_action"]["action"] == "dedent_line"

    @pytest.mark.parametrize("text", [
        "indentation error fix karo",
        "print statement ko loop ke andar karo",
        "print ke pehle four spaces add karo",
    ])
    def test_fix_indentation_indents(self, client, text):
        d = _action(client, text, code=BROKEN)
        assert d["action"] == "conversational_edit"
        assert d["ai_action"]["action"] == "indent_line"

    @pytest.mark.parametrize("text", [
        "is program ko samjhao",
        "ye code kya karta hai batao",
        "code ko step by step samjhao",
    ])
    def test_explain_program_is_walkthrough(self, client, text):
        assert _action(client, text)["action"] == "walk_through"

    @pytest.mark.parametrize("text", ["step by step run karke samjhao", "har step narrate karo"])
    def test_step_narration(self, client, text):
        assert _action(client, text)["action"] == "step_narration"

    @pytest.mark.parametrize("text", ["block sonify karo", "is block ka audio structure sunao"])
    def test_sonify(self, client, text):
        assert _action(client, text)["action"] == "sonify_block"

    def test_snippet_save_list_load(self, client):
        save = _action(client, "is code ko first loop naam se snippet save karo")
        assert save["action"] == "save_snippet_named"
        assert save["name"].lower() == "first loop"
        assert _action(client, "mere snippets dikhao")["action"] == "list_snippets"
        assert _action(client, "first loop wala snippet load karo")["action"] == "load_snippet"

    @pytest.mark.parametrize("text", ["save snippet", "save this snippet"])
    def test_bare_save_snippet_matches_the_guide_canonical_phrase(self, client, text):
        # CodeUp User Guide section 9 documents the bare "save snippet"
        # (no name) as its own canonical command, distinct from the named
        # "save snippet as <name>" form - it previously fell through to
        # "as a snippet" wording that this exact phrase never contains.
        assert _action(client, text)["action"] == "save_snippet_auto"

    def test_named_save_snippet_still_matches_after_the_bare_phrase_fix(self, client):
        # Guard against the new bare pattern shadowing the named form.
        named = _action(client, "save code as a snippet called greeting")
        assert named["action"] == "save_snippet_named"
        assert named["name"].lower() == "greeting"

    @pytest.mark.parametrize("text", [
        "range three ka matlab kya hai?",
        "loop kya hota hai?",
        "print statement indented kyun hai?",
        "print function kya karta hai?",
        "i yahan kya represent karta hai?",
    ])
    def test_concept_questions_explain_only(self, client, text):
        d = _action(client, text)
        assert d["action"] == "mentor_chat"
        assert d["mode"] == "concept"
        assert d["action"] not in {"conversational_edit", "run", "walk_through"}

    def test_english_flow_unchanged(self, client):
        assert _action(client, "clear editor", code="")["action"] == "clear_editor"
        assert _action(client, "run")["action"] == "run"
        assert _action(client, "walk me through this program")["action"] == "walk_through"
        assert _action(client, "What does range three mean?")["action"] in ("mentor_chat", "deterministic_message")



class TestCodeMapSynonyms:

    @pytest.mark.parametrize("text", [
        "code map",
        "map my code",
        "show me the code map",
        "show the code map",
        "explain the structure of my code",
        "show the structure of this program",
        "mere code ka map batao",
        "code ka structure samjhao",
    ])
    def test_synonyms_route_to_code_map(self, client, text):
        assert _action(client, text)["action"] == "code_map"

    def test_give_me_a_map_remains_an_equivalent_code_map(self, client):
        assert _action(client, "give me a map of my code")["action"] == "mentor_code_map"

    @pytest.mark.parametrize("text,not_action", [
        ("walk me through this program", "code_map"),
        ("read code", "code_map"),
        ("run with step narration", "code_map"),
        ("What does this code do?", "code_map"),
    ])
    def test_no_collision_with_other_features(self, client, text, not_action):
        assert _action(client, text)["action"] != not_action

    def test_code_map_does_not_mutate_or_run(self, client):
        d = _action(client, "map my code")
        assert d["action"] == "code_map"
        assert "ai_action" not in d
