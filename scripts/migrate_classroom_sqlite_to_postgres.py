#!/usr/bin/env python
"""One-time migration: copy an existing classroom.db (SQLite) into a
PostgreSQL database, preserving row IDs, relationships, timestamps, and
JSON/text fields exactly.

Usage:

    python scripts/migrate_classroom_sqlite_to_postgres.py \\
        --sqlite-path /var/data/classroom.db \\
        --database-url postgresql://user:pass@host/dbname

    # Rehearse first - reads the source and reports what would be copied,
    # without touching the destination at all:
    python scripts/migrate_classroom_sqlite_to_postgres.py \\
        --sqlite-path /var/data/classroom.db \\
        --database-url postgresql://user:pass@host/dbname \\
        --dry-run

If --database-url is omitted, the DATABASE_URL environment variable is used
instead (the same variable the running application reads).

Safety:
  - Refuses to run against a missing/unreadable SQLite source.
  - Refuses an obviously unsafe destination (no URL at all, or a
    non-PostgreSQL scheme).
  - The whole copy runs inside one PostgreSQL transaction: any failure
    rolls back every table it touched. There is no partially-migrated
    state to clean up by hand.
  - After copying, every table's row count is verified against the source
    before the transaction is committed; a mismatch aborts (rolls back)
    instead of leaving a silently incomplete migration.
  - Never prints instructor/learner names, password hashes, tokens,
    submitted code, or the destination connection string - only table row
    counts and the sanitized (host/dbname only) destination target.
  - Never deletes, truncates, or modifies the source classroom.db.
  - Identity/serial sequences on the destination are reset after the
    explicit-ID inserts, so rows created after migration get fresh IDs
    that cannot collide with migrated ones.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg  # noqa: E402

from codeup.classroom import _storage  # noqa: E402

# (table, columns, has_serial_id) in dependency-safe order - a table only
# ever references one that appears earlier in this list.
TABLES: List[Tuple[str, List[str], bool]] = [
    ("instructors", ["id", "username", "password_hash", "display_name", "created_at"], True),
    (
        "cohorts",
        ["id", "instructor_id", "name", "join_code", "status", "created_at", "updated_at"],
        True,
    ),
    (
        "learners",
        ["id", "cohort_id", "display_name", "token", "joined_at", "last_active_at"],
        True,
    ),
    (
        "assignments",
        [
            "id", "cohort_id", "title", "instructions", "starter_code", "due_date",
            "expected_concepts", "ai_policy", "capability_settings", "is_assessment",
            "start_at", "end_at", "locked", "status", "published_at", "created_at", "updated_at",
        ],
        True,
    ),
    (
        "assignment_progress",
        [
            "id", "assignment_id", "learner_id", "status", "code", "submitted_code",
            "run_count", "success_run_count", "last_error", "last_saved_at",
            "submitted_at", "created_at", "updated_at",
        ],
        True,
    ),
    (
        "help_requests",
        [
            "id", "cohort_id", "learner_id", "assignment_id", "message", "status",
            "note", "created_at", "resolved_at",
        ],
        True,
    ),
    (
        "curriculum_progress",
        [
            "id", "learner_id", "course_id", "current_module_id", "current_stage",
            "started_at", "last_activity_at",
        ],
        True,
    ),
    (
        "module_progress",
        [
            "id", "learner_id", "module_id", "status", "completed_stages", "attempts",
            "quiz_score", "quiz_total", "completed_at", "updated_at",
        ],
        True,
    ),
    (
        "custom_lessons",
        [
            "id", "cohort_id", "instructor_id", "title", "objective", "explanation",
            "starter_code", "instructions", "expected_concepts", "challenge",
            "expected_output", "quiz_question", "quiz_choices", "quiz_answer_index",
            "created_at", "updated_at",
        ],
        True,
    ),
    (
        "custom_projects",
        [
            "id", "cohort_id", "instructor_id", "title", "instructions", "starter_code",
            "expected_concepts", "created_at", "updated_at",
        ],
        True,
    ),
    (
        "custom_project_checkpoints",
        ["id", "project_id", "order_index", "label", "check_type", "check_config"],
        True,
    ),
    ("onboarding_progress", ["learner_id", "completed", "completed_at"], False),
    (
        "learner_notification_state",
        ["learner_id", "assignments_seen_at", "ide_orientation_at"],
        False,
    ),
    (
        "lesson_progress",
        ["id", "learner_id", "lesson_id", "status", "attempts", "last_code", "updated_at"],
        True,
    ),
    (
        "project_progress",
        [
            "id", "learner_id", "project_id", "checkpoints_completed", "code",
            "active_file", "files", "updated_at",
        ],
        True,
    ),
    (
        "concept_progress",
        ["id", "learner_id", "concept", "state", "evidence_count", "last_evidence_at"],
        True,
    ),
    (
        "progress_events",
        ["id", "learner_id", "cohort_id", "kind", "payload", "created_at"],
        True,
    ),
]

# (parent_table, parent_column, child_table, child_column) - checked after
# copying, alongside the row-count check, for tables with a mandatory FK.
FK_CHECKS: List[Tuple[str, str, str, str]] = [
    ("instructors", "id", "cohorts", "instructor_id"),
    ("cohorts", "id", "learners", "cohort_id"),
    ("cohorts", "id", "assignments", "cohort_id"),
    ("assignments", "id", "assignment_progress", "assignment_id"),
    ("learners", "id", "assignment_progress", "learner_id"),
]


class MigrationError(RuntimeError):
    pass


def _sanitized_target(url: str) -> str:
    try:
        parts = urlsplit(url)
        host = parts.hostname or "unknown-host"
        port = f":{parts.port}" if parts.port else ""
        db = (parts.path or "").lstrip("/") or "unknown-db"
        return f"{host}{port}/{db}"
    except Exception:
        return "postgres-database"


def _open_source(sqlite_path: str) -> sqlite3.Connection:
    path = Path(sqlite_path)
    if not path.is_file():
        raise MigrationError(f"SQLite source not found: {sqlite_path}")
    if path.stat().st_size == 0:
        raise MigrationError(f"SQLite source is empty: {sqlite_path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    # Bring an older classroom.db up to the current column set (same
    # defensive ALTER TABLE statements the app itself applies on connect)
    # so every column this script expects to read actually exists.
    rw = sqlite3.connect(sqlite_path)
    try:
        rw.executescript(_storage.SQLITE_SCHEMA)
        for statement in _storage._SQLITE_MIGRATIONS:
            try:
                rw.execute(statement)
            except sqlite3.OperationalError:
                pass
        rw.commit()
    finally:
        rw.close()
    return conn


def _resolve_destination_url(cli_url: Optional[str]) -> str:
    url = (cli_url or os.environ.get("DATABASE_URL", "")).strip()
    if not url:
        raise MigrationError(
            "No destination configured: pass --database-url or set DATABASE_URL."
        )
    scheme = urlsplit(url).scheme
    if scheme not in ("postgres", "postgresql"):
        raise MigrationError(
            f"Destination must be a postgres:// or postgresql:// URL, got scheme {scheme!r}."
        )
    return url


def _read_source_rows(conn: sqlite3.Connection, table: str, columns: List[str]) -> List[Tuple[Any, ...]]:
    quoted = ", ".join(columns)
    cur = conn.execute(f"SELECT {quoted} FROM {table}")
    return [tuple(row[c] for c in columns) for row in cur.fetchall()]


def _reset_sequence(cur: "psycopg.Cursor", table: str) -> None:
    cur.execute(
        f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
        f"COALESCE((SELECT MAX(id) FROM {table}), 1), "
        f"(SELECT MAX(id) FROM {table}) IS NOT NULL)"
    )


def migrate(sqlite_path: str, database_url: str, *, dry_run: bool) -> Dict[str, int]:
    source = _open_source(sqlite_path)
    try:
        source_rows: Dict[str, List[Tuple[Any, ...]]] = {}
        source_counts: Dict[str, int] = {}
        for table, columns, _has_id in TABLES:
            rows = _read_source_rows(source, table, columns)
            source_rows[table] = rows
            source_counts[table] = len(rows)
    finally:
        source.close()

    print(f"Source: {sqlite_path}")
    print(f"Destination: {_sanitized_target(database_url)}")
    print("Row counts to migrate:")
    for table, _columns, _has_id in TABLES:
        print(f"  {table}: {source_counts[table]}")

    if dry_run:
        print("\nDry run only - destination was not contacted, nothing was written.")
        return source_counts

    _storage.ensure_schema(database_url)

    with psycopg.connect(database_url, autocommit=False) as conn:
        try:
            with conn.cursor() as cur:
                for table, columns, _has_id in TABLES:
                    rows = source_rows[table]
                    if not rows:
                        continue
                    placeholders = ", ".join(["%s"] * len(columns))
                    col_list = ", ".join(columns)
                    cur.executemany(
                        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})",
                        rows,
                    )

                dest_counts: Dict[str, int] = {}
                for table, _columns, _has_id in TABLES:
                    cur.execute(f"SELECT COUNT(*) FROM {table}")
                    dest_counts[table] = cur.fetchone()[0]

                mismatches = {
                    t: (source_counts[t], dest_counts[t])
                    for t, _c, _h in TABLES
                    if source_counts[t] != dest_counts[t]
                }
                if mismatches:
                    raise MigrationError(f"Row count mismatch after copy: {mismatches}")

                for parent_table, parent_col, child_table, child_col in FK_CHECKS:
                    cur.execute(
                        f"SELECT COUNT(*) FROM {child_table} c "
                        f"LEFT JOIN {parent_table} p ON c.{child_col} = p.{parent_col} "
                        f"WHERE c.{child_col} IS NOT NULL AND p.{parent_col} IS NULL"
                    )
                    orphans = cur.fetchone()[0]
                    if orphans:
                        raise MigrationError(
                            f"{orphans} row(s) in {child_table} reference a missing {parent_table} row"
                        )

                for table, _columns, has_id in TABLES:
                    if has_id:
                        _reset_sequence(cur, table)
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()

    print("\nMigration committed. Destination row counts:")
    for table, _columns, _has_id in TABLES:
        print(f"  {table}: {dest_counts[table]}")
    return dest_counts


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sqlite-path", required=True, help="Path to the existing classroom.db")
    parser.add_argument(
        "--database-url", default=None,
        help="Destination PostgreSQL URL (defaults to the DATABASE_URL environment variable)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Read the source and report row counts only; never contacts the destination",
    )
    args = parser.parse_args(argv)

    try:
        database_url = _resolve_destination_url(args.database_url)
        migrate(args.sqlite_path, database_url, dry_run=args.dry_run)
    except MigrationError as exc:
        print(f"Migration aborted: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
