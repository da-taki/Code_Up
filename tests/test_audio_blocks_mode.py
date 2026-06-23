import ast
import io
import json
import zipfile
from pathlib import Path

import pytest

import audio_blocks
from app import app
from intent_parser import parse_intent


@pytest.fixture
def client():
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client


def voice(client, text, **payload):
    # Audio Blocks Mode can only be entered by a real spoken command, so the
    # voice helper defaults to source="voice" to mirror microphone input.
    payload.setdefault("source", "voice")
    return client.post("/voice-command", json={"text": text, **payload}).get_json()


def typed(client, text, **payload):
    payload.setdefault("source", "typed")
    return client.post("/voice-command", json={"text": text, **payload}).get_json()


VALID_SLOTS = {
    "print_text": {"text": "Hello"},
    "print_variable": {"variable": "total"},
    "set_number": {"variable": "total", "value": 1},
    "set_text": {"variable": "name", "text": "Asha"},
    "set_expression": {"variable": "total", "expression": "2 + 3"},
    "change_variable": {"variable": "total", "value": 1},
    "add_numbers": {"variable": "total", "left": "2", "right": "3"},
    "subtract_numbers": {"variable": "total", "left": "5", "right": "2"},
    "multiply_numbers": {"variable": "total", "left": "2", "right": "3"},
    "divide_numbers": {"variable": "total", "left": "6", "right": "2"},
    "expression": {"variable": "total", "expression": "2 * 3"},
    "if_condition": {"condition": "total > 2"},
    "if_else_condition": {"condition": "total > 2"},
    "compare_values": {
        "variable": "passed",
        "left": "total",
        "operator": ">",
        "right": "2",
    },
    "repeat_times": {"times": 3},
    "for_range": {"variable": "number", "start": 0, "stop": 3},
    "while_condition": {"condition": "total < 3"},
    "create_list": {"variable": "scores", "values": "[70, 80]"},
    "append_list": {"variable": "scores", "value": "90"},
    "print_list_item": {"variable": "scores", "index": 0},
    "loop_list": {"variable": "scores", "item": "score"},
    "define_function": {"name": "greet"},
    "call_function": {"name": "greet", "arguments": ""},
    "return_value": {"value": "total"},
    "ask_input": {"variable": "name", "text": "Name: "},
    "ask_number_input": {"variable": "age", "text": "Age: "},
    "comment_note": {"text": "remember to test"},
}


def test_catalog_has_all_categories_and_required_types():
    assert list(audio_blocks.CATALOG) == [
        "output",
        "variables",
        "math",
        "conditions",
        "loops",
        "lists",
        "functions",
        "input",
        "comments",
    ]
    assert set(audio_blocks.BLOCK_LABELS) == set(VALID_SLOTS)


def test_public_workspace_tags_each_block_with_its_category():
    # The category styling hook lets the UI color/group blocks without changing
    # the block model. Every block type maps to exactly one catalog category.
    assert set(audio_blocks.BLOCK_CATEGORY) == set(VALID_SLOTS)
    workspace = audio_blocks.new_workspace()
    audio_blocks.add_block(workspace, "print_text", {"text": "hi"})
    audio_blocks.add_block(workspace, "repeat_times", {"times": 2})
    public = audio_blocks.public_workspace(workspace)
    assert [block["category"] for block in public["blocks"]] == ["output", "loops"]


@pytest.mark.parametrize("block_type", sorted(VALID_SLOTS))
def test_every_block_type_is_structured_and_generates_python(block_type):
    workspace = audio_blocks.new_workspace()
    block, error = audio_blocks.add_block(
        workspace, block_type, VALID_SLOTS[block_type]
    )
    assert not error and block
    assert block == {
        "id": 1,
        "type": block_type,
        "label": audio_blocks.block_label(block_type, block["slots"]),
        "slots": block["slots"],
        "indent": 0,
        "parent_id": None,
        "branch": "body",
    }
    assert audio_blocks._line_for(block)


def test_ids_are_stable_after_move_delete_undo_and_serialization():
    workspace = audio_blocks.new_workspace()
    first, _ = audio_blocks.add_block(workspace, "print_text", {"text": "one"})
    second, _ = audio_blocks.add_block(workspace, "print_text", {"text": "two"})
    assert (first["id"], second["id"]) == (1, 2)
    audio_blocks.move_relative(workspace, 2, "up")
    assert [block["id"] for block in workspace["blocks"]] == [2, 1]
    audio_blocks.delete_block(workspace, 1)
    third, _ = audio_blocks.add_block(workspace, "print_text", {"text": "three"})
    assert third["id"] == 3
    audio_blocks.undo(workspace)
    assert [block["id"] for block in workspace["blocks"]] == [2]
    audio_blocks.redo(workspace)
    assert [block["id"] for block in workspace["blocks"]] == [2, 3]
    serialized = json.loads(audio_blocks.serialize_workspace(workspace))
    assert [block["id"] for block in serialized["blocks"]] == [2, 3]


@pytest.mark.parametrize("name", ["2total", "for", "bad-name", "__class__", ""])
def test_invalid_names_are_rejected(name):
    workspace = audio_blocks.new_workspace()
    block, error = audio_blocks.add_block(
        workspace, "set_number", {"variable": name, "value": 1}
    )
    assert block is None and "cannot use" in error


@pytest.mark.parametrize(
    "expression",
    ["open('x')", "eval('2')", "os.system('x')", "lambda: 1", "[x for x in y]"],
)
def test_unsafe_expressions_are_rejected(expression):
    assert audio_blocks.safe_expression(expression)[0] is False
    workspace = audio_blocks.new_workspace()
    block, error = audio_blocks.add_block(
        workspace, "set_expression", {"variable": "value", "expression": expression}
    )
    assert block is None and "unsafe" in error


def build_loop_workspace():
    workspace = audio_blocks.new_workspace()
    audio_blocks.add_block(workspace, "set_number", {"variable": "total", "value": 0})
    audio_blocks.add_block(workspace, "repeat_times", {"times": 3})
    audio_blocks.add_block(
        workspace, "change_variable", {"variable": "total", "value": 1}
    )
    audio_blocks.nest_block(workspace, 3, 2)
    audio_blocks.add_block(workspace, "print_variable", {"variable": "total"})
    return workspace


def test_nested_loop_compiles_to_readable_python_and_runs_in_existing_sandbox(client):
    workspace = build_loop_workspace()
    code, error = audio_blocks.compile_workspace(workspace)
    assert not error
    assert code == "total = 0\nfor i in range(3):\n    total += 1\nprint(total)\n"
    result = client.post("/run", json={"code": code}).get_json()
    assert result["success"] is True
    assert result["output"].strip() == "3"


def test_if_else_function_and_list_compilers():
    workspace = audio_blocks.new_workspace()
    audio_blocks.add_block(
        workspace, "create_list", {"variable": "scores", "values": "[70, 80]"}
    )
    audio_blocks.add_block(workspace, "define_function", {"name": "result"})
    audio_blocks.add_block(
        workspace, "if_else_condition", {"condition": "scores[0] >= 50"}, parent_id=2
    )
    audio_blocks.add_block(workspace, "return_value", {"value": "'Pass'"}, parent_id=3)
    audio_blocks.add_block(
        workspace, "return_value", {"value": "'Fail'"}, parent_id=3, branch="else"
    )
    audio_blocks.add_block(
        workspace, "call_function", {"name": "result", "arguments": ""}
    )
    code, error = audio_blocks.compile_workspace(workspace)
    assert not error
    ast.parse(code)
    assert "def result():" in code and "else:" in code and "scores = [70, 80]" in code


def test_every_required_block_type_participates_in_a_valid_compilation():
    workspace = audio_blocks.new_workspace()
    noncontainers = [
        "print_text",
        "print_variable",
        "set_number",
        "set_text",
        "set_expression",
        "change_variable",
        "add_numbers",
        "subtract_numbers",
        "multiply_numbers",
        "divide_numbers",
        "expression",
        "compare_values",
        "create_list",
        "append_list",
        "print_list_item",
        "call_function",
        "ask_input",
        "ask_number_input",
        "comment_note",
    ]
    for block_type in noncontainers:
        block, error = audio_blocks.add_block(
            workspace, block_type, VALID_SLOTS[block_type]
        )
        assert block and not error

    for container_type in (
        "if_condition",
        "repeat_times",
        "for_range",
        "while_condition",
        "loop_list",
    ):
        parent, error = audio_blocks.add_block(
            workspace, container_type, VALID_SLOTS[container_type]
        )
        assert parent and not error
        child, error = audio_blocks.add_block(
            workspace, "print_text", {"text": container_type}, parent_id=parent["id"]
        )
        assert child and not error

    conditional, error = audio_blocks.add_block(
        workspace, "if_else_condition", VALID_SLOTS["if_else_condition"]
    )
    assert conditional and not error
    audio_blocks.add_block(
        workspace, "print_text", {"text": "yes"}, parent_id=conditional["id"]
    )
    audio_blocks.add_block(
        workspace,
        "print_text",
        {"text": "no"},
        parent_id=conditional["id"],
        branch="else",
    )

    function, error = audio_blocks.add_block(
        workspace, "define_function", VALID_SLOTS["define_function"]
    )
    assert function and not error
    returned, error = audio_blocks.add_block(
        workspace, "return_value", {"value": "1"}, parent_id=function["id"]
    )
    assert returned and not error

    code, error = audio_blocks.compile_workspace(workspace)
    assert code and not error
    compile(code, "<test-audio-blocks>", "exec")
    assert set(VALID_SLOTS).issubset({block["type"] for block in workspace["blocks"]})
    assert not any(
        token in code
        for token in ("eval(", "exec(", "open(", "import os", "__import__")
    )


def test_incomplete_container_refuses_compilation():
    workspace = audio_blocks.new_workspace()
    audio_blocks.add_block(workspace, "repeat_times", {"times": 3})
    code, error = audio_blocks.compile_workspace(workspace)
    assert code is None and "needs at least one child" in error


def test_nesting_move_before_after_outdent_and_invalid_cycle():
    workspace = build_loop_workspace()
    assert (
        audio_blocks.move_before_after(workspace, 4, 1, "before")
        == "Moved block 4 before block 1."
    )
    assert audio_blocks.outdent_block(workspace, 3) == "Outdented block 3."
    assert audio_blocks.nest_block(workspace, 3, 2).startswith("Put block 3")
    nested_loop, _ = audio_blocks.add_block(
        workspace, "repeat_times", {"times": 2}, parent_id=2
    )
    assert "cannot contain itself" in audio_blocks.nest_block(
        workspace, 2, nested_loop["id"]
    )
    assert "cannot contain" in audio_blocks.nest_block(workspace, 1, 4)


def test_mode_palette_add_edit_navigation_and_code_preservation(client):
    initial = voice(client, "what mode am I in", code="print('keep')")
    assert "Code Mode" in initial["speech"] and "ai_action" not in initial
    entered = voice(client, "enter block mode", code="print('keep')")
    assert entered["audio_blocks"]["mode"] == "audio_blocks"
    assert "categories" in voice(client, "list block categories")["speech"].lower()
    for category in (
        "output",
        "variable",
        "math",
        "condition",
        "loop",
        "list",
        "function",
        "input",
        "comment",
    ):
        assert (
            "blocks include"
            in voice(client, f"list {category} blocks")["speech"].lower()
        )
    added = voice(client, "add variable total equals 0")
    assert added["audio_blocks"]["blocks"][0]["slots"]["variable"] == "total"
    voice(client, "add print variable total")
    assert "Block 2" in voice(client, "read block 2")["speech"]
    assert "Current block" in voice(client, "previous block")["speech"]
    edited = voice(client, "set block 1 variable to score")
    assert edited["audio_blocks"]["blocks"][0]["slots"]["variable"] == "score"
    renamed = voice(client, "rename block variable score to total")
    assert "Renamed" in renamed["speech"]
    exited = voice(client, "switch to code mode", code="print('keep')")
    assert exited["audio_blocks"]["mode"] == "code"
    assert "editor is unchanged" in exited["speech"] and "ai_action" not in exited


@pytest.mark.parametrize(
    "command, block_type",
    [
        ("add print text hello", "print_text"),
        ("add print variable total", "print_variable"),
        ("add variable total equals 1", "set_number"),
        ("add variable name text Asha", "set_text"),
        ("add variable total expression 2 + 3", "set_expression"),
        ("add change total by 1", "change_variable"),
        ("add 2 plus 3 into total", "add_numbers"),
        ("add 5 minus 2 into total", "subtract_numbers"),
        ("add 2 times 3 into total", "multiply_numbers"),
        ("add 6 divided by 2 into total", "divide_numbers"),
        ("add expression total equals 2 * 3", "expression"),
        ("add if total greater than 2", "if_condition"),
        ("add if else total greater than 2", "if_else_condition"),
        ("add comparison total > 2 into passed", "compare_values"),
        ("add repeat 3 times block", "repeat_times"),
        ("add for range block from 0 to 3", "for_range"),
        ("add while total less than 3", "while_condition"),
        ("add list named scores", "create_list"),
        ("add list scores with 70, 80", "create_list"),
        ("append 90 to scores", "append_list"),
        ("add print item 0 from scores", "print_list_item"),
        ("add loop through scores as score", "loop_list"),
        ("add function named greet", "define_function"),
        ("add call function greet", "call_function"),
        ("add call function greet with 1", "call_function"),
        ("add return total", "return_value"),
        ("add input block for name", "ask_input"),
        ("add number input block for age", "ask_number_input"),
        ("add comment remember to test", "comment_note"),
    ],
)
def test_typed_commands_create_every_block_type(client, command, block_type):
    voice(client, "enter block mode")
    result = voice(client, command)
    assert result["audio_blocks"]["blocks"][-1]["type"] == block_type, result


def test_clear_value_marks_block_incomplete_and_compiler_refuses(client):
    voice(client, "enter block mode")
    voice(client, "add print text hello")
    cleared = voice(client, "clear block 1 value")
    assert cleared["audio_blocks"]["blocks"][0]["incomplete"] is True
    refused = voice(client, "compile blocks to Python")
    assert refused["action"] == "deterministic_message"
    assert "incomplete" in refused["speech"]


def test_command_workflow_compile_preview_compare_and_run_action(client):
    voice(client, "enter block mode")
    voice(client, "add variable total equals 0")
    voice(client, "add repeat 3 times block")
    voice(client, "add change total by 1")
    voice(client, "put block 3 inside block 2")
    voice(client, "add print variable total")
    order = voice(client, "read block order")
    assert "Workspace has 4 blocks" in order["speech"]
    preview = voice(client, "preview generated code")
    assert "total += 1" in preview["code_preview"]
    compiled = voice(client, "compile blocks to Python")
    assert compiled["action"] == "conversational_edit"
    code = compiled["ai_action"]["code"]
    assert voice(client, "compare blocks and code", code=code)["speech"].startswith(
        "The editor matches"
    )
    run = voice(client, "run blocks")
    assert run["action"] == "audio_blocks_run" and run["code"] == code


def test_parsons_overlap_remains_available_when_audio_mode_is_off(client):
    voice(client, "start block practice 3")
    order = voice(client, "read block order")
    assert "indentation" in order["speech"]


@pytest.mark.parametrize(
    "code, expected_types",
    [
        ('print("Hello")\n', ["print_text"]),
        (
            "total = 0\nfor i in range(3):\n    total += 1\nprint(total)\n",
            ["set_number", "repeat_times", "change_variable", "print_variable"],
        ),
        (
            "def greet():\n    return 'Hello'\n\ngreet()\n",
            ["define_function", "return_value", "call_function"],
        ),
        (
            "# note\nscores = [70, 80]\nscores.append(90)\n",
            ["comment_note", "create_list", "append_list"],
        ),
    ],
)
def test_safe_python_imports_into_structured_blocks(code, expected_types):
    workspace = audio_blocks.new_workspace()
    success, message = audio_blocks.import_python(workspace, code)
    assert success, message
    assert [block["type"] for block in workspace["blocks"]] == expected_types


def test_complex_python_import_refuses_without_destroying_workspace():
    workspace = audio_blocks.new_workspace()
    audio_blocks.add_block(workspace, "print_text", {"text": "keep"})
    original = audio_blocks.serialize_workspace(workspace)
    success, message = audio_blocks.import_python(
        workspace, "values = {x for x in range(3)}\n"
    )
    assert success is False and "does not support" in message or "cannot use" in message
    assert audio_blocks.serialize_workspace(workspace) == original


def test_all_block_lessons_fail_then_solution_passes_and_compiles(client):
    voice(client, "start block lesson")
    for index, lesson in enumerate(audio_blocks.LESSONS):
        if index:
            voice(client, "next block lesson")
        assert voice(client, "check block lesson")["lesson_passed"] is False
        assert voice(client, "give block lesson hint")["speech"] == lesson[3]
        solution = voice(client, "show block lesson solution")
        assert "Generated Python" in solution["speech"]
        passed = voice(client, "check block lesson")
        assert passed["lesson_passed"] is True
        ast.parse(passed["audio_blocks"]["code_preview"])
    assert "closed" in voice(client, "exit block lesson")["speech"]


def test_audio_blocks_export_contains_python_json_notes_and_handoff(client):
    voice(client, "enter block mode")
    voice(client, "add print text hello world")
    action = voice(client, "export blocks and Python")
    assert action["action"] == "export_audio_blocks"
    prepared = client.post("/export-audio-blocks", json={}).get_json()
    assert prepared["success"] is True
    assert {
        "main.py",
        "audio_blocks_workspace.json",
        "AUDIO_BLOCKS_NOTES.md",
        "ACCESSIBILITY_NOTES.md",
        ".vscode/settings.json",
    }.issubset(prepared["included"])
    archive = client.get(prepared["download_url"])
    with zipfile.ZipFile(io.BytesIO(archive.data)) as bundle:
        names = set(bundle.namelist())
        assert set(prepared["included"]) == names
        assert bundle.read("main.py").decode() == "print('hello world')\n"
        workspace = json.loads(bundle.read("audio_blocks_workspace.json"))
        assert workspace["blocks"][0]["type"] == "print_text"
        notes = bundle.read("AUDIO_BLOCKS_NOTES.md").decode()
        assert "not Scratch" in notes and "VS Code" in notes


def test_audio_blocks_export_refuses_possible_secrets(client):
    voice(client, "enter block mode")
    voice(client, "add print text API_KEY=do-not-export")
    refused = client.post("/export-audio-blocks", json={})
    assert refused.status_code == 400
    assert "secret" in refused.get_json()["error"].lower()


@pytest.mark.parametrize(
    "command",
    [
        "enter block mode",
        "list block categories",
        "add print block",
        "read block workspace",
        "compile blocks to Python",
        "run blocks",
        "convert code to blocks",
        "start block lesson",
        "export block project",
    ],
)
def test_audio_block_commands_are_registered_in_existing_parser(command):
    assert parse_intent(command)["intent"] == "audio_blocks"


def test_no_ai_provider_is_called(client, monkeypatch):
    import app as app_module

    def fail(*args, **kwargs):
        raise AssertionError("AI provider was called")

    monkeypatch.setattr(app_module, "call_gemini", fail)
    monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", fail)
    for command in (
        "enter block mode",
        "add print block",
        "read block workspace",
        "preview generated code",
        "start block lesson",
    ):
        assert voice(client, command)["success"] is True


def test_frontend_has_labeled_regions_buttons_keyboard_and_actions(client):
    html = client.get("/ide").get_data(as_text=True)
    for token in (
        'aria-label="Audio Blocks Mode"',
        'aria-label="Block palette"',
        'aria-label="Block workspace"',
        'aria-label="Generated code preview"',
        'aria-label="Move current block up"',
        'aria-label="Compile and run blocks"',
    ):
        assert token in html
    js = Path("static/app.js").read_text(encoding="utf-8")
    for token in (
        "renderAudioBlocks",
        "audio_blocks_run",
        "export_audio_blocks",
        "event.key === 'ArrowDown'",
        "event.key === 'Delete'",
        "compile blocks to Python",
        "run blocks",
    ):
        assert token in js


ENTER_PHRASES = sorted(audio_blocks.ENTER_PHRASES)


@pytest.mark.parametrize("phrase", ENTER_PHRASES)
def test_voice_source_enters_audio_blocks_mode(client, phrase):
    result = voice(client, phrase)
    assert result["audio_blocks"]["mode"] == "audio_blocks"
    assert "Audio Blocks Mode is on" in result["speech"]


@pytest.mark.parametrize("phrase", ENTER_PHRASES)
def test_typed_source_refuses_to_enter_audio_blocks_mode(client, phrase):
    result = typed(client, phrase)
    assert result["audio_blocks"]["mode"] == "code"
    assert "only be opened by voice" in result["speech"]
    assert "ai_action" not in result


def test_missing_source_does_not_enter_audio_blocks_mode(client):
    # No source field at all is treated as typed/unknown and must not switch.
    result = client.post(
        "/voice-command", json={"text": "open audio blocks"}
    ).get_json()
    assert result["audio_blocks"]["mode"] == "code"
    assert "only be opened by voice" in result["speech"]


def test_typed_entry_is_refused_again_after_returning_to_code_mode(client):
    assert voice(client, "enter block mode")["audio_blocks"]["mode"] == "audio_blocks"
    assert voice(client, "switch to code mode")["audio_blocks"]["mode"] == "code"
    refused = typed(client, "open audio blocks")
    assert refused["audio_blocks"]["mode"] == "code"
    assert "only be opened by voice" in refused["speech"]


def test_block_commands_still_work_after_voice_entry(client):
    assert voice(client, "open audio blocks")["audio_blocks"]["mode"] == "audio_blocks"
    # Typed block editing and navigation stay available once inside block mode.
    added = typed(client, "add variable total equals 0")
    assert added["audio_blocks"]["mode"] == "audio_blocks"
    assert added["audio_blocks"]["blocks"][-1]["slots"]["variable"] == "total"
    typed(client, "add print variable total")
    assert "Block 2" in typed(client, "read block 2")["speech"]
    assert "Current block" in typed(client, "previous block")["speech"]


def test_typed_reentry_while_inside_block_mode_is_allowed(client):
    voice(client, "open audio blocks")
    again = typed(client, "open audio blocks")
    assert again["audio_blocks"]["mode"] == "audio_blocks"
    assert "Audio Blocks Mode is on" in again["speech"]


def test_exit_audio_blocks_mode_returns_to_code(client):
    voice(client, "enter block mode")
    exited = voice(client, "switch to code mode", code="print('keep')")
    assert exited["audio_blocks"]["mode"] == "code"
    assert "editor is unchanged" in exited["speech"]
    voice(client, "enter block mode")
    assert voice(client, "exit block mode")["audio_blocks"]["mode"] == "code"


def test_typed_entry_refusal_calls_no_ai_provider(client, monkeypatch):
    import app as app_module

    def fail(*args, **kwargs):
        raise AssertionError("AI provider was called")

    monkeypatch.setattr(app_module, "call_gemini", fail)
    monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", fail)
    monkeypatch.setattr(app_module, "_call_ollama", fail)
    refused = typed(client, "open audio blocks")
    assert refused["success"] is True
    assert "only be opened by voice" in refused["speech"]
