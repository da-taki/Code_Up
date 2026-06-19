import pytest

import app as app_module
import trainer_review

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


class TestReview:

    def test_empty_session_gives_limited_review(self):
        r = trainer_review.build_trainer_review("", {}, {})
        assert r.get("limited") is True
        assert "next activity" in r["message"].lower()

    def test_run_error_hint_fix_mentions_debugging(self):
        mem = {"features_used": ["ran code", "debugged errors"],
               "concepts_practiced": ["loops"], "last_run_ok": True,
               "last_run_error": "", "hint_level": "answer"}
        r = trainer_review.build_trainer_review(LOOP_OK, {}, mem)
        assert "debug" in (r["message"] + r["speech"]).lower()

    def test_handoff_features_mentioned(self):
        mem = {"features_used": ["exported the project", "made a project report", "reviewed the session"]}
        r = trainer_review.build_trainer_review(LOOP_OK, {}, mem)
        low = r["message"].lower()
        assert "export" in low or "report" in low or "recap" in low

    def test_multifile_project_mentions_structure(self):
        project = {"files": {"main.py": "import utils\n", "utils.py": "x = 1\n"}, "entry": "main.py"}
        r = trainer_review.build_trainer_review("import utils\n", project, {"features_used": ["ran code"]})
        low = r["message"].lower()
        assert "multi-file" in low or "files" in low
        assert "main.py" in r["message"]

    def test_next_activity_included(self):
        r = trainer_review.build_trainer_review(LOOP_OK, {}, {"features_used": ["ran code"]})
        assert "next activity" in r["message"].lower()
        assert r.get("next_activity")

    def test_does_not_invent_details(self):
        r = trainer_review.build_trainer_review(LOOP_OK, {}, {"features_used": ["ran code"]})
        low = (r["message"] + r["speech"]).lower()
        for forbidden in ("school", "nab", "excellent", "struggled", "brilliant", "lazy"):
            assert forbidden not in low

    def test_differs_from_project_report(self):
        r = trainer_review.build_trainer_review(
            LOOP_OK, {}, {"features_used": ["ran code"], "concepts_practiced": ["loops"]})
        assert "trainer notes" in r["message"].lower()


class TestRoute:

    def test_make_trainer_notes_routes(self, client):
        d = client.post("/voice-command", json={"text": "make trainer notes", "code": LOOP_OK}).get_json()
        assert d["action"] == "deterministic_message"
        assert d.get("trainer_review") is True

    def test_does_not_collide_with_project_report(self, client):
        d = client.post("/voice-command", json={"text": "make a project report"}).get_json()
        assert d["action"] == "project_report"
        assert d.get("trainer_review") is not True
