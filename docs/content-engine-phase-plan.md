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
