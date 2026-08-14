# Content Engine phase plan

A separate phase sequence (`CE-1`, `CE-2`, ...) from Video Studio's
`docs/phase-plan.md` (Phase 0-11), which is frozen and not modified by
anything in this document or under `src/content_engine/`.

**Boundary principle**: Content Engine calls Video Studio only through the
already-public `seoulkit-studio` CLI, as a subprocess reading `--json`
output - never by importing `seoulkit_studio` internals directly. This is
deliberate, not incidental: it makes it structurally impossible (not just
a convention) for Content Engine code to reach into Video Studio's
internals, and it keeps Video Studio genuinely independent of whatever
Content Engine eventually needs (SNS credentials, LLM providers, a
database) - Video Studio has no idea Content Engine, or SNS publishing,
exists at all.

| Phase | Scope | Status |
|---|---|---|
| CE-1 | `jobs` table + `Job`/`JobState` data model (no queue, no persistence abstraction) | ✅ Done |
| CE-2A | Job state transitions (validation + atomic SQLite updates, no worker) | ✅ Done |
| CE-2B | Single worker / queue execution (worker loop, queue polling, pause/resume/stop/cancel *enforcement*, crash recovery, retry) | Not started |
| CE-3 | Video Studio Adapter (the only module allowed to shell out to `seoulkit-studio`) | Not started |
| CE-4a | `ContentPackage` model + Video Studio project-directory assembly | Not started |
| CE-4b | Topic/Research (content-strategy logic; undefined, may move later in the sequence) | Not started |
| CE-5 | Google Flow human checkpoint (`AWAITING_HUMAN_ASSET` job state) | Not started |
| CE-6 | Video Studio invocation wiring (preflight → preview → render via CE-3) | Not started |
| CE-7 | Metadata generation (LLM-based title/description/tags) | Not started |
| CE-8 | Scheduler (publish date/time calculation) | Not started |
| CE-9a | YouTube Publisher (+ first real credential store) | Not started |
| CE-9b | Threads Publisher | Not started |
| CE-9c | Pinterest Publisher | Not started |
| CE-10 | Publish orchestration + result storage | Not started |
| CE-11 | Content Engine CLI (`content-engine status`/`report`) | Not started |
| CE-12 (deferred) | Analytics ingestion | Not planned yet |
| CE-13 (deferred) | Optional Service API (REST/MCP) | Not planned yet |

## Architecture Freeze Review (before CE-1 implementation)

Four points were reviewed before writing any CE-1 code, each with a
concrete resolution that shaped what CE-1 actually contains:

1. **`JobState` vs `ContentPackage.status`**: `jobs.state` is the sole
   source of truth for execution state. `ContentPackage.status` (once
   `ContentPackage` exists, CE-4a) will be a derived/cached field, written
   by exactly one handler per transition (the specific phase's
   job-completion handler) - never a second, independently-mutable state
   machine. No code for this exists yet since `ContentPackage` doesn't
   exist in CE-1.

2. **CE-4 split**: the original single "Content Package + Topic/Research"
   phase was split into CE-4a (project assembly - well-defined now, since
   Video Studio's project-folder contract has been settled since Phase 0)
   and CE-4b (Topic/Research - genuinely undefined, highest-risk-of-change
   part, may even move later in the sequence without blocking anything
   else). CE-4a can be built and tested with a manually-entered topic
   string, with no automated research required.

3. **Credential store deferred to CE-9a**: no phase before CE-9a (YouTube
   Publisher) needs to read or write a credential at all. If CE-7
   (metadata generation) ends up needing a cloud LLM API key, that's a
   single env var read, not a reusable credential-store module - the real
   multi-credential, refresh-aware store only gets designed once CE-9a's
   actual requirements (OAuth refresh tokens) are known.

4. **CE-1 re-scoped to the minimum**: `content_packages`, `publish_results`,
   and `credentials_meta` tables were all removed from CE-1's schema
   (they belong to CE-4a, CE-10, and CE-9a respectively, not Foundation).
   `jobs.content_package_id` and `jobs.retry_count` were also cut from the
   `jobs` table - SQLite `ALTER TABLE ADD COLUMN` is cheap, so adding them
   pre-emptively "to save a later migration" was itself judged a YAGNI
   violation. Two further corrections came from a second review pass right
   before implementation:
   - **No `JobRepository`/DAO in CE-1.** `content_engine/db/connection.py`
     only opens a connection and applies `schema.sql` - it has no
     `insert_job()`/`get_job()`-shaped functions, and `Job` (in
     `jobs/models.py`) has no `to_row()`/`from_row()` methods either (even
     that much would have been the start of a persistence abstraction).
     `tests/test_ce_jobs_models.py`'s round-trip test does its own raw
     `INSERT`/`SELECT` directly, exactly as any CE-1-era caller would have
     to - a real persistence layer is deferred to whenever CE-2 (or a
     later phase) actually needs one.
   - **`jobs.content_package_id`'s migration strategy is not decided now.**
     The CE-1 schema has no such column, and this document does not commit
     to *how* it will be added later (a bare `ALTER TABLE`, a formal
     migration tool, etc.) - that decision is left to CE-4a, once
     `ContentPackage`'s actual shape and the real query patterns against
     `jobs` are known.

## CE-1: `jobs` table + `Job`/`JobState` model

**Files**: `src/content_engine/__init__.py`, `config.py`,
`db/{__init__.py,schema.sql,connection.py}`,
`jobs/{__init__.py,models.py}`; `tests/test_ce_db.py`,
`tests/test_ce_jobs_models.py`.

**Package placement**: `content_engine` lives under `src/`, alongside
`seoulkit_studio` - `pyproject.toml`'s existing
`[tool.setuptools.packages.find] where = ["src"]` (no `include`/`exclude`
filter) auto-discovers it with zero `pyproject.toml` changes, confirmed by
a real `pip install -e ".[dev]"` + `import content_engine` check, not
assumed. `content_engine` has no dependency on `seoulkit_studio` and vice
versa - they are simply two packages the same distribution happens to
install right now; nothing about CE-1 requires them to stay packaged
together.

**Schema** (`db/schema.sql`, the entire file):
```sql
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    state TEXT NOT NULL,
    stage TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);
```
`stage`/`error_message` exist now (a MoneyPrinterTurbo-inspired per-stage
failure-recording hook, see the external-research report this plan
follows from) even though nothing populates them yet in CE-1 - they cost
one nullable column each and don't presuppose any particular job type's
stages, unlike `content_package_id`/`retry_count`, which were cut because
they'd be silently unused *and* couple `jobs` to concepts (a specific FK
target, a specific retry policy) CE-1 doesn't own.

**`JobState`** (`jobs/models.py`): exactly 7 values -
`PENDING/PROCESSING/PAUSED/STOPPED/CANCELLED/COMPLETE/FAILED`. Deliberately
excludes `AWAITING_HUMAN_ASSET` (a CE-5 concept) - adding a new state to a
SQLite `TEXT` column needs no schema migration, only a Python enum
addition when CE-5 actually needs it.

**Testing**: 9 new tests (`test_ce_db.py`: table creation, exact column
list, parent-directory auto-creation, idempotent re-connection;
`test_ce_jobs_models.py`: `JobState`'s 7 values, explicit absence of
`AWAITING_HUMAN_ASSET`, `Job`'s optional-field defaults, and two raw-SQL
round-trip tests - one with all fields populated, one confirming `NULL`
columns round-trip correctly). Full suite: 403/403 passing (394 existing
Video Studio + 9 new), zero regressions.

Two red/green demonstrations were performed before landing:
1. Renamed `schema.sql`'s `error_message` column to `error_msg` (a
   realistic schema/model-drift bug) - 3 tests failed exactly as expected
   (`sqlite3.OperationalError: table jobs has no column named
   error_message`), confirming the round-trip tests actually exercise the
   real column names rather than trivially passing. Reverted, full CE-1
   suite green again (9/9).
2. Removed `JobState.FAILED` - `test_job_state_has_exactly_seven_values`
   failed with the exact missing value reported. Reverted, green again.

No `seoulkit_studio` file was touched during CE-1.

## Architecture Freeze Review (before CE-2A implementation)

Reviewed against the actual CE-1 code and this document before writing
any CE-2 code:

1. **CE-2 split into CE-2A/CE-2B.** The original single "Job Manager
   (single-worker queue, state transitions, retry/failure recording)"
   description bundled two different things, the same shape of problem
   CE-4 had. CE-2A (this phase) is only "given a specific `job_id` the
   caller already knows, validate and atomically apply a state
   transition." CE-2B (worker loop, queue polling, actually *enforcing*
   pause/stop/cancel on running work, crash recovery, retry) is deferred
   - there is no job body to execute yet (CE-3 doesn't exist), so a
   worker would have nothing to run.
2. **No "claim next pending job" function in CE-2A.** Every CE-2A
   function takes an explicit `job_id` the caller already has - there is
   no "give me any work" pull function, since that's queue-consumer
   behavior and belongs to CE-2B.
3. **Exceptions, not Result objects, for invalid operations**:
   `JobNotFoundError` and `InvalidJobTransitionError`, distinguished by
   trying the conditional `UPDATE ... WHERE id = ? AND state IN (...)`
   first and only running a classifying `SELECT` when `rowcount == 0` -
   never `SELECT`-then-`UPDATE`, which would leave a TOCTOU race.
4. **Repeated identical transition requests are not silently absorbed.**
   Calling e.g. `cancel_job()` twice raises `InvalidJobTransitionError` on
   the second call (the job is no longer in an allowed source state) -
   no idempotent-no-op special case was added; that would be unrequested
   scope.
5. **`retry_count` and crash recovery for an orphaned `PROCESSING` row
   after a process restart remain out of scope**, for the same reason as
   CE-2B generally: nothing autonomous runs yet that could need retrying
   or could crash mid-job.

## CE-2A: job state transitions

**Files**: new `src/content_engine/jobs/transitions.py`; new
`tests/test_ce_jobs_transitions.py`. **No schema change** - CE-1's `jobs`
table is used exactly as committed.

**Approved 7x7 transition table** (unchanged from the design review):
```
PENDING    -> PROCESSING, CANCELLED
PROCESSING -> PAUSED, STOPPED, CANCELLED, COMPLETE, FAILED
PAUSED     -> PROCESSING, STOPPED, CANCELLED
STOPPED    -> (terminal)
CANCELLED  -> (terminal)
COMPLETE   -> (terminal)
FAILED     -> (terminal)
```
No `FAILED -> PENDING` retry edge - a retry is a new job, not a
transition on the old one.

**Seven functions**, each owning exactly one edge (or, for `PROCESSING`,
one of the two functions that can reach it depending on source):
`start_job()` (`PENDING -> PROCESSING`, sets `started_at`), `pause_job()`
(`PROCESSING -> PAUSED`), `resume_job()` (`PAUSED -> PROCESSING`, does
*not* touch `started_at`), `stop_job()`/`cancel_job()`/`complete_job()`/
`fail_job()` (all four terminal, all four set `completed_at`;
`fail_job(conn, job_id, *, error_message, stage=None)` additionally
records both). `updated_at` is set on every successful transition, with
no exception. Every function is a thin wrapper around one shared private
`_transition()` helper that does the conditional `UPDATE` + classify-on-
failure `SELECT` described above - no `JobRepository`/DAO, no
`manager.py`, no generic CRUD abstraction; `content_engine/db/
connection.py` still only opens a connection and applies the schema.

**Testing**: 66 new cases across 9 test functions - a fully parametrized
49-case matrix test (7 functions x 7 possible source states each,
checking both the 10 legal edges succeed and the 39 illegal attempts
raise `InvalidJobTransitionError` with the exact `job_id`/
`current_state`/`attempted_state`, and that the row is left completely
unchanged), 7 `JobNotFoundError` cases (one per function, missing
`job_id`), `started_at` set-once-and-preserved-through-pause/resume,
`completed_at` set-on-each-of-4-terminal-transitions plus
stays-`None`-through-pause/resume, `updated_at` advancing past an
explicit fixed-past fixture value (`"2020-01-01T00:00:00+00:00"`, no
`sleep`, no clock abstraction - just comparing two real ISO timestamps),
`fail_job()` recording `stage`/`error_message` correctly, `fail_job()`
requiring `error_message` (a plain Python `TypeError` for a missing
required keyword argument - no extra validation code needed), and one
full-row-byte-identical check after a rejected transition. Full suite:
469/469 passing (394 Video Studio + 9 CE-1 + 66 CE-2A), zero regressions.

Two red/green demonstrations were performed before landing:
1. Added `JobState.PENDING` to `stop_job()`'s allowed source states (a
   real violation of the approved table) - exactly one matrix case failed
   (`stop_job:pending->reject`, "DID NOT RAISE InvalidJobTransitionError"),
   with every other case unaffected. Reverted, full suite green again.
2. Inverted the `if row is None` check in `_transition()`'s failure path
   to `if row is not None` - this broke the exact ordering CE-2A's design
   required: a found row now (wrongly) raised `JobNotFoundError` instead
   of `InvalidJobTransitionError` (failing all 39 illegal-transition
   matrix cases), and a missing row fell through to
   `InvalidJobTransitionError(job_id, JobState(row[0]), ...)` with
   `row is None`, raising a real `TypeError` on `row[0]` (failing all 7
   `JobNotFoundError` cases). 46 tests failed in total, confirming the
   specified check order is load-bearing, not cosmetic. Reverted, full
   suite (469/469) green again.

No `seoulkit_studio` file, and no CE-1 file, was touched during CE-2A.
