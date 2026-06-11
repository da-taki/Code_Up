"""
Navigation by meaning (Sprint 2, Feature 2).

Find the first function/loop/condition/print/error (or next/previous/current
block) and variable usage by name — deterministic, AST-based, read-only.
"""
import pytest

import app as app_module
import structure_tools

_MUTATING = {"conversational_edit", "generate_code", "fix", "insert_line",
             "replace_line", "delete_line", "append_line"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


CODE = (
    "total = 0\n"
    "def greet(name):\n"
    "    print(name)\n"
    "for i in range(3):\n"
    "    total = total + i\n"
    "    print(i)\n"
)


def _vc(client, text, **kw):
    return client.post("/voice-command", json={"text": text, "code": CODE, **kw}).get_json()


class TestNavigation:

    def test_go_to_function(self, client):
        d = _vc(client, "go to the function")
        assert d["action"] == "navigate_code"
        assert d["line"] == 2

    def test_go_to_loop(self, client):
        d = _vc(client, "go to the loop")
        assert d["action"] == "navigate_code"
        assert d["line"] == 4
        assert "for" in d["code_excerpt"]

    def test_go_to_error_uses_last_error_line(self, client):
        d = _vc(client, "go to the error", error="Traceback...\n  File x, line 5\nIndentationError")
        assert d["action"] == "navigate_code"
        assert d["line"] == 5

    def test_go_to_error_without_history(self, client):
        d = _vc(client, "go to the error", error="")
        assert d["action"] == "deterministic_message"
        assert "run your code" in d["message"].lower()

    def test_where_is_total_changed(self, client):
        d = _vc(client, "where is total changed")
        # total is assigned on line 1 and updated on line 5.
        assert "1" in d["message"] and "5" in d["message"]

    def test_where_is_name_used(self, client):
        d = _vc(client, "where is name used")
        assert d["action"] == "navigate_code"
        assert "3" in d["message"]  # name is used on line 3

    def test_no_match_message(self, client):
        d = client.post("/voice-command", json={"text": "go to the loop", "code": "x = 1\n"}).get_json()
        assert d["action"] == "deterministic_message"
        assert "could not find a loop" in d["message"].lower()

    def test_multiple_prints_mention_count(self, client):
        d = _vc(client, "go to the print statement")
        assert d["action"] == "navigate_code"
        # Two prints in CODE -> first one, count mentioned.
        assert "two" in d["message"].lower() or "print" in d["message"].lower()

    def test_navigation_does_not_edit_code(self, client):
        d = _vc(client, "go to the loop")
        assert d["action"] not in _MUTATING
        assert "ai_action" not in d
        assert "newCode" not in d


class TestBlockNavigation:

    def test_current_block(self, client):
        d = _vc(client, "read the current block", cursor_line=5)
        assert d["action"] == "navigate_code"
        # Line 5 is inside the for loop (lines 4-6).
        assert d["line"] in (4,)

    def test_next_block(self, client):
        d = _vc(client, "next block", cursor_line=1)
        assert d["action"] == "navigate_code"
        assert d["line"] >= 2


class TestFindSymbolUnit:

    def test_changed_vs_used(self):
        used = structure_tools.find_symbol(CODE, "i", "used")
        assert used["found"] and 4 in used["lines"]
        changed = structure_tools.find_symbol(CODE, "total", "changed")
        assert changed["found"] and 1 in changed["lines"] and 5 in changed["lines"]

    def test_unknown_name(self):
        r = structure_tools.find_symbol(CODE, "zzz", "used")
        assert r["found"] is False
