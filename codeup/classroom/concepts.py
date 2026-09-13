"""Deterministic, conservative concept-mastery tracking.

Five states, matching the product spec exactly:
    not_started | introduced | practised | demonstrated | needs_practice

State is derived purely from an ordered sequence of real evidence events
(never invented, never AI-judged) so it stays auditable: replaying the same
event history always produces the same state. Concept *detection* reuses
the existing AST-based ``report_support.detect_python_concepts`` (the same
vocabulary already used by the Teacher Report) plus two beginner-curriculum
concepts it doesn't cover (``input``, ``data types``).
"""

from __future__ import annotations

import ast
import re
from typing import Dict, List, Sequence, Tuple

from codeup.reports import report_support
from codeup.classroom import db

STATES = ("not_started", "introduced", "practised", "demonstrated", "needs_practice")

CURRICULUM_CONCEPTS = (
    "print output",
    "variables",
    "input",
    "data types",
    "conditionals (if/else)",
    "loops",
    "lists",
    "dictionaries",
    "functions",
)

_TYPE_CONVERSION_RE = re.compile(r"\b(int|str|float|bool|type)\s*\(")

# Every label detect_concepts() can actually report - the only labels an
# instructor's "expected concepts" can be automatically checked against.
SUPPORTED_CONCEPTS = CURRICULUM_CONCEPTS + (
    "f-strings", "classes", "imports", "list comprehension", "error handling", "file reading",
)

# Instructors type expected concepts as free text; map the common ways of
# naming each concept onto the detector's canonical label.
_CONCEPT_ALIASES = {
    "print": "print output", "prints": "print output", "printing": "print output", "output": "print output",
    "print statement": "print output", "print statements": "print output", "print()": "print output",
    "variable": "variables", "assignment": "variables", "assignments": "variables",
    "user input": "input", "inputs": "input", "input()": "input", "reading input": "input",
    "data type": "data types", "types": "data types", "type conversion": "data types",
    "type casting": "data types", "casting": "data types",
    "conditionals": "conditionals (if/else)", "conditional": "conditionals (if/else)",
    "conditions": "conditionals (if/else)", "condition": "conditionals (if/else)",
    "if": "conditionals (if/else)", "if/else": "conditionals (if/else)", "if else": "conditionals (if/else)",
    "if-else": "conditionals (if/else)", "if statement": "conditionals (if/else)",
    "if statements": "conditionals (if/else)", "if/elif/else": "conditionals (if/else)",
    "loop": "loops", "for loop": "loops", "for loops": "loops", "while loop": "loops",
    "while loops": "loops", "iteration": "loops",
    "list": "lists", "dictionary": "dictionaries", "dict": "dictionaries", "dicts": "dictionaries",
    "function": "functions", "def": "functions",
    "f-string": "f-strings", "fstring": "f-strings", "fstrings": "f-strings", "f string": "f-strings",
    "f strings": "f-strings", "string formatting": "f-strings",
    "class": "classes", "import": "imports", "modules": "imports",
    "list comprehensions": "list comprehension",
    "exceptions": "error handling", "exception handling": "error handling", "try/except": "error handling",
    "try except": "error handling", "files": "file reading", "file handling": "file reading",
}


def normalize_expected_concepts(raw) -> Tuple[List[str], List[str]]:
    """Map free-text expected concepts (a comma-separated string or a list)
    onto detector labels. Returns (canonical labels, unrecognized labels);
    order is preserved and duplicates dropped."""
    items = raw.split(",") if isinstance(raw, str) else list(raw or [])
    canonical: List[str] = []
    unknown: List[str] = []
    for item in items:
        text = " ".join(str(item or "").strip().split())
        if not text:
            continue
        key = text.lower()
        label = key if key in SUPPORTED_CONCEPTS else _CONCEPT_ALIASES.get(key)
        if label is None:
            if key not in (u.lower() for u in unknown):
                unknown.append(text)
        elif label not in canonical:
            canonical.append(label)
    return canonical, unknown


def detect_concepts(code: str) -> List[str]:
    """AST-based concept detection over one snippet of code."""
    found = set(report_support.detect_python_concepts(code))
    text = code or ""
    try:
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "input":
                found.add("input")
    except SyntaxError:
        if re.search(r"\binput\s*\(", text):
            found.add("input")
    if _TYPE_CONVERSION_RE.search(text):
        found.add("data types")
    return [c for c in CURRICULUM_CONCEPTS if c in found] + sorted(
        c for c in found if c not in CURRICULUM_CONCEPTS
    )


def compute_state(evidence_sequence: Sequence[str]) -> str:
    """Fold an ordered evidence history into a single conservative state."""
    state = "not_started"
    fail_streak = 0
    for kind in evidence_sequence:
        if kind == "seen":
            if state == "not_started":
                state = "introduced"
        elif kind in ("run_success", "lesson_passed"):
            fail_streak = 0
            if state in ("not_started", "introduced", "needs_practice"):
                state = "practised"
        elif kind in ("checkpoint", "assignment_submitted_ok"):
            fail_streak = 0
            state = "demonstrated"
        elif kind == "run_failure":
            fail_streak += 1
            if state == "not_started":
                state = "introduced"
            if fail_streak >= 3 and state == "introduced":
                state = "needs_practice"
    return state


def _concept_evidence_history(events: List[Dict], concept: str) -> List[str]:
    out = []
    for event in reversed(events):  # events come back newest-first
        if event.get("kind") != "concept_evidence":
            continue
        payload = event.get("payload") or {}
        if payload.get("concept") == concept:
            out.append(str(payload.get("evidence") or ""))
    return out


def _apply_evidence(learner_id: int, cohort_id: int, concept: str, evidence: str) -> None:
    db.log_event(learner_id, cohort_id, "concept_evidence", {"concept": concept, "evidence": evidence})
    history = _concept_evidence_history(db.list_events_for_learner(learner_id, limit=500), concept)
    new_state = compute_state(history)
    db.set_concept_state(learner_id, concept, new_state, bump_evidence=False)


def record_run(learner_id: int, cohort_id: int, code: str, ran_ok: bool) -> List[str]:
    """Log 'seen' + success/failure evidence for every concept in this run."""
    concepts = detect_concepts(code)
    for concept in concepts:
        _apply_evidence(learner_id, cohort_id, concept, "seen")
        _apply_evidence(learner_id, cohort_id, concept, "run_success" if ran_ok else "run_failure")
    return concepts


def record_lesson_passed(learner_id: int, cohort_id: int, concept: str) -> None:
    _apply_evidence(learner_id, cohort_id, concept, "seen")
    _apply_evidence(learner_id, cohort_id, concept, "lesson_passed")


def record_checkpoint(learner_id: int, cohort_id: int, concepts: Sequence[str]) -> None:
    for concept in concepts:
        _apply_evidence(learner_id, cohort_id, concept, "checkpoint")


def record_assignment_submitted(learner_id: int, cohort_id: int, code: str, expected_concepts: Sequence[str]) -> None:
    """On submission, only concepts BOTH expected AND actually present in the
    submitted code count as demonstrated - never invent mastery of a concept
    that isn't in the code just because it was assigned."""
    present = set(detect_concepts(code))
    canonical, unknown = normalize_expected_concepts(expected_concepts)
    for concept in canonical + unknown:
        if concept in present:
            _apply_evidence(learner_id, cohort_id, concept, "assignment_submitted_ok")
        else:
            _apply_evidence(learner_id, cohort_id, concept, "seen")


def summary_for_learner(learner_id: int) -> Dict[str, str]:
    stored = db.get_concept_progress(learner_id)
    return {concept: (stored.get(concept) or {}).get("state", "not_started") for concept in CURRICULUM_CONCEPTS}


def summary_for_learners(learner_ids: List[int]) -> Dict[int, Dict[str, str]]:
    """Batched equivalent of calling :func:`summary_for_learner` once per id -
    used by the cohort dashboard so it issues one query for the whole
    cohort instead of one per learner."""
    stored_by_learner = db.get_concept_progress_for_learners(learner_ids)
    return {
        learner_id: {
            concept: (stored.get(concept) or {}).get("state", "not_started")
            for concept in CURRICULUM_CONCEPTS
        }
        for learner_id, stored in stored_by_learner.items()
    }
