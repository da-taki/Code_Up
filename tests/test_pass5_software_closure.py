import json
import os

import pytest

import app as app_module


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "false")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    snippets_file = tmp_path / "snippets.json"
    snippets_file.write_text(json.dumps({"snippets": []}), encoding="utf-8")
    monkeypatch.setattr(app_module, "SNIPPETS_FILE", str(snippets_file))
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def vc(client, text, code="", **extra):
    payload = {"text": text, "code": code, "language": "en"}
    payload.update(extra)
    return client.post("/voice-command", json=payload, headers={"Origin": "http://localhost"}).get_json()


def assert_clarifies_without_edit(data, reason=None):
    assert data["success"] is True
    assert data["action"] == "clarify"
    assert data.get("needs_clarification") is True
    assert "ai_action" not in data
    if reason:
        assert data.get("reason") == reason


def test_nl_edit_ambiguous_function_name_clarifies(client):
    data = vc(client, "add a function to add numbers")
    assert_clarifies_without_edit(data, "invalid_function_name")
    assert "function" in data["speech"].lower()


def test_nl_edit_valid_function_name_routes_deterministically(client):
    data = vc(client, "add a function called greet")
    assert data["action"] == "insert_function"
    assert data["function_name"] == "greet"


def test_nl_edit_duplicate_function_name_clarifies(client):
    code = "def greet():\n    pass\n"
    data = vc(client, "add a function called greet", code=code)
    assert_clarifies_without_edit(data, "function_name_collision")


def test_nl_edit_keyword_class_name_clarifies(client):
    data = vc(client, "add a class called for")
    assert_clarifies_without_edit(data, "invalid_class_name")


def test_nl_edit_valid_class_name_routes_deterministically(client):
    data = vc(client, "add a class called Student")
    assert data["action"] == "insert_class"
    assert data["class_name"] == "Student"


def test_nl_edit_missing_parameter_name_clarifies(client):
    code = "def greet():\n    pass\n"
    data = vc(client, "add a parameter to greet", code=code)
    assert_clarifies_without_edit(data, "missing_parameter_name")


def test_nl_edit_parameter_to_named_function_routes_deterministically(client):
    code = "def greet():\n    pass\n"
    data = vc(client, "add parameter name to greet", code=code)
    assert data["action"] == "add_parameter"
    assert data["param_name"] == "name"
    assert data["function_name"] == "greet"


def test_nl_edit_parameter_without_target_clarifies_when_multiple_functions(client):
    code = "def greet():\n    pass\n\ndef bye():\n    pass\n"
    data = vc(client, "add parameter name", code=code)
    assert_clarifies_without_edit(data, "ambiguous_parameter_target")


def test_nl_edit_bare_if_statement_clarifies(client):
    data = vc(client, "insert an if statement")
    assert_clarifies_without_edit(data, "missing_if_condition")


def test_nl_edit_explicit_if_condition_is_preserved(client):
    data = vc(client, "add if score greater than 10")
    assert data["action"] == "insert_if"
    assert data["condition"] == "score greater than 10"


def test_learning_grade_without_activity_is_clear(client):
    data = vc(client, "grade my attempt", code="print('hi')\n")
    assert data["action"] == "deterministic_message"
    assert data["intent"] == "understanding_grade"
    assert "do not have an answer to grade" in data["speech"].lower()


def test_reset_lesson_clears_only_active_lesson_progress(client):
    client.set_cookie(app_module.SESSION_COOKIE_NAME, "pass5-reset-lesson")
    vc(client, "start loops lesson")
    vc(client, "complete lesson")
    before = vc(client, "lesson status")
    assert "Completed 1" in before["message"]

    reset = vc(client, "reset lesson")
    assert reset["literacy_command"] == "reset_lesson"
    assert reset["lesson_id"] == "loops"
    assert "editor and other lessons were not changed" in reset["message"]

    after = vc(client, "lesson status")
    assert "Completed 0" in after["message"]


def test_tutorial_validate_does_not_trust_client_ran_ok_without_matching_server_run(client):
    code = 'print("Hello")\n'
    data = client.post(
        "/tutorial/validate",
        json={"module": "print", "code": code, "ran_ok": True, "output": "Hello"},
        headers={"Origin": "http://localhost"},
    ).get_json()

    assert data["success"] is True
    assert data["passed"] is False


def test_tutorial_validate_accepts_matching_successful_server_run(client):
    code = 'print("Hello")\n'
    run = client.post("/run", json={"code": code, "language": "en"}, headers={"Origin": "http://localhost"}).get_json()
    assert run["success"] is True

    data = client.post(
        "/tutorial/validate",
        json={"module": "print", "code": code, "ran_ok": True, "output": run["output"]},
        headers={"Origin": "http://localhost"},
    ).get_json()

    assert data["success"] is True
    assert data["passed"] is True


# ---------------------------------------------------------------------------
# Section 4: rate-limit / execution-budget identity
# ---------------------------------------------------------------------------

from codeup.classroom import db as classroom_db  # noqa: E402


def _run(client, code="print(1)"):
    return client.post("/run", json={"code": code, "language": "en"}, headers={"Origin": "http://localhost"})


def _cookie_value(client_obj, name):
    cookie = client_obj.get_cookie(name)
    return getattr(cookie, "value", cookie)


def test_session_id_is_a_stable_server_verified_identity_across_requests(client):
    client.get("/")
    first_raw = _cookie_value(client, app_module.SESSION_COOKIE_NAME)
    first_id = app_module._verify_session_id(first_raw)
    assert first_id

    client.get("/")
    second_raw = _cookie_value(client, app_module.SESSION_COOKIE_NAME)
    assert second_raw == first_raw
    assert app_module._verify_session_id(second_raw) == first_id


def test_tampered_session_cookie_mints_fresh_budget_not_a_free_reset_of_the_blocked_one(client, monkeypatch):
    monkeypatch.setattr(app_module, "RUN_RATE_LIMIT", 2)
    assert _run(client, "print(1)").status_code == 200
    assert _run(client, "print(2)").status_code == 200
    assert _run(client, "print(3)").status_code == 429

    client.set_cookie(app_module.SESSION_COOKIE_NAME, "forged-not-signed-by-server")
    tampered = _run(client, "print(4)")
    assert tampered.status_code == 200
    new_raw = _cookie_value(client, app_module.SESSION_COOKIE_NAME)
    assert new_raw != "forged-not-signed-by-server"
    assert app_module._verify_session_id(new_raw)


def test_unsigned_cookie_cannot_impersonate_another_sessions_identity(client, monkeypatch):
    monkeypatch.setattr(app_module, "RUN_RATE_LIMIT", 1)
    with app_module.app.test_client() as victim:
        assert _run(victim, "print('victim')").status_code == 200
        victim_raw = _cookie_value(victim, app_module.SESSION_COOKIE_NAME)
    victim_id = app_module._verify_session_id(victim_raw)
    assert victim_id

    with app_module.app.test_client() as attacker:
        attacker.set_cookie(app_module.SESSION_COOKIE_NAME, victim_id)
        attacker.get("/")
        attacker_raw = _cookie_value(attacker, app_module.SESSION_COOKIE_NAME)
        assert app_module._verify_session_id(attacker_raw) != victim_id
        assert _run(attacker, "print('attacker')").status_code == 200


def test_new_visitor_with_no_cookie_gets_its_own_full_budget(monkeypatch):
    monkeypatch.setattr(app_module, "RUN_RATE_LIMIT", 1)
    with app_module.app.test_client() as a:
        assert _run(a, "print(1)").status_code == 200
        assert _run(a, "print(2)").status_code == 429
    with app_module.app.test_client() as b:
        assert _run(b, "print(3)").status_code == 200


def test_two_classroom_learners_sharing_a_browser_remain_distinct_budgets(client, monkeypatch):
    monkeypatch.setattr(app_module, "RUN_RATE_LIMIT", 1)
    instructor = classroom_db.create_instructor("pass5-teacher", "hashed", "Ms Pass5")
    cohort = classroom_db.create_cohort(instructor["id"], "Pass5 Cohort")
    learner_a = classroom_db.join_cohort(cohort["id"], "Learner A")
    learner_b = classroom_db.join_cohort(cohort["id"], "Learner B")

    client.set_cookie(app_module.CLASSROOM_LEARNER_COOKIE, learner_a["token"])
    assert _run(client, "print('a')").status_code == 200
    assert _run(client, "print('a again')").status_code == 429

    client.set_cookie(app_module.CLASSROOM_LEARNER_COOKIE, learner_b["token"])
    assert _run(client, "print('b')").status_code == 200


# ---------------------------------------------------------------------------
# Section 5: cohort dashboard performance
# ---------------------------------------------------------------------------

import time as _time
from contextlib import contextmanager

from codeup.classroom import concepts as classroom_concepts, reports as classroom_reports


def _seed_cohort(n_learners, *, teacher_suffix):
    instructor = classroom_db.create_instructor(f"pass5-perf-{teacher_suffix}", "h", "Ms Perf")
    cohort = classroom_db.create_cohort(instructor["id"], f"Perf Cohort {teacher_suffix}")
    assignment = classroom_db.create_assignment(
        cohort["id"], "Marks", "Do it", "marks = {}", None, ["dictionaries"], "FULL",
    )
    classroom_db.publish_assignment(assignment["id"])
    for i in range(n_learners):
        learner = classroom_db.join_cohort(cohort["id"], f"Learner {i}")
        classroom_db.submit_assignment(assignment["id"], learner["id"], "marks = {'a': 1}")
        classroom_concepts.record_assignment_submitted(
            learner["id"], cohort["id"], "marks = {'a': 1}\nprint(marks)", ["dictionaries"]
        )
    return cohort


def test_cohort_report_issues_a_fixed_number_of_queries_not_one_per_learner(monkeypatch):
    cohort = _seed_cohort(12, teacher_suffix="qcount")

    call_count = {"n": 0}
    orig_connect = classroom_db.connect

    @contextmanager
    def counting_connect():
        call_count["n"] += 1
        with orig_connect() as conn:
            yield conn

    monkeypatch.setattr(classroom_db, "connect", counting_connect)
    report = classroom_reports.build_cohort_report(cohort["id"])
    assert len(report["rows"]) == 12

    # Previously ~2 queries per learner (assignment progress + concept
    # progress), so 12 learners meant ~24+ round trips. Batched queries keep
    # this to a small constant regardless of cohort size.
    assert call_count["n"] <= 6, f"expected batched queries, got {call_count['n']} connect() calls for 12 learners"


def test_cohort_dashboard_scales_sub_linearly_not_quadratically(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cohort_50 = _seed_cohort(50, teacher_suffix="fifty")
    cohort_100 = _seed_cohort(100, teacher_suffix="hundred")

    start = _time.perf_counter()
    report_50 = classroom_reports.build_cohort_report(cohort_50["id"])
    elapsed_50_ms = (_time.perf_counter() - start) * 1000

    start = _time.perf_counter()
    report_100 = classroom_reports.build_cohort_report(cohort_100["id"])
    elapsed_100_ms = (_time.perf_counter() - start) * 1000

    assert len(report_50["rows"]) == 50
    assert len(report_100["rows"]) == 100

    # A pathological O(n) *connections* implementation (what Pass 3 measured:
    # 50 -> ~715ms, 100 -> ~2549ms, a >3x jump for 2x the learners) would
    # roughly double-then-some here too. Batched queries make cohort size
    # barely matter - generous ceiling to stay non-flaky across machines.
    assert elapsed_100_ms < max(2000.0, elapsed_50_ms * 3), (
        f"cohort report time grew super-linearly: 50 learners={elapsed_50_ms:.1f}ms, "
        f"100 learners={elapsed_100_ms:.1f}ms"
    )


# ---------------------------------------------------------------------------
# Section 8: decimal input parsing
# ---------------------------------------------------------------------------

def _set_input(client, phrase):
    return vc(client, phrase, code="x = input()")


def test_set_input_leading_decimal_point_is_not_corrupted(client):
    data = _set_input(client, "set input to .5")
    assert data["values"] == [".5"]


@pytest.mark.parametrize("phrase,expected", [
    ("set input to .5", [".5"]),
    ("set input to 0.5", ["0.5"]),
    ("set input to -.5", ["-.5"]),
    ("set input to -0.5", ["-0.5"]),
    ("set input to 1.", ["1"]),
    ("set input to 0", ["0"]),
    ("set input to 00.5", ["00.5"]),
])
def test_decimal_input_variants_preserve_the_exact_numeric_value(client, phrase, expected):
    data = _set_input(client, phrase)
    assert data["values"] == expected


def test_trailing_sentence_period_is_still_stripped(client):
    data = _set_input(client, "set input to 16.")
    assert data["values"] == ["16"]


def test_multi_value_decimal_list_preserves_leading_dot_on_each_value(client):
    data = _set_input(client, "set input to .5 and 3.")
    assert data["values"] == [".5", "3"]


# ---------------------------------------------------------------------------
# Section 6: sandbox defense-in-depth (environment minimization)
# ---------------------------------------------------------------------------

def test_sandbox_env_excludes_server_secrets(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "secret-groq-should-not-leak")
    monkeypatch.setenv("GROQ_API_KEY_2", "secret-groq-2-should-not-leak")
    monkeypatch.setenv("GEMINI_API_KEY", "secret-gemini-should-not-leak")
    monkeypatch.setenv("FLASK_SECRET_KEY", "secret-flask-key-should-not-leak")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@host/db-should-not-leak")

    env = app_module._minimal_sandbox_env()

    for secret_name in ("GROQ_API_KEY", "GROQ_API_KEY_2", "GEMINI_API_KEY", "FLASK_SECRET_KEY", "DATABASE_URL"):
        assert secret_name not in env, f"{secret_name} leaked into the sandboxed subprocess environment"
    for value in env.values():
        assert "secret" not in value.lower() and "should-not-leak" not in value.lower()


def test_sandbox_env_still_has_what_the_interpreter_needs_to_start(monkeypatch):
    env = app_module._minimal_sandbox_env()
    # Whichever of these the current OS actually sets should survive -
    # without them Python itself (DLL loading on Windows, temp dirs, etc.)
    # may fail to start, which would be a functional regression, not a
    # security win.
    present = {k for k in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME") if k in os.environ}
    for key in present:
        assert key in env


def test_run_code_still_works_with_minimized_sandbox_env(client):
    data = _run(client, "print('sandbox env still works')").get_json()
    assert data["success"] is True
    assert "sandbox env still works" in data["output"]
