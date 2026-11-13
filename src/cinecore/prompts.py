"""把口水话收成两家官方要求的提示词。

静帧按 Qwen-Image 官方改写器（prompt_utils.py 的中文规则）：
补全主体、风格、空间关系和景别；画面里要写的字保留原样并用引号标出位置；
改写结果里不出现否定词；收成一段；末尾加上官方后缀「超清，4K，电影级构图」。
像素和宽高比交给 latent，不写进提示词。

视频按 MiniMax-H3 的 Ref2VA 指南（h3-prompt-writing / ref-en.txt）：
六段、英文、对白保留中文；标签含义全文一致；
只定义角色的图写进 Subject，不单独成 Picture；
首帧才是独立的 Picture，并 fully_preserved；
summary 用方括号任务类型，且不新起标签；
detailed_description 在 [Shot 1] 之前先写风格，镜头内写构图、主体、环境、动作、运镜和出声点。
"""

from __future__ import annotations

import re

from cinecore.constants import PROMPT_SECTIONS
from cinecore.duration import seconds_for_frames, snap_frames

_NEGATION = re.compile(r"[不没别勿]|禁止")
_QUOTE = re.compile(r"「([^」]+)」|\"([^\"]+)\"")
QWEN_SUFFIX = "超清，4K，电影级构图"

_FRAMING_EN = {
    "远景": "wide shot",
    "中景": "medium shot",
    "近景": "close shot",
}
_FRAMING_ZH = {
    "远景": "全身处于画面中部，四周留出环境",
    "中景": "腰部以上的中景，人物处于画面中部",
    "近景": "肩部以上的近景，脸处于焦点",
}
_CAMERA_EN = {
    "远景": "The camera holds a wide view and drifts slowly sideways.",
    "中景": "The camera holds a medium shot and eases forward a short distance.",
    "近景": "The camera stays on the face and shoulders with a very small drift.",
}
_LIGHT = {
    "night": "one practical lamp and deep shadow",
    "dusk": "low warm sun and long shadow",
    "day": "soft daylight from one side",
    "rain-day": "flat overcast light and wet surfaces",
}


def positive_clauses(text: str) -> str:
    """丢掉含否定词的分句。Qwen 官方要求改写后的提示里不要出现否定词。"""
    parts = re.split(r"[，。！？；、\n]+", text or "")
    kept = [part.strip() for part in parts if part.strip() and not _NEGATION.search(part)]
    return "，".join(kept)


def _one_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _framing_of(text: str, default: str = "中景") -> str:
    for name in ("近景", "远景", "中景"):
        if name in (text or ""):
            return name
    return default


def derive_look(source_type: str, text: str = "") -> dict[str, str]:
    """分镜前先定一套全片共用的影调和色彩。三档是本仓库自己的对应，不是照搬示例文案。"""
    blob = text or ""
    if any(word in blob for word in ("夜", "巷", "灯")) and source_type == "interesting":
        key = "interesting"
    else:
        key = source_type if source_type in {"news", "touching", "interesting"} else "interesting"
    table = {
        "news": {
            "tone": "纪实摄影，日光平实",
            "light": "一侧的散射日光",
            "palette": "灰绿和暗赭",
            "light_en": "flat daylight from one side",
            "palette_en": "muted gray-green and dull ochre",
        },
        "touching": {
            "tone": "室内写实，安静",
            "light": "窗边的柔光",
            "palette": "暖木色和浅灰",
            "light_en": "soft window light",
            "palette_en": "warm wood and pale gray",
        },
        "interesting": {
            "tone": "夜街写实",
            "light": "一盏实用灯和深影",
            "palette": "蓝黑底色和一小块暖光",
            "light_en": "one practical lamp and deep shadow",
            "palette_en": "blue-black with a small warm lamp",
        },
    }
    return table[key]


def infer_still_kind(text: str) -> str:
    person = any(word in text for word in ("女", "男", "她", "他", "孩", "人"))
    if any(word in text for word in ("道具", "灯", "船", "筐")) and not person:
        return "prop"
    if any(word in text for word in ("街", "巷", "房", "室", "厨", "空镜", "场地")) and not person:
        return "location"
    return "photo"


def _empty_place(text: str) -> str:
    if any(word in text for word in ("房", "室", "厨", "桌")):
        return "空着的房间"
    return "无人的街道"


def compile_qwen_still(text: str, *, kind: str = "photo", look: dict | None = None) -> str:
    """一句口水话，或一段已有描述，收成一条 Qwen-Image 静帧提示。

    角色、空镜、道具先按资产圣经的方法组织（全身站立、空场景、单独物件、全片同一套色彩），
    再收成 Qwen 要求的一段肯定句。不写 `--ar`，不写像素。
    """
    kept = positive_clauses(text)
    if not kept:
        kept = "一位成年人站在真实环境里，神情自然，衣服素净"
    tone = (look or {}).get("tone") or "纪实摄影风格"
    light = (look or {}).get("light") or "柔和的侧光"
    palette = (look or {}).get("palette") or "压暗的自然色"
    framing = _framing_of(text)
    if kind == "location":
        body = (
            f"{tone}，{palette}，{light}。{kept}。"
            f"这是{_empty_place(text)}，广角能看见地面和墙面的全貌，材质清楚。"
        )
    elif kind == "prop":
        body = (
            f"{tone}，{palette}，{light}。单独陈列的物件：{kept}。"
            "正面摆放，背景是均匀的浅灰，边缘有细微的使用痕迹。"
        )
    elif kind == "keyframe":
        body = (
            f"{tone}，{palette}，{light}。{_FRAMING_ZH[framing]}。{kept}。"
            "双手垂在身侧，脸和身体是焦点，环境留在身后。"
        )
    else:
        body = (
            f"{tone}，{palette}，{light}。{kept}。"
            "全身站立，正面朝向，表情平稳，双臂垂在身侧。背景是均匀的浅灰墙。"
        )
    prompt = _one_line(f"{body}{QWEN_SUFFIX}。")
    if "--ar" in prompt or _NEGATION.search(prompt):
        raise ValueError("Qwen 静帧提示含有否定词或 Midjourney 画幅后缀")
    return prompt


def still_prompt(*, kind: str, text: str, look: dict | None = None) -> str:
    if "道具" in kind:
        mode = "prop"
    elif "场景" in kind:
        mode = "location"
    elif "首帧" in kind:
        mode = "keyframe"
    else:
        mode = "character"
    return compile_qwen_still(text, kind=mode, look=look)


def _task_prefix(shot: dict) -> str:
    kinds: list[str] = []
    if shot.get("continuation"):
        kinds.append("video continuation")
    kinds.extend(["keyframe completion", "reference generation"])
    if shot.get("dialogue"):
        kinds.append("audio reference")
    return "[" + " + ".join(kinds) + "]"


_PHRASES = (
    ("三十岁上下", "around thirty"),
    ("四十岁上下", "around forty"),
    ("十一二岁", "about twelve"),
    ("齐耳短发", "a bob"),
    ("低马尾", "a low ponytail"),
    ("短发", "short hair"),
    ("瘦高", "a slim tall build"),
    ("面容平静", "a calm face"),
    ("深色素面外套", "a plain dark coat"),
    ("旧工装外套", "a worn work jacket"),
    ("眼神清醒", "a clear gaze"),
    ("耳际干净", "bare ears"),
    ("浅灰色雨衣", "a light gray raincoat"),
    ("素颜", "an unmade face"),
    ("素色外套", "a plain coat"),
    ("颜色压暗", "muted cloth"),
    ("布面干净", "clean cloth"),
    ("深色薄外套", "a thin dark overcoat"),
    ("露出眼睛", "eyes visible under a raised brim"),
    ("窄巷", "a narrow stone alley"),
    ("铁钩", "a metal hook"),
    ("台阶", "stone steps"),
    ("东市棚架", "a market shed"),
    ("菜摊", "a vegetable stall"),
    ("地面潮湿", "a damp floor"),
    ("旧楼厨房", "an old kitchen"),
    ("木桌", "a wooden table"),
    ("浅水盆", "a shallow basin of water"),
    ("窗外有雨", "rain beyond the window"),
    ("普通房间", "an ordinary room"),
    ("一扇窗", "a window"),
)


_FIGHT_WORDS = ("挥拳", "对打", "格斗", "搏斗", "出手", "打来", "一拳", "踢", "砍")


def is_fight(text: str) -> bool:
    return any(word in (text or "") for word in _FIGHT_WORDS)


def fight_sentence(seconds: float) -> str:
    """打戏只留一组主动作，写成因果链，并给出一处环境反馈。时长仍是这一镜的网格秒数。"""
    return (
        f"Across this single {seconds:.2f}-second shot, one causal chain plays out. "
        "An incoming force arrives first. <Subject 1> turns with that momentum. "
        "One strike then travels from the raised forearm along a short downward path. "
        "Dust lifts off the floor where the strike lands. "
        "The face, hair, and clothes stay the same as defined for <Subject 1>."
    )


def performance_sentence(line: str) -> str:
    """把一句对白写成说话前、说话中、说话后能看见的动作。默认目光避开镜头。"""
    if "！" in line or "!" in line:
        during = "the voice stays low and the jaw stays level"
        after = "the gaze holds on a point beside the lens"
    elif "？" in line or "?" in line:
        during = "the voice stays quiet and the pace stays even"
        after = "one breath leaves through the nose"
    else:
        during = "the voice stays quiet and level"
        after = "the shoulders drop a little and the eyes move aside"
    return (
        "Before speaking, the gaze rests on a point beside the lens and the breath holds once. "
        f"While speaking, {during}, <d>[Chinese] {line}</d> "
        f"After speaking, {after}."
    )


def gloss_action(text: str) -> str:
    visual = _QUOTE.sub("", text or "")
    bits: list[str] = []

    def add(flag: bool, phrase: str) -> None:
        if flag and phrase not in bits:
            bits.append(phrase)

    add(("灯" in visual or "挂" in visual) and "钩" in visual, "hangs a lamp on a metal hook")
    add("灯" in visual and "钩" not in visual, "stays beside a lamp")
    add("雨" in visual, "stands in visible rain")
    add(any(word in visual for word in ("船", "舟", "纸")), "sets a small paper boat on water")
    add(any(word in visual for word in ("摊", "筐", "菜")), "packs a stall and lifts an empty basket")
    add("坐" in visual, "sits")
    add("看" in visual, "looks once to the side")
    add(any(word in visual for word in ("走", "离开", "骑")), "moves away")
    add("折" in visual, "folds a small object with both hands kept low")
    if not bits:
        return "completes one clear physical action and then holds the final pose"
    return " and ".join(bits)


def visual_english(text: str, fallback: str) -> str:
    found = [english for chinese, english in _PHRASES if chinese in (text or "")]
    action = gloss_action(text)
    generic = "completes one clear physical action and then holds the final pose"
    parts = found + ([action] if action != generic else [])
    unique: list[str] = []
    for part in parts:
        if part not in unique:
            unique.append(part)
    return ", ".join(unique) if unique else fallback


def _quoted_line(shot: dict) -> str:
    dialogue = shot.get("dialogue") or []
    if not dialogue:
        return ""
    return str(dialogue[0].get("line") or "").strip()


def build_video_prompt(shot: dict, context: dict) -> dict[str, str]:
    """按 H3 Ref2VA 六段写这一镜。正文英文，对白保留原话。"""
    name = context["character_name"]
    appearance = visual_english(context.get("character_description", ""), "an adult with a clear face and plain clothes")
    place = visual_english(context["location_name"], "the setting of this shot")
    place_detail = visual_english(
        context.get("location_description", ""),
        "a real floor, one wall or open side, and a single light source",
    )
    wardrobe = visual_english(context.get("wardrobe_description", ""), "plain everyday clothes")
    action = gloss_action(shot.get("action", ""))
    framing = _framing_of(shot.get("camera", {}).get("framing", ""), "中景")
    framing_en = _FRAMING_EN[framing]
    seconds = float(shot.get("duration_sec") or 5.17)
    look = context.get("look") or {}
    light = look.get("light_en") or _LIGHT.get(str(context.get("time_of_day", "")), "soft side light")
    line = _quoted_line(shot)
    has_tail = bool(shot.get("continuation"))
    fight = is_fight(shot.get("action", ""))
    if fight:
        action = fight_sentence(seconds)
        camera_line = "The camera stays close and shakes once as the dust lifts, then holds."
    else:
        action = gloss_action(shot.get("action", ""))
        camera_line = _CAMERA_EN[framing]

    definitions = [
        (
            f"<Subject 1> is {name}. Appearance and wardrobe come from the character still: "
            f"{appearance}. Clothes in this shot: {wardrobe}."
        ),
        (
            f"<Subject 2> is the place {_label(place)}. Materials and light come from the location still: "
            f"{place_detail}. The still guides the place, and the camera follows this prompt."
        ),
        f"<Picture 1> is the first frame of [Shot 1], the approved keyframe of {_label(name)} in {_label(place)}.",
    ]
    retention = [
        (
            f"<Subject 1> (appears in [Shot 1]): fully_preserved - {name}'s face, hair, and "
            f"this set of clothes stay as defined."
        ),
        (
            "<Subject 2> (appears in [Shot 1]): fully_preserved - the place's materials and light "
            "stay as defined, while the camera position follows this shot."
        ),
        "<Picture 1> ([Shot 1] first frame): fully_preserved - the video opens on this exact frame.",
    ]
    if has_tail:
        definitions.append(
            "<Video 1> is the previous clip of this same action. The new clip extends that motion."
        )
        retention.append(
            "<Video 1> (same-action extension): partially_preserved - only the ongoing action continues."
        )
    if line:
        definitions.append(
            f"<Audio 1> is the Mandarin line spoken by <Subject 1> (S1). It is a new line, referenced for delivery."
        )
        retention.append(
            "<Audio 1>: reference - the single Mandarin line in [Shot 1] is performed, without copying an existing recording."
        )

    summary = (
        f"{_task_prefix(shot)} The target video is one {framing_en} lasting {seconds:.2f} seconds. "
        f"It begins from <Picture 1>. <Subject 1> carries out one action inside <Subject 2>."
    )
    if has_tail:
        summary += " The motion extends <Video 1>."
    if line:
        summary += " <Audio 1> guides the spoken delivery."

    if line:
        spoken = " " + performance_sentence(line)
    else:
        spoken = " No one speaks. Room tone is the only sound inside the shot."

    tail = " The motion continues from the end of <Video 1>." if has_tail else ""
    detail = (
        f"The image is realistic and photographic, with {light}, muted color, and concrete textures. "
        f"[Shot 1] The shot begins from <Picture 1> and runs {seconds:.2f} seconds without a cut. "
        f"It is a {framing_en}. <Subject 1>, {name}, {appearance}, wearing {wardrobe}, "
        f"{_position(framing)} Both hands hang beside the thighs, and the face stays in focus. "
        f"<Subject 2> fills the background: {place}, {place_detail}, under {light}. "
        f"{action if fight else '<Subject 1> ' + action + '.'}{tail} {camera_line}{spoken}"
    )
    if fight:
        sound = (
            f"A dull impact and a short scrape of grit carry the {seconds:.2f} seconds. "
            "The score stays silent."
        )
    else:
        sound = (
            f"Room tone of {place} and the physical sound of {light} continue for the whole {seconds:.2f} seconds."
        )
    sections = {
        "subject_definitions": "\n".join(definitions),
        "summary": summary,
        "retention_analysis": "\n".join(retention),
        "detailed_description": detail,
        "overall_soundscape": sound,
        "non_diegetic_music": "N/A",
    }
    _assert_h3(sections, has_line=bool(line), has_tail=has_tail)
    return sections


def _label(text: str) -> str:
    if re.search(r"[\u4e00-\u9fff]", text or ""):
        return f"「{text}」"
    return text


def _position(framing: str) -> str:
    if framing == "远景":
        return "stands small in the center, with the place visible on every side."
    if framing == "近景":
        return "fills the center from the shoulders upward."
    return "occupies the center from the waist upward."


def _assert_h3(sections: dict[str, str], *, has_line: bool, has_tail: bool) -> None:
    missing = [name for name in PROMPT_SECTIONS if not str(sections.get(name, "")).strip()]
    if missing:
        raise ValueError(f"提示缺段：{missing}")
    summary = sections["summary"]
    if not summary.startswith("["):
        raise ValueError("summary 必须以任务类型前缀开头")
    if "keyframe completion" not in summary or "reference generation" not in summary:
        raise ValueError("summary 缺少 keyframe completion 或 reference generation")
    definitions = sections["subject_definitions"]
    if "<Picture 1>" not in definitions or "first frame of [Shot 1]" not in definitions:
        raise ValueError("首帧必须是独立的 <Picture 1>")
    if "<Picture 2>" in definitions:
        raise ValueError("场景图只写进 Subject，不能再占一个 Picture 编号")
    if "[Shot 1]" not in sections["detailed_description"]:
        raise ValueError("detailed_description 必须以 [Shot 1] 描述这一镜")
    if has_line and "<d>[Chinese]" not in sections["detailed_description"]:
        raise ValueError("对白必须放在 <d>[Chinese] 里")
    if has_line and "(S1)" in sections["retention_analysis"]:
        raise ValueError("retention_analysis 里不写说话人编号")
    if has_tail and "video continuation" not in summary:
        raise ValueError("同一动作延长必须带 video continuation")
    if sections["non_diegetic_music"] != "N/A":
        raise ValueError("v0 这一镜不加配乐，应写 N/A")


def render_prompt_text(sections: dict[str, str]) -> str:
    parts = [f"{name}:\n{sections[name].strip()}" for name in PROMPT_SECTIONS]
    return "\n\n".join(parts)


def compile_h3_from_colloquial(text: str, seconds: float | None = None) -> dict[str, str]:
    """一句口水话收成一条 H3 视频提示。默认吸附到约 5.17 秒。"""
    frames = snap_frames(seconds if seconds is not None else 5.17)
    quoted = _QUOTE.search(text or "")
    line = ""
    if quoted:
        line = quoted.group(1) or quoted.group(2) or ""
    shot = {
        "action": text,
        "camera": {"framing": _framing_of(text), "angle": "平视", "forbids_hand_closeup": True},
        "duration_sec": round(seconds_for_frames(frames), 5),
        "frame_count": frames,
        "dialogue": [{"speaker_id": "lead", "line": line}] if line else [],
        "continuation": None,
    }
    context = {
        "character_name": "the person",
        "character_description": "an adult with a clear face and plain clothes",
        "location_name": "the setting of this shot",
        "location_description": "a real floor, one wall or open side, and a single light source",
        "wardrobe_description": "plain everyday clothes",
        "time_of_day": "day",
    }
    return build_video_prompt(shot, context)
