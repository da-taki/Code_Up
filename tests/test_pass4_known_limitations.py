import errno
import json
import os
from pathlib import Path
import threading

import subprocess
import sys
import pytest

import app as app_module
from codeup.commands import deterministic_code_tools as tools
from codeup.projects.project_support import ProjectPathError, normalize_file_map, normalize_project_path
from codeup.runtime import state_watch


@pytest.fixture
def client(tmp_path, monkeypatch):
    snippets_file = tmp_path / "snippets.json"
    snippets_file.write_text(json.dumps({"snippets": []}), encoding="utf-8")
    monkeypatch.setenv("SNIPPETS_FILE", str(snippets_file))
    monkeypatch.setattr(app_module, "SNIPPETS_FILE", str(snippets_file))
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "false")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def test_state_watch_uses_structured_changes_when_values_contain_to():
    trace = [
        {
            "type": "state_change",
            "line": 2,
            "file": "main.py",
            "changes": ["msg changed from 'from A to B' to 'go to class'"],
            "structured_changes": [
                {
                    "variable": "msg",
                    "kind": "changed",
                    "before": "'from A to B'",
                    "after": "'go to class'",
                }
            ],
        }
    ]

    parsed = state_watch.parse_state(trace)

    assert parsed["msg"]["from"] == "'from A to B'"
    assert parsed["msg"]["value"] == "'go to class'"
    assert "changed from 'from A to B' to 'go to class'" in state_watch.variable_value(parsed, "msg")


def test_project_paths_reject_manifest_case_variants_and_case_collisions():
    with pytest.raises(ProjectPathError):
        normalize_project_path("CODEUP.PROJECT.JSON")

    with pytest.raises(ProjectPathError):
        normalize_file_map({"main.py": "print(1)", "MAIN.py": "print(2)"})


def test_snippet_create_and_rename_reject_duplicate_names(client):
    first = client.post("/snippets", json={"name": "Loop Helper", "code": "for i in range(3):\n    print(i)\n"})
    assert first.status_code == 200
    first_id = first.get_json()["id"]

    duplicate = client.post("/snippets", json={"name": " loop   helper ", "code": "print('same')\n"})
    assert duplicate.status_code == 409

    second = client.post("/snippets", json={"name": "Printer", "code": "print('ok')\n"})
    assert second.status_code == 200
    second_id = second.get_json()["id"]

    rename = client.put(f"/snippets/{second_id}", json={"name": "LOOP HELPER"})
    assert rename.status_code == 409

    still_there = client.get("/snippets").get_json()["snippets"]
    assert {item["id"] for item in still_there} == {first_id, second_id}


def test_mistake_replay_does_not_pair_unrelated_later_success(client):
    client.set_cookie(app_module.SESSION_COOKIE_NAME, "pass4-mistake")
    failed = client.post("/run", json={"code": "total = 1 / 0\n"}).get_json()
    assert failed["success"] is False

    unrelated = client.post("/run", json={"code": "print('new lesson')\n"}).get_json()
    assert unrelated["success"] is True

    replay = client.post("/mistake-replay", json={"code": "print('new lesson')\n"}).get_json()
    assert replay["success"] is False
    assert "recent corrected mistake" in replay["speech"]


def test_csv_preview_strips_bom_and_discloses_preview_rows():
    project = {
        "is_project": True,
        "files": {"data.csv": "\ufeffname,score\nAsha,10\nMina,12\n"},
    }

    preview = tools.csv_preview(project)

    assert "\ufeff" not in preview
    assert "name, score" in preview
    assert "Previewing 2 rows" in preview


def test_find_definition_prefers_real_main_function_over_variable():
    result = tools.find_definition("main = 'not the function'\n\ndef main():\n    pass\n", "main")

    assert result["found"] is True
    assert result["line"] == 3
    assert "Function main" in result["message"]


def test_audio_breakpoint_continue_surfaces_later_different_breakpoint(client):
    client.set_cookie(app_module.SESSION_COOKIE_NAME, "pass4-audio-bp")
    first = client.post("/audio-breakpoints", json={"action": "add", "condition": "a greater than 0"}).get_json()
    second = client.post("/audio-breakpoints", json={"action": "add", "condition": "b greater than 0"}).get_json()
    assert first["success"] is True
    assert second["success"] is True

    code = "a = 0\nb = 0\na = 1\nb = 1\nprint(a, b)\n"
    paused = client.post("/step-narration", json={"code": code}).get_json()
    assert paused["success"] is True
    assert paused["paused"] is True
    assert paused["pause"]["change"]["variable"] == "a"

    continued = client.post("/audio-breakpoints", json={"action": "continue"}).get_json()
    assert continued["success"] is True
    assert continued["active"] is True
    assert continued["paused"] is True
    assert continued["pause"]["change"]["variable"] == "b"
    assert continued["output"] == ""


def test_run_stream_input_claims_awaiting_slot_before_write(client, monkeypatch, tmp_path):
    client.set_cookie(app_module.SESSION_COOKIE_NAME, "pass4-stream")
    run_id = "run-pass4"
    fifo = tmp_path / "input.pipe"
    fifo.write_text("", encoding="utf-8")
    opened = []

    class Proc:
        def poll(self):
            return None

    class Writer:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def write(self, value):
            opened.append(value)

    def fake_open(path, flags):
        assert app_module._active_runs[run_id]["awaiting_input"] is False
        return 7

    monkeypatch.setattr(app_module.os, "open", fake_open)
    monkeypatch.setattr(app_module.os, "fdopen", lambda *args, **kwargs: Writer())
    app_module._active_runs[run_id] = {
        "session_id": "pass4-stream",
        "awaiting_input": True,
        "awaiting_input_lock": threading.Lock(),
        "proc": Proc(),
        "fifo": str(fifo),
    }
    try:
        response = client.post(f"/run-stream/{run_id}/input", json={"value": "Asha"})
        assert response.status_code == 200
        assert response.get_json()["success"] is True
        assert opened == ["Asha\n"]
    finally:
        app_module._active_runs.pop(run_id, None)


def test_run_stream_input_restores_awaiting_when_writer_not_ready(client, monkeypatch, tmp_path):
    client.set_cookie(app_module.SESSION_COOKIE_NAME, "pass4-stream-restore")
    run_id = "run-pass4-restore"
    fifo = tmp_path / "input.pipe"
    fifo.write_text("", encoding="utf-8")

    class Proc:
        def poll(self):
            return None

    def fake_open(path, flags):
        raise OSError(errno.ENXIO, "not ready")

    monkeypatch.setattr(app_module.os, "open", fake_open)
    app_module._active_runs[run_id] = {
        "session_id": "pass4-stream-restore",
        "awaiting_input": True,
        "awaiting_input_lock": threading.Lock(),
        "proc": Proc(),
        "fifo": str(fifo),
    }
    try:
        response = client.post(f"/run-stream/{run_id}/input", json={"value": "Asha"})
        assert response.status_code == 409
        assert app_module._active_runs[run_id]["awaiting_input"] is True
    finally:
        app_module._active_runs.pop(run_id, None)


def test_input_dialog_contains_keyboard_focus_trap():
    source = Path("static/app.js").read_text(encoding="utf-8")
    start = source.index("function showInputDialog")
    end = source.index("// ---------- LIST VARIABLES", start)
    body = source[start:end]

    assert "focusableDialogControls" in body
    assert "e.key !== 'Tab'" in body
    assert "document.activeElement === first" in body
    assert "document.activeElement === last" in body
    assert "last.focus()" in body
    assert "first.focus()" in body


def test_interactive_runner_consumes_prepared_inputs_before_live_fallback(tmp_path):
    code_file = tmp_path / "program.py"
    trace_file = tmp_path / "trace.json"
    inputs_file = tmp_path / "inputs.txt"
    code_file.write_text("for i in range(2):\n    name = input('Name: ')\n    print(name)\n", encoding="utf-8")
    inputs_file.write_text("Alexis\nMina\n", encoding="utf-8")
    env = os.environ.copy()
    env.update({
        "CODEUP_CODE_FILE": str(code_file),
        "CODEUP_TRACE_FILE": str(trace_file),
        "CODEUP_INPUTS_FILE": str(inputs_file),
        "CODEUP_INTERACTIVE": "1",
        "CODEUP_INPUT_FIFO": "",
        "PYTHONIOENCODING": "utf-8",
    })

    result = subprocess.run(
        [sys.executable, "codeup/runtime/sandbox_runner.py"],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert "Alexis" in result.stdout
    assert "Mina" in result.stdout
    assert trace_file.exists()
