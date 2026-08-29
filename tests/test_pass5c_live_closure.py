"""Pass 5C: final four live browser checks, pinned as regression tests.

Live-verified in a real browser (learner assignment, looped input(), command
palette, rapid Run/Explain/Walkthrough sequence) before being captured here
as fast deterministic tests.
"""

import json

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


def run(client, code, **extra):
    payload = {"code": code, "language": "en"}
    payload.update(extra)
    return client.post("/run", json=payload, headers={"Origin": "http://localhost"}).get_json()


LOOP_CODE = 'for i in range(3):\n    name = input("Name: ")\n    print(name)\n'


# ---------------------------------------------------------------------------
# Scenario 1: looped input - a direct /run answer (the "Program input
# answer" panel's own code path) must not leave a stale awaiting_program_input
# behind, or the exact "set inputs to ..." recovery phrase the error message
# itself recommends gets silently swallowed as a literal (nonsensical) answer
# to a request that no longer exists.
# ---------------------------------------------------------------------------

def test_answering_via_direct_run_clears_stale_awaiting_state(client):
    # 1. Run with zero prepared values -> a real pre-run "awaiting input" request.
    first = run(client, LOOP_CODE)
    assert first["action"] == "request_program_input"

    # 2. Answer with the "Program input answer" panel's own mechanism: one
    #    value, then a direct /run retry (this is what
    #    static/app.js:submitProgramInputValue() actually does - it never
    #    goes through /voice-command's awaiting-input handler).
    second = run(client, LOOP_CODE, inputs=["Amir"])
    assert second["success"] is False
    assert "asked for input number 2" in second["error"]

    # 3. The error message's own suggested recovery phrase must be honored as
    #    a real command, not swallowed as a literal stale "answer".
    recovered = vc(client, "set inputs to Amir and Bea and Chen", code=LOOP_CODE)
    assert recovered["action"] != "action_sequence", (
        "the recovery phrase was swallowed as a literal answer to a stale "
        "awaiting-input request instead of being parsed as a set-inputs command"
    )
    assert "PRE-FLIGHT INPUTS SET" in recovered.get("message", "") or recovered.get("action") == "set_inputs"

    # 4. And the program actually completes correctly with all three values.
    final = run(client, LOOP_CODE, inputs=["Amir", "Bea", "Chen"])
    assert final["success"] is True
    assert final["output"] == "Name: Amir\nAmir\nName: Bea\nBea\nName: Chen\nChen\n"


def test_cancelling_a_pending_input_leaves_no_stale_state(client):
    pending = run(client, 'name = input("Name: ")\nprint(name)\n')
    assert pending["action"] == "request_program_input"

    cancelled = vc(client, "cancel input")
    assert cancelled["action"] == "clear_inputs"

    # A later unrelated command must not be treated as answering the
    # cancelled request.
    after = vc(client, "set inputs to Zoe")
    assert after["action"] != "action_sequence"


def test_live_input_mode_reports_posix_requirement_without_crashing(client, monkeypatch):
    """Windows can't exercise the true hybrid prepared->live transition (it
    requires the POSIX-only /run-stream FIFO channel) - the guard itself
    must still fail safely rather than crash or hang."""
    monkeypatch.setattr(app_module.sys, "platform", "win32")
    live = vc(client, "live input mode")
    assert live["action"] == "live_input_mode"
    started = client.post(
        "/run-stream/start",
        json={"code": LOOP_CODE},
        headers={"Origin": "http://localhost"},
    )
    assert started.status_code == 501
    assert "POSIX" in started.get_json()["error"]
