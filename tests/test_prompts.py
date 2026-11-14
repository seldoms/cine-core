import re

from cinecore.constants import PROMPT_SECTIONS
from cinecore.prompts import (
    QWEN_SUFFIX,
    build_video_prompt,
    compile_h3_from_colloquial,
    compile_qwen_still,
    positive_clauses,
)


def test_empty_place_is_a_positive_qwen_still():
    from cinecore.prompts import derive_look

    look = derive_look("interesting", "夜里的窄巷")
    prompt = compile_qwen_still("一条夜里的窄巷，石板和一枚铁钩", kind="location", look=look)
    assert "无人的街道" in prompt
    assert prompt.endswith(f"{QWEN_SUFFIX}。")
    assert "--ar" not in prompt
    assert not re.search(r"[不没别勿]|禁止", prompt)
    assert look["palette"] in prompt


def test_fight_becomes_one_causal_beat_inside_h3():
    sections = compile_h3_from_colloquial("他挥拳打来，她侧身躲开，尘土扬起", seconds=5.17)
    detail = sections["detailed_description"]
    assert list(sections) == list(PROMPT_SECTIONS)
    assert "incoming force arrives first" in detail
    assert "One strike then travels" in detail
    assert "Dust lifts" in detail
    assert "0:00" not in detail
    assert "0：00" not in detail
    assert "<Picture 2>" not in sections["subject_definitions"]
    assert sections["non_diegetic_music"] == "N/A"


def test_performance_is_visible_before_during_and_after_the_line():
    sections = compile_h3_from_colloquial("她压低声音说「到此为止」。", seconds=5.17)
    detail = sections["detailed_description"]
    assert "Before speaking" in detail
    assert "While speaking" in detail
    assert "After speaking" in detail
    assert "<d>[Chinese] 到此为止</d>" in detail
    assert "beside the lens" in detail
    assert "looking at the camera" not in detail.lower()
    assert "angry" not in detail.lower()
    assert "sad" not in detail.lower()


def test_story_assets_use_the_same_compiler():
    from cinecore.adapt import adapt_story
    from cinecore.config import load_config
    from cinecore.corpus import FilesystemCorpus
    from cinecore.schema_util import project_root

    root = project_root()
    item = FilesystemCorpus(root / "samples" / "corpus").get("sample-last-lantern")
    _screenplay, bible, storyboard = adapt_story(item, load_config(root / "config.example.yaml")["duration"])
    assert bible["look"]["palette"]
    assert bible["props"][0]["id"] == "lamp"
    assert bible["props"][0]["still_prompt"].endswith(f"{QWEN_SUFFIX}。")
    assert "--ar" not in bible["props"][0]["still_prompt"]
    spoken = next(shot for shot in storyboard["shots"] if shot["dialogue"])
    detail = spoken["video_prompt"]["detailed_description"]
    assert "Before speaking" in detail
    assert "<d>[Chinese]" in detail
    assert spoken["video_prompt"]["summary"].startswith("[")


def test_qwen_still_expands_colloquial_speech_without_negation():
    prompt = compile_qwen_still("不要路人，雨里发呆的短发女人", kind="photo")
    assert prompt.endswith(f"{QWEN_SUFFIX}。")
    assert "\n" not in prompt
    assert not re.search(r"[不没别勿]|禁止", prompt)
    assert "路人" not in prompt
    assert "雨里发呆的短发女人" in prompt
    assert "纪实摄影风格" in prompt
    assert "768" not in prompt
    assert "9:16" not in prompt


def test_h3_colloquial_line_becomes_six_english_sections():
    sections = compile_h3_from_colloquial("她把灯挂上铁钩，看了一眼，说「到此为止」。", seconds=5.17)
    assert list(sections) == list(PROMPT_SECTIONS)
    assert sections["summary"].startswith("[keyframe completion + reference generation + audio reference]")
    assert "<Picture 1>" in sections["subject_definitions"]
    assert "first frame of [Shot 1]" in sections["subject_definitions"]
    assert "<Picture 2>" not in sections["subject_definitions"]
    assert "<Subject 2>" in sections["subject_definitions"]
    assert "[Shot 1]" in sections["detailed_description"]
    assert "<d>[Chinese] 到此为止</d>" in sections["detailed_description"]
    assert "(S1)" not in sections["retention_analysis"]
    assert "reference" in sections["retention_analysis"]
    assert "fully_copy" not in sections["retention_analysis"]
    assert sections["non_diegetic_music"] == "N/A"
    assert "cinematic" not in sections["detailed_description"].lower()
    assert "beautiful" not in sections["detailed_description"].lower()


def test_same_action_extension_uses_video_continuation():
    shot = {
        "action": "顺着上一镜再停留一会儿",
        "camera": {"framing": "中景"},
        "duration_sec": 5.16667,
        "dialogue": [],
        "continuation": {"mode": "same_action_extend", "from_shot_id": "s001"},
    }
    context = {
        "character_name": "陈舟",
        "character_description": "短发，瘦高，深色素面外套",
        "location_name": "窄巷",
        "location_description": "石板路和一枚铁钩",
        "wardrobe_description": "深色薄外套",
        "time_of_day": "night",
    }
    sections = build_video_prompt(shot, context)
    assert sections["summary"].startswith("[video continuation + keyframe completion + reference generation]")
    assert "<Video 1>" in sections["subject_definitions"]
    assert "one practical lamp" in sections["detailed_description"]


def test_positive_clauses_drop_only_the_negative_piece():
    assert positive_clauses("短发，没有花纹，面容平静") == "短发，面容平静"
