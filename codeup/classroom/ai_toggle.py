"""Simple, class-and-student AI ON/OFF toggle for the Vision-Aid build.

Deliberately NOT the granular nine-capability ``codeup.classroom.ai_policy``
matrix (assignment-scoped, kept intact but unused by the Vision-Aid flow) -
this is a single boolean per cohort and per learner, with one easy-to-state
rule:

    effective_ai_enabled = class_ai_enabled AND learner_ai_enabled

Accessibility/deterministic features (run, output, navigation, program
input, exact-code reading, etc.) are never gated by this module - no route
that implements them calls into here.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

AI_DISABLED_MESSAGE = "AI help is currently disabled by your instructor."


def effective_ai_enabled(cohort: Optional[Dict[str, Any]], learner: Optional[Dict[str, Any]]) -> bool:
    """True unless a joined learner's cohort or the learner themself has AI
    explicitly turned off. Anonymous/non-cohort use (cohort and learner both
    None) is always allowed - the classroom layer must never restrict the
    existing single-user IDE experience."""
    class_enabled = bool(cohort.get("ai_enabled", True)) if cohort else True
    learner_enabled = bool(learner.get("ai_enabled", True)) if learner else True
    return class_enabled and learner_enabled
