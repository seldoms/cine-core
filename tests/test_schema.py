import json
from pathlib import Path

import pytest

from cinecore.adapt import adapt_story, assert_not_verbatim
from cinecore.cli import main
from cinecore.config import load_config
from cinecore.corpus import FilesystemCorpus
from cinecore.schema_util import project_root, validate
from cinecore.errors import CineError

ROOT = project_root()
CORPUS = ROOT / "samples" / "corpus"


def test_sample_items_match_the_schema_and_are_original_fiction():
    items = FilesystemCorpus(CORPUS).list_items()
    assert [item["id"] for item in items] == [
        "sample-last-lantern",
        "sample-market-rain",
        "sample-paper-boat",
    ]
    assert {item["source_type"] for item in items} == {"interesting", "news", "touching"}
    for item in items:
        assert "虚构" in item["raw_text"]
        assert "八公里" not in item["raw_text"]
        assert "澄香" not in item["raw_text"]


def test_invalid_story_is_rejected():
    with pytest.raises(CineError):
        validate({"id": "missing", "title": "无", "source_type": "news"}, "story_item.schema.json")


def test_rule_adapter_hits_five_minutes_without_copying_the_source():
    config = load_config(ROOT / "config.example.yaml")
    for item in FilesystemCorpus(CORPUS).list_items():
        screenplay, bible, storyboard = adapt_story(item, config["duration"], backend="rule")
        validate(screenplay, "screenplay.schema.json")
        validate(bible, "bible.schema.json")
        validate(storyboard, "storyboard.schema.json")
        assert_not_verbatim(item, screenplay, bible, storyboard, window=18)
        assert 270 <= storyboard["planned_duration_sec"] <= 330
        assert screenplay["adaptation_note"]
        assert "不逐字" in screenplay["adaptation_note"]
        on_screen = [character for character in bible["characters"] if character["on_screen"]]
        off_screen = [character["id"] for character in bible["characters"] if not character["on_screen"]]
        assert len(on_screen) == 1
        by_id = {shot["shot_id"]: shot for shot in storyboard["shots"]}
        for shot in storyboard["shots"]:
            assert shot["subject_ids"] == [on_screen[0]["id"]]
            assert shot["dialogue"] == [] or len(shot["dialogue"]) == 1
            assert shot["video_prompt"]["summary"].startswith("[")
            assert "keyframe completion" in shot["video_prompt"]["summary"]
            refs = " ".join(ref["ref_id"] for ref in shot["reference_plan"])
            assert all(character_id not in refs for character_id in off_screen)
            if shot["continuation"]:
                previous = by_id[shot["continuation"]["from_shot_id"]]
                assert previous["scene_id"] == shot["scene_id"]
                assert previous["action_group"] == shot["action_group"]
                assert previous["camera"]["framing"] == shot["camera"]["framing"]
        continued = [shot for shot in storyboard["shots"] if shot["continuation"]]
        assert continued
        assert len(continued) < len(storyboard["shots"]) / 2


def test_ingest_roundtrip_and_plan_command(tmp_path: Path):
    dest = tmp_path / "corpus.sqlite"
    out = tmp_path / "plan"
    assert main(["ingest", "--source", str(CORPUS), "--dest", str(dest)]) == 0
    assert main(
        [
            "plan",
            "--story",
            "sample-paper-boat",
            "--source",
            str(dest),
            "--out",
            str(out),
            "--config",
            str(ROOT / "config.example.yaml"),
        ]
    ) == 0
    bible = json.loads((out / "bible.json").read_text(encoding="utf-8"))
    assert any(character["id"] == "grandfather" and character["on_screen"] is False for character in bible["characters"])


def test_repo_does_not_commit_a_private_host_or_secret_env():
    import re

    pattern = re.compile(r"192\.168\.\d+\.\d+")
    allowed_suffixes = {".py", ".md", ".yaml", ".yml", ".json", ".toml", ".txt"}
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {".git", ".venv", "runs", "__pycache__", ".pytest_cache"} for part in path.parts):
            continue
        if path.suffix not in allowed_suffixes and path.name not in {".env.example", ".gitignore"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert pattern.search(text) is None, path
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "CINE_COMFYUI_HOST=" in example
    assert "sk-" not in example
