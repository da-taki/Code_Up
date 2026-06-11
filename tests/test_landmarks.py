"""
Accessible code-map landmarks / bookmarks (Sprint 2, Feature 5).

Named, session-scoped code bookmarks (landmarks.py). Read-only with respect to
code, and deliberately separate from CodeUp's existing output bookmarks.
"""
import pytest

import app as app_module
import landmarks


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


LOOP = "for i in range(3):\n    print(i)\n"


def _vc(client, text, **kw):
    return client.post("/voice-command", json={"text": text, "code": LOOP, **kw}).get_json()


# =====================================================================
# Pure landmark store
# =====================================================================

class TestLandmarkStore:

    def test_create_get_list_delete_clear(self):
        store = {}
        c = landmarks.create_landmark(store, "main loop", line=1, end_line=2,
                                      block_type="for loop", preview="for i in range(3):")
        assert c["action"] == "bookmark_created"
        assert "main loop" in c["message"]
        g = landmarks.get_landmark(store, "main loop")
        assert g["action"] == "bookmark_read" and g["line"] == 1
        lst = landmarks.list_landmarks(store)
        assert lst["action"] == "bookmark_list" and len(lst["items"]) == 1
        d = landmarks.delete_landmark(store, "main loop")
        assert d["action"] == "bookmark_deleted"
        assert landmarks.list_landmarks(store)["items"] == []

    def test_get_missing(self):
        assert landmarks.get_landmark({}, "nope")["action"] == "bookmark_error"

    def test_clear(self):
        store = {"a": {"name": "a", "line": 1, "end_line": 1}}
        landmarks.clear_landmarks(store)
        assert store == {}


# =====================================================================
# Route lifecycle (session-scoped)
# =====================================================================

class TestLandmarkRoute:

    def test_bookmark_after_navigation(self, client):
        _vc(client, "go to the loop")
        d = _vc(client, "bookmark this loop as main loop")
        assert d["action"] == "bookmark_created"
        assert "main loop" in d["message"]

    def test_go_to_bookmark_returns_line(self, client):
        _vc(client, "go to the loop")
        _vc(client, "bookmark this loop as main loop")
        d = _vc(client, "go to bookmark main loop")
        assert d["action"] == "bookmark_read"
        assert d["line"] == 1

    def test_list_and_delete_and_clear(self, client):
        _vc(client, "go to the loop")
        _vc(client, "bookmark this loop as main loop")
        lst = _vc(client, "list bookmarks")
        assert lst["action"] == "bookmark_list"
        assert "main loop" in lst["message"]
        d = _vc(client, "delete bookmark main loop")
        assert d["action"] == "bookmark_deleted"
        cleared = _vc(client, "clear bookmarks")
        assert cleared["action"] == "bookmark_deleted"

    def test_missing_target_asks_to_navigate(self, client):
        # Fresh session, no navigation and empty editor -> ask.
        d = client.post("/voice-command", json={"text": "bookmark this loop as main loop", "code": ""}).get_json()
        assert d["action"] == "bookmark_error"
        assert "go to the loop" in d["message"].lower()

    def test_bookmarks_are_session_scoped(self, client):
        _vc(client, "go to the loop")
        _vc(client, "bookmark this loop as main loop")
        # A different session/client has no landmarks.
        other = app_module.app.test_client()
        d = other.post("/voice-command", json={"text": "go to bookmark main loop", "code": LOOP}).get_json()
        # No landmark named "main loop" here -> falls through to output bookmark read.
        assert d["action"] != "bookmark_read"

    def test_bookmarking_does_not_edit_code(self, client):
        _vc(client, "go to the loop")
        d = _vc(client, "bookmark this loop as main loop")
        assert "ai_action" not in d and "newCode" not in d

    def test_output_bookmark_still_works(self, client):
        # Regression: bare "bookmark this" is still the existing output bookmark.
        d = _vc(client, "bookmark this")
        assert d["action"] == "bookmark_output"
