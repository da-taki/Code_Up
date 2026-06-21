# Teacher reports

Teacher reports provide a deterministic, session-local summary for trainers. No report is uploaded or sent by CodeUp.

## Commands

- `teacher mode on` and `teacher mode off`
- `generate lesson report`
- `generate student report`
- `generate mistakes report`
- `show common mistakes`
- `export teacher report`
- `reset teacher report`
- `include code in teacher report`
- `exclude code from teacher report`

## Contents

The report covers lessons attempted, current lesson, passed and failed lesson checks, commands used, run count, recent output/error summaries, tracked beginner error types, hints requested, block-practice attempts, accessibility settings, and whether project export was used when tracked.

Full code is excluded by default. `include code in teacher report` changes that setting for the current session; `exclude code from teacher report` restores the privacy default. Export downloads a local Markdown file named `CodeUp_Teacher_Report.md`.

## Limitations

The report is not an assessment grade. It records only the current browser session and bounded CodeUp memory. Clearing session state or changing browsers can remove the history. Error counts cover recognized error types and should be reviewed with the learner rather than treated as a complete record.
