"""Stage 1 -> Stage 2 handoff package - CE-4c scope only.

`write_stage2_input()` writes exactly one file, `project_dir/stage2_input.json`,
carrying only Stage 1 content data - nothing this module (or CE-4c as a
whole) is allowed to decide on Stage 2/3/4's behalf:

- `story_function`/`continuity`: Stage 2's own output, not Stage 1's
  (confirmed directly in the Stage 3 manual's own pipeline table - Stage 2
  produces these alongside its keyframe image prompts, Stage 1 does not).
- style anchor selection: Stage 2 decides this itself by generating Shot
  1A first and iterating until approved (Stage 2 manual, "THE CRITICAL
  STEP" section) - nothing to record here in advance.
- `camera_behavior`, motion prompt content: Stage 3's own creative
  decisions, made from Stage 2's *output*, which doesn't exist yet at the
  time this file is written.
- clip duration in any form: the Stage 3 manual is explicit that
  `CLIP_DURATION` is "the duration actually produced/supported by the
  selected generation mode" - a property of whichever Flow/Veo mode is in
  use, not a per-shot creative decision this module could make.
- `expected_filename`: a pure function of `beat`/`shot` (deterministic,
  no state), deliberately *not* precomputed and stored here - Stage 2
  never reads it, and computing it once as a derived value at the point
  it's actually consumed (CE-4d, Stage 2 -> Stage 3 handoff) means there
  is exactly one place a filename-convention change would need to happen,
  not two files that could silently drift apart.
- `stage2_master_style_block`/`stage2_negative_block`: fixed Stage 2
  manual text, not Stage 1 content. Copying it into every project's JSON
  (and into a Content Engine constant) would create a second source of
  truth alongside the Stage 2 manual itself - whoever runs Stage 2 uses
  the official manual directly for these, and this module doesn't carry
  a copy.

The write is UTF-8 explicit (`encoding="utf-8"`, not left to the platform
default) - `voice_text`/`visual_purpose`/etc. are expected to contain
Korean text, and the eventual production environment is Windows, where
the platform-default text encoding is not UTF-8.
"""

from __future__ import annotations

import json
from pathlib import Path

from content_engine.video_planning.models import PlannedShot, Stage2InputPackage


def build_stage2_input(topic: str, shots: list[PlannedShot]) -> Stage2InputPackage:
    return Stage2InputPackage(schema_version="1.0", topic=topic, shots=shots)


def write_stage2_input(project_dir: Path, package: Stage2InputPackage) -> None:
    path = project_dir / "stage2_input.json"
    if path.exists():
        raise FileExistsError(f"{path} already exists - CE-4c does not overwrite an existing plan")

    payload = {
        "schema_version": package.schema_version,
        "topic": package.topic,
        "shots": [
            {
                "beat": s.beat,
                "shot": s.shot,
                "shot_type": s.shot_type,
                "visual_purpose": s.visual_purpose,
                "screen_number": s.screen_number,
                "screen_label": s.screen_label,
                "on_screen_text": s.on_screen_text,
                "voice_text": s.voice_text,
            }
            for s in package.shots
        ],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
