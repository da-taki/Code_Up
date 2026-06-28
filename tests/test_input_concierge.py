import pytest

import app as app_module
import input_concierge as ic


NAME_AGE = 'name = input("Enter name: ")\nage = int(input("Enter age: "))\nprint(name, age)\n'
THREE_MARKS = 'a = int(input("mark"))\nb = int(input("mark"))\nc = int(input("mark"))\nprint(a, b, c)\n'


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as flask_client:
        yield flask_client



def test_detects_input_with_prompt():
    assert ic.detect_inputs('name = input("Enter name")\n') == [
        {"label": "Enter name", "name": "name", "type": "str"}
    ]


def test_detects_int_input():
    detected = ic.detect_inputs('age = int(input("Enter age"))\n')
    assert detected[0]["type"] == "int"
    assert detected[0]["name"] == "age"


def test_detects_float_input():
    detected = ic.detect_inputs('m = float(input("Enter marks"))\n')
    assert detected[0]["type"] == "float"
    assert detected[0]["name"] == "marks"


def test_detects_inputs_in_source_order_with_types():
    detected = ic.detect_inputs(NAME_AGE)
    assert [d["name"] for d in detected] == ["name", "age"]
    assert [d["type"] for d in detected] == ["str", "int"]


def test_bare_input_has_str_type_and_placeholder_name():
    detected = ic.detect_inputs("x = input()\n")
    assert detected[0]["type"] == "str"
    assert detected[0]["label"] == "Input 1"



@pytest.mark.parametrize(
    "phrase,expected",
    [
        ("sixteen", "16"),
        ("ninety two", "92"),
        ("ninety two point five", "92.5"),
        ("16", "16"),
        ("92.5", "92.5"),
        ("zero", "0"),
    ],
)
def test_spoken_number_normalisation(phrase, expected):
    assert ic.normalize_spoken_number(phrase) == expected


def test_a_name_is_not_a_number():
    assert ic.normalize_spoken_number("Taknoor") is None



def test_run_with_named_values_maps_in_order():
    plan = ic.build_input_plan(NAME_AGE, "run with name Taknoor and age 16")
    assert plan["status"] == "ready"
    assert plan["values"] == ["Taknoor", "16"]


def test_name_is_and_age_is_spoken_number():
    plan = ic.build_input_plan(NAME_AGE, "name is Taknoor and age is sixteen")
    assert plan["values"] == ["Taknoor", "16"]


def test_anchored_messy_phrase_without_connectors():
    plan = ic.build_input_plan(NAME_AGE, "name Taknoor age sixteen")
    assert plan["values"] == ["Taknoor", "16"]


def test_use_sample_values_supplies_typed_values():
    plan = ic.build_input_plan(NAME_AGE, "use sample values")
    assert plan["status"] == "ready"
    assert len(plan["values"]) == 2
    int(plan["values"][1])


def test_run_with_sample_values_phrasing():
    plan = ic.build_input_plan(NAME_AGE, "run with sample values")
    assert plan["status"] == "ready"
    assert len(plan["values"]) == 2


def test_marks_are_positional_list():
    plan = ic.build_input_plan(THREE_MARKS, "marks are 90 85 95")
    assert plan["values"] == ["90", "85", "95"]



def test_wrong_type_gives_friendly_clarification():
    plan = ic.build_input_plan(NAME_AGE, "run with name Taknoor and age hello")
    assert plan["status"] == "type_error"
    assert "should be a number" in plan["message"]
    assert "age is 16" in plan["message"]


def test_no_code_asks_for_code_first():
    plan = ic.build_input_plan("", "run with name Taknoor and age 16")
    assert plan["status"] == "ask_for_code"


def test_no_input_code_reports_no_input():
    plan = ic.build_input_plan('print("hi")\n', "run with name Taknoor and age 16")
    assert plan["status"] == "no_input"


@pytest.mark.parametrize("text", ["run", "what is your name", "explain this program", "clear editor"])
def test_non_value_commands_are_ignored(text):
    assert ic.build_input_plan(NAME_AGE, text) is None



def test_key2_missing_does_not_break_simple_typed_values():
    plan = ic.build_input_plan(NAME_AGE, "run with name Taknoor and age 16", ai_value_fn=None)
    assert plan["values"] == ["Taknoor", "16"]


def test_key2_failure_falls_back_safely():
    def boom(code_inputs, text):
        raise RuntimeError("service busy")

    plan = ic.build_input_plan(NAME_AGE, "run with name Taknoor and age 16", ai_value_fn=boom)
    assert plan["values"] == ["Taknoor", "16"]


def test_key2_fills_a_gap_only_when_needed():
    seen = {"called": 0}

    def fake_ai(code_inputs, text):
        seen["called"] += 1
        return ["Zoya", "19"]

    plan = ic.build_input_plan(NAME_AGE, "run with whatever", ai_value_fn=fake_ai)
    assert plan["status"] == "ready"
    assert plan["values"][1] == "19"
    assert seen["called"] == 1



def test_voice_route_run_with_values_returns_set_then_run(client):
    data = client.post(
        "/voice-command", json={"text": "run with name Taknoor and age 16", "code": NAME_AGE}
    ).get_json()
    assert data["action"] == "action_sequence"
    assert [a["action"] for a in data["actions"]] == ["set_inputs", "run"]
    assert data["actions"][0]["values"] == ["Taknoor", "16"]
    assert data["input_concierge"] is True


def test_voice_route_use_sample_values(client):
    data = client.post("/voice-command", json={"text": "use sample values", "code": NAME_AGE}).get_json()
    assert data["action"] == "action_sequence"
    assert data["actions"][0]["action"] == "set_inputs"
    assert len(data["actions"][0]["values"]) == 2


def test_voice_route_wrong_type_clarifies(client):
    data = client.post(
        "/voice-command", json={"text": "run with name Taknoor and age hello", "code": NAME_AGE}
    ).get_json()
    assert data["action"] == "deterministic_message"
    assert "should be a number" in data["message"]


def test_voice_route_no_code_asks_for_code(client):
    data = client.post("/voice-command", json={"text": "run with name Taknoor and age 16", "code": ""}).get_json()
    assert data["action"] == "deterministic_message"
    assert "no code" in data["message"].lower()


def test_voice_route_does_not_hijack_run_with_step_narration(client):
    data = client.post("/voice-command", json={"text": "run with step narration", "code": NAME_AGE}).get_json()
    assert data["action"] == "step_narration"


def test_voice_route_plain_run_unchanged_for_no_input(client):
    data = client.post("/voice-command", json={"text": "run", "code": 'print("hi")\n'}).get_json()
    assert data["action"] == "run"


def test_run_endpoint_feeds_supplied_values_to_stdin(client):
    data = client.post("/run", json={"code": NAME_AGE, "inputs": ["Taknoor", "16"]}).get_json()
    assert data["success"] is True
    assert "Taknoor" in data["output"]
    assert "16" in data["output"]


def test_run_endpoint_hint_uses_concierge_phrasing(client):
    data = client.post("/run", json={"code": NAME_AGE}).get_json()
    assert data["action"] == "request_program_input"
    assert "name" in data["prompt"].lower()
    assert data["input_index"] == 1
    assert data["input_count"] == 2
