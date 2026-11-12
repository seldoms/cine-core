import json
from pathlib import Path

from cinecore.cli import main
from cinecore.config import load_config
from cinecore.errors import CineError
from cinecore.pipeline import Pipeline
from cinecore.schema_util import project_root
import pytest

ROOT = project_root()
CORPUS = ROOT / "samples" / "corpus"


def _short_config(tmp_path: Path) -> Path:
    path = tmp_path / "short.yaml"
    path.write_text(
        "duration:\n"
        "  target_seconds: 48\n"
        "  min_seconds: 40\n"
        "  max_seconds: 60\n"
        "dry_run:\n"
        "  inject: []\n",
        encoding="utf-8",
    )
    return path


def test_crash_resume_finishes_the_same_story_without_repeating_shots(tmp_path: Path):
    config_path = _short_config(tmp_path)
    config = load_config(config_path)
    run_dir = tmp_path / "runs" / "run-resume"
    first = Pipeline(
        config,
        source=CORPUS,
        run_dir=run_dir,
        ledger_path=tmp_path / "ledger.json",
        dry_run=True,
        limit=1,
        run_id="run-resume",
        stop_after_completed_shots=2,
    )
    assert first.run()["status"] == "running"
    progress_path = run_dir / "stories" / "sample-last-lantern" / "progress.json"
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    assert progress["finished_shot_ids"] == ["s001", "s002"]

    assert (
        main(
            [
                "run",
                "--resume",
                str(run_dir),
                "--dry-run",
                "--config",
                str(config_path),
                "--source",
                str(CORPUS),
                "--ledger",
                str(tmp_path / "ledger.json"),
                "--limit",
                "1",
            ]
        )
        == 0
    )
    log = json.loads((run_dir / "run_log.json").read_text(encoding="utf-8"))
    assert log["status"] == "completed"
    assert [item["story_id"] for item in log["stories"]] == ["sample-last-lantern"]
    assert log["stories"][0]["quarantined_shots"] == 0
    board = json.loads((run_dir / "stories" / "sample-last-lantern" / "storyboard.json").read_text(encoding="utf-8"))
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    assert progress["finished_shot_ids"] == [shot["shot_id"] for shot in board["shots"]]
    assert len(progress["finished_shot_ids"]) == len(set(progress["finished_shot_ids"]))
    assert (run_dir / "stories" / "sample-last-lantern" / "assemble" / "film_sidecar.json").is_file()


def test_supervised_dry_run_records_pauses_and_continues(tmp_path: Path):
    config = load_config(_short_config(tmp_path))
    run_dir = tmp_path / "runs" / "run-super"
    result = Pipeline(
        config,
        source=CORPUS,
        run_dir=run_dir,
        ledger_path=tmp_path / "ledger.json",
        mode="supervised",
        dry_run=True,
        limit=1,
        run_id="run-super",
    ).run()
    assert result["status"] == "completed"
    names = {path.name for path in (run_dir / "pauses").glob("*.json")}
    assert "sample-last-lantern-after_plan_and_keyframes.json" in names
    assert "sample-last-lantern-after_audit.json" in names


def test_live_comfy_batch_is_refused_before_a_run_directory_is_created(tmp_path: Path):
    config = load_config(ROOT / "config.example.yaml")
    config["render"]["backend"] = "comfyui"
    run_dir = tmp_path / "should-not-exist"
    with pytest.raises(CineError):
        Pipeline(
            config,
            source=CORPUS,
            run_dir=run_dir,
            ledger_path=tmp_path / "ledger.json",
            dry_run=False,
        )
    assert not run_dir.exists()
