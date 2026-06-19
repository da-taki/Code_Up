import pytest

import app as app_module
import error_replay

BROKEN_INDENT = "for i in range(3):\nprint(i)\n"
FIXED_INDENT = "for i in range(3):\n    print(i)\n"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


class TestExplain:

    def test_no_history(self):
        r = error_replay.explain("", "")
        assert r["has_comparison"] is False
        assert "broken-and-fixed" in r["explanation"] or "do not have" in r["explanation"].lower()

    def test_indentation_fix(self):
        r = error_replay.explain(BROKEN_INDENT, FIXED_INDENT, "IndentationError")
        assert r["has_comparison"] is True
        low = r["explanation"].lower()
        assert "indent" in low
        assert "2" in r["explanation"]  # the print line

    def test_nameerror_fix(self):
        before = "print(total)\n"
        after = "total = 0\nprint(total)\n"
        r = error_replay.explain(before, after, "NameError: name 'total' is not defined")
        low = r["explanation"].lower()
        assert "total" in low
        assert "nameerror" in low or "defined" in low

    def test_diff_includes_changed_lines(self):
        r = error_replay.explain(BROKEN_INDENT, FIXED_INDENT, "IndentationError")
        assert r["changed_lines"]
        assert 2 in r["changed_lines"]

    def test_missing_colon_fix(self):
        before = "for i in range(3)\n    print(i)\n"
        after = "for i in range(3):\n    print(i)\n"
        r = error_replay.explain(before, after, "SyntaxError: expected ':'")
        assert "colon" in r["explanation"].lower()

    def test_from_snapshot_no_history(self):
        assert error_replay.from_snapshot({})["has_comparison"] is False
        assert error_replay.from_snapshot({"error_code": "x"})["has_comparison"] is False


class TestReplayRoute:

    def test_no_history_message(self, client):
        d = client.post("/voice-command", json={"text": "explain the fix", "code": FIXED_INDENT}).get_json()
        assert d["action"] == "deterministic_message"
        assert "do not have" in d["message"].lower()

    def test_replay_after_run_explains_fix(self, client):
        client.post("/run", json={"code": BROKEN_INDENT})
        client.post("/run", json={"code": FIXED_INDENT})
        d = client.post("/voice-command", json={"text": "compare broken and fixed code", "code": FIXED_INDENT}).get_json()
        assert d["action"] == "deterministic_message"
        assert "indent" in d["message"].lower()

    def test_replay_does_not_overwrite_editor_or_output(self, client):
        d = client.post("/voice-command", json={"text": "show me what went wrong", "code": FIXED_INDENT}).get_json()
        assert d["action"] == "deterministic_message"
        assert "ai_action" not in d and "newCode" not in d and "code" not in d
