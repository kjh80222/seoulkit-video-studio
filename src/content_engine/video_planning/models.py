"""Stage 1 -> Stage 2 handoff data shape - CE-4c scope only.

Pure data shape, exactly like CE-1's `Job` and CE-4a's `ContentPackage` -
no persistence methods, no derived fields. Every field here is Stage 1
content (the beat/shot table CE-4b will eventually produce) - nothing
Stage 2 itself decides (`story_function`, `continuity`, style anchor
selection) lives here, and nothing from later stages
(`camera_behavior`, motion prompts, clip duration, `expected_filename`)
does either. See `stage2_input.py`'s module docstring for why each of
those was deliberately excluded.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PlannedShot:
    beat: int
    shot: str
    shot_type: str
    visual_purpose: str
    screen_number: str | None
    screen_label: str | None
    on_screen_text: str | None
    voice_text: str


@dataclass
class Stage2InputPackage:
    schema_version: str
    topic: str
    shots: list[PlannedShot]
