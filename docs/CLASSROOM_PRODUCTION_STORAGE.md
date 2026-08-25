# Classroom production storage

CodeUp's classroom layer (instructors, cohorts, learners, assignments,
progress, help requests, curriculum state) persists through
[`codeup/classroom/db.py`](../codeup/classroom/db.py). Which database
actually stores that data is controlled entirely by one environment
variable.

## The rule

```
DATABASE_URL set     -> PostgreSQL
DATABASE_URL absent  -> SQLite (unchanged local-file behavior)
```

**If `DATABASE_URL` is set but PostgreSQL cannot be reached or
initialized, the app does NOT fall back to SQLite.** It fails loudly - an
unhandled error, logged as `PostgreSQL classroom storage unavailable
(...)` - instead of silently starting a second, disconnected SQLite
database. A broken `DATABASE_URL` producing "some requests hit Postgres,
others hit an empty local file" (split-brain classroom data) is
considered worse than downtime, and the code is written to make that
outcome impossible rather than just unlikely.

## Local development

Do nothing. No `DATABASE_URL`, no Postgres install, no Docker. Classroom
data lives in `${DATA_DIR:-.}/classroom.db`, exactly as before this
change. The full test suite (`pytest -q`, `pytest -q --run-full`) runs
against SQLite and requires no PostgreSQL server.

## Production (Render + a managed Postgres, e.g. Neon)

1. **Create a managed PostgreSQL database.** Any standard provider works;
   this was built and validated for compatibility with Neon specifically
   (see below).
2. **Obtain the connection string.** It looks like
   `postgresql://user:password@host/dbname?sslmode=require`.
3. **Set `DATABASE_URL` as a secret environment variable on the CodeUp web
   service** in the Render dashboard. Never commit it to the repo, a
   `.env` file, or a log line.
4. **Redeploy** the service.
5. **Open the authenticated storage diagnostic**: sign in as an
   instructor, then visit `/classroom/admin/storage-status` (also linked
   from the instructor dashboard as "Storage status").
6. **Confirm it reports:**
   ```
   Storage backend: PostgreSQL
   Database connection: healthy
   Schema version: 1
   ```
   If it instead shows `unreachable`, or the deploy failed at boot with a
   `PostgreSQL classroom storage unavailable` error in the Render logs,
   **do not proceed** - fix the connection string/network/SSL
   configuration first. The diagnostic never displays the connection
   string, hostname, username, password, or any learner data - only the
   backend name, a healthy/unreachable status, and the schema version.
7. **Only then create the real institutional cohort.** Creating a cohort
   before step 6 passes risks that cohort's data landing in a throwaway
   SQLite file on Render's local (non-durable) disk instead of the
   managed database.

### Migrating existing SQLite classroom data

If a `classroom.db` already has real instructor/cohort/learner data (e.g.
from a pilot run before Postgres was configured), copy it over with:

```bash
python scripts/migrate_classroom_sqlite_to_postgres.py \
    --sqlite-path /path/to/classroom.db \
    --database-url "$DATABASE_URL" \
    --dry-run   # rehearse first - reads the source, writes nothing
```

Drop `--dry-run` to actually copy. The tool:

- Preserves every row's original ID, all relationships, timestamps, and
  JSON/text fields exactly.
- Runs the whole copy as one PostgreSQL transaction - any failure rolls
  the destination back completely, so there is no partially-migrated
  state to clean up by hand.
- Verifies every table's row count (and referential integrity for the
  core instructor/cohort/learner/assignment chain) before committing.
- Resets PostgreSQL's identity sequences after the explicit-ID inserts,
  so cohorts/learners/etc. created after migration get fresh IDs that
  cannot collide with migrated ones.
- Never deletes, truncates, or modifies the source `classroom.db` file.
- Only ever prints table row counts and a sanitized (host/dbname only)
  destination target - never names, password hashes, tokens, code, or the
  connection string.

## Neon connection strings: pooled vs. direct

Neon exposes two connection strings for the same database: a **pooled**
one (through Neon's PgBouncer, host usually ending in `-pooler`) and a
**direct** one straight to Postgres. For CodeUp's web service - a
long-running gunicorn process that keeps its own small `psycopg_pool`
connection pool alive for the process lifetime (see
`codeup/classroom/_storage.py`) - **use the direct connection string**.
Layering one persistent pool (ours) on top of another persistent
transaction pooler (Neon's) is unnecessary and, depending on PgBouncer's
pooling mode, can misbehave with session-level features (advisory locks,
used here for schema migrations, require session pooling or a direct
connection to work correctly). The pooled endpoint exists for
short-lived/serverless callers making many brief connections, which is
not this deployment's shape.

## What the connection pool looks like

- `min_size=1`, `max_size=5` connections by default (override with
  `CLASSROOM_PG_POOL_MAX`), created lazily on first classroom database
  operation - not one giant pool stood up per call.
- Render currently runs CodeUp as a single gunicorn worker with 8 threads
  (`Procfile`); the pool is sized for that shape. If the deployment ever
  moves to multiple worker processes, each process gets its own pool (a
  `psycopg_pool.ConnectionPool` cannot be shared across processes) - keep
  `CLASSROOM_PG_POOL_MAX * worker_count` comfortably under the database's
  max connection limit.
- Stale/dropped connections are detected and replaced by
  `psycopg_pool` automatically; the pool is not a hand-rolled cache of raw
  connections.

## Concurrency behavior worth knowing

- **Join-code generation** checks for an existing code before inserting,
  which is a check-then-act race under concurrent cohort creation. The
  `UNIQUE(join_code)` database constraint is the real safety net: a rare
  collision on insert is caught and retried with a fresh code (up to 5
  attempts) rather than surfacing an error to the instructor.
- **First-touch progress rows** (`assignment_progress`, `project_progress`,
  `module_progress`) are created with `INSERT ... ON CONFLICT DO NOTHING`
  followed by a `SELECT`, so two near-simultaneous requests that both try
  to initialize a learner's progress for the same assignment/project/
  module never race into a duplicate-row error - one wins, the other sees
  the winner's row.
- **Autosave and resubmission** are last-write-wins, same as before this
  change - not something Postgres makes worse, and not hardened further
  here (a learner double-clicking Save or Submit overwriting their own
  most recent save is expected behavior, not a data-safety issue).

## Backup expectations

Managed Postgres providers (Neon included) take automatic snapshots/point-
in-time recovery on their own schedule and retention window - confirm the
plan tier's specifics before the pilot starts, since retention varies by
plan. This project does not run its own backup job; it relies on the
managed provider's backups. `classroom.db` itself is never a backup target
- once `DATABASE_URL` is set, it is legacy/local-dev-only.

## Verification status of this implementation

See the persistence-pass PR/report for exact wording, but as a standing
reminder: **Postgres support existing in code is not the same claim as
"production durable."** That phrase is reserved for a state where all of
the following have actually happened, not merely been made possible:

- A real PostgreSQL instance has passed the full workflow acceptance test.
- A real SQLite -> Postgres migration has passed.
- A broken `DATABASE_URL` has been confirmed to fail loudly, not fall back.
- The deployed Render service has been explicitly confirmed (via the
  storage diagnostic above) to be running on PostgreSQL.

Until all four are true for a given deployment, treat this as "Postgres
support is implemented and unit/SQLite-tested, real-Postgres behavior is
still unverified" - not as a production-ready guarantee.
