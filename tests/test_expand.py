import json
import re

from cinecore.adapt import adapt_story, assert_not_verbatim, input_kind
from cinecore.config import load_config
from cinecore.constants import PROMPT_SECTIONS
from cinecore.duration import is_usable_shot
from cinecore.im import handle_message, parse_message
from cinecore.prompts import QWEN_SUFFIX
from cinecore.schema_util import project_root, validate

ROOT = project_root()
DURATION = load_config(ROOT / "config.example.yaml")["duration"]


def _item(story_id: str, title: str, source_type: str, raw_text: str) -> dict:
    return {
        "id": story_id,
        "title": title,
        "source_type": source_type,
        "raw_text": raw_text,
        "tags": ["im"],
        "rights_notes": "测试用虚构或改写文本。",
    }


def _prose(screenplay: dict) -> str:
    parts = [screenplay["logline"]]
    for scene in screenplay["scenes"]:
        parts.append(scene["heading"])
        parts.append(scene["action"])
        parts.extend(line["line"] for line in scene["dialogue"])
    return "\n".join(parts)


def test_keywords_expand_into_a_five_minute_board():
    item = _item("im-keywords", "雨夜纸船", "touching", "雨夜 纸船")
    assert input_kind(item["raw_text"]) == "keywords"
    screenplay, bible, storyboard = adapt_story(item, DURATION)
    validate(screenplay, "screenplay.schema.json")
    validate(bible, "bible.schema.json")
    validate(storyboard, "storyboard.schema.json")
    assert screenplay["expansion"]["input_kind"] == "keywords"
    assert screenplay["expansion"]["target_seconds"] == 300
    assert any("过程" in gap for gap in screenplay["expansion"]["gaps"])
    assert [scene["heading"].split()[0] for scene in screenplay["scenes"]] == [
        "开场",
        "触发",
        "发展",
        "转折",
        "收束",
    ]
    assert 270 <= storyboard["planned_duration_sec"] <= 330
    assert all(is_usable_shot(shot["frame_count"]) for shot in storyboard["shots"])
    shot = storyboard["shots"][0]
    assert list(shot["video_prompt"]) == list(PROMPT_SECTIONS)
    assert shot["video_prompt"]["summary"].startswith("[")
    assert shot["first_frame"]["prompt"].endswith(f"{QWEN_SUFFIX}。")
    assert "--ar" not in shot["first_frame"]["prompt"]
    assert parse_message("跑 雨夜 纸船").kind == "run_new"


def test_paragraph_expands_without_copying_eighteen_characters():
    raw = "孙女在雨天把旧报纸折成小船，放进厨房的水盆，听老人在旁边慢慢说话。"
    item = _item("im-paragraph", "雨天小船", "touching", raw)
    assert input_kind(raw) == "paragraph"
    screenplay, bible, storyboard = adapt_story(item, DURATION)
    assert_not_verbatim(item, screenplay, bible, storyboard, window=18)
    assert len(screenplay["scenes"]) == 5
    assert 270 <= storyboard["planned_duration_sec"] <= 330
    spoken = next(shot for shot in storyboard["shots"] if shot["dialogue"])
    assert "<d>[Chinese]" in spoken["video_prompt"]["detailed_description"]
    assert "Before speaking" in spoken["video_prompt"]["detailed_description"]


def test_news_expansion_does_not_invent_a_real_outcome():
    raw = "昨晚有市民反映小区停电，具体户数和原因都没有写明。"
    item = _item("im-outage", "小区停电", "news", raw)
    screenplay, _bible, storyboard = adapt_story(item, DURATION)
    prose = _prose(screenplay)
    assert screenplay["expansion"]["fictionalized"] is True
    gaps = " ".join(screenplay["expansion"]["gaps"])
    assert "数字" in gaps
    assert "结果" in gaps
    assert "虚构" in gaps
    for invented in ("恢复", "逮捕", "死亡", "教育局", "供电公司", "三人"):
        assert invented not in prose
    assert not re.search(r"\d", prose)
    assert "虚构人物" in screenplay["logline"]
    assert "一个夜里" in screenplay["logline"]
    assert "小区停电" not in prose
    assert all(scene["heading"].split()[1] != "日" for scene in screenplay["scenes"])
    assert storyboard["shots"][0]["video_prompt"]["non_diegetic_music"] == "N/A"
    dumped = json.dumps(screenplay["expansion"], ensure_ascii=False)
    assert "不补写输入里没有的真实姓名" in dumped


def test_keyword_and_news_messages_enter_the_existing_chain(tmp_path):
    from pathlib import Path

    config_path = tmp_path / "expand.yaml"
    config_path.write_text(
        "paths:\n"
        f"  corpus: {ROOT / 'samples' / 'corpus'}\n"
        f"  runs: {tmp_path / 'runs'}\n"
        f"  ledger: {tmp_path / 'ledger.json'}\n"
        "im:\n"
        f"  inbox: {tmp_path / 'inbox'}\n"
        "dry_run:\n"
        "  inject: []\n",
        encoding="utf-8",
    )
    config = load_config(config_path)
    keyword_reply = handle_message("跑 雨夜 纸船", config)
    assert "缺口" in keyword_reply
    assert "计划" in keyword_reply
    assert "[summary]" in keyword_reply
    assert "不是成片" in keyword_reply
    news = (
        "跑\n"
        "标题：小区停电\n"
        "体裁：新闻\n"
        "正文：\n"
        "昨晚有市民反映小区停电，具体户数和原因都没有写明。\n"
    )
    news_reply = handle_message(news, config)
    assert "缺口" in news_reply
    assert "恢复" not in news_reply.split("第 1 镜")[0]
    runs = sorted((tmp_path / "runs").glob("run-*"))
    assert len(runs) == 2
    for run_dir in runs:
        log = json.loads((run_dir / "run_log.json").read_text(encoding="utf-8"))
        assert log["dry_run"] is True
        assert log["status"] == "completed"
        story = log["stories"][0]
        assert story["status"] in {"completed", "completed_with_quarantine"}
        board = json.loads(
            (run_dir / "stories" / story["story_id"] / "storyboard.json").read_text(encoding="utf-8")
        )
        assert 270 <= board["planned_duration_sec"] <= 330
        assert board["shots"][0]["first_frame"]["prompt"].endswith(f"{QWEN_SUFFIX}。")
        assert list(board["shots"][0]["video_prompt"]) == list(PROMPT_SECTIONS)
