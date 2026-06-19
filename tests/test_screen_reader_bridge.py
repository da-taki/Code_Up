import pytest

import app as app_module
import screen_reader_bridge as srb

LOOP_OK = "for i in range(3):\n    print(i)\n"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


class TestBridge:

    def test_single_file_loop_mentions_line_indentation_and_reader(self):
        r = srb.build_screen_reader_bridge(LOOP_OK, {}, "prepare this for nvda")
        m = r["message"].lower()
        assert "line" in m
        assert "indent" in m
        assert "nvda" in m
        assert r["target"] == "NVDA"

    def test_multifile_bridge_mentions_entry_point(self):
        project = {"files": {"main.py": "import data\n", "data.py": "x = 1\n"}, "entry": "main.py"}
        r = srb.build_screen_reader_bridge("import data\nprint(1)\n", project, "screen reader bridge")
        assert "main.py" in r["message"]
        assert "entry point" in r["message"].lower()

    def test_vscode_handoff_mentions_keyboard_transition(self):
        r = srb.build_screen_reader_bridge(LOOP_OK, {}, "help me move this to vs code")
        m = r["message"].lower()
        assert "vs code" in m
        assert "keyboard" in m or "line-by-line" in m

    def test_empty_code_asks_for_code(self):
        r = srb.build_screen_reader_bridge("", {}, "prepare this for nvda")
        assert "no code" in r["message"].lower()

    def test_does_not_claim_to_replace_screen_readers(self):
        r = srb.build_screen_reader_bridge(LOOP_OK, {}, "prepare this for jaws")
        m = r["message"].lower()
        assert "not a replacement" in m
        assert "codeup replaces" not in m


class TestRoute:

    def test_prepare_for_nvda_routes(self, client):
        d = client.post("/voice-command", json={"text": "prepare this for NVDA", "code": LOOP_OK}).get_json()
        assert d["action"] == "deterministic_message"
        assert d.get("screen_reader_bridge") is True
        assert "not a replacement" in d["message"].lower()

    def test_move_to_vs_code_routes(self, client):
        d = client.post("/voice-command", json={"text": "help me move this to VS Code", "code": LOOP_OK}).get_json()
        assert d.get("screen_reader_bridge") is True
        assert "vs code" in d["message"].lower()
