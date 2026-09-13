"""Non-production evaluation script for the Vision-Aid utterance corpus
(tests/vision_aid_nlu_corpus.py), run against the EXISTING, unmodified
command-routing stack - no custom fuzzy/semantic runtime involved (see
that file's module docstring for why).

Not a pytest file (no test_ prefix, no assertions) - a report generator.
Run directly:

    py tests/evaluate_nlu_corpus.py

Reports three separate coverage modes, per this hardening pass's explicit
instruction not to blend them into one synthetic accuracy score:

  A. DETERMINISTIC PARSER COVERAGE - how much of the corpus
     codeup.commands.intent_parser.parse_intent() resolves on its own,
     with no fallback chain involved at all.

  B. AI-ENABLED FALLBACK COVERAGE - how much of what's left after (A),
     app.py's full existing /voice-command endpoint resolves once its
     own conversational/repaired/best_two_commands/AI-mapper fallback
     chain gets a turn, with AI enabled (uses the real AI provider if one
     is configured in this environment; reports honestly if not).

  C. AI-OFF CORE-COMMAND COVERAGE - for the corpus's core, always-
     available intent families only (run, read output, read last output,
     stop speech, where am i, read around me, why indented, what contains
     line, help), whether the full endpoint still resolves them
     deterministically with AI explicitly turned off for the learner.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from codeup.commands.intent_parser import parse_intent
from tests.vision_aid_nlu_corpus import CORPUS

CORE_FAMILIES = {
    "RUN_CODE", "READ_OUTPUT", "READ_LAST_OUTPUT", "STOP_SPEECH",
    "WHERE_AM_I", "READ_AROUND_ME", "WHY_INDENTED", "WHAT_CONTAINS_LINE", "HELP",
}


def mode_a_deterministic():
    total = 0
    resolved = 0
    per_family = {}
    for family, entries in CORPUS.items():
        fam_total = fam_resolved = 0
        for text, _category in entries:
            total += 1
            fam_total += 1
            if parse_intent(text).get("intent"):
                resolved += 1
                fam_resolved += 1
        per_family[family] = (fam_resolved, fam_total)
    return resolved, total, per_family


def _make_client():
    tmpdir = tempfile.mkdtemp()
    os.environ["DATA_DIR"] = tmpdir
    os.environ.setdefault("CODEUP_AI_ENABLED", "1")
    import app as app_module
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client(), app_module


def mode_b_ai_enabled_fallback(client, sample_per_family=8):
    """Samples (rather than exhaustively running all ~1200 utterances
    through a live HTTP round-trip) since this mode's value is in
    confirming the fallback chain PRODUCES SOME non-jargon resolution for
    previously-unresolved utterances, not in a precise accuracy count -
    see this file's module docstring."""
    headers = {"Origin": "http://localhost", "Referer": "http://localhost/ide"}
    total = resolved = 0
    unresolved_examples = []
    for family, entries in CORPUS.items():
        sample = entries[:sample_per_family]
        for text, _category in sample:
            if parse_intent(text).get("intent"):
                continue  # already covered by mode A, not interesting here
            total += 1
            resp = client.post("/voice-command", json={"text": text}, headers=headers).get_json()
            action = resp.get("action")
            if action and action not in ("clarify", "unknown"):
                resolved += 1
            elif len(unresolved_examples) < 10:
                unresolved_examples.append((text, action, resp.get("message")))
    return resolved, total, unresolved_examples


def mode_c_ai_off_core_commands(client):
    from codeup.classroom import db as classroom_db
    instr = classroom_db.create_instructor("eval_instructor", "pw12345", "Eval")
    cohort = classroom_db.create_cohort(instr["id"], "Eval Cohort")  # ai_enabled defaults OFF
    headers = {"Origin": "http://localhost", "Referer": "http://localhost/ide"}
    client.get("/ide")
    client.post("/voice-command", json={"text": f"join {cohort['join_code']}"}, headers=headers)
    client.post("/voice-command", json={"text": "Eval Learner"}, headers=headers)

    total = resolved = 0
    unresolved_examples = []
    for family in CORE_FAMILIES:
        for text, _category in CORPUS.get(family, [])[:15]:
            total += 1
            resp = client.post("/voice-command", json={"text": text}, headers=headers).get_json()
            action = resp.get("action")
            msg = str(resp.get("message") or "")
            if action and action not in ("clarify", "unknown") and "AI help is currently disabled" not in msg:
                resolved += 1
            elif len(unresolved_examples) < 10:
                unresolved_examples.append((text, action, msg))
    return resolved, total, unresolved_examples


def main():
    print("=" * 70)
    print("MODE A - deterministic parser coverage")
    print("=" * 70)
    resolved, total, per_family = mode_a_deterministic()
    print(f"{resolved}/{total} = {100*resolved/total:.1f}% resolved by parse_intent() alone")
    for family, (r, t) in sorted(per_family.items()):
        print(f"  {family:20} {r:3}/{t:3}  {100*r/t:5.1f}%")

    client, _app_module = _make_client()

    print()
    print("=" * 70)
    print("MODE B - AI-enabled fallback coverage (sampled, not exhaustive)")
    print("=" * 70)
    resolved_b, total_b, examples_b = mode_b_ai_enabled_fallback(client)
    if total_b:
        print(f"{resolved_b}/{total_b} = {100*resolved_b/total_b:.1f}% of previously-unresolved sampled utterances "
              f"got a non-jargon resolution via the existing fallback chain")
    else:
        print("nothing sampled (mode A already resolved everything sampled)")
    if examples_b:
        print("unresolved examples:")
        for text, action, msg in examples_b:
            print(f"  {text!r} -> action={action!r} message={msg!r}")

    print()
    print("=" * 70)
    print("MODE C - AI-off core-command coverage")
    print("=" * 70)
    resolved_c, total_c, examples_c = mode_c_ai_off_core_commands(client)
    print(f"{resolved_c}/{total_c} = {100*resolved_c/total_c:.1f}% of core-command family utterances "
          f"resolve without AI, with the classroom AI toggle off")
    if examples_c:
        print("unresolved examples:")
        for text, action, msg in examples_c:
            print(f"  {text!r} -> action={action!r} message={msg!r}")


if __name__ == "__main__":
    main()
