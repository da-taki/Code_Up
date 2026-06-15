import pytest

import app as app_module


DEFAULT_FOR = "for i in range(3):\n    print(i)"
FOR_1_TO_5 = "for i in range(1, 6):\n    print(i)"
EVEN_TO_10 = "for i in range(2, 11, 2):\n    print(i)"
ODD_TO_9 = "for i in range(1, 10, 2):\n    print(i)"
FRUITS_LOOP = 'fruits = ["apple", "banana", "mango"]\n\nfor fruit in fruits:\n    print(fruit)'
SAFE_WHILE = "count = 0\n\nwhile count < 3:\n    print(count)\n    count = count + 1"
WHILE_1_TO_5 = "count = 1\n\nwhile count <= 5:\n    print(count)\n    count = count + 1"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CODEUP_AI_ENABLED", "0")
    monkeypatch.setenv("GEMINI_ENABLED", "0")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_2", raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as flask_client:
        yield flask_client


def _vc(client, text, **kw):
    return client.post("/voice-command", json={"text": text, **kw}).get_json()


def _edit_code(data):
    assert data["action"] == "conversational_edit", data
    assert data["ai_action"]["action"] in {"append_code", "replace_code"}
    return data["ai_action"]["code"]


@pytest.mark.parametrize("text,expected", [
    ("loop from 1 to 5", FOR_1_TO_5),
    ("print numbers 1 to 5", FOR_1_TO_5),
    ("loop even to 10", EVEN_TO_10),
    ("loop odd to 9", ODD_TO_9),
    ("make a fruits list loop", FRUITS_LOOP),
    ("make a safe while loop", SAFE_WHILE),
    ("make a while loop from 1 to 5", WHILE_1_TO_5),
])
def test_slot_aware_loop_commands_insert_expected_templates(client, text, expected):
    data = _vc(client, text, code="")
    assert _edit_code(data) == expected
    assert data["template_intent"] in {"insert_for_loop", "insert_while_loop"}
    assert text.lower() not in data["ai_action"]["code"].lower()


@pytest.mark.parametrize("text,expected", [
    ("make a variable example for marks", "marks = 85\nprint(marks)"),
    ("make an input example for name", 'name = input("Enter your name: ")\nprint("Hello", name)'),
    ("make an if statement for age", 'age = 18\n\nif age >= 18:\n    print("Adult")\nelse:\n    print("Not adult yet")'),
    ("make a list example", 'fruits = ["apple", "banana", "mango"]\nprint(fruits)'),
    ("make a function example", 'def greet(name):\n    print("Hello", name)\n\ngreet("Taknoor")'),
])
def test_beginner_template_categories_route_to_code(client, text, expected):
    assert _edit_code(_vc(client, text, code="")) == expected


def test_comments_simplify_and_conversion_replace_current_code_without_confirmation(client):
    commented = _vc(client, "add comments", code=DEFAULT_FOR)
    assert commented["ai_action"]["action"] == "replace_code"
    assert commented["ai_action"]["requires_confirmation"] is False
    assert "# Loop through the values" in commented["ai_action"]["code"]
    assert "# Show a result" in commented["ai_action"]["code"]

    simplified = _vc(client, "simplify this code", code="for i in range(0, 3, 1):\n    print(i)")
    assert simplified["ai_action"]["action"] == "replace_code"
    assert simplified["ai_action"]["requires_confirmation"] is False
    assert simplified["ai_action"]["code"] == DEFAULT_FOR

    converted = _vc(client, "change it to while loop", code=DEFAULT_FOR)
    assert converted["ai_action"]["action"] == "replace_code"
    assert converted["ai_action"]["requires_confirmation"] is False
    assert converted["ai_action"]["code"] == SAFE_WHILE


def test_infinite_loop_command_clarifies_and_does_not_insert_code(client):
    data = _vc(client, "while true loop", code="")
    assert data["action"] == "clarify"
    assert data["needs_clarification"] is True
    assert data["reason"] == "unsafe_infinite_loop"
    assert "ai_action" not in data


def test_existing_spoken_insert_and_noisy_loop_repairs_still_work(client):
    assert _edit_code(_vc(client, "insert print hello", code="")) == 'print("hello")'
    assert _edit_code(_vc(client, "insert a for loop that prints hello three times", code="")) == (
        'for i in range(3):\n    print("hello")'
    )
    assert _edit_code(_vc(client, "insert a loop that prince 3 whole numbers", code="")) == DEFAULT_FOR
