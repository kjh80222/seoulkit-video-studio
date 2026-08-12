# EXAMPLE — Stage 1 output (not a real production)

This is placeholder Stage 1 output, reusing the worked example from the
SEOULKIT MINI Script System v2.0 manual (sections 12-13), so that this
scaffold demonstrates a real beat/shot pair flowing end to end through
Stage 4's `edit_plan.json` and the Phase 0 validator.

## Script excerpt

> In 1953, Seoul was mostly rubble.

This scaffold only carries Beat 1 through to shots/clips/edit_plan.json —
enough to demonstrate one full beat end to end. The manual's own example
continues with a Beat 2 ("To understand what happened next, look at the
tariff tables.", screen number `$116` / label `tariff`), which is omitted
here to keep the example self-contained.

## Beat table

| Beat | Narration | Visual Purpose | Screen Number | Screen Label |
|---|---|---|---|---|
| 1 | "In 1953, Seoul was mostly rubble." | Establish scale of destruction | 1953 | — |

## Shot table

| Shot | Beat | Type | Scene Description | Visual Focus | On-Screen Text |
|---|---|---|---|---|---|
| 1A | 1 | wide | Establishing shot, full scene, headline included | Overall composition | 1953 |
| 1B | 1 | detail | Close-up cut-in, no headline | One specific detail | — |

See `stage1_beat_shot_table.json` for the machine-readable form (field
mapping per Stage 1 manual, section 17-1).
