import hashlib
import json
from pathlib import Path

import pytest

from seoulkit_studio.execution import check_clip_manifest_consistency, check_sfx_contract_resolution


def base_plan_with_segment(**overrides) -> dict:
    segment = {
        "beat": 1,
        "shot": "1A",
        "usable_start_ms": 0,
        "usable_end_ms": 5800,
        "key_event_end_ms": 4200,
        "settle_start_ms": 4800,
    }
    segment.update(overrides)
    return {"segments": [segment]}


def base_manifest_entry(**overrides) -> dict:
    entry = {
        "shot": "1A",
        "usable_start_ms": 0,
        "usable_end_ms": 5800,
        "key_event_end_ms": 4200,
        "settle_start_ms": 4800,
    }
    entry.update(overrides)
    return entry


def write_manifest(path: Path, clips: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"clips": clips}))


def test_matching_values_produce_no_issues(tmp_path):
    plan = base_plan_with_segment()
    manifest_path = tmp_path / "clip_manifest.json"
    write_manifest(manifest_path, [base_manifest_entry()])

    assert check_clip_manifest_consistency(plan, manifest_path) == []


def test_missing_manifest_file_is_a_single_warning(tmp_path):
    plan = base_plan_with_segment()
    manifest_path = tmp_path / "clip_manifest.json"  # never created

    issues = check_clip_manifest_consistency(plan, manifest_path)

    assert len(issues) == 1
    assert issues[0].code == "CLIP_MANIFEST_MISSING"
    assert issues[0].severity == "warning"


def test_unreadable_manifest_file_is_blocking(tmp_path):
    plan = base_plan_with_segment()
    manifest_path = tmp_path / "clip_manifest.json"
    manifest_path.write_text("{ not valid json")

    issues = check_clip_manifest_consistency(plan, manifest_path)

    assert len(issues) == 1
    assert issues[0].code == "CLIP_MANIFEST_UNREADABLE"
    assert issues[0].severity == "blocking"


def test_shot_missing_from_manifest_is_blocking(tmp_path):
    plan = base_plan_with_segment(shot="1B")
    manifest_path = tmp_path / "clip_manifest.json"
    write_manifest(manifest_path, [base_manifest_entry()])  # only has "1A"

    issues = check_clip_manifest_consistency(plan, manifest_path)

    assert len(issues) == 1
    assert issues[0].code == "CLIP_MANIFEST_SHOT_MISSING"
    assert issues[0].severity == "blocking"
    assert issues[0].segment_ref == "beat=1 shot=1B"


def test_duplicate_shot_in_manifest_is_blocking(tmp_path):
    # A dict built by iterating clips[] would silently let the second entry
    # win, hiding the ambiguity entirely. That's exactly the bug this test
    # guards against - even though the second entry happens to match
    # edit_plan.json (i.e. a naive last-wins comparison would report no
    # issues at all), the duplicate itself must still be reported.
    plan = base_plan_with_segment()
    manifest_path = tmp_path / "clip_manifest.json"
    write_manifest(
        manifest_path,
        [
            base_manifest_entry(usable_end_ms=1111),  # stale/wrong entry, listed first
            base_manifest_entry(),  # "correct" entry, listed second - matches edit_plan.json
        ],
    )

    issues = check_clip_manifest_consistency(plan, manifest_path)

    assert len(issues) == 1
    assert issues[0].code == "CLIP_MANIFEST_DUPLICATE_SHOT"
    assert issues[0].severity == "blocking"
    assert "1A" in issues[0].detail


def test_duplicate_shot_suppresses_further_checks_for_that_shot(tmp_path):
    # Once a shot is flagged as ambiguous, comparing edit_plan.json against
    # whichever manifest entry happens to win the dict build would add a
    # second, potentially misleading issue on top of the ambiguity itself.
    plan = base_plan_with_segment(usable_end_ms=999)  # would also mismatch, if compared
    manifest_path = tmp_path / "clip_manifest.json"
    write_manifest(manifest_path, [base_manifest_entry(), base_manifest_entry()])

    issues = check_clip_manifest_consistency(plan, manifest_path)

    assert len(issues) == 1  # only CLIP_MANIFEST_DUPLICATE_SHOT, no CLIP_MANIFEST_MISMATCH alongside it
    assert issues[0].code == "CLIP_MANIFEST_DUPLICATE_SHOT"


@pytest.mark.parametrize("field", ["usable_start_ms", "usable_end_ms", "key_event_end_ms", "settle_start_ms"])
def test_field_mismatch_is_blocking(tmp_path, field):
    plan = base_plan_with_segment(**{field: 999})
    manifest_path = tmp_path / "clip_manifest.json"
    write_manifest(manifest_path, [base_manifest_entry()])  # unchanged, e.g. not 999

    issues = check_clip_manifest_consistency(plan, manifest_path)

    assert len(issues) == 1
    assert issues[0].code == "CLIP_MANIFEST_MISMATCH"
    assert issues[0].severity == "blocking"
    assert field in issues[0].detail


def test_clip_in_out_ms_are_not_compared(tmp_path):
    # Whether clip_in_ms/clip_out_ms fall inside the (now-verified) usable
    # range is Phase 1's job against edit_plan.json's own copy - this
    # module only compares the four usable-range fields themselves. A
    # segment with absurd clip_in_ms/clip_out_ms but matching usable-range
    # fields must still produce no issues, proving those two fields play
    # no part in this module's comparison.
    plan = base_plan_with_segment(clip_in_ms=999999, clip_out_ms=1)
    manifest_path = tmp_path / "clip_manifest.json"
    write_manifest(manifest_path, [base_manifest_entry()])

    assert check_clip_manifest_consistency(plan, manifest_path) == []


# --- immutability: neither file is ever modified ----------------------------


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_check_never_modifies_either_file_on_match(tmp_path):
    plan_path = tmp_path / "edit_plan.json"
    plan = base_plan_with_segment()
    plan_path.write_text(json.dumps(plan))
    manifest_path = tmp_path / "clip_manifest.json"
    write_manifest(manifest_path, [base_manifest_entry()])

    plan_before, manifest_before = _sha256(plan_path), _sha256(manifest_path)
    issues = check_clip_manifest_consistency(json.loads(plan_path.read_text()), manifest_path)
    plan_after, manifest_after = _sha256(plan_path), _sha256(manifest_path)

    assert issues == []
    assert plan_after == plan_before
    assert manifest_after == manifest_before


def test_check_never_modifies_either_file_on_mismatch(tmp_path):
    plan_path = tmp_path / "edit_plan.json"
    plan = base_plan_with_segment(usable_start_ms=999)
    plan_path.write_text(json.dumps(plan))
    manifest_path = tmp_path / "clip_manifest.json"
    write_manifest(manifest_path, [base_manifest_entry()])

    plan_before, manifest_before = _sha256(plan_path), _sha256(manifest_path)
    issues = check_clip_manifest_consistency(json.loads(plan_path.read_text()), manifest_path)
    plan_after, manifest_after = _sha256(plan_path), _sha256(manifest_path)

    assert len(issues) == 1  # confirms the mismatch was actually detected
    assert plan_after == plan_before
    assert manifest_after == manifest_before


def test_check_never_modifies_manifest_when_manifest_missing(tmp_path):
    plan_path = tmp_path / "edit_plan.json"
    plan = base_plan_with_segment()
    plan_path.write_text(json.dumps(plan))
    manifest_path = tmp_path / "clip_manifest.json"  # never created

    issues = check_clip_manifest_consistency(json.loads(plan_path.read_text()), manifest_path)

    assert len(issues) == 1
    assert not manifest_path.exists()  # still never created by the check itself


# --- check_sfx_contract_resolution() ----------------------------------------


def base_plan_with_sfx(resolved_shots: list[str] | None = None) -> dict:
    return {
        "segments": [],
        "audio_layers": {
            "sfx_source": {
                "mode": "stage3_optional",
                "clips": [{"shot": shot, "file": f"clips/sfx/{shot}.wav", "action": "adopted"} for shot in (resolved_shots or [])],
            }
        },
    }


def write_contract_manifest(path: Path, clips: list[dict], version: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sfx_contract_version": version, "clips": clips}))


def test_legacy_manifest_without_version_is_skipped_entirely(tmp_path):
    # No sfx_contract_version at all - even a shot that would otherwise be
    # SFX_CONTRACT_FIELD_MISSING (no optional_source_audio) must produce
    # nothing, since this manifest never opted into the contract.
    plan = base_plan_with_sfx()
    manifest_path = tmp_path / "clip_manifest.json"
    write_manifest(manifest_path, [{"shot": "1A"}])  # no optional_source_audio, no version key

    assert check_sfx_contract_resolution(plan, manifest_path) == []


def test_available_candidate_resolved_in_plan_produces_no_issue(tmp_path):
    plan = base_plan_with_sfx(resolved_shots=["1A"])
    manifest_path = tmp_path / "clip_manifest.json"
    write_contract_manifest(manifest_path, [{"shot": "1A", "optional_source_audio": {"available": True, "file": "x.wav"}}])

    assert check_sfx_contract_resolution(plan, manifest_path) == []


def test_available_candidate_missing_from_plan_is_a_warning(tmp_path):
    plan = base_plan_with_sfx(resolved_shots=[])  # Stage 4 never resolved it
    manifest_path = tmp_path / "clip_manifest.json"
    write_contract_manifest(manifest_path, [{"shot": "1A", "optional_source_audio": {"available": True, "file": "x.wav"}}])

    issues = check_sfx_contract_resolution(plan, manifest_path)

    assert len(issues) == 1
    assert issues[0].code == "SFX_UNRESOLVED_CANDIDATE"
    assert issues[0].severity == "warning"
    assert issues[0].segment_ref == "shot=1A"


def test_unavailable_candidate_produces_no_issue_even_if_unresolved(tmp_path):
    plan = base_plan_with_sfx(resolved_shots=[])
    manifest_path = tmp_path / "clip_manifest.json"
    write_contract_manifest(manifest_path, [{"shot": "1A", "optional_source_audio": {"available": False, "file": None}}])

    assert check_sfx_contract_resolution(plan, manifest_path) == []


def test_missing_optional_source_audio_field_is_blocking(tmp_path):
    # sfx_contract_version is declared, so every clip must carry the field -
    # its absence is a broken promise, not "still deciding."
    plan = base_plan_with_sfx()
    manifest_path = tmp_path / "clip_manifest.json"
    write_contract_manifest(manifest_path, [{"shot": "1A"}])  # no optional_source_audio

    issues = check_sfx_contract_resolution(plan, manifest_path)

    assert len(issues) == 1
    assert issues[0].code == "SFX_CONTRACT_FIELD_MISSING"
    assert issues[0].severity == "blocking"
    assert issues[0].segment_ref == "shot=1A"


def test_duplicate_shot_is_skipped_rather_than_double_reported(tmp_path):
    # CLIP_MANIFEST_DUPLICATE_SHOT is already reported by
    # check_clip_manifest_consistency() - this check must not add a second,
    # possibly-wrong verdict on top of an already-ambiguous manifest.
    plan = base_plan_with_sfx()
    manifest_path = tmp_path / "clip_manifest.json"
    write_contract_manifest(
        manifest_path,
        [
            {"shot": "1A", "optional_source_audio": {"available": True, "file": "a.wav"}},
            {"shot": "1A"},  # duplicate, and would also be SFX_CONTRACT_FIELD_MISSING on its own
        ],
    )

    assert check_sfx_contract_resolution(plan, manifest_path) == []


def test_missing_manifest_file_produces_no_issue_here(tmp_path):
    # CLIP_MANIFEST_MISSING is check_clip_manifest_consistency()'s issue to
    # raise - this function must not duplicate it.
    plan = base_plan_with_sfx()
    manifest_path = tmp_path / "clip_manifest.json"  # never created

    assert check_sfx_contract_resolution(plan, manifest_path) == []


def test_unreadable_manifest_file_produces_no_issue_here(tmp_path):
    plan = base_plan_with_sfx()
    manifest_path = tmp_path / "clip_manifest.json"
    manifest_path.write_text("{ not valid json")

    assert check_sfx_contract_resolution(plan, manifest_path) == []


def test_sfx_contract_check_never_modifies_either_file(tmp_path):
    plan_path = tmp_path / "edit_plan.json"
    plan = base_plan_with_sfx(resolved_shots=[])
    plan_path.write_text(json.dumps(plan))
    manifest_path = tmp_path / "clip_manifest.json"
    write_contract_manifest(manifest_path, [{"shot": "1A", "optional_source_audio": {"available": True, "file": "x.wav"}}])

    plan_before, manifest_before = _sha256(plan_path), _sha256(manifest_path)
    issues = check_sfx_contract_resolution(json.loads(plan_path.read_text()), manifest_path)
    plan_after, manifest_after = _sha256(plan_path), _sha256(manifest_path)

    assert len(issues) == 1  # confirms the check actually ran
    assert plan_after == plan_before
    assert manifest_after == manifest_before
