import json
from pathlib import Path

from cinecore.cli import main
from cinecore.duration import is_usable_shot
from cinecore.schema_util import project_root, validate
from cinecore.workflows import assert_still_contract

ROOT = project_root()


def _run(tmp_path: Path, run_id: str, *, requeue: bool = False) -> Path:
    args = [
        "run",
        "--mode",
        "unattended",
        "--dry-run",
        "--config",
        str(ROOT / "config.example.yaml"),
        "--source",
        str(ROOT / "samples" / "corpus"),
        "--out",
        str(tmp_path / "runs"),
        "--ledger",
        str(tmp_path / "ledger.json"),
        "--run-id",
        run_id,
    ]
    if requeue:
        args.append("--requeue")
    assert main(args) == 0
    return tmp_path / "runs" / run_id


def test_unattended_dry_run_walks_every_stage_and_keeps_going(tmp_path: Path):
    run_dir = _run(tmp_path, "run-sample")
    log = json.loads((run_dir / "run_log.json").read_text(encoding="utf-8"))
    validate(log, "run_log.schema.json")
    assert log["status"] == "completed"
    assert log["dry_run"] is True
    assert log["mode"] == "unattended"
    assert [item["story_id"] for item in log["stories"]] == [
        "sample-last-lantern",
        "sample-market-rain",
        "sample-paper-boat",
    ]
    by_story = {item["story_id"]: item for item in log["stories"]}
    assert by_story["sample-last-lantern"]["status"] == "completed"
    assert by_story["sample-market-rain"]["status"] == "completed_with_quarantine"
    assert by_story["sample-paper-boat"]["status"] == "completed"
    assert by_story["sample-paper-boat"]["quarantined_shots"] == 0

    for story_id in by_story:
        story = run_dir / "stories" / story_id
        for name, schema in (
            ("screenplay.json", "screenplay.schema.json"),
            ("bible.json", "bible.schema.json"),
            ("storyboard.json", "storyboard.schema.json"),
        ):
            validate(json.loads((story / name).read_text(encoding="utf-8")), schema)
        assert (story / "library.json").is_file()
        assert (story / "assemble" / "concat_plan.json").is_file()
        assert (story / "assemble" / "subtitles.srt").is_file()
        sidecar = json.loads((story / "assemble" / "film_sidecar.json").read_text(encoding="utf-8"))
        assert sidecar["stub"] is True
        assert sidecar["native_video"]["width"] == 416
        assert sidecar["upscale_to_720p"]["applied"] is False
        assert sidecar["loudness"]["applied"] is False
        board = json.loads((story / "storyboard.json").read_text(encoding="utf-8"))
        assert all(is_usable_shot(shot["frame_count"]) for shot in board["shots"])

    market = run_dir / "stories" / "sample-market-rain"
    quarantine = json.loads((market / "quarantine" / "s004.json").read_text(encoding="utf-8"))
    assert quarantine["shot_id"] == "s004"
    audits = sorted((market / "shots" / "s004" / "takes").glob("*/audit.json"))
    assert len(audits) == 6
    decisions = [json.loads(path.read_text(encoding="utf-8"))["decision"] for path in audits]
    assert decisions == ["retry", "retry", "regenerate", "retry", "retry", "quarantine"]
    for path in audits:
        report = json.loads(path.read_text(encoding="utf-8"))
        validate(report, "audit_report.schema.json")
        assert report["renderer_exit_code"] == 0
        assert report["renderer_exit_ignored"] is True
        assert report["passed"] is False
    clean = list((run_dir / "stories" / "sample-last-lantern" / "shots" / "s001" / "takes").glob("r*_t*"))
    assert len(clean) == 1
    assert not list((run_dir / "stories" / "sample-last-lantern" / "quarantine").glob("*.json"))

    concat = json.loads((market / "assemble" / "concat_plan.json").read_text(encoding="utf-8"))
    assert all(segment["shot_id"] != "s004" for segment in concat["segments"])
    sidecar = json.loads((market / "assemble" / "film_sidecar.json").read_text(encoding="utf-8"))
    assert sidecar["accepted_duration_sec"] < sidecar["planned_duration_sec"]
    assert (market / "assemble" / "subtitles.srt").read_text(encoding="utf-8").strip()

    job = json.loads((market / "shots" / "s001" / "job.json").read_text(encoding="utf-8"))
    validate(job, "shot_job.schema.json")
    assert_still_contract(job["workflow"]["keyframe"])
    video = job["workflow"]["video"]
    sampler = next(node for node in video.values() if node["class_type"] == "H3Ref2VASampler")
    assert sampler["inputs"]["width"] == 416
    assert sampler["inputs"]["height"] == 736
    assert is_usable_shot(sampler["inputs"]["frames"])
    assert len(job["references"]) == 2
    assert job["first_frame"]["approved"] is True

    assert main(["status", "--run-dir", str(run_dir)]) == 0
    assert main(["audit", "--run-dir", str(run_dir)]) == 0
    summary = json.loads((run_dir / "audit_summary.json").read_text(encoding="utf-8"))
    assert summary["command_exit_0_is_not_acceptance"] is True
    assert summary["failed"] >= 6
    assert summary["quarantine_files"] >= 1

    again = _run(tmp_path, "run-empty")
    empty = json.loads((again / "run_log.json").read_text(encoding="utf-8"))
    assert empty["stories"] == []
    assert empty["status"] == "completed"
