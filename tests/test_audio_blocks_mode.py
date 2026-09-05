import ast
import io
import json
import zipfile
from pathlib import Path

import pytest

from codeup.accessibility import audio_blocks
from app import app
from codeup.commands.intent_parser import parse_intent


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
    "import_module": {"module": "math"},
    "import_alias": {"module": "numpy", "alias": "np"},
    "from_import": {"module": "math", "name": "sqrt"},
    "print_text": {"text": "Hello"},
    "print_variable": {"variable": "total"},
    "print_format": {"text": "Total is {total}"},
    "set_number": {"variable": "total", "value": 1},
    "set_text": {"variable": "name", "text": "Asha"},
    "set_boolean": {"variable": "passed", "value": "true"},
    "set_list": {"variable": "scores", "values": "[70, 80]"},
    "set_dict": {"variable": "student", "values": "{'marks': 90}"},
    "set_expression": {"variable": "total", "expression": "2 + 3"},
    "change_variable": {"variable": "total", "value": 1},
    "convert_type": {"variable": "age", "conversion": "int", "value": "raw_age"},
    "add_numbers": {"variable": "total", "left": "2", "right": "3"},
    "subtract_numbers": {"variable": "total", "left": "5", "right": "2"},
    "multiply_numbers": {"variable": "total", "left": "2", "right": "3"},
    "divide_numbers": {"variable": "total", "left": "6", "right": "2"},
    "modulo_numbers": {"variable": "remainder", "left": "5", "right": "2"},
    "power_numbers": {"variable": "squared", "left": "3", "right": "2"},
    "boolean_expression": {"variable": "ok", "expression": "total > 2 and passed"},
    "expression": {"variable": "total", "expression": "2 * 3"},
    "if_condition": {"condition": "total > 2"},
    "elif_condition": {"condition": "total == 2"},
    "else_block": {},
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
    "break_block": {},
    "continue_block": {},
    "pass_block": {},
    "create_list": {"variable": "scores", "values": "[70, 80]"},
    "append_list": {"variable": "scores", "value": "90"},
    "print_list_item": {"variable": "scores", "index": 0},
    "get_list_item": {"variable": "first_score", "list": "scores", "index": 0},
    "set_list_item": {"list": "scores", "index": 0, "value": "95"},
    "loop_list": {"variable": "scores", "item": "score"},
    "len_value": {"variable": "score_count", "target": "scores"},
    "define_function": {"name": "greet", "params": ""},
    "call_function": {"name": "greet", "arguments": ""},
    "return_value": {"value": "total"},
    "try_block": {},
    "except_block": {"exception": "Exception"},
    "finally_block": {},
    "ask_input": {"variable": "name", "text": "Name: "},
    "ask_number_input": {"variable": "age", "text": "Age: "},
    "ask_decimal_input": {"variable": "marks", "text": "Marks: "},
    "comment_note": {"text": "remember to test"},
}


def test_catalog_has_all_categories_and_required_types():
    assert list(audio_blocks.CATALOG) == [
        "imports",
        "output",
        "variables",
        "math",
        "conditions",
        "loops",
        "lists",
        "functions",
        "exceptions",
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
        "import_module",
        "import_alias",
        "from_import",
        "print_text",
        "print_variable",
        "print_format",
        "set_number",
        "set_text",
        "set_boolean",
        "set_list",
        "set_dict",
        "set_expression",
        "change_variable",
        "convert_type",
        "add_numbers",
        "subtract_numbers",
        "multiply_numbers",
        "divide_numbers",
        "modulo_numbers",
        "power_numbers",
        "boolean_expression",
        "expression",
        "compare_values",
        "create_list",
        "append_list",
        "print_list_item",
        "get_list_item",
        "set_list_item",
        "len_value",
        "call_function",
        "ask_input",
        "ask_number_input",
        "ask_decimal_input",
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
        child_type = "break_block" if container_type == "while_condition" else "print_text"
        child_slots = {} if child_type == "break_block" else {"text": container_type}
        child, error = audio_blocks.add_block(
            workspace, child_type, child_slots, parent_id=parent["id"]
        )
        assert child and not error

    if_block, error = audio_blocks.add_block(
        workspace, "if_condition", {"condition": "total > 10"}
    )
    assert if_block and not error
    audio_blocks.add_block(workspace, "print_text", {"text": "big"}, parent_id=if_block["id"])
    elif_block, error = audio_blocks.add_block(
        workspace, "elif_condition", VALID_SLOTS["elif_condition"]
    )
    assert elif_block and not error
    audio_blocks.add_block(workspace, "print_text", {"text": "same"}, parent_id=elif_block["id"])
    else_block, error = audio_blocks.add_block(workspace, "else_block", {})
    assert else_block and not error
    audio_blocks.add_block(workspace, "pass_block", {}, parent_id=else_block["id"])

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

    for_block, error = audio_blocks.add_block(
        workspace, "for_range", {"variable": "j", "start": 0, "stop": 1}
    )
    assert for_block and not error
    continued, error = audio_blocks.add_block(workspace, "continue_block", {}, parent_id=for_block["id"])
    assert continued and not error

    try_block, error = audio_blocks.add_block(workspace, "try_block", {})
    assert try_block and not error
    audio_blocks.add_block(workspace, "print_text", {"text": "try"}, parent_id=try_block["id"])
    except_block, error = audio_blocks.add_block(
        workspace, "except_block", VALID_SLOTS["except_block"]
    )
    assert except_block and not error
    audio_blocks.add_block(workspace, "pass_block", {}, parent_id=except_block["id"])
    finally_block, error = audio_blocks.add_block(workspace, "finally_block", {})
    assert finally_block and not error
    audio_blocks.add_block(workspace, "pass_block", {}, parent_id=finally_block["id"])

    code, error = audio_blocks.compile_workspace(workspace)
    assert code and not error
    compile(code, "<test-audio-blocks>", "exec")
    assert set(VALID_SLOTS).issubset({block["type"] for block in workspace["blocks"]})
    assert not any(
        token in code
        for token in ("eval(", "exec(", "open(", "import os", "__import__")
    )


def test_input_blocks_generate_integer_decimal_python_and_source_map():
    workspace = audio_blocks.new_workspace()
    audio_blocks.add_block(workspace, "ask_input", {"variable": "name", "text": "Enter name: "})
    audio_blocks.add_block(workspace, "ask_number_input", {"variable": "age", "text": "Enter age: "})
    audio_blocks.add_block(workspace, "ask_decimal_input", {"variable": "marks", "text": "Enter marks: "})
    code, error = audio_blocks.compile_workspace(workspace)
    assert code and not error
    assert 'name = input("Enter name: ")' in code
    assert 'age = int(input("Enter age: "))' in code
    assert 'marks = float(input("Enter marks: "))' in code
    assert workspace["source_map"]["1"] == {"start": 1, "end": 1}
    assert workspace["line_map"]["2"] == 2


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
        "import",
        "output",
        "variable",
        "math",
        "condition",
        "loop",
        "list",
        "function",
        "exception",
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
        ("add print block hello", "print_text"),
        ("add a print block that says hello", "print_text"),
        ("add import math block", "import_module"),
        ("add import numpy as np block", "import_alias"),
        ("add from math import sqrt block", "from_import"),
        ("add print variable total", "print_variable"),
        ("add formatted print block", "print_format"),
        ("add variable total equals 1", "set_number"),
        ("add variable age equals 16", "set_number"),
        ("add variable name equals Taki", "set_text"),
        ("add variable block name equals Taki", "set_text"),
        ("add variable name text Asha", "set_text"),
        ("add variable passed boolean true", "set_boolean"),
        ("add variable scores list 70, 80", "set_list"),
        ("add variable student dictionary 'marks': 90", "set_dict"),
        ("add variable total expression 2 + 3", "set_expression"),
        ("add change total by 1", "change_variable"),
        ("add int conversion block for raw_age into age", "convert_type"),
        ("add 2 plus 3 into total", "add_numbers"),
        ("add 5 minus 2 into total", "subtract_numbers"),
        ("add 2 times 3 into total", "multiply_numbers"),
        ("add 6 divided by 2 into total", "divide_numbers"),
        ("add 5 modulo 2 into remainder", "modulo_numbers"),
        ("add 3 exponent 2 into squared", "power_numbers"),
        ("add boolean expression ok equals total greater than 2", "boolean_expression"),
        ("add expression total equals 2 * 3", "expression"),
        ("add if total greater than 2", "if_condition"),
        ("add elif block", "elif_condition"),
        ("add else block", "else_block"),
        ("add if else total greater than 2", "if_else_condition"),
        ("add comparison total > 2 into passed", "compare_values"),
        ("add repeat 3 times block", "repeat_times"),
        # XRCVC functional audit (Issue 13): "block before times" is at
        # least as natural as the original phrasing and used to fall
        # through to "That block command is not available yet."
        ("add repeat block 3 times", "repeat_times"),
        ("add a repeat block for 3 times", "repeat_times"),
        ("add for range block from 0 to 3", "for_range"),
        ("add for loop block", "for_range"),
        ("add while total less than 3", "while_condition"),
        ("add while loop block", "while_condition"),
        ("add break block", "break_block"),
        ("add continue block", "continue_block"),
        ("add pass block", "pass_block"),
        ("add list named scores", "create_list"),
        ("add list scores with 70, 80", "create_list"),
        ("append 90 to scores", "append_list"),
        ("add print item 0 from scores", "print_list_item"),
        ("add get item 0 from scores into first_score", "get_list_item"),
        ("add set item 0 of scores to 95", "set_list_item"),
        ("add length of scores into score_count", "len_value"),
        ("add loop through scores as score", "loop_list"),
        ("add function block", "define_function"),
        ("add function named greet", "define_function"),
        ("add call function greet", "call_function"),
        ("add call function greet with 1", "call_function"),
        ("add return total", "return_value"),
        ("add return block", "return_value"),
        ("add try block", "try_block"),
        ("add except block", "except_block"),
        ("add finally block", "finally_block"),
        ("add input block for name", "ask_input"),
        ("add number input block for age", "ask_number_input"),
        ("add decimal input block for marks", "ask_decimal_input"),
        ("ask for age as number", "ask_number_input"),
        ("ask for marks as decimal", "ask_decimal_input"),
        ("add comment block remember to test", "comment_note"),
        ("add comment remember to test", "comment_note"),
    ],
)
def test_typed_commands_create_every_block_type(client, command, block_type):
    voice(client, "enter block mode")
    result = voice(client, command)
    assert result["audio_blocks"]["blocks"][-1]["type"] == block_type, result


def test_bare_add_if_block_uses_safe_default_condition_not_the_literal_word_block(client):
    # Regression: "add if block" used to be matched by the general
    # "add if (.+)$" pattern before the bare-default pattern got a chance,
    # capturing the literal word "block" as the condition and compiling to
    # the malformed "if block:". See codeup/accessibility/audio_blocks.py.
    voice(client, "enter block mode")
    result = voice(client, "add if block")
    block = result["audio_blocks"]["blocks"][-1]
    assert block["type"] == "if_condition"
    assert block["slots"]["condition"] == "total > 0"
    assert block["generated"] == "if total > 0:"
    compile(block["generated"] + "\n    pass\n", "<audio-blocks-if-default>", "exec")


def test_bare_add_return_block_uses_safe_default_value_not_the_literal_word_block(client):
    voice(client, "enter block mode")
    result = voice(client, "add return block")
    block = result["audio_blocks"]["blocks"][-1]
    assert block["type"] == "return_value"
    assert block["slots"]["value"] == "None"
    assert block["generated"] == "return None"
    compile("def f():\n    " + block["generated"] + "\n", "<audio-blocks-return-default>", "exec")


def test_add_if_with_explicit_condition_still_preserves_it(client):
    voice(client, "enter block mode")
    result = voice(client, "add if score greater than 10")
    block = result["audio_blocks"]["blocks"][-1]
    assert block["type"] == "if_condition"
    assert block["slots"]["condition"] == "score > 10"
    assert block["generated"] == "if score > 10:"


def test_add_return_with_explicit_value_still_preserves_it(client):
    voice(client, "enter block mode")
    result = voice(client, "add return total")
    block = result["audio_blocks"]["blocks"][-1]
    assert block["type"] == "return_value"
    assert block["slots"]["value"] == "total"
    assert block["generated"] == "return total"


def test_audio_blocks_natural_demo_commands_generate_valid_python(client):
    typed(client, "open audio blocks")
    typed(client, "add print block hello")
    compiled = typed(client, "convert blocks to code")
    assert compiled["code_preview"] == "print('hello')\n"
    compile(compiled["code_preview"], "<audio-blocks-demo-print>", "exec")

    typed(client, "clear blocks")
    typed(client, "add variable name equals Taki")
    typed(client, "add print variable name")
    compiled = typed(client, "convert blocks to code")
    assert compiled["code_preview"] == "name = 'Taki'\nprint(name)\n"
    compile(compiled["code_preview"], "<audio-blocks-demo-variable>", "exec")

    typed(client, "clear blocks")
    typed(client, "add for loop range 3")
    shown = typed(client, "show blocks")
    assert "Workspace has 2 blocks." in shown["speech"]
    assert "{" not in shown["speech"]
    compiled = typed(client, "convert blocks to code")
    assert compiled["code_preview"] == "for i in range(3):\n    print(i)\n"
    compile(compiled["code_preview"], "<audio-blocks-demo-loop>", "exec")

    typed(client, "clear blocks")
    typed(client, "add for loop from 1 to 3")
    compiled = typed(client, "convert blocks to code")
    assert compiled["code_preview"] == "for i in range(1, 4):\n    print(i)\n"
    compile(compiled["code_preview"], "<audio-blocks-demo-loop-from>", "exec")

    typed(client, "clear blocks")
    typed(client, "add variable age equals 16")
    typed(client, "add if block age greater than 12")
    compiled = typed(client, "convert blocks to code")
    assert compiled["code_preview"] == (
        "age = 16\nif age > 12:\n    print('Condition is true')\n"
    )
    compile(compiled["code_preview"], "<audio-blocks-demo-if>", "exec")


def test_clear_blocks_clears_generated_preview_and_maps(client):
    typed(client, "open audio blocks")
    typed(client, "add print block hello")
    compiled = typed(client, "convert blocks to code")
    assert compiled["audio_blocks"]["code_preview"] == "print('hello')\n"
    assert compiled["audio_blocks"]["source_map"]
    assert compiled["audio_blocks"]["line_map"]

    cleared = typed(client, "clear blocks")
    workspace = cleared["audio_blocks"]
    assert workspace["blocks"] == []
    assert workspace["generated"] is False
    assert workspace["dirty"] is True
    assert workspace["code_preview"] == ""
    assert workspace["source_map"] == {}
    assert workspace["line_map"] == {}


def test_audio_blocks_invalid_and_unknown_natural_commands_fail_gracefully(client):
    typed(client, "open audio blocks")
    invalid = typed(client, "add variable 123abc equals 5")
    assert "cannot use 123abc" in invalid["speech"]
    assert invalid["audio_blocks"]["blocks"] == []
    assert "ai_action" not in invalid

    unsupported = typed(client, "add turtle block")
    assert "not available yet" in unsupported["speech"]
    assert unsupported["audio_blocks"]["blocks"] == []
    assert "ai_action" not in unsupported


def test_source_map_commands_explain_line_and_block(client):
    voice(client, "enter block mode")
    voice(client, "ask for age as number")
    voice(client, "add print variable age")
    voice(client, "compile blocks")
    line = voice(client, "which block made line 1")
    assert "block 1" in line["speech"].lower()
    block = voice(client, "what Python did block 1 create")
    assert 'age = int(input("Enter age: "))' in block["speech"]


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
    assert compiled["action"] == "deterministic_message"
    code = compiled["code_preview"]
    assert voice(client, "compare blocks and code", code=code)["speech"].startswith(
        "The editor matches"
    )
    run = voice(client, "run blocks")
    assert run["action"] == "audio_blocks_run" and run["code"] == code
    transferred = voice(client, "transfer blocks to Python mode")
    assert transferred["action"] == "conversational_edit"
    assert transferred["audio_blocks"]["activeMode"] == "python"
    assert transferred["ai_action"]["code"] == code


def test_compare_blocks_and_code_does_not_crash_on_invalid_editor_python(client):
    """Regression: "compare blocks and code" called ast.parse(code) with no
    try/except (unlike every other ast.parse in this module), so a syntactically
    invalid Python editor - normal mid-edit state, since Code Mode and Audio
    Blocks Mode are independent - crashed the whole /voice-command request with
    an unhandled SyntaxError (500) instead of reporting "differs from the
    generated block code".
    """
    voice(client, "enter block mode")
    voice(client, "add print text block")
    result = voice(client, "compare blocks and code", code="def foo(")
    assert result["success"] is True
    assert result["speech"].startswith("The editor differs")


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
        "transfer blocks to Python mode",
        "run blocks",
        "add import math block",
        "add from math import sqrt block",
        "set selected block message to Hello CodeUp",
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
        '<h3 id="audioBlocksHeading">Audio Blocks Mode</h3>',
        'aria-label="Open Audio Blocks Mode"',
        '<h3>Block palette</h3>',
        '<h3>Numbered block workspace</h3>',
        'aria-describedby="audioBlocksKeyboardHelp"',
        'aria-label="Numbered blocks"',
        '<h3>Generated Python preview</h3>',
        'aria-label="Move current block up"',
        'aria-label="Compile and run blocks"',
    ):
        assert token in html
    js = Path("static/app.js").read_text(encoding="utf-8")
    for token in (
        "renderAudioBlocks",
        "audio_blocks_run",
        "export_audio_blocks",
        "audioBlocksModeBtn) audioBlocksModeBtn.addEventListener('click', () => audioBlocksCommand('open audio blocks'))",
        "await runCode('', (payload && payload.code) || '')",
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
    assert result["audio_blocks"]["activeMode"] == "audio_blocks"
    assert "Audio Blocks Mode opened" in result["speech"]


@pytest.mark.parametrize("phrase", ENTER_PHRASES)
def test_typed_source_enters_audio_blocks_mode(client, phrase):
    result = typed(client, phrase)
    assert result["audio_blocks"]["mode"] == "audio_blocks"
    assert result["audio_blocks"]["activeMode"] == "audio_blocks"
    assert "Audio Blocks Mode opened" in result["speech"]
    assert "ai_action" not in result


def test_missing_source_enters_audio_blocks_mode_for_button_parity(client):
    result = client.post(
        "/voice-command", json={"text": "open audio blocks"}
    ).get_json()
    assert result["audio_blocks"]["mode"] == "audio_blocks"
    assert result["audio_blocks"]["activeMode"] == "audio_blocks"
    assert "Audio Blocks Mode opened" in result["speech"]


def test_typed_entry_works_again_after_returning_to_code_mode(client):
    assert voice(client, "enter block mode")["audio_blocks"]["mode"] == "audio_blocks"
    assert voice(client, "switch to code mode")["audio_blocks"]["mode"] == "code"
    opened = typed(client, "open audio blocks")
    assert opened["audio_blocks"]["mode"] == "audio_blocks"
    assert "Audio Blocks Mode opened" in opened["speech"]


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
    assert "Audio Blocks Mode opened" in again["speech"]


def test_exit_audio_blocks_mode_returns_to_code(client):
    voice(client, "enter block mode")
    exited = voice(client, "switch to python code mode", code="print('keep')")
    assert exited["audio_blocks"]["mode"] == "code"
    assert exited["audio_blocks"]["activeMode"] == "python"
    assert "editor is unchanged" in exited["speech"]
    voice(client, "enter block mode")
    assert voice(client, "exit block mode")["audio_blocks"]["mode"] == "code"


def test_typed_entry_calls_no_ai_provider(client, monkeypatch):
    import app as app_module

    def fail(*args, **kwargs):
        raise AssertionError("AI provider was called")

    monkeypatch.setattr(app_module, "call_gemini", fail)
    monkeypatch.setattr(app_module, "call_conversation_orchestrator_ai", fail)
    monkeypatch.setattr(app_module, "_call_ollama", fail)
    opened = typed(client, "open audio blocks")
    assert opened["success"] is True
    assert "Audio Blocks Mode opened" in opened["speech"]


def test_audio_blocks_contextual_help_differs_from_python_help(client):
    typed(client, "switch to python mode")
    python_help = typed(client, "what can I do here")
    assert python_help["action"] in {"help", "deterministic_message"}
    assert "Audio Blocks Mode" not in python_help.get("speech", "")
    opened = typed(client, "open audio blocks")
    assert opened["audio_blocks"]["activeMode"] == "audio_blocks"
    block_help = typed(client, "what can I do here")
    assert block_help["action"] == "deterministic_message"
    assert "Audio Blocks Mode" in block_help["speech"]
    assert "add print block" in block_help["speech"]
    assert block_help["speech"] != python_help.get("speech")


def test_audio_blocks_run_uses_generated_blocks_not_python_editor(client):
    typed(client, "open audio blocks")
    typed(client, "add print block")
    typed(client, "set message to Hello CodeUp")
    run = typed(client, "run", code="print('python editor should not run')")
    assert run["action"] == "audio_blocks_run"
    assert run["code"] == "print('Hello CodeUp')\n"
    assert run["audio_blocks"]["activeMode"] == "audio_blocks"


def test_audio_blocks_import_commands_compile_at_top(client):
    typed(client, "open audio blocks")
    typed(client, "add print block")
    typed(client, "add import random block")
    typed(client, "add from math import sqrt block")
    typed(client, "add import numpy as np block")
    compiled = typed(client, "compile blocks")
    assert compiled["action"] == "deterministic_message"
    code = compiled["code_preview"]
    assert code.startswith("import random\nfrom math import sqrt\nimport numpy as np\n")
    assert "print('Hello world')" in code


def test_audio_blocks_selected_block_voice_editing_and_history(client):
    typed(client, "open audio blocks")
    typed(client, "add print block")
    typed(client, "select block one")
    edited = typed(client, "set selected block message to Hello CodeUp")
    assert edited["audio_blocks"]["blocks"][0]["slots"]["text"] == "Hello CodeUp"
    duplicated = typed(client, "duplicate selected block")
    assert len(duplicated["audio_blocks"]["blocks"]) == 2
    moved = typed(client, "move selected block up")
    assert "Moved block 2 up" in moved["speech"]
    deleted = typed(client, "delete selected block")
    assert len(deleted["audio_blocks"]["blocks"]) == 1
    undone = typed(client, "undo")
    assert len(undone["audio_blocks"]["blocks"]) == 2
    redone = typed(client, "redo")
    assert len(redone["audio_blocks"]["blocks"]) == 1


def test_audio_blocks_variable_import_condition_range_function_and_return_edits(client):
    typed(client, "open audio blocks")
    typed(client, "add variable block")
    typed(client, "set variable name to marks")
    value = typed(client, "set variable value to 90")
    assert value["audio_blocks"]["blocks"][0]["slots"] == {"variable": "marks", "value": "90"}
    typed(client, "add import block")
    typed(client, "set import library to math")
    assert typed(client, "read selected block")["audio_blocks"]["blocks"][-1]["slots"]["module"] == "math"
    typed(client, "add if block")
    condition = typed(client, "set condition to marks greater than 40")
    assert condition["audio_blocks"]["blocks"][-1]["slots"]["condition"] == "marks > 40"
    typed(client, "add for loop block")
    typed(client, "set loop variable to i")
    typed(client, "set range start to 0")
    range_stop = typed(client, "set range stop to 5")
    assert range_stop["audio_blocks"]["blocks"][-1]["slots"]["stop"] == "5"
    typed(client, "add function block")
    typed(client, "set function name to calculate average")
    typed(client, "set parameter to marks")
    function = typed(client, "read selected block")
    assert function["audio_blocks"]["blocks"][-1]["slots"]["name"] == "calculate_average"
    assert function["audio_blocks"]["blocks"][-1]["slots"]["params"] == "marks"
    typed(client, "add return block")
    returned = typed(client, "set return value to marks")
    assert returned["audio_blocks"]["blocks"][-1]["slots"]["value"] == "marks"


def test_transfer_blocks_to_python_mode_action(client):
    typed(client, "open audio blocks")
    typed(client, "add import math block")
    typed(client, "add print block")
    typed(client, "select block two")
    typed(client, "set message to Hello CodeUp")
    transferred = typed(client, "transfer blocks to Python mode")
    assert transferred["action"] == "conversational_edit"
    assert transferred["audio_blocks"]["activeMode"] == "python"
    assert transferred["audio_blocks"]["mode"] == "code"
    assert transferred["ai_action"]["code"] == "import math\nprint('Hello CodeUp')\n"


def test_audio_blocks_python_only_command_safe_fails_without_editor_mutation(client):
    typed(client, "open audio blocks")
    response = typed(client, "clear editor", code="print('keep')")
    assert response["action"] == "deterministic_message"
    assert "Python Code Mode" in response["speech"]
    assert "ai_action" not in response


def test_read_block_map_is_advertised_command_with_deterministic_response(client):
    typed(client, "open audio blocks")
    empty = typed(client, "read block map")
    assert empty["action"] == "deterministic_message"
    assert "not available yet" not in empty["speech"].lower()
    assert "empty" in empty["speech"].lower()

    typed(client, "add print block")
    mapped = typed(client, "read block map")
    assert mapped["action"] == "deterministic_message"
    assert "Audio Blocks structure" in mapped["speech"]
    assert "1 block" in mapped["speech"]
    assert "not available yet" not in mapped["speech"].lower()


@pytest.mark.parametrize(
    "command",
    [
        "add print block",
        "add variable age equals 16",
        "add print variable name",
        "add for loop range 3",
        "compile blocks",
        "run blocks",
        "list blocks",
        "show blocks",
        "clear blocks",
    ],
)
def test_audio_block_commands_refuse_in_python_mode(client, command):
    typed(client, "switch to python mode")
    response = typed(client, command, code="print('keep')\n")
    assert response["action"] == "deterministic_message"
    assert "Audio Blocks Mode" in response["speech"]
    assert "open audio blocks" in response["speech"]
    assert response["audio_blocks"]["activeMode"] == "python"
    assert response["audio_blocks"]["blocks"] == []
    assert "ai_action" not in response


def test_visible_python_mode_overrides_stale_audio_blocks_session(client):
    typed(client, "open audio blocks")
    typed(client, "add print block")
    response = typed(
        client,
        "add print block",
        active_mode="python",
        code="print('keep')\n",
    )
    assert response["action"] == "deterministic_message"
    assert "Audio Blocks Mode" in response["speech"]
    assert response["audio_blocks"]["activeMode"] == "python"
    assert len(response["audio_blocks"]["blocks"]) == 1
    assert "ai_action" not in response


@pytest.mark.parametrize(
    "command",
    [
        "insert a for loop that prints the first 3 whole numbers",
        "clear editor",
        "fix this code",
    ],
)
def test_python_editor_commands_refuse_in_audio_blocks_mode(client, command):
    typed(client, "open audio blocks")
    response = typed(client, command, code="print('keep')\n")
    assert response["action"] == "deterministic_message"
    assert "Python Code Mode" in response["speech"]
    assert "switch to Python mode" in response["speech"]
    assert "ai_action" not in response


@pytest.mark.parametrize(
    "command",
    [
        "read current line",
        "read imports",
        "read functions",
        "go to main function",
        "read current function",
        "read surrounding code",
        "next function",
        "previous function",
        "jump to changed line",
        "next error",
    ],
)
def test_python_navigation_commands_refuse_in_audio_blocks_mode(client, command):
    typed(client, "open audio blocks")
    response = typed(
        client,
        command,
        code="def main():\n    print('keep')\n\nmain()\n",
    )
    assert response["action"] == "deterministic_message"
    assert "Python Code Mode" in response["speech"]
    assert "switch to Python mode" in response["speech"]
    assert "not available yet" not in response["speech"].lower()
    assert "ai_action" not in response


def test_project_map_in_audio_blocks_reports_block_workspace(client):
    typed(client, "open audio blocks")
    typed(client, "add print block")
    response = typed(client, "project map")
    assert response["action"] == "deterministic_message"
    assert "Audio Blocks structure" in response["speech"]
    assert "1 block" in response["speech"]
    assert "ai_action" not in response


def test_audio_blocks_project_report_and_export_use_block_workspace(client):
    typed(client, "open audio blocks")
    typed(client, "add print block")
    report = typed(client, "make a project report")
    assert report["action"] == "audio_blocks_project_report"
    assert "Workspace summary" in report["report"]
    assert "Generated Python" in report["report"]
    export = typed(client, "export this project")
    assert export["action"] == "export_audio_blocks"
