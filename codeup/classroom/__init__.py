"""Cohort / instructor / assignment layer for running a beginner Python cohort.

Everything a single anonymous learner needs already exists in the rest of
CodeUp (editor, run, accessibility, tutorials). This package adds the
durable, multi-learner layer on top: instructor accounts, cohorts, join
codes, assignments, AI policy enforcement, progress tracking, concept
mastery, guided project checkpoints, help requests and reports.

State here is persisted to a small SQLite database (see ``db.py``) instead
of the rest of the app's per-process, TTL-evicted in-memory session dict,
because cohort/instructor data must survive restarts and be visible across
different learners' browser sessions.
"""
