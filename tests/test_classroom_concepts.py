from codeup.classroom import concepts, db


def test_detect_concepts_covers_curriculum():
    code = (
        "marks = {'Amir': 78}\n"
        "name = input('name? ')\n"
        "age = int(name)\n"
        "if age > 10:\n"
        "    print(age)\n"
        "for k in marks:\n"
        "    print(k)\n"
        "def total(d):\n"
        "    return sum(d.values())\n"
    )
    found = concepts.detect_concepts(code)
    for expected in ["print output", "variables", "input", "data types",
                      "conditionals (if/else)", "loops", "dictionaries", "functions"]:
        assert expected in found, f"missing {expected} in {found}"


def test_compute_state_conservative_progression():
    assert concepts.compute_state([]) == "not_started"
    assert concepts.compute_state(["seen"]) == "introduced"
    assert concepts.compute_state(["seen", "run_success"]) == "practised"
    assert concepts.compute_state(["seen", "run_success", "checkpoint"]) == "demonstrated"
    # never invents demonstrated without explicit checkpoint/submission evidence
    assert concepts.compute_state(["seen", "run_success", "run_success", "run_success"]) == "practised"


def test_compute_state_needs_practice_after_repeated_failure():
    history = ["seen", "run_failure", "run_failure", "run_failure"]
    assert concepts.compute_state(history) == "needs_practice"


def test_compute_state_recovers_from_needs_practice_on_success():
    history = ["seen", "run_failure", "run_failure", "run_failure", "run_success"]
    assert concepts.compute_state(history) == "practised"


def test_compute_state_never_downgrades_demonstrated():
    history = ["seen", "run_success", "checkpoint", "run_failure", "run_failure", "run_failure"]
    assert concepts.compute_state(history) == "demonstrated"


def test_record_run_persists_evidence_and_state():
    instructor = db.create_instructor("t1", "h", "Ms Rao")
    cohort = db.create_cohort(instructor["id"], "Beginners")
    learner = db.join_cohort(cohort["id"], "Amir")

    found = concepts.record_run(learner["id"], cohort["id"], "x = 1\nprint(x)", True)
    assert "variables" in found and "print output" in found

    summary = concepts.summary_for_learner(learner["id"])
    assert summary["variables"] == "practised"
    assert summary["print output"] == "practised"
    assert summary["loops"] == "not_started"


def test_record_assignment_submitted_only_counts_concepts_actually_present():
    instructor = db.create_instructor("t2", "h", "Ms Rao")
    cohort = db.create_cohort(instructor["id"], "Beginners")
    learner = db.join_cohort(cohort["id"], "Priya")

    code = "marks = {'a': 1}\nprint(marks)"
    concepts.record_assignment_submitted(
        learner["id"], cohort["id"], code, ["dictionaries", "print output", "loops"],
    )
    summary = concepts.summary_for_learner(learner["id"])
    assert summary["dictionaries"] == "demonstrated"
    assert summary["print output"] == "demonstrated"
    # "loops" was expected but never appears in the code - must NOT be invented as demonstrated
    assert summary["loops"] != "demonstrated"


def test_record_checkpoint_marks_demonstrated():
    instructor = db.create_instructor("t3", "h", "Ms Rao")
    cohort = db.create_cohort(instructor["id"], "Beginners")
    learner = db.join_cohort(cohort["id"], "Kai")

    concepts.record_checkpoint(learner["id"], cohort["id"], ["dictionaries"])
    summary = concepts.summary_for_learner(learner["id"])
    assert summary["dictionaries"] == "demonstrated"
