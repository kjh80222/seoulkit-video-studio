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
| CE-3 | Video Studio Adapter (the only module allowed to shell out to `seoulkit-studio`) | ✅ Done |
| CE-4a | `ContentPackage` model + Video Studio project-directory assembly | ✅ Done |
| CE-4b | Topic/Research (content-strategy logic; undefined, may move later in the sequence) | Not started |
| CE-4c | Stage 1 → Stage 2 Handoff Package (`stage2_input.json` - Stage 1 content data only, no Stage 2/3/4 creative or measured values) | ✅ Done |
| CE-4d | Stage 2 → Stage 3 Handoff Package (`stage3_input.json` - merges CE-4c's Stage 1 data with Stage 2's structured output by shot identity; `expected_clip_filename()` first computed/consumed here) | ✅ Done |
| CE-4e | Stage 3 QC Assist (`clip_manifest.json` - measuring real Flow clips, Stage 3 QC's responsibility per the file's own header comment) | Not started |
| CE-4f | Stage 4 Voice / Alignment / Semantic Sync (`edit_plan.json` - the actual, sole producer per the Stage 4/5 contract; Stage 5 remains a read-only consumer) | Not started |
| CE-5 | Human Asset Intake / Google Flow Handoff (Google Flow human clip readiness - a thin re-interpretation of CE-3's `run_preflight()`, not a new asset-validation layer) | ✅ Done |
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

## Design refinement (before CE-3 implementation)

CE-3 investigation surfaced a real exit-code collision (see below), and an
approval-round review of the resulting design caught one more issue
before any code was written:

**`preflight`'s `REVIEW_REQUIRED`/`NOT_READY` outcomes are not adapter
failures.** An earlier draft of `_classify()` treated `preflight`'s exit
1 (`REVIEW_REQUIRED`) and exit 2 (`NOT_READY`) identically to
`preview`/`render`'s genuine gate-rejection exit codes, labeling both
`GATE_REJECTED`. That contradicts `preflight`'s own nature: it's a
read-only validation operation, not a gated execution. A `preflight` call
that runs successfully and *discovers* `NOT_READY` is an adapter
**success** (`ok=True`) - no render was ever attempted, so nothing failed.
Only `preview`/`render` can be gate-*rejected*, because only they attempt
to run something a gate can refuse. `_classify()` was made command-aware
(`_classify(command, exit_code, stdout)`) specifically to keep these two
meanings apart, rather than sharing one exit-code table across all three
commands.

## CE-3: Video Studio Adapter

**Files**: new `src/content_engine/video_studio/__init__.py`; new
`src/content_engine/video_studio/adapter.py`; new
`tests/test_ce_video_studio_adapter.py`. **No schema change, no new
dependency.**

**Boundary**: this module is the only place in Content Engine allowed to
shell out to Video Studio, and it does so exactly as
`[sys.executable, "-m", "seoulkit_studio.cli.main", command, str(project_dir), "--json"]`
- a real subprocess reading `--json` stdout, never an import of
`seoulkit_studio` internals. Command scope is exactly `preflight`,
`preview`, `render` (`status`/`report` are out of scope). This module does
not call CE-2A's transition functions, does not know about `job_id`/
`JobState`, does not touch the database, does not create project
directories, does not implement timeout/retry/cancel/pause/worker/queue,
and does not re-parse Render Report file contents - `output_path`/
`report_path`/`log_path` are passed through exactly as the CLI's JSON
payload already exposes them.

**Exit-code collision found and defended against**: an argparse-level
usage error (e.g. a missing required `project_dir` positional) exits with
code 2 and leaves stdout completely empty, colliding numerically with the
app's own domain `NOT_READY=2`. `_classify()` never trusts the exit code
before confirming stdout parses as a JSON object shaped like a real Video
Studio payload (`effective_status` present, or `error`/`exit_code` present
for a usage error) - a bare exit code alone can't tell them apart, but a
valid payload always accompanies a real domain outcome and an argparse
error never produces one. The same check also covers the empirically
confirmed shape of an uninstalled `seoulkit_studio` (a bare venv without
the package produces a normal exit=1, empty stdout, plain-text
`ModuleNotFoundError` on stderr - not a Python-level `FileNotFoundError`
in the calling process) - both collapse into the same
`ADAPTER_INVOCATION_ERROR` bucket rather than a speculative dedicated
category.

**`AdapterFailureCategory`** (4 values): `GATE_REJECTED`,
`EXECUTION_FAILED`, `USAGE_ERROR`, `ADAPTER_INVOCATION_ERROR`. No
`SEOULKIT_STUDIO_NOT_FOUND` - removed after empirical testing showed that
failure mode is indistinguishable in shape from the argparse collision.

**`_classify(command, exit_code, stdout)`** (command-aware, see refinement
above): `preflight` treats exit 0/1/2 with a valid payload as `ok=True`
regardless of `effective_status`; `preview`/`render` treat exit 0 as
`ok=True`, exit 1/2 as `GATE_REJECTED`, exit 3 as `EXECUTION_FAILED`. Both
branches treat exit 4 with a valid `{"error", "exit_code"}` payload as
`USAGE_ERROR`, and anything with a malformed/empty/unexpected-shaped
stdout as `ADAPTER_INVOCATION_ERROR`. `_run_cli()` additionally catches a
stray `OSError` from `subprocess.run()` itself and converts it to
`ADAPTER_INVOCATION_ERROR` rather than letting it propagate raw. Payload
validation is a minimal key-presence check, not a schema library.

**Public functions**: `run_preflight(project_dir)`, `run_preview
(project_dir)`, `run_render(project_dir)`, each a thin wrapper over
`_run_cli(command, project_dir) -> AdapterResult`.

**Testing**: 32 new cases across 5 groups - `_classify()` for `preflight`
(6 cases: exit 0/1/2 all `ok=True`, exit 4 `USAGE_ERROR`, the argparse
exit-2/empty-stdout collision, malformed JSON), `_classify()` for
`preview`/`render` (7 cases x 2 commands = 14: exit 0 `ok=True`, exit 1/2
`GATE_REJECTED`, exit 3 `EXECUTION_FAILED`, exit 4 `USAGE_ERROR`, the same
argparse collision, malformed JSON), minimal payload-shape validation (3
cases: a JSON array instead of an object, a dict missing
`effective_status`, an exit-4 dict missing `error`/`exit_code`), real
subprocess-level `_run_cli()` behavior (6 cases: a monkeypatched stray
`OSError` converted to `ADAPTER_INVOCATION_ERROR`, a real successful
`preflight`, a real successful `render` with `output_path` verified
against the actual file on disk, a real `NOT_READY` `render` producing
`GATE_REJECTED`, a real invocation against a bare venv that never had
`seoulkit_studio` installed, and a real argparse collision reproduced by
omitting the required `project_dir` argument), and public-function wiring
(3 cases: each of `run_preflight`/`run_preview`/`run_render` calls
`_run_cli()` with the correct command string). Full suite: 501/501
passing (394 Video Studio + 9 CE-1 + 66 CE-2A + 32 CE-3), zero
regressions.

One red/green demonstration was performed before landing, reproducing the
exact bug caught during design review: reverted `_classify()`'s
`preflight` branch to classify exit 1/2 as `GATE_REJECTED` instead of
`ok=True`. Exactly two tests failed -
`test_classify_preflight[exit1-ok]` and `test_classify_preflight[exit2-ok]`
- with every other case (including the 14 `preview`/`render` cases and all
6 real-subprocess cases) unaffected. Reverted, full suite (501/501) green
again.

No `seoulkit_studio` file, and no CE-1/CE-2A file, was touched during
CE-3.

## Architecture Freeze Review (before CE-4a implementation)

Real-repository investigation (`examples/sample-project`, `schema/edit_plan.schema.json`,
`cli/project.py`, `preflight/validator.py`, `render/report.py`) established
facts that shaped CE-4a's scope before any code was written:

- Video Studio (`src/seoulkit_studio/`) implements only "Stage 5" of a
  larger, external methodology. `clips/clip_manifest.json`'s own header
  comment in the real example project reads: *"OWNER: Stage 3 QC... Stage
  4 and Stage 5 are READ-ONLY consumers of this file."* `edit_plan.json`'s
  schema requires per-segment timing/QC fields (`usable_start_ms`,
  `key_event_end_ms`, `hold_strategy`, ...) that only Stage 1-4 (script,
  shot metadata, Flow clip QC, voice generation/alignment) can produce -
  no code anywhere in this repository computes them.
- Video Studio itself only auto-creates `output/`, `preview/`, and
  report/log parent directories at render time
  (`render/report.py`'s `mkdir(parents=True, exist_ok=True)`, 4 call
  sites, confirmed by full-repo grep). It never creates `project_dir`,
  `clips/`, or `clips/audio/` - those must already exist, or at least the
  individual files `preflight` checks (`voice.audio_file`, each
  `segments[].source_clip`, etc.) must exist as real files.
- `edit_plan.json`'s own `project` field, and the example's
  `seoulkit.project.json` scaffold file, are never read by any Video
  Studio code (confirmed by grep) - directory naming is unconstrained by
  Video Studio.

This grounded CE-4a's scope to exactly: create a `ContentPackage` DB row
and the minimal directory skeleton Video Studio actually requires to
exist beforehand - never `edit_plan.json` or `clip_manifest.json`
content, which belong to Stage 1-4/Stage 3 respectively and are not
reimplemented anywhere in Content Engine's roadmap.

Five decisions were then locked before implementation:

1. **`ContentPackage.id` = UUID4.** No slug/timestamp-based id, so the
   same topic can back multiple independent packages. `Job.id` (never
   actually generated by any shipped code before now) adopts the same
   policy going forward, though CE-4a does not itself create any `Job`.
2. **No `ContentPackage.status`.** Nothing consumes a whole-package
   progress value yet; adding one now would risk a second, drifting
   source of truth alongside `jobs.state`. A future consumer computes it
   by joining `jobs` on `content_package_id` at read time instead.
3. **Workspace assembly is not a `Job`.** It's treated as a short local
   filesystem operation - no `Job` row, no CE-2A transition calls, no
   worker/queue, and therefore no `jobs.content_package_id` column in
   this phase (deferred to whichever phase first needs a real job tied to
   a package).
4. **`project_dir` is stored as a fully resolved absolute path**, not
   recomputed from `root + id` at read time - `resolve_projects_root()`
   always returns an absolute, `~`-expanded path even when
   `CONTENT_ENGINE_PROJECTS_ROOT` is set to something relative, so the
   stored value stays valid regardless of the reading process's current
   working directory.
5. **CE-4a creates exactly `project_dir/` and `project_dir/clips/`** -
   nothing else. `edit_plan.json`, `clip_manifest.json`, `clips/audio/*`,
   `preview/`, `output/`, `logs/`, and `seoulkit.project.json` are all
   deliberately not created, per the ownership facts above.

## CE-4a: `ContentPackage` + Video Studio project assembly

**Files**: new `src/content_engine/content_packages/{__init__.py,models.py,workspace.py,create.py}`;
new `tests/test_ce_config.py`,
`tests/test_ce_content_packages_{models,workspace,create}.py`; modified
`src/content_engine/db/schema.sql` (new `content_packages` table, `jobs`
table untouched), `src/content_engine/config.py` (new
`resolve_projects_root()`).

**Schema** (appended to `db/schema.sql`):
```sql
CREATE TABLE IF NOT EXISTS content_packages (
    id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    project_dir TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

**`ContentPackage`** (`content_packages/models.py`): exactly `id`,
`topic`, `project_dir`, `created_at`, `updated_at` - no `status`, no DAO
(raw SQL round-trip in tests, same as CE-1's `Job`).

**`resolve_projects_root()`** (`config.py`, alongside `resolve_db_path()`):
default `~/.local/share/seoulkit-content-engine/projects`, overridable via
`CONTENT_ENGINE_PROJECTS_ROOT`; always returns an absolute, `~`-expanded,
`.resolve()`d path regardless of what form the override takes.

**`assemble_workspace(project_dir)`** (`content_packages/workspace.py`):
pure filesystem, no DB, no id - `project_dir.mkdir(parents=True, exist_ok=True)`
then `(project_dir / "clips").mkdir(exist_ok=True)`. Idempotent by
construction (`exist_ok=True` no-ops on an already-existing directory,
and the function never lists or touches files already inside `clips/`).
Raises the standard library's own `FileExistsError`/`NotADirectoryError`
unwrapped if either path is occupied by a non-directory.

**`create_content_package(conn, topic)`** (`content_packages/create.py`):
the single entry point, guaranteeing DB-insert-then-workspace-assembly
ordering. Strips and validates `topic` (rejects empty/whitespace-only,
raising a plain `ValueError`), generates the `id` via `uuid.uuid4()`
inline (no ID-generation abstraction), `INSERT`s and commits the
`content_packages` row *before* calling `assemble_workspace()`. If
assembly then fails, the raw `OSError` is wrapped in
`WorkspaceAssemblyError(content_package_id, project_dir, cause)` - the
already-committed row is never rolled back or deleted; the exception
exists purely to hand the caller the id/path needed to retry
`assemble_workspace()` directly, since that function is idempotent.

**Why DB-first**: if the DB write is the one guaranteed to happen first,
a filesystem failure afterward still leaves something a caller can find
and repair (the row's own `project_dir`, reusable directly). The reverse
order has no equivalent recovery path - a directory created before an
then-failing DB write becomes an orphan nothing can locate again.

**Testing**: 26 new cases across 4 files - `resolve_projects_root()`
default/absolute-override/relative-override/`~`-override (4, in
`test_ce_config.py`), `content_packages` table+column shape and
`ContentPackage` round-trip (4), `assemble_workspace()` creation/idempotent-
recall/occupied-path-errors/never-touches-existing-files (5), and
`create_content_package()`'s row+workspace creation, UUID4 `id` shape,
same-topic-twice producing distinct packages, topic validation
(empty/whitespace/strip/Korean), the DB-insert-failure-leaves-nothing
case, the workspace-failure-leaves-a-recoverable-row case (plus the
wrapped exception's exact fields and a real retry-to-recovery), and one
real end-to-end check that a workspace this module builds - with a
hand-authored `edit_plan.json`/`clip_manifest.json`/clip dropped in by
the test itself, exactly as a human would - passes through CE-3's
`run_preflight()` (13, in `test_ce_content_packages_create.py`). Full
suite: 527/527 passing (394 Video Studio + 9 CE-1 + 66 CE-2A + 32 CE-3 +
26 CE-4a), zero regressions.

One red/green demonstration was performed before landing, on the single
most architecturally important guarantee of this phase: inverted
`create_content_package()`'s order to assemble the workspace *before* the
DB insert (a real violation of the DB-first principle above). Exactly 3
tests failed - `test_db_insert_failure_leaves_no_row_and_no_directory`
(a directory was now left behind on a simulated DB failure),
`test_workspace_assembly_failure_raises_wrapped_error_but_row_survives`,
and `test_workspace_assembly_error_carries_the_exact_package_id_and_project_dir`
(both because the row no longer existed when workspace assembly failed
first) - with the other 10 `test_ce_content_packages_create.py` cases and
all other files unaffected. Reverted, full suite (527/527) green again.

No `seoulkit_studio` file, and no CE-1/CE-2A/CE-3 file, was touched during
CE-4a. `jobs` table definition unchanged.

## Architecture Freeze Review (before CE-5 implementation)

A real-repository investigation preceded this phase (see the git history
around this section for the full report). Three findings shaped CE-5's
scope down to something much narrower than its original description:

- **`preflight` never decodes media.** `preflight/validator.py::check_file_existence()`
  only calls `.is_file()` - no `ffprobe`/`subprocess` call exists anywhere
  under `preflight/` or `execution/` (confirmed by a full-repo grep). A
  corrupt or truncated clip passes `preflight` cleanly and only fails
  later, at actual render time, as `EXECUTION_FAILED`. CE-5 does **not**
  add its own `ffprobe` check to compensate - doing so would open a
  second, independent media-validation path outside the
  Content-Engine-to-CE-3-to-Video-Studio-CLI boundary, one that could
  silently drift from `preflight` if Video Studio's own validation is
  ever strengthened later. This is recorded here as a known Video Studio
  gap, not fixed at the Content Engine layer.
- **The sole source of truth for "which Flow clips are required" is
  `edit_plan.json`'s `segments[].source_clip`.** `clips/clip_manifest.json`
  never introduces a requirement on its own - it only cross-validates
  timing fields for shots `edit_plan.json` already references
  (`execution/clip_manifest.py`, confirmed by reading it directly). No
  code anywhere in this repository produces `edit_plan.json` - CE-4a
  deliberately doesn't, and CE-4b (Topic/Research) is scoped narrowly to
  Stage 1 (research), not Stage 2 (segment/timing planning). This is a
  genuine, still-open architecture gap, not something CE-5 absorbs.
- **A new phase, CE-4c ("Video Planning / Flow Handoff Package"), is
  registered in the phase table above to eventually close that gap** -
  producing `edit_plan.json` and the human-facing Google Flow prompt
  *together*, from one shared internal plan, so that values both outputs
  need (expected filename, clip id, target duration) are decided exactly
  once rather than independently re-derived in two places. CE-4c is
  **not implemented in this phase** - only registered as "Not started".
  CE-5 remains fully buildable and testable without it, the same way
  CE-3's own tests already do: by hand-authoring `edit_plan.json`
  directly in test fixtures.
- **`clips/clip_manifest.json`'s real content (measured usable ranges)
  can only be produced by inspecting real Flow clips after a human has
  generated them** - it is Stage 3 QC's responsibility per the file's own
  header comment (`"OWNER: Stage 3 QC, written at G4 pass time"`), and no
  phase in this roadmap owns Stage 3 QC automation yet. This remains an
  explicit, unassigned gap - CE-5 does not absorb it either.

Two scope-narrowing decisions were then made in review, before any CE-5
code was written:

1. **`AWAITING_HUMAN_ASSET` is not added to `JobState` in this phase.**
   Nothing would ever produce it - CE-2B (the only thing that could pause
   a running `Job` on a missing asset) doesn't exist, and CE-5 itself
   creates no `Job`. Adding an unused enum value/transition would repeat
   the exact pattern CE-1 and CE-4a already rejected for
   `jobs.content_package_id` and `ContentPackage.status`. `jobs/transitions.py`
   is therefore untouched by CE-5.
2. **CE-5's `ready=False` condition is limited to exactly `MISSING_CLIP`
   plus `edit_plan.json` load failure.** An earlier draft also included
   `MISSING_VOICE_ASSET`/`MISSING_BGM_FILE`/`MISSING_SFX_FILE` (all real
   `preflight` issue codes), but review caught that this quietly expanded
   CE-5 from "did the human supply the Google Flow clips" into "is
   everything needed for Final Render present" - a different, larger
   responsibility that isn't this phase's to own (Voice/BGM/SFX are each
   some other Stage's asset, not a Flow clip). `CLIP_MANIFEST_MISSING`
   was excluded for the same reason one review round earlier: it's
   Video Studio's own non-blocking warning, and CE-5 does not promote it
   to a blocker Video Studio itself doesn't consider one.

## CE-5: Human Asset Intake / Google Flow Handoff

**Files**: new `src/content_engine/content_packages/readiness.py`; new
`tests/test_ce_content_packages_readiness.py`. **No schema change, no new
dependency, no `JobState`/`jobs/transitions.py` change.**

**Scope**: exactly two questions - *can the required Google Flow clips be
determined at all* (`edit_plan.json` must exist and load), and *are they
all present* (no `MISSING_CLIP` issue in `preflight`'s output). Nothing
else `preflight` reports (`MISSING_VOICE_ASSET`, `MISSING_BGM_FILE`,
`MISSING_SFX_FILE`, `CLIP_MANIFEST_MISSING`, or any plan/QC-consistency
code) affects CE-5's verdict - Video Studio's own `preflight` output still
carries all of them untouched; CE-5 simply has no opinion on them. CE-5
writes nothing to `clips/` - a human places Flow clips there directly,
exactly as the existing workspace convention (CE-4a) already expects.

**`AssetReadinessResult(ready, missing)`** (`content_packages/readiness.py`):
a plain Result dataclass, not a new state machine. `check_asset_readiness(project_dir)`
calls CE-3's `run_preflight(project_dir)` unmodified and re-interprets its
`payload` - never re-implementing any check Video Studio already performs.
`payload["load_error"]` set (edit_plan.json missing/invalid) and any
`MISSING_CLIP` issue are the only two conditions that produce
`ready=False`. A `preflight` call that itself fails to run
(`result.ok is False` - `USAGE_ERROR`/`ADAPTER_INVOCATION_ERROR`) is not
swallowed into `ready=False`: it re-raises as a plain `RuntimeError`
carrying `category`/`exit_code`/`stderr`, keeping "the human hasn't
supplied a clip yet" clearly distinct from "the system itself is broken".

**Testing**: 11 new cases, all filesystem-only (Video Studio's `preflight`
only checks file existence, so no real FFmpeg-encoded media is needed -
empty placeholder files suffice) - `edit_plan.json` missing (1),
one/multiple `MISSING_CLIP` (2), Voice/BGM/SFX/`CLIP_MANIFEST_MISSING`
each missing alone still `ready=True` (4), all four missing
*simultaneously* with every clip present still `ready=True` (1, the
scope-narrowing decision's direct proof), a fully clean project (1),
idempotent re-invocation (1), and an `AdapterResult(ok=False, ...)`
producing a `RuntimeError` carrying `category`/`exit_code`/`stderr` (1).
Full suite: 538/538 passing (394 Video Studio + 9 CE-1 + 66 CE-2A + 32
CE-3 + 26 CE-4a + 11 CE-5), zero regressions.

One red/green demonstration was performed before landing, reproducing
the exact scope-widening this review caught: added `CLIP_MANIFEST_MISSING`
back into `_ASSET_ISSUE_CODES` (a real reversion of the just-made
decision). 7 of the 11 new tests failed -
`test_missing_clip_manifest_alone_is_still_ready` directly (asserted
`ready is True`, got `False`), plus 6 others
(`test_one_missing_clip_is_not_ready`,
`test_multiple_missing_clips_are_all_reported`,
`test_missing_voice_alone_is_still_ready`,
`test_missing_bgm_alone_is_still_ready`,
`test_missing_sfx_alone_is_still_ready`, and
`test_voice_bgm_sfx_and_clip_manifest_all_missing_together_is_still_ready`)
that never write `clips/clip_manifest.json` in their own fixtures at all
(it was never needed once the file's absence was confirmed non-blocking) -
each unexpectedly flipped to `ready=False` too. This turned out to be a
more convincing demonstration than a single-test failure would have been:
it shows concretely how many of CE-5's own fixtures silently depend on
`CLIP_MANIFEST_MISSING` staying excluded, not just the one test written
to name that fact directly. The other 4 cases and all other files were
unaffected. Reverted, full suite (538/538) green again.

No `seoulkit_studio` file, and no CE-1/CE-2A/CE-3/CE-4a file, was touched
during CE-5. `jobs` table and `JobState` unchanged. CE-4c remains
registered in the phase table as "Not started" - not implemented.

## Architecture Freeze Review (before CE-4c implementation)

CE-4c's scope was investigated and narrowed across several review rounds,
grounded in two source documents outside this repository -
`SEOULKIT_Stage2_MINI_Image_Prompt_Manual_v2.0` and
`SEOULKIT_Stage3_MINI_Video_Prompt_Manual_v2.0` - read directly rather
than assumed. Reading them corrected an entire earlier draft:

- **CE-4c does not produce `edit_plan.json`.** An early draft had CE-4c
  write a draft `edit_plan.json` for Stage 4 to later revise - a genuine
  dual-ownership violation of the Stage 4/5 contract ("Stage 4 produces,
  Stage 5 only reads"). `edit_plan.json` production was moved entirely to
  a new, not-yet-implemented CE-4f.
- **`story_function`/`continuity`/style-anchor selection are Stage 2's own
  output, not Stage 1's.** The Stage 3 manual's own pipeline table
  confirms this directly ("Stage 2에서 읽는 것: ... Story Function,
  Continuity intent" - produced by Stage 2 alongside its keyframe image
  prompts). An earlier `PlannedShot` draft wrongly modeled these as CE-4c
  inputs.
- **`camera_behavior` is Stage 3's own creative decision** (chosen from a
  defined camera-move palette based on each shot's Story Function, per
  the Stage 3 manual), not something CE-4c can predetermine before Stage
  2 has even produced the images Stage 3 works from.
- **Clip duration is not a per-shot creative value at all.** The Stage 3
  manual is explicit: `CLIP_DURATION = duration actually
  produced/supported by the selected generation mode` - a property of
  whichever Flow/Veo mode is in use, not something a shot planner
  chooses. An earlier `flow_target_duration_ms` field was removed
  entirely.
- **Stage 2 and Stage 3 are two separate, sequential human+LLM-chat
  loops**, each already fully specified by its own manual's
  copy-paste-into-an-LLM-chat master prompt - Stage 3 cannot even begin
  until Stage 2's output (approved images + Story Function + Continuity)
  exists. This is why the phase table now has CE-4c (Stage 1 → Stage 2)
  and CE-4d (Stage 2 → Stage 3) as two separate phases rather than one -
  no single CE-4c invocation has both halves' data available at once.
- **The Stage 2 manual's `MASTER STYLE BLOCK`/`NEGATIVE BLOCK` are fixed
  manual text, not Stage 1 content.** Copying them into every project's
  JSON (and into a Content Engine constant, verified against the manual
  by a string-equality test) would create a second source of truth
  alongside the manual itself. CE-4c carries none of this text - whoever
  runs Stage 2 uses the official manual directly.
- **`expected_filename` is a pure, deterministic function of `beat`/
  `shot`** (`shot="1A"` → `clips/shot_1a_flow.mp4`, matching the existing
  `clips/README.md` convention) - but it is computed nowhere in CE-4c.
  Stage 2 never reads it, so precomputing and storing it now would be a
  derived value with no consumer, sitting in a file that could drift from
  the naming rule if that rule ever changed. It is deferred to CE-4d,
  where it is first actually needed and consumed.

What survived every round of narrowing: CE-4c carries **only Stage 1
content data** - `topic` and, per shot, `beat`/`shot`/`shot_type`/
`visual_purpose`/`screen_number`/`screen_label`/`on_screen_text`/
`voice_text`. Nothing else.

## CE-4c: Stage 1 → Stage 2 Handoff Package

**Files**: new `src/content_engine/video_planning/{__init__.py,models.py,stage2_input.py}`;
new `tests/test_ce_video_planning_{models,stage2_input}.py`. **No schema
change, no new dependency, no DB change** - this phase writes exactly one
plain file into an already-existing workspace.

**`PlannedShot`/`Stage2InputPackage`** (`video_planning/models.py`): pure
data shape, no persistence methods, matching CE-1's `Job`/CE-4a's
`ContentPackage` precedent.

**`write_stage2_input(project_dir, package)`** (`video_planning/stage2_input.py`):
writes `project_dir/stage2_input.json` with UTF-8 explicit
(`encoding="utf-8"`) - `voice_text`/`visual_purpose` are expected to
carry Korean text, and the eventual production environment is Windows,
where the platform-default text encoding is not UTF-8. If
`stage2_input.json` already exists, raises a plain `FileExistsError`
(matching CE-2A's stdlib-exception precedent) rather than overwriting it
- a human may have already started a Stage 2 conversation from the
existing file's content, and silently replacing it could orphan that
work. No auto-overwrite, backup, or versioning was built for this case.

**Testing**: 9 new cases, all filesystem-only (no FFmpeg, no DB) -
`PlannedShot`/`Stage2InputPackage` carry exactly the expected fields and
none of the explicitly-excluded ones (2), `build_stage2_input()` passthrough
(1), the written JSON has exactly the expected keys and none of the
forbidden ones (1), `None` optional fields serialize to JSON `null` (1),
multiple shots preserve order (1), Korean/Unicode content round-trips
through explicit UTF-8 (1), an existing `stage2_input.json` is rejected
and left byte-unchanged (1), and nothing else in `project_dir` (e.g.
`clips/`) is touched (1). Full suite: 547/547 passing (394 Video Studio +
9 CE-1 + 66 CE-2A + 32 CE-3 + 26 CE-4a + 11 CE-5 + 9 CE-4c), zero
regressions.

One red/green demonstration was performed before landing, on the
overwrite-rejection guarantee: removed the `path.exists()` check from
`write_stage2_input()`. Exactly one test failed -
`test_write_stage2_input_rejects_an_existing_file_without_modifying_it`
("DID NOT RAISE FileExistsError") - with the other 6
`test_ce_video_planning_stage2_input.py` cases and all other files
unaffected. Reverted, full suite (547/547) green again.

No `seoulkit_studio` file, and no CE-1/CE-2A/CE-3/CE-4a/CE-5 file, was
touched during CE-4c. DB schema unchanged. CE-4d/CE-4e/CE-4f remain
registered in the phase table as "Not started" - not implemented.

## Architecture Freeze Review (before CE-4d implementation)

Four corrections were made to an earlier CE-4d draft before any code was
written, all caught by re-checking the draft against the same two Stage
2/3 manuals CE-4c's review used:

1. **`visual_purpose` was missing from the draft `stage3_input.json`
   despite the Stage 3 manual listing it as part of the required input**
   ("Stage 1 테이블에서: Shot 번호, Beat, Visual Purpose ..."). Restored,
   sourced from CE-4c's `stage2_input.json` unchanged.
2. **A top-level `style_anchor_path` field was removed.** The Stage 3
   manual's required-input list is exactly three items (Beat+Shot table,
   approved keyframe image, Story Function + Continuity) and does not
   separately reference a style anchor - by the time Stage 2 hands off an
   approved keyframe, whatever style consistency the anchor established
   is already baked into that image. A second top-level pointer to the
   same thing would have been redundant, drift-prone data.
3. **Story Function/Continuity are consumed as structured input
   (`Stage2ShotOutput`), not typed ad hoc per call** - matching the real
   shape already used by `examples/sample-project/references/stage2_shot_metadata.json`
   (`shot`/`beat`/`story_function`/`continuity` per entry). Merging this
   against CE-4c's `stage2_input.json` is done strictly by `shot` identity,
   never by list position - a positional zip would silently attach one
   shot's Story Function to a different shot's keyframe on any ordering
   mismatch. Four failure classes are checked and reported together in
   one `ShotIdentityMismatchError`: `missing`, `extra`, `duplicate`, and
   `beat_mismatches`.
4. **A `keyframes/` project-relative workspace convention was adopted**
   for Stage 2's approved keyframe images (parallel to `clips/`, which is
   reserved for Stage 3's video output). CE-4d only reads this location -
   it never creates, copies, moves, or modifies the image files a human
   places there. Checking that a referenced keyframe file actually exists
   is not validation-duplication (Video Studio has no concept of a
   keyframe at all, unlike the `preflight` overlap CE-5 deliberately
   avoided) - so `write_stage3_input()` does check existence, but never
   opens, decodes, or judges the image's content/resolution/quality.

## CE-4d: Stage 2 → Stage 3 Handoff Package

**Files**: new `src/content_engine/video_planning/stage3_input.py`; new
`tests/test_ce_video_planning_stage3_input.py`; modified
`src/content_engine/video_planning/models.py` (new `Stage2ShotOutput`/
`Stage3PlannedShot`/`Stage3InputPackage`), `tests/test_ce_video_planning_models.py`
(field-shape tests for the three new dataclasses). **No schema change, no
new dependency, no DB change.**

**`build_stage3_input(stage2_input, stage2_outputs)`**: merges CE-4c's
`Stage2InputPackage` with a `list[Stage2ShotOutput]` by `shot` identity,
preserving `stage2_input.shots`'s own order in the result. Raises
`ShotIdentityMismatchError` (carrying `missing`/`extra`/`duplicate`/
`beat_mismatches` all at once, never stopping at the first problem found)
if the two inputs don't line up exactly. `expected_clip_filename(shot)`
is computed here for the first time in the whole pipeline (`"1A"` ->
`"clips/shot_1a_flow.mp4"`, the same rule CE-4c's design review already
settled on but deferred).

**`write_stage3_input(project_dir, package)`**: fixed validation order,
so a later check can never partially undermine the overwrite guard's
meaning - (1) reject if `stage3_input.json` already exists
(`FileExistsError`), (2) reject any `approved_keyframe_path` that's
absolute or escapes `project_dir` via `..` (`ValueError`), (3) collect
every missing keyframe file and raise once with the complete list
(`MissingKeyframeError`), (4) only then serialize with UTF-8 explicit
(`encoding="utf-8"`).

**Testing**: 17 new cases, all filesystem-only (no FFmpeg, no DB) - the
three new dataclasses carry exactly their expected fields and none of the
forbidden ones (3, in `test_ce_video_planning_models.py`), `expected_clip_filename()`
(1), a correct merge preserving order (1), each of the four identity-
mismatch classes raising independently (4), the written JSON has exactly
the expected keys (1), absolute-path and `..`-traversal rejection (2),
every missing keyframe reported together (1), a successful write when
keyframes exist (1), an existing `stage3_input.json` rejected and left
byte-unchanged (1), Korean/UTF-8 round-trip (1), and the keyframe file's
own bytes left untouched (1). Full suite: 564/564 passing (394 Video
Studio + 9 CE-1 + 66 CE-2A + 32 CE-3 + 26 CE-4a + 11 CE-5 + 9 CE-4c + 17
CE-4d), zero regressions.

One red/green demonstration was performed before landing, on the
duplicate-shot safeguard specifically - the review's stated top concern
for this phase, since a silent duplicate is a data-corruption risk (one
shot's Story Function attaching to another), not merely a missing-file
inconvenience like a simple overwrite. Removed the `duplicate.append()`
call from the loop building `output_by_shot`, letting a later entry for
the same `shot` silently replace an earlier one in the dict. Exactly one
test failed - `test_build_stage3_input_raises_on_duplicate_shot` ("DID
NOT RAISE ShotIdentityMismatchError") - with the other 13
`test_ce_video_planning_stage3_input.py` cases (including the missing/
extra/beat-mismatch identity checks) and all other files unaffected.
Reverted, full suite (564/564) green again.

No `seoulkit_studio` file, and no CE-1/CE-2A/CE-3/CE-4a/CE-5/CE-4c file,
was touched during CE-4d. DB schema unchanged. CE-4e/CE-4f remain
registered in the phase table as "Not started" - not implemented.
