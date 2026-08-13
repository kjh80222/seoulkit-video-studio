"""clip_manifest.json cross-validation (Stage 5 spec, ch. 24).

"Stage 3 writes -> Stage 4 consumes -> Stage 5 reads-and-verifies (never
recalculates, never writes)." Stage 5 never estimates or recomputes a
usable range - it only checks that the usable-range fields Stage 4 copied
into edit_plan.json still agree with what Stage 3 actually observed and
recorded in clip_manifest.json.

Scope: exactly the four fields Stage 4 is supposed to have copied from
clip_manifest.json into each segment - usable_start_ms, usable_end_ms,
key_event_end_ms, settle_start_ms. Whether clip_in_ms/clip_out_ms fall
inside that range is already Phase 1's job (against edit_plan.json's own
copy of the range); this module checks whether that copy itself is still
truthful, which Phase 1 has no way to know on its own.

Read-only, like everything else in this pipeline: `clip_manifest.json` is
never opened for writing, matching edit_plan.json's own immutability
guarantee (ch. 06).

`check_sfx_contract_resolution()` (added after Phase 9) closes a gap left
open since Phase 8: `preflight/validator.py::check_declared_warnings()`
can only see an SFX candidate Stage 4 *did* write a `warnings[]` entry
for - if Stage 4 silently drops a candidate (adds it to neither
`sfx_source.clips[]` nor `warnings[]`), nothing in `edit_plan.json` alone
carries any trace that a candidate ever existed. Closing that requires an
independent record of which shots actually had a candidate - the same
"Stage 3 writes, Stage 5 reads-and-verifies" shape this module already
uses for usable-range fields, now extended to
`clip_manifest.json[].optional_source_audio`.

This is opt-in, via a top-level `sfx_contract_version` key on
clip_manifest.json itself. Its absence means "this manifest predates the
SFX contract, or its writer hasn't adopted it yet" - not "there are no
candidates" - so a legacy manifest is skipped by this check entirely,
the same way a missing/unreadable manifest already short-circuits before
any per-shot logic runs (this check deliberately does not re-report
`CLIP_MANIFEST_MISSING`/`CLIP_MANIFEST_UNREADABLE` itself -
`check_clip_manifest_consistency()` already owns those). Declaring
`sfx_contract_version` is a promise: every clip entry must carry
`optional_source_audio` from that point on, so a shot missing that field
once the version is declared is a broken promise
(`SFX_CONTRACT_FIELD_MISSING`, blocking - the same severity as
`CLIP_MANIFEST_SHOT_MISSING`, since both mean "this shot cannot be
verified at all"), not a soft "still deciding" signal. An
`optional_source_audio.available: true` shot that never shows up in
`edit_plan.json`'s `sfx_source.clips[]` is `SFX_UNRESOLVED_CANDIDATE`
(warning - matches Stage 5 spec ch. 04-6's own example of this code) -
that means Stage 4 hasn't finished yet, not that Stage 3's writer is
broken.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from seoulkit_studio.preflight import Preflig
