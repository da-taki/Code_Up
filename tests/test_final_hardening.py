"""Final Vision-Aid hardening pass.

1. Guided learning cannot bypass classroom assistance policy (real
   assignments, real learner cookies, the one _ai_capability_check gate).
2. Diagnostics carry a source fingerprint; stale ones never drive navigation,
   explanation or fixes.
3. "fix this error" sends the selected diagnostic as structured context and
   refuses a stale selection.
4. Bare "cancel" is context-aware.
"""

import json
import re
import shutil
import subprocess

import pytest

import app as app_module
from codeup.learning import guided_learning, learner_model
from codeup.runtime import session_memory

APP_JS = open("static/app.js", encoding="utf-8").read()


def _extract(pattern, data):
    match = re.search(pattern, data)
    assert match, pattern
    return match.group(1).decode()


def _classroom_learner(policy, username, settings=None):
    """A learner who joined a cohort and opened a published assignment with
    the given preset (and optional per-capability settings form)."""
    instructor, learner = app_module.app.test_client(), app_module.app.test_client()
    instructor.post("/classroom/instructor/register",
                    data={"username": username, "password": "correct-horse-1", "display_name": "T"})
    r = instructor.post("/classroom/cohorts", data={"name": "C"}, follow_redirects=True)
    join_code = _extract(rb'cu-join-code">([A-Z0-9]+)<', r.data)
    cohort_id = _extract(rb'cohorts/(\d+)"', r.data)
    r = instructor.post(f"/classroom/cohorts/{cohort_id}/assignments",
                        data={"title": "A", "instructions": "i", "starter_code": "", "ai_policy": policy},
                        follow_redirects=True)
    assignment_id = _extract(rb"assignments/(\d+)/publish", r.data)
    instructor.post(f"/classroom/assignments/{assignment_id}/publish")
    if settings is not None:
        instructor.post(f"/classroom/assignments/{assignment_id}/settings", data=settings)
    learner.post("/classroom/join", data={"join_code": join_code, "display_name": "L"}, follow_redirects=True)
    learner.get(f"/classroom/assignments/{assignment_id}/open")
    return learner


@pytest.fixture(autouse=True)
def _no_ai(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app_module.app.config["TESTING"] = True


def _vc(client, text, code=""):
    return client.post("/voice-command", json={"text": text, "code": code}).get_json()


def _to_print_marks_step(client):
    _vc(client, "start guided learning first programs")
    assert _vc(client, "check my work", 'print("Hello")')["guided"]["index"] == 1
    assert _vc(client, "check my work", 'print("Hello")\nmarks = 80')["guided"]["index"] == 2


WRONG = 'print("Hello")\nmarks = 80\nprint("marks")'
RIGHT = 'print("Hello")\nmarks = 80\nprint(marks)'


# ---- 1. guided learning x classroom policy ------------------------------------------------

def test_assessment_guided_learning_gives_verdicts_but_no_assistance():
    learner = _classroom_learner("ASSESSMENT", "gl_assess")
    start = _vc(learner, "start guided learning first programs")
    assert start["guided"]["active"] is True
    assert "hint" not in start["speech"].lower()
    _to_print_marks_step(learner)

    failed = _vc(learner, "check my work", WRONG)
    assert failed["guided_passed"] is False
    assert "quotes" not in failed["speech"] and "marks" not in failed["speech"]
    assert failed["speech"].startswith("Not yet.")

    for command, capability in (("give me a hint", "hint"), ("another hint", "hint"),
                                ("why is this wrong", "hint"), ("show me an example", "hint"),
                                ("i don't understand", "concept_qa")):
        blocked = _vc(learner, command, WRONG)
        assert blocked["policy_blocked"] is True, command
        assert blocked["capability"] == capability, command
        assert "print(marks)" not in blocked["speech"] and "instructor has turned off" in blocked["speech"]

    # Harmless navigation stays available.
    assert "Now print marks" in _vc(learner, "repeat the task", WRONG)["speech"]
    assert "step 3" in _vc(learner, "my progress", WRONG)["speech"]
    assert _vc(learner, "check my work", RIGHT)["guided_passed"] is True
    # The understanding check after a pass is a question, not assistance; a
    # wrong answer gets no explanation when concept questions are off.
    miss = _vc(learner, "hello", RIGHT)
    assert miss["speech"].startswith("Not quite.") and "value stored" not in miss["speech"]


def test_guided_practice_allows_hints_but_not_worked_examples():
    learner = _classroom_learner("GUIDED_PRACTICE", "gl_guided")
    _to_print_marks_step(learner)
    failed = _vc(learner, "check my work", WRONG)
    assert "quotes" in failed["speech"], "targeted feedback is a hint, and hints are on"
    assert _vc(learner, "why is this wrong", WRONG)["speech"].startswith("You printed the word marks")
    assert _vc(learner, "give me a hint", WRONG)["speech"].startswith("Hint:")
    assert _vc(learner, "another hint", WRONG)["speech"].startswith("Stronger hint:")
    end = _vc(learner, "another hint", WRONG)
    assert "Full examples are turned off" in end["speech"] and "example_code" not in end
    blocked = _vc(learner, "show me an example", WRONG)
    assert blocked["policy_blocked"] is True and blocked["capability"] == "generate"
    assert "marks box" in _vc(learner, "i don't understand", WRONG)["speech"]


def test_individual_capabilities_not_preset_names_decide():
    # FULL preset, then the instructor switches hints off individually.
    settings = {f"cap_{cap}": "on" for cap in ("generate", "fix", "explain", "error_help", "concept_qa")}
    learner = _classroom_learner("FULL", "gl_custom", settings=settings)
    _to_print_marks_step(learner)
    assert _vc(learner, "give me a hint", WRONG)["capability"] == "hint"
    assert _vc(learner, "check my work", WRONG)["speech"].startswith("Not yet.")
    # concept questions are on, but the step explanation is task-specific, so it is a hint too
    assert _vc(learner, "i don't understand", WRONG)["capability"] == "hint"


def test_no_classroom_context_is_unrestricted():
    client = app_module.app.test_client()
    _to_print_marks_step(client)
    assert "quotes" in _vc(client, "check my work", WRONG)["speech"]
    assert _vc(client, "show me an example", WRONG)["example_code"] == "print(marks)"


def test_small_step_scaffolding_is_a_hint_and_respects_policy():
    mem = {}
    for _ in range(4):
        learner_model.record_evidence(mem, "loops", "checkpoint_failed")
    assert learner_model.scaffolding(mem, "loops") == "more"
    blocked = guided_learning.handle("start", mem, "", "loops", assistance={"hint": False})
    assert "smaller steps" not in blocked["speech"] and "hint" not in blocked["speech"].lower()
    mem2 = {"learner_model": json.loads(json.dumps(mem["learner_model"]))}
    allowed = guided_learning.handle("start", mem2, "", "loops", assistance={"hint": True})
    assert "smaller steps" in allowed["speech"]
    assert guided_learning._ASSIST.get() is None, "policy never outlives the call"


# ---- 2. diagnostic fingerprint ----------------------------------------------------------------

TWO = "print(score)\nx = 1\nprint(total)\n"


def test_unchanged_code_keeps_diagnostics_and_whitespace_does_not_count():
    client = app_module.app.test_client()
    client.post("/run", json={"code": TWO})
    assert _vc(client, "next error", TWO)["line"] == 3
    assert _vc(client, "how many errors", TWO + "\n\n")["diagnostic_count"] == 2
    assert _vc(client, "previous error", TWO.replace("x = 1", "x = 1   "))["line"] == 1


@pytest.mark.parametrize("command", ["next error", "previous error", "first error", "last error", "repeat error",
                                     "how many errors", "read all errors", "explain this error", "fix this error"])
def test_changed_code_invalidates_every_diagnostic_command(command):
    client = app_module.app.test_client()
    client.post("/run", json={"code": TWO})
    _vc(client, "next error", TWO)
    changed = "score = 5\nprint(score)\nx = 1\nprint(total)\n"
    data = _vc(client, command, changed)
    assert data["diagnostics_stale"] is True
    assert data["action"] == "deterministic_message", "no navigation or fix from stale state"
    assert "changed since" in data["speech"]
    # invalidated, not just hidden: the old set is gone
    assert "no known problems" in _vc(client, "how many errors", changed)["speech"].lower()


def test_fresh_run_and_check_syntax_establish_new_diagnostics():
    client = app_module.app.test_client()
    client.post("/run", json={"code": TWO})
    changed = "print(a)\nprint(b)\nprint(c)\n"
    assert _vc(client, "next error", changed)["diagnostics_stale"] is True
    client.post("/run", json={"code": changed})
    assert _vc(client, "how many errors", changed)["diagnostic_count"] == 3
    other = "print(q)\nprint(r)\n"
    client.post("/check-syntax", json={"code": other})
    assert _vc(client, "last error", other)["line"] == 2


def test_switching_files_never_reuses_another_files_diagnostics():
    client = app_module.app.test_client()
    main_py, helper_py = TWO, "def helper():\n    return 1\n"
    client.post("/run", json={"code": main_py})
    # The editor now shows a different file: its content has a different fingerprint.
    assert _vc(client, "next error", helper_py)["diagnostics_stale"] is True


def test_legacy_sets_without_fingerprint_are_not_stale():
    mem = {}
    session_memory.set_diagnostics(mem, [{"line": 1, "message": "m"}])
    assert not session_memory.diagnostics_are_stale(mem, "anything")
    session_memory.set_diagnostics(mem, [{"line": 1, "message": "m"}], code="a = 1")
    assert session_memory.diagnostics_are_stale(mem, "a = 2")
    assert not session_memory.diagnostics_are_stale(mem, "a = 1\n")


# ---- 3. selected-diagnostic fix ---------------------------------------------------------------

@pytest.fixture
def fixer(monkeypatch):
    calls = []

    def fake(capability, system, user, **kw):
        if capability == "fix":
            calls.append({"system": system, "user": user})
        return "```python\nprint(score)\nx = 1\ntotal = 0\nprint(total)\n```"

    monkeypatch.setattr(app_module, "call_gemini_capability", fake)
    return calls


def test_fix_targets_the_second_diagnostic_with_structured_context(fixer):
    client = app_module.app.test_client()
    client.post("/run", json={"code": TWO})
    assert _vc(client, "next error", TWO)["selected_index"] == 1
    selected = _vc(client, "fix this error", TWO)["selected_diagnostic"]
    assert selected["line"] == 3
    result = client.post("/fix", json={"code": TWO, "selected_diagnostic": True}).get_json()
    assert result["success"] is True
    prompt = fixer[-1]["user"]
    for expected in (f"id: {selected['id']}", "line: 3, column:", "category: static", "total",
                     "3: print(total)", "1: print(score)", "Resolve the selected problem",
                     "Keep every unrelated line exactly as it is", "add a short comment"):
        assert expected in prompt, expected
    assert "priority over finding and fixing every problem" in fixer[-1]["system"]
    # the returned fix changed only what the selected problem needed
    assert result["code"].startswith("print(score)\nx = 1\n")


def test_stale_selected_diagnostic_is_refused_without_calling_the_fixer(fixer):
    client = app_module.app.test_client()
    client.post("/run", json={"code": TWO})
    _vc(client, "next error", TWO)
    changed = TWO.replace("print(total)", "total = 3\nprint(total)")
    result = client.post("/fix", json={"code": changed, "selected_diagnostic": True}).get_json()
    assert result["success"] is False and result["stale_diagnostic"] is True
    assert "changed since" in result["speech"]
    assert fixer == []
    # a plain whole-program fix still works on the new code
    assert client.post("/fix", json={"code": changed}).get_json()["success"] is True


# ---- 4. bare "cancel" ------------------------------------------------------------------------

def test_cancel_resolves_a_pending_clarification_on_the_server():
    client = app_module.app.test_client()
    assert _vc(client, "make a program")["action"] == "clarify"
    assert "will not make a program" in _vc(client, "cancel")["speech"]
    # no pending question afterwards
    assert _vc(client, "school marks").get("resolved_from_clarification") is not True


def test_cancel_with_nothing_pending_is_left_to_the_client_suggestion_picker():
    data = _vc(app_module.app.test_client(), "cancel")
    assert data["action"] == "choose_suggestion" and data["choice"] == "cancel"


def test_client_cancel_dismisses_suggestions_or_stops_speech():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    start = APP_JS.index("function chooseSuggestion(choice) {")
    end = APP_JS.index("\n}\n", start) + 3
    script = APP_JS[start:end] + r"""
const log = [];
let _lastSuggestions = ['print(x)', 'x = 1'];
function speak(t) { log.push('speak:' + t); }
function insertAtCursor(t) { log.push('insert:' + t); }
function handleConfirmedAction(a) { log.push('action:' + a); }
chooseSuggestion('cancel');
const afterDismiss = _lastSuggestions.length;
chooseSuggestion('cancel');
_lastSuggestions = ['print(x)'];
chooseSuggestion('first');
console.log(JSON.stringify({ log, afterDismiss }));
"""
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["afterDismiss"] == 0
    assert out["log"] == ["speak:Suggestions dismissed.", "action:stop_speaking",
                          "insert:print(x)", "speak:Inserted: print(x)"]


def test_request_without_code_cannot_prove_staleness():
    # Non-editor callers may omit "code"; only the IDE's editor content decides.
    client = app_module.app.test_client()
    client.post("/check-syntax", json={"code": TWO})
    data = client.post("/voice-command", json={"text": "next error"}).get_json()
    assert data.get("diagnostics_stale") is not True and data["line"] == 3
    assert _vc(client, "next error", "")["diagnostics_stale"] is True, "an emptied editor is a change"
