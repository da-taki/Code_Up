import pytest

import app as app_module


NAME = 'name = input("Enter name: ")\nprint("Hello", name)\n'
AGE = 'age = int(input("Enter age: "))\nprint(age + 1)\n'
NAME_AGE = 'name = input("Name: ")\nage = int(input("Age: "))\nprint(name, age)\n'


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as flask_client:
        yield flask_client


def vc(client, text, code=""):
    return client.post("/voice-command", json={"text": text, "code": code}).get_json()


@pytest.mark.parametrize(
    "command",
    [
        "how do I work with inputs in CodeUp",
        "how do I work with inputs",
        "how do inputs work",
        "how does input work",
        "how do I use input",
        "teach me input",
        "explain input",
    ],
)
def test_input_help_commands_are_deterministic(client, command):
    data = vc(client, command)
    assert data["action"] == "deterministic_message"
    assert data["input_help"] is True
    assert "input asks the user" in data["message"]
    assert data.get("needs_clarification") is not True


@pytest.mark.parametrize("command", ["use 16 as input", "insert 16 as value"])
def test_single_input_value_commands_store_pending_value(client, command):
    data = vc(client, command)
    assert data["action"] == "set_inputs"
    assert data["values"] == ["16"]
    assert "next input call will receive: 16" in data["speech"]


def test_multiple_input_values_parse_in_order(client):
    data = vc(client, "use Alice, 16, and 90 as inputs")
    assert data["action"] == "set_inputs"
    assert data["values"] == ["Alice", "16", "90"]


def test_repeated_single_input_commands_append_in_order(client):
    first = vc(client, "use Taknoor as input")
    assert first["values"] == ["Taknoor"]
    second = vc(client, "use 16 as input")
    assert second["action"] == "set_inputs"
    assert second["values"] == ["Taknoor", "16"]


def test_clear_and_read_input_values(client):
    vc(client, "use Alice and 16 as inputs")
    listed = vc(client, "what input values are set")
    assert listed["action"] == "list_inputs"
    assert listed["values"] == ["Alice", "16"]
    cleared = vc(client, "clear input values")
    assert cleared["action"] == "clear_inputs"
    listed_again = vc(client, "read input values")
    assert listed_again["values"] == []
    assert "No input values" in listed_again["speech"]


def test_run_with_pending_input_executes_and_clears(client):
    vc(client, "use 16 as input")
    data = client.post("/run", json={"code": AGE}).get_json()
    assert data["success"] is True
    assert "17" in data["output"]
    assert data["inputs_consumed"] == 1
    assert data["clear_inputs_after_run"] is True
    assert vc(client, "read input values")["values"] == []


def test_input_without_value_requests_program_input(client):
    data = client.post("/run", json={"code": NAME}).get_json()
    assert data["success"] is True
    assert data["action"] == "request_program_input"
    assert data["prompt"] == "Enter name:"
    assert data["input_index"] == 1
    assert data["input_count"] == 1


def test_reply_while_awaiting_input_runs_next(client):
    client.post("/run", json={"code": NAME})
    data = vc(client, "my input is Taknoor", code=NAME)
    assert data["action"] == "action_sequence"
    assert [a["action"] for a in data["actions"]] == ["set_inputs", "run"]
    assert data["actions"][0]["values"] == ["Taknoor"]


def test_runtime_input_clears_backend_pending_after_success(client):
    client.post("/run", json={"code": NAME})
    data = vc(client, "Taknoor", code=NAME)
    values = data["actions"][0]["values"]
    done = client.post("/run", json={"code": NAME, "inputs": values}).get_json()
    assert done["success"] is True

    next_run = client.post("/run", json={"code": NAME_AGE}).get_json()
    assert next_run["action"] == "request_program_input"
    assert next_run["prompt"] == "Name:"
    assert next_run["values"] == []


def test_multiple_input_prompts_are_asked_in_order(client):
    first = client.post("/run", json={"code": NAME_AGE}).get_json()
    assert first["prompt"] == "Name:"
    second = vc(client, "Alice", code=NAME_AGE)
    assert second["action"] == "request_program_input"
    assert second["prompt"] == "Age:"
    assert second["input_index"] == 2
    final = vc(client, "16", code=NAME_AGE)
    assert final["action"] == "action_sequence"
    assert final["actions"][0]["values"] == ["Alice", "16"]


def test_int_and_float_inputs_include_beginner_conversion_notes(client):
    int_req = client.post("/run", json={"code": AGE}).get_json()
    assert "converted to an integer with int()" in int_req["speech"]
    float_req = client.post(
        "/run",
        json={"code": 'marks = float(input("Enter marks: "))\nprint(marks)\n'},
    ).get_json()
    assert "converted to a decimal number with float()" in float_req["speech"]


def test_invalid_int_input_gets_clean_value_error_explanation(client):
    data = client.post("/run", json={"code": AGE, "inputs": ["Taknoor"]}).get_json()
    assert data["success"] is False
    assert "expected a number" in data["explanation"]
    assert "16" in data["explanation"]


def test_dynamic_prompt_falls_back_safely(client):
    code = 'prompt = "Name: "\nname = input(prompt)\nprint(name)\n'
    data = client.post("/run", json={"code": code}).get_json()
    assert data["action"] == "request_program_input"
    assert data["prompt"] == "This program needs input value 1."


def test_cancel_input_and_code_change_clear_awaiting_state(client):
    client.post("/run", json={"code": NAME})
    cancelled = vc(client, "cancel input", code=NAME)
    assert cancelled["action"] == "clear_inputs"

    client.post("/run", json={"code": NAME})
    stale = vc(client, "Taknoor", code='print("changed")\n')
    assert stale["action"] == "deterministic_message"
    assert "code changed" in stale["speech"].lower()


def test_canonical_non_input_commands_still_route_normally(client):
    data = vc(client, "run", code='print("hi")\n')
    assert data["action"] == "run"
    status = vc(client, "intel toolkit status", code='print("hi")\n')
    assert status["action"] == "deterministic_message"


def test_teacher_report_mentions_input_usage(client):
    client.post("/run", json={"code": NAME, "inputs": ["Taknoor"]})
    report = vc(client, "make a teacher report", code=NAME)
    assert report["action"] == "deterministic_message"
    assert "Program Input" in report["message"]
    assert "Program used input" in report["message"]


def test_state_watch_reuses_last_run_inputs(client):
    code = 'name = input("Enter name: ")\nage = int(input("Enter age: "))\nprint(name, age + 1)\n'
    data = client.post("/run", json={"code": code, "inputs": ["Taknoor", "16"]}).get_json()
    assert data["success"] is True
    state = vc(client, "show program state", code=code)
    assert state["action"] == "deterministic_message"
    assert "Taknoor" in state["speech"]
    assert "age is 16" in state["speech"]
    assert "program printed" in state["speech"].lower()
