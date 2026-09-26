"""Vision-Aid follow-up pass: behaviors verified end to end.

* multiple diagnostics through the real run + voice paths (one navigator);
* Python-aware speech (exact spoken strings, both engines);
* voice lifecycle and instructor announcements (real JS in Node);
* instructor command center (help first, current assignment, presets);
* IDE / classroom draft isolation and voice resume wiring;
* reachability of the expanded curriculum and guided learning.
"""

import os
import re
import shutil
import subprocess
import uuid

import pytest

import app as app_module
from codeup.accessibility.speech_output import python_code_to_speech
from codeup.classroom import ai_policy
from codeup.learning import tutorial_engine

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_JS = open(os.path.join(ROOT, "static", "app.js"), encoding="utf-8").read()
INDEX_HTML = open(os.path.join(ROOT, "templates", "index.html"), encoding="utf-8").read()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _vc(client, text, code=""):
    return client.post("/voice-command", json={"text": text, "code": code}).get_json()


def _node(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    result = subprocess.run([node, os.path.join(ROOT, "tests", script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "groups passed" in result.stdout
    return result.stdout


# ---- multiple diagnostics ------------------------------------------------------------

TWO_PROBLEMS = "print(score)\nx = 1\nprint(total)\n"


def test_failed_run_keeps_every_known_problem_runtime_first(client):
    data = client.post("/run", json={"code": TWO_PROBLEMS}).get_json()
    assert data["success"] is False
    assert [d["line"] for d in data["diagnostics"]] == [1, 3]
    assert data["diagnostics"][0]["category"] == "runtime"
    assert data["diagnostic_summary"].startswith("CodeUp found 2 problems.")


def test_diagnostic_navigation_single_handler(client):
    client.post("/run", json={"code": TWO_PROBLEMS})
    assert "knows about 2 problems" in _vc(client, "how many errors", TWO_PROBLEMS)["speech"]
    nxt = _vc(client, "next error", TWO_PROBLEMS)
    assert (nxt["line"], nxt["intent"]) == (3, "diagnostic_navigation")
    assert "the last one" in nxt["speech"]
    again = _vc(client, "next error", TWO_PROBLEMS)
    assert again["speech"].startswith("That is the last problem.")
    prev = _vc(client, "previous error", TWO_PROBLEMS)
    assert prev["line"] == 1 and prev["intent"] == "diagnostic_navigation"
    assert _vc(client, "last error", TWO_PROBLEMS)["line"] == 3
    assert _vc(client, "first error", TWO_PROBLEMS)["line"] == 1
    everything = _vc(client, "read all errors", TWO_PROBLEMS)["speech"]
    assert "1. Line 1" in everything and "2. Line 3" in everything
    explain = _vc(client, "explain this error", TWO_PROBLEMS)["speech"]
    assert "Line 1: Line 1" not in explain and "Possible fix" in explain
    assert _vc(client, "fix this error", TWO_PROBLEMS)["action"] == "fix"


def test_successful_run_clears_diagnostics(client):
    client.post("/run", json={"code": TWO_PROBLEMS})
    client.post("/run", json={"code": "score = 1\ntotal = 2\nprint(score, total)\n"})
    assert "no known problems" in _vc(client, "how many errors")["speech"].lower()


def test_syntax_check_speaks_navigable_summary_not_categories():
    block = APP_JS[APP_JS.index("async function checkSyntaxErrors"):]
    block = block[:block.index("} catch (e)")]
    assert "speak(data.summary" in block
    assert "${e.type} on line" not in block


# ---- Python-aware speech ------------------------------------------------------------------

@pytest.mark.parametrize("code,spoken", [
    ("print", "print"),
    ("print()", "print open parenthesis close parenthesis"),
    ("x = 5", "x equals 5"),
    ("x == 5", "x equals equals 5"),
    ("x != 5", "x not equals 5"),
    ("items[0]", "items open bracket 0 close bracket"),
    ('d = {"a": 1}', "d equals open brace double quote a double quote colon 1 close brace"),
    ("if marks >= 40:", "if marks greater than or equal to 40 colon"),
    ("total = 3.14", "total equals 3 point 14"),
    ("student.name", "student dot name"),
    ("def greet(name):", "def greet open parenthesis name close parenthesis colon"),
    ('    print("Hi, you")', 'indented, print open parenthesis double quote Hi, you double quote close parenthesis'),
])
def test_backend_python_speech_exact(code, spoken):
    assert python_code_to_speech(code) == spoken


def test_frontend_python_speech_matches_and_is_mode_aware():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    start = APP_JS.index("function speakPythonCodeLine(code) {")
    end = APP_JS.index("// Code read aloud as part of a sentence.")
    script = APP_JS[start:end] + """
let _speechMode = 'codeup-voice';
function speakableCode(code) { return _speechMode === 'codeup-voice' ? speakPythonCodeLine(code) : String(code || ''); }
const out = [speakPythonCodeLine('print'), speakPythonCodeLine('print()'), speakPythonCodeLine('total = 3.14'),
             speakableCode('print()')];
_speechMode = 'sr-safe';
out.push(speakableCode('print()'));
console.log(JSON.stringify(out));
"""
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    import json
    voice_print, voice_call, voice_float, voice_mode, sr_mode = json.loads(result.stdout)
    assert voice_print == "print"
    assert voice_call == "print open parenthesis close parenthesis"
    assert voice_float == "total equals 3 point 14"
    assert voice_mode == voice_call
    assert sr_mode == "print()", "Screen Reader Safe leaves punctuation to the screen reader"


def test_code_reading_paths_use_speakable_code():
    for fn in ("function readLine(", "function readCurrentLine(", "function readMyCodeAloud(",
               "async function readLineEnhanced("):
        body = APP_JS[APP_JS.index(fn):]
        body = body[:body.index("\n}\n")]
        assert "speakableCode(" in body, fn


def test_prose_is_not_symbolized():
    assert app_module.sanitize_speech_text("Call the print function.") == "Call the print function."


# ---- voice lifecycle and instructor announcements (real JS) ----------------------------

def test_voice_input_lifecycle_behaviour():
    out = _node("voice_input_lifecycle.test.js")
    assert "14 groups passed" in out


def test_instructor_announcements_behaviour():
    assert "3 groups passed" in _node("instructor_sync_announce.test.js")


def test_app_mirrors_voice_input_status_and_resumes_after_navigation():
    assert "VoiceEngine.VoiceInput.onStatusChange(" in APP_JS
    assert "resumeVoiceAfterNavigation();" in APP_JS
    toggle = APP_JS[APP_JS.index("function toggleVoice()"):]
    toggle = toggle[:toggle.index("\n}\n")]
    assert "markVoiceListeningOff();\n        }\n      }, 1500);" not in toggle, "no timer switching voice off"
    assert "Waiting for the microphone" in toggle


def test_language_change_uses_the_running_recognizer():
    assert "changeRecognitionLanguage(e.target.value)" in INDEX_HTML
    assert "stopListening();\n          setTimeout(function () { startListening(); }, 800);" not in INDEX_HTML


# ---- IDE / classroom draft isolation ------------------------------------------------------------

def test_classroom_pages_never_share_the_plain_ide_draft():
    recover = APP_JS[APP_JS.index("function recoverAutosaveDraft() {"):]
    recover = recover[:recover.index("\n}\n")]
    assert "if (classroomContextKey()) return;" in recover
    assert "localStorage.setItem(AUTOSAVE_KEY" not in APP_JS
    assert "localStorage.getItem(AUTOSAVE_KEY)" not in APP_JS
    assert "AUTOSAVE_KEY + ':' + ctx" in APP_JS


# ---- instructor command center ----------------------------------------------------------------

def _instructor_with_cohort(c):
    user = "va_" + uuid.uuid4().hex[:8]
    c.post("/classroom/instructor/register",
           data={"username": user, "password": "correct-horse-1", "display_name": "Ms Rao"})
    r = c.post("/classroom/cohorts", data={"name": "Vision Aid"}, follow_redirects=True)
    cohort_id = re.search(rb'cohorts/(\d+)"', r.data).group(1).decode()
    page = c.get(f"/classroom/cohorts/{cohort_id}").data
    join_code = re.search(rb'cu-join-code">([A-Z0-9]+)<', page).group(1).decode()
    return cohort_id, join_code


def _learner_with_help(join_code, name, message):
    learner = app_module.app.test_client()
    learner.post("/classroom/join-api", json={"join_code": join_code, "display_name": name})
    learner.post("/classroom/help-requests", json={"message": message})
    return learner


def test_dashboard_puts_help_requests_first_with_unambiguous_actions():
    instructor = app_module.app.test_client()
    cohort_id, join_code = _instructor_with_cohort(instructor)
    _learner_with_help(join_code, "Asha", "My loop never stops")
    html = instructor.get(f"/classroom/cohorts/{cohort_id}").data.decode()
    assert html.index('id="helpNowHeading"') < html.index('id="learnersHeading"') < html.index('id="newAssignmentHeading"')
    assert "Needs help now (1)" in html and "My loop never stops" in html
    assert 'aria-label="Start helping Asha"' in html
    assert "aria-label=\"Mark Asha&#39;s request resolved\"" in html or "aria-label=\"Mark Asha's request resolved\"" in html
    assert "<th scope=\"col\">Current assignment</th>" in html
    assert 'data-last-event-id="' in html


def test_live_summary_carries_help_requests_current_assignment_and_submissions():
    instructor = app_module.app.test_client()
    cohort_id, join_code = _instructor_with_cohort(instructor)
    r = instructor.post(f"/classroom/cohorts/{cohort_id}/assignments",
                        data={"title": "Marks", "instructions": "x", "ai_policy": "GUIDED_PRACTICE"})
    assignment_id = re.search(r"assignments/(\d+)", r.headers["Location"]).group(1)
    instructor.post(f"/classroom/assignments/{assignment_id}/publish")
    learner = _learner_with_help(join_code, "Ravi", "NameError")
    learner.get(f"/classroom/assignments/{assignment_id}/open")
    data = instructor.get(f"/classroom/cohorts/{cohort_id}/live-summary").get_json()
    assert data["help_requests"][0]["learner_name"] == "Ravi"
    assert data["help_requests"][0]["message"] == "NameError"
    row = data["learners"][0]
    assert row["help"] == "Waiting for help"
    assert row["current_assignment"] == "Marks"
    assert re.match(r"^(Just now|\d+ min ago)$", row["last_active_at"])
    assert data["assignments"][0]["submitted"] == "0 of 1"
    assert data["assignments"][0]["ai_policy_label"] == "Guided practice"


def test_resolving_from_the_dashboard_returns_there_and_next_is_not_an_open_redirect():
    instructor = app_module.app.test_client()
    cohort_id, join_code = _instructor_with_cohort(instructor)
    _learner_with_help(join_code, "Meera", "help")
    hr_id = instructor.get(f"/classroom/cohorts/{cohort_id}/live-summary").get_json()["help_requests"][0]["id"]
    r = instructor.post(f"/classroom/help-requests/{hr_id}/helping", data={"next": "dashboard"})
    assert r.headers["Location"].endswith(f"/classroom/cohorts/{cohort_id}#helpNowHeading")
    r = instructor.post(f"/classroom/help-requests/{hr_id}/resolve", data={"next": "https://evil.example/"})
    assert r.headers["Location"].endswith(f"/classroom/cohorts/{cohort_id}/help-requests")


def test_presets_are_explained_and_guided_practice_never_writes_code():
    assert ai_policy.PRESET_INFO["GUIDED_PRACTICE"][0] == "Guided practice"
    guided = ai_policy.PRESETS["GUIDED_PRACTICE"]
    assert guided["generate"] is False and guided["fix"] is False
    assert guided["hint"] and guided["explain"] and guided["error_help"]
    assert set(ai_policy.CAPABILITY_DESCRIPTIONS) == set(ai_policy.CAPABILITIES)
    instructor = app_module.app.test_client()
    cohort_id, _ = _instructor_with_cohort(instructor)
    html = instructor.get(f"/classroom/cohorts/{cohort_id}").data.decode()
    assert "<legend>AI assistance for this assignment</legend>" in html
    for label in ("Learning", "Guided practice", "Assessment"):
        assert f">{label}</label>" in html


def test_assignment_permissions_each_have_a_description():
    instructor = app_module.app.test_client()
    cohort_id, _ = _instructor_with_cohort(instructor)
    r = instructor.post(f"/classroom/cohorts/{cohort_id}/assignments", data={"title": "T", "ai_policy": "FULL"})
    html = instructor.get(r.headers["Location"]).data.decode()
    for cap in ai_policy.CAPABILITIES:
        assert f'aria-describedby="cap_{cap}_desc"' in html
    assert "never writes or fixes the code" in html


def test_help_queue_buttons_name_the_learner():
    instructor = app_module.app.test_client()
    cohort_id, join_code = _instructor_with_cohort(instructor)
    _learner_with_help(join_code, "Zoya", "stuck")
    html = instructor.get(f"/classroom/cohorts/{cohort_id}/help-requests").data.decode()
    assert 'aria-label="Mark as helping Zoya"' in html


# ---- curriculum and guided learning reachability ------------------------------------------------

def test_tutorial_modules_route_keeps_core_order_and_adds_practice_topics(client):
    data = client.get("/tutorial/modules").get_json()
    assert data["order"] == ["print", "variables", "if", "for", "while"]
    assert data["count"] == 5
    assert len(data["practice_order"]) >= 31
    assert "dictionaries" in data["practice_modules"]


def test_expanded_topics_reachable_by_voice_and_typing(client):
    listing = _vc(client, "list tutorial topics")
    assert "Dictionaries" in listing["speech"] and "Return values" in listing["speech"]
    practise = _vc(client, "practise dictionaries")
    assert (practise["action"], practise["module"]) == ("tutorial_practice", "dictionaries")
    assert _vc(client, "practice type conversion")["module"] == "type_conversion"
    assert _vc(client, "practise for loops")["module"] == "for"
    assert tutorial_engine.practice_module_for("recursion") is None


def test_ui_entry_points_exist_for_topics_and_guided_learning():
    assert 'id="tutorialTopicsDetails"' in INDEX_HTML and 'id="tutorialTopicsList"' in INDEX_HTML
    assert 'id="guidedLearningBtn"' in INDEX_HTML
    assert "handleCommandText('start guided learning')" in INDEX_HTML
    tutorial_js = open(os.path.join(ROOT, "static", "tutorial.js"), encoding="utf-8").read()
    assert "practice_modules" in tutorial_js and "self.practice(moduleId)" in tutorial_js
