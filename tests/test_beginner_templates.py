import pytest

from codeup.commands import beginner_templates as bt


DEFAULT_FOR = "for i in range(3):\n    print(i)"
FOR_1_TO_5 = "for i in range(1, 6):\n    print(i)"
EVEN_TO_10 = "for i in range(2, 11, 2):\n    print(i)"
ODD_TO_9 = "for i in range(1, 10, 2):\n    print(i)"
FRUITS_LOOP = 'fruits = ["apple", "banana", "mango"]\n\nfor fruit in fruits:\n    print(fruit)'
SAFE_WHILE = "count = 0\n\nwhile count < 3:\n    print(count)\n    count = count + 1"
WHILE_1_TO_5 = "count = 1\n\nwhile count <= 5:\n    print(count)\n    count = count + 1"


@pytest.mark.parametrize("code", [
    bt.make_print_template("hello"),
    bt.make_variable_template("name"),
    bt.make_variable_template("marks"),
    bt.make_input_template("name"),
    bt.make_input_template("marks"),
    bt.make_if_template("marks"),
    bt.make_if_template("age"),
    bt.make_for_loop_template(),
    bt.make_while_loop_template(),
    bt.make_list_template("fruits", loop=True),
    bt.make_function_template(),
])
def test_templates_compile_to_python(code):
    compile(code, "<template>", "exec")
    assert bt.validate_template_code(code)


def test_required_template_outputs_are_stable():
    assert bt.make_for_loop_template() == DEFAULT_FOR
    assert bt.make_for_loop_template(1, 6) == FOR_1_TO_5
    assert bt.make_for_loop_template(2, 11, 2) == EVEN_TO_10
    assert bt.make_for_loop_template(1, 10, 2) == ODD_TO_9
    assert bt.make_list_template("fruits", loop=True) == FRUITS_LOOP
    assert bt.make_while_loop_template() == SAFE_WHILE
    assert bt.make_while_loop_template(1, 5, inclusive_stop=True) == WHILE_1_TO_5


def test_beginner_examples_are_safe_and_specific():
    assert bt.make_variable_template("marks") == "marks = 85\nprint(marks)"
    assert bt.make_input_template("name") == 'name = input("Enter your name: ")\nprint("Hello", name)'
    assert bt.make_if_template("age") == (
        'age = 18\n\nif age >= 18:\n    print("Adult")\nelse:\n    print("Not adult yet")'
    )
    assert bt.make_function_template() == 'def greet(name):\n    print("Hello", name)\n\ngreet("Taknoor")'


def test_match_loop_commands_with_slots():
    assert bt.match_template_command("loop from 1 to 5").code == FOR_1_TO_5
    assert bt.match_template_command("loop even to 10").code == EVEN_TO_10
    assert bt.match_template_command("loop odd to 9").code == ODD_TO_9
    assert bt.match_template_command("make a fruits list loop").code == FRUITS_LOOP
    assert bt.match_template_command("make a safe while loop").code == SAFE_WHILE
    assert bt.match_template_command("make a while loop from 1 to 5").code == WHILE_1_TO_5


def test_unsafe_infinite_loop_clarifies_without_code():
    result = bt.match_template_command("while true loop")
    assert result.needs_clarification is True
    assert result.code == ""
    assert result.reason == "unsafe_infinite_loop"


def test_comment_simplify_and_loop_conversion_helpers():
    loop = DEFAULT_FOR
    commented = bt.add_comments_to_code(loop)
    assert "# Loop through the values" in commented
    assert "# Show a result" in commented
    assert bt.simplify_code("for i in range(0, 3, 1):\n    print(i)") == DEFAULT_FOR
    assert bt.convert_for_to_while(loop) == SAFE_WHILE
    assert bt.convert_while_to_for(SAFE_WHILE) == DEFAULT_FOR
