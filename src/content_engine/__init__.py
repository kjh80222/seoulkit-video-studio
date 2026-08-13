"""SeoulKit Content Engine (CE) - a new top-level layer above the existing,
frozen Video Studio CORE (`seoulkit_studio`, Phase 0-11).

The boundary is deliberate: Content Engine calls Video Studio only through
its already-public `seoulkit-studio` CLI (subprocess, `--json` output),
never by importing `seoulkit_studio` internals directly - see the
Architecture Freeze Review in `docs/content-engine-phase-plan.md` for why.
Video Studio is not modified by anything under this package.

CE-1 (this phase): a `jobs` table + `Job`/`JobState` data model only - no
queue, no worker, no credentials, no `ContentPackage`, no publishers, no
SNS API, no LLM provider. Later CE phases build on this incrementally;
see `docs/content-engine-phase-plan.md` for the full phase plan.
"""
