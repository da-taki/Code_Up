from codeup.classroom import guided_projects as gp


def test_list_and_get_project():
    projects = gp.list_projects()
    assert any(p["id"] == "student_marks" for p in projects)
    project = gp.get_project("student_marks")
    assert project["title"] == "Student Marks Program"
    assert [c["id"] for c in project["checkpoints"]] == ["dictionary", "total", "average", "output"]
    assert gp.get_project("does-not-exist") is None


def test_checkpoints_progress_through_real_code():
    empty = ""
    assert gp.evaluate("student_marks", empty) == {
        "dictionary": False, "total": False, "average": False, "output": False,
    }

    only_dict = "marks = {'Amir': 78, 'Priya': 91}"
    result = gp.evaluate("student_marks", only_dict)
    assert result["dictionary"] is True
    assert result["total"] is False

    with_total = only_dict + "\ntotal = sum(marks.values())"
    result = gp.evaluate("student_marks", with_total)
    assert result["total"] is True
    assert result["average"] is False

    with_average = with_total + "\naverage = total / len(marks)"
    result = gp.evaluate("student_marks", with_average)
    assert result["average"] is True
    assert result["output"] is False

    full = with_average + "\nprint(average)"
    result = gp.evaluate("student_marks", full)
    assert all(result.values())


def test_checkpoints_require_dictionary_first_even_if_math_present():
    # sum()/division without ever building a dict should not satisfy total/average
    code = "numbers = [1, 2, 3]\ntotal = sum(numbers)\naverage = total / len(numbers)\nprint(average)"
    result = gp.evaluate("student_marks", code)
    assert result["dictionary"] is False
    assert result["total"] is False
    assert result["average"] is False
    assert result["output"] is False


def test_manual_accumulation_loop_counts_as_total():
    code = "marks = {'Amir': 78, 'Priya': 91}\ntotal = 0\nfor v in marks.values():\n    total += v\n"
    result = gp.evaluate("student_marks", code)
    assert result["total"] is True


def test_newly_completed_only_reports_transitions():
    code_with_dict_only = "marks = {'Amir': 78}"
    newly = gp.newly_completed("student_marks", code_with_dict_only, [])
    assert newly == ["dictionary"]

    # already had 'dictionary' - re-checking the same code should report nothing new
    newly_again = gp.newly_completed("student_marks", code_with_dict_only, ["dictionary"])
    assert newly_again == []


def test_invalid_syntax_never_crashes_and_fails_all_checkpoints():
    result = gp.evaluate("student_marks", "marks = {\ndef broken(:::")
    assert all(v is False for v in result.values())
