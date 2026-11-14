import pytest

from cinecore.audit import audit_video, has_pronoun_collision, report_passed
from cinecore.errors import ResumeInterrupt
from cinecore.library import AssetLibrary, face_token
from cinecore.policy import film_status, iter_unattended, next_decision

POLICY = {
    "takes_per_shot": 3,
    "max_regenerations": 1,
    "max_quarantined_shots": 3,
    "quarantine_shot_ratio": 0.15,
}


def _job(**overrides):
    job = {
        "frame_count": 124,
        "camera": {"framing": "中景", "angle": "平视", "forbids_hand_closeup": True},
        "action": "一个人站着。",
        "references": [
            {"asset_id": "char-a", "role": "subject"},
            {"asset_id": "loc-a", "role": "location"},
        ],
        "first_frame": {
            "asset_id": "kf-s001",
            "approved": True,
            "latent_node": "EmptySD3LatentImage",
            "width": 768,
            "height": 1344,
        },
        "character_face_token": "same",
        "continuation": None,
        "video_prompt": {
            "subject_definitions": "<Picture 1> is the first frame of [Shot 1].",
            "summary": "[keyframe completion + reference generation] One person.",
            "retention_analysis": "fully_preserved",
            "detailed_description": "[Shot 1] A medium shot.",
            "overall_soundscape": "Quiet room tone.",
            "non_diegetic_music": "N/A",
        },
    }
    job.update(overrides)
    return job


def _take(**overrides):
    take = {
        "frames": 124,
        "fps": 24,
        "duration_sec": 124 / 24,
        "width": 416,
        "height": 736,
        "reference_ids": ["char-a", "loc-a", "kf-s001"],
        "face_token": "same",
        "hand_closeup": False,
        "dialogue": [],
        "speech_sec": 0.0,
        "rendered_as_single_narration": False,
        "visual_defect": None,
        "prompt_sections": _job()["video_prompt"],
        "uses_tail_frame": False,
        "renderer_exit_code": 0,
    }
    take.update(overrides)
    return take


def test_exit_code_zero_does_not_accept_a_failed_take():
    checks = audit_video(_job(), _take(hand_closeup=True, renderer_exit_code=0), {"face_distance_max": 0.35})
    assert report_passed(checks) is False
    assert next_decision(passed=False, take=1, regeneration=0, policy=POLICY) == "retry"


def test_retry_then_regenerate_then_quarantine():
    assert next_decision(passed=False, take=2, regeneration=0, policy=POLICY) == "retry"
    assert next_decision(passed=False, take=3, regeneration=0, policy=POLICY) == "regenerate"
    assert next_decision(passed=True, take=1, regeneration=1, policy=POLICY) == "accept"
    assert next_decision(passed=False, take=3, regeneration=1, policy=POLICY) == "quarantine"


def test_one_bad_shot_does_not_quarantine_the_film_or_stop_the_loop():
    assert film_status(accepted=40, quarantined=1, policy=POLICY) == "completed_with_quarantine"
    assert film_status(accepted=10, quarantined=3, policy=POLICY) == "quarantined"
    assert film_status(accepted=2, quarantined=2, policy=POLICY) == "quarantined"
    seen = []

    def run_one(item):
        if item == "b":
            raise RuntimeError("镜头崩了")
        return {"id": item}

    def on_error(item, exc):
        return {"id": item, "error": str(exc)}

    for row in iter_unattended(["a", "b", "c"], run_one, on_error):
        seen.append(row)
    assert [row["id"] for row in seen] == ["a", "b", "c"]
    assert "镜头崩了" in seen[1]["error"]


def test_resume_interrupt_is_not_swallowed():
    def run_one(_item):
        raise ResumeInterrupt()

    with pytest.raises(ResumeInterrupt):
        list(iter_unattended(["a"], run_one, lambda item, exc: {}))


def test_pronoun_collision_ignores_plural_and_flags_mixed_singular():
    assert has_pronoun_collision("他跟她道别")
    assert not has_pronoun_collision("他们先走")
    assert not has_pronoun_collision("我留下")


def test_tail_frame_across_a_scene_change_fails():
    job = _job(
        continuation={
            "mode": "same_action_extend",
            "from_shot_id": "s001",
            "same_scene": False,
            "same_action_group": False,
        }
    )
    checks = audit_video(job, _take(uses_tail_frame=True), {"face_distance_max": 0.35, "long_line_chars": 40})
    names = {check["name"]: check for check in checks}
    assert names["continuation_scope"]["passed"] is False
    assert report_passed(checks) is False


def test_asset_revision_dirties_dependent_shots_only():
    library = AssetLibrary("story")
    library.add(
        asset_id="char-linxia-still-daily",
        kind="character_still",
        width=768,
        height=1344,
        latent_node="EmptySD3LatentImage",
        face_token_value=face_token("linxia", 1),
        dependents=["s001", "s002"],
        prompt="人",
        stub=True,
    )
    library.add(
        asset_id="loc-market-still",
        kind="location_still",
        width=768,
        height=1344,
        latent_node="EmptySD3LatentImage",
        face_token_value=None,
        dependents=["s009"],
        prompt="地方",
        stub=True,
    )
    dirty = library.revise("char-linxia-still-daily", "换衣服")
    assert dirty == ["s001", "s002"]
    assert library.get("char-linxia-still-daily")["version"] == 2
    assert library.get("char-linxia-still-daily")["face_token"] != face_token("linxia", 1)
    assert "s009" not in library.dirty_shots
