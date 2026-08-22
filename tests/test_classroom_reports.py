from codeup.classroom import concepts, db, reports


def _setup_cohort_with_activity():
    instructor = db.create_instructor("teach", "h", "Ms Rao")
    cohort = db.create_cohort(instructor["id"], "Beginners")
    learner = db.join_cohort(cohort["id"], "Amir")
    assignment = db.create_assignment(
        cohort["id"], "Marks", "Do it", "marks = {}", None, ["dictionaries"], "FULL",
    )
    db.publish_assignment(assignment["id"])
    code = "marks = {'a': 1}\nprint(marks)"
    db.submit_assignment(assignment["id"], learner["id"], code)
    concepts.record_assignment_submitted(learner["id"], cohort["id"], code, ["dictionaries"])
    return cohort, learner, assignment


def test_learner_report_reflects_real_submission():
    cohort, learner, assignment = _setup_cohort_with_activity()
    report = reports.build_learner_report(learner["id"])
    assert "Amir" in report["report_md"]
    assert "Submitted: 1" in report["report_md"]
    assert "Demonstrated" in report["report_md"]


def test_learner_report_missing_learner_is_honest_not_fabricated():
    report = reports.build_learner_report(999999)
    assert "not found" in report["report_md"].lower()


def test_cohort_report_and_csv_reflect_real_rows():
    cohort, learner, assignment = _setup_cohort_with_activity()
    report = reports.build_cohort_report(cohort["id"])
    assert len(report["rows"]) == 1
    assert report["rows"][0]["display_name"] == "Amir"
    assert report["rows"][0]["assignments_submitted"] == 1

    csv_text = reports.cohort_report_csv(cohort["id"])
    assert "Amir" in csv_text
    assert csv_text.strip().splitlines()[0].startswith("learner,")


def test_cohort_report_never_invents_struggling_concepts_with_no_evidence():
    instructor = db.create_instructor("teach2", "h", "Mr Lee")
    cohort = db.create_cohort(instructor["id"], "Fresh Cohort")
    db.join_cohort(cohort["id"], "NewLearner")
    report = reports.build_cohort_report(cohort["id"])
    assert "No concept-level error patterns detected yet." in report["report_md"]
