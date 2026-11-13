"""规则改编器：把语料改成约五分钟的单人短片，不复述原文。

换成更好的语言模型时，替换 adapt_story 这一层，后面的阶段图不用重写。
"""

from __future__ import annotations

import json
import re

from cinecore.constants import FPS, LATENT_NODE, STILL_HEIGHT, STILL_WIDTH
from cinecore.duration import grid_n, plan_shot_frames, seconds_for_frames
from cinecore.errors import CineError, VerbatimOverlap
from cinecore.prompts import build_video_prompt, compile_qwen_still, derive_look, still_prompt

_SPACE = re.compile(r"\s+")

_WARDROBE = [
    {"id": "daily", "description": "日常：素色外套，颜色压暗，布面干净。"},
    {"id": "night", "description": "夜里：同一套衣服外加一件深色薄外套，帽檐抬起，露出眼睛。"},
]


def _clean(text: str) -> str:
    return _SPACE.sub("", text)


def find_verbatim(raw: str, blob: str, *, title: str = "", window: int = 18) -> str | None:
    """原文里连续 window 个字出现在改编结果里，就当成复述。标题本身允许保留。"""
    raw_s = _clean(raw)
    blob_s = _clean(blob)
    title_s = _clean(title)
    if len(raw_s) < window:
        return None
    for index in range(len(raw_s) - window + 1):
        fragment = raw_s[index : index + window]
        if title_s and fragment in title_s:
            continue
        if fragment in blob_s:
            return fragment
    return None


def assert_not_verbatim(item: dict, *documents: dict, window: int = 18) -> None:
    blob = "\n".join(json.dumps(doc, ensure_ascii=False) for doc in documents)
    fragment = find_verbatim(item.get("raw_text", ""), blob, title=item.get("title", ""), window=window)
    if fragment:
        raise VerbatimOverlap(fragment)


def _character(character_id: str, name: str, description: str, *, on_screen: bool = True) -> dict:
    return {
        "id": character_id,
        "name": name,
        "on_screen": on_screen,
        "description": description,
        "wardrobe_variants": [dict(item) for item in _WARDROBE],
    }


def _location(location_id: str, name: str, description: str, time_of_day: str) -> dict:
    return {
        "id": location_id,
        "name": name,
        "description": description,
        "time_of_day": time_of_day,
    }


# 示例故事的改编是另写的，不搬原文句子。其他语料走 _generic_film。
_FILMS: dict[str, dict] = {
    "sample-last-lantern": {
        "title": "台阶上的灯",
        "logline": "陈舟把一盏不肯随便发光的灯挂到窄巷尽头，守到天明就收走。",
        "note": "奇闻由头收成一夜能看完的一件事，不往下写成连载。",
        "hero": _character(
            "chenzhou",
            "陈舟",
            "三十岁上下，短发，瘦高，面容平静，深色素面外套。",
        ),
        "offscreen": [],
        "location": _location(
            "narrow-alley",
            "窄巷尽头",
            "石板巷，一头是铁钩，一头是台阶，夜里巷子安静。",
            "night",
        ),
        "scenes": [
            {
                "scene_id": "sc1",
                "heading": "黄昏 内 修灯桌",
                "action": "陈舟在木桌前拧好最后一颗螺丝，试过这盏灯不会自己乱亮。",
                "lines": ["今晚只挂这一盏。"],
            },
            {
                "scene_id": "sc2",
                "heading": "黄昏 外 窄巷",
                "action": "陈舟把灯挂上巷尾的铁钩，然后坐到台阶里侧。",
                "lines": ["我在这儿等它自己亮。"],
            },
            {
                "scene_id": "sc3",
                "heading": "夜 外 窄巷",
                "action": "夜色变深，灯一直是暗的。陈舟把外套拉紧，不招呼过路的人。",
                "lines": ["不亮也没关系。"],
            },
            {
                "scene_id": "sc4",
                "heading": "深夜 外 窄巷",
                "action": "灯短短亮了一下。画外有人说要离开，陈舟没有转头。",
                "lines": ["我听见了，但我不过去。"],
            },
            {
                "scene_id": "sc5",
                "heading": "黎明 外 窄巷",
                "action": "天一亮，陈舟摘下灯放进布袋，离开已经空掉的巷子。",
                "lines": ["到此为止。"],
            },
        ],
    },
    "sample-market-rain": {
        "title": "提早收摊",
        "logline": "林夏听到市场要提前打烊，只把自己的菜盖好，提着空筐走进雨里。",
        "note": "新闻体裁只当由头。成片是一个人的行动，不做成口播，也不念消息原文。",
        "hero": _character(
            "linxia",
            "林夏",
            "四十岁上下，低马尾，旧工装外套，眼神清醒，耳际干净。",
        ),
        "offscreen": [],
        "location": _location(
            "east-market",
            "东市棚架",
            "铁皮棚下的一个菜摊，地面潮湿，通道逐渐变空。",
            "day",
        ),
        "scenes": [
            {
                "scene_id": "sc1",
                "heading": "午后 内 摊位后",
                "action": "林夏清点剩下的青菜，听见邻摊有人在打听一场传闻。",
                "lines": ["我先把自家的菜盖上。"],
            },
            {
                "scene_id": "sc2",
                "heading": "午后 外 通道",
                "action": "林夏把空筐叠齐，站在自己的摊位里，不去通道中央解释。",
                "lines": ["传闻我不跟。"],
            },
            {
                "scene_id": "sc3",
                "heading": "午后 外 棚架",
                "action": "周围摊位一个个空了。林夏独自拉上塑料布，手只在腰侧忙碌。",
                "lines": ["早点离开。"],
            },
            {
                "scene_id": "sc4",
                "heading": "傍晚 外 门口",
                "action": "棚架安静下来。林夏提着一只空筐走到门口，停了停。",
                "lines": ["今天就到这儿。"],
            },
            {
                "scene_id": "sc5",
                "heading": "傍晚 外 路口",
                "action": "林夏把空筐放上自行车后架，骑进细雨，不再看身后的棚子。",
                "lines": ["回家。"],
            },
        ],
    },
    "sample-paper-boat": {
        "title": "盆里的小舟",
        "logline": "周晚把旧报折成小舟放进厨房水盆，听祖父在画外让它走慢些。",
        "note": "双人关系收成单人入画。祖父只在画外出声，避免 416 宽的竖屏里挤两个人。",
        "hero": _character(
            "zhouwan",
            "周晚",
            "十一二岁，齐耳短发，浅灰色雨衣，素颜。",
        ),
        "offscreen": [
            _character(
                "grandfather",
                "祖父",
                "七十多岁，灰白短发，旧棉衫。这一版不把他放进画面，只留声音。",
                on_screen=False,
            )
        ],
        "location": _location(
            "rain-kitchen",
            "旧楼厨房",
            "一张木桌，一只浅水盆，窗外有雨。",
            "rain-day",
        ),
        "scenes": [
            {
                "scene_id": "sc1",
                "heading": "雨天 内 厨房",
                "action": "周晚展开一张发黄的报纸，折出一只能浮起来的小舟。",
                "lines": ["今天只做这一只。"],
            },
            {
                "scene_id": "sc2",
                "heading": "雨天 内 桌边",
                "action": "周晚把小舟放到老人手边，没有催他想起任何旧址。",
                "lines": ["让它走得慢些。"],
            },
            {
                "scene_id": "sc3",
                "heading": "雨天 内 水盆",
                "action": "浅水盆里，周晚放开小舟，看它碰到盆沿又转回来。",
                "lines": ["你看，它自己会回头。"],
            },
            {
                "scene_id": "sc4",
                "heading": "雨天 内 厨房",
                "action": "画外的老人重复着让小舟慢下来。周晚不解释年份，只把水轻轻拨开。",
                "lines": ["我在听。"],
            },
            {
                "scene_id": "sc5",
                "heading": "雨势变弱 内 厨房",
                "action": "雨声变弱。老人叫出周晚幼年的称呼。周晚应了一声，把小舟留在盆里。",
                "lines": ["我应你。"],
            },
        ],
    },
}


_NAMES = ("江禾", "宋晚", "许宁")
_BEATS = (
    ("sc1", "开场", "日 内 桌边", "把由头放在桌上，先看清今天只做这一件"),
    ("sc2", "触发", "日 内 门口", "一件具体的小事打断准备，人决定出门"),
    ("sc3", "发展", "日 外 街角", "独自把这件事做完一段，四周只有这一处地方"),
    ("sc4", "转折", "傍晚 外 街角", "留下一样东西，带走一样东西"),
    ("sc5", "收束", "夜 内 桌边", "回到桌边，这件事在今天结束"),
)
_NEWS_LINES = (
    "我只处理眼前能看见的这一件。",
    "缺的部分我空着。",
    "名字是我自己起的。",
    "今天就走到这里。",
    "到今天为止。",
)
_STORY_LINES = (
    "我今天把这一件做完。",
    "那我现在就去。",
    "先走完这一段。",
    "这个留下。",
    "到今天为止。",
)


def classify_source(text: str, explicit: str | None = None) -> str:
    if explicit in {"news", "touching", "interesting"}:
        return explicit
    blob = text or ""
    if any(word in blob for word in ("新闻", "报道", "通报", "市民反映", "停电", "事故")):
        return "news"
    if any(word in blob for word in ("爷爷", "奶奶", "孩子", "纸船", "感动", "病")):
        return "touching"
    return "interesting"


def input_kind(text: str) -> str:
    parts = [part for part in re.split(r"[\s，。！？；、,]+", (text or "").strip()) if part]
    if any(len(part) > 8 for part in parts):
        return "paragraph"
    if len("".join(parts)) > 16 and len(parts) > 4:
        return "paragraph"
    return "keywords"


def content_hooks(text: str) -> list[str]:
    parts = re.split(r"[\s，。！？；、,]+", (text or "").strip())
    hooks: list[str] = []
    for part in parts:
        part = part.strip()
        if 2 <= len(part) <= 8 and part not in hooks:
            hooks.append(part)
    return hooks[:6]


def news_gaps(text: str) -> list[str]:
    gaps = ["人物用虚构名，不对应输入里没有点名的真实个人"]
    if not re.search(r"\d", text or ""):
        gaps.append("输入没有可核对的数字，成片不写户数、金额、比例或伤亡")
    if not any(word in (text or "") for word in ("恢复", "逮捕", "死亡", "获救", "赔偿", "结案", "查明")):
        gaps.append("输入没有写明事件结果，成片不替它收场")
    if not any(word in (text or "") for word in ("局", "公司", "医院", "学校", "政府")):
        gaps.append("输入没有可核对的机构，成片不写成某个真实单位的报道")
    return gaps


def expansion_record(item: dict, kind: str) -> dict:
    locked = ["时长锁在约五分钟", "一部短片，不写成连续剧", "单人入画"]
    gaps: list[str] = []
    if item["source_type"] == "news":
        locked.append("不补写输入里没有的真实姓名、机构、事件结果和数字")
        gaps = news_gaps(item.get("raw_text", ""))
    elif kind == "keywords":
        gaps.append("关键词之间的过程和动机是补出来的情节，输入本身没有写")
    return {
        "input_kind": kind,
        "target_seconds": 300,
        "genre_boundary": "约五分钟的一部短片，不写成连续剧，也不写成十二分钟的戏",
        "locked": locked,
        "gaps": gaps,
        "fictionalized": True,
    }


def _generic_film(item: dict) -> dict:
    kind = input_kind(item.get("raw_text") or item.get("title") or "")
    source = item.get("raw_text") or item.get("title") or ""
    hooks = content_hooks(source) or content_hooks(item.get("title", "")) or ["这件事"]
    name = _NAMES[sum(item["id"].encode("utf-8")) % len(_NAMES)]
    hero = _character(
        "lead",
        name,
        "三十岁上下，黑发扎起，深色上衣，长相普通，便于以后替换静帧。",
    )
    night = any(word in source for word in ("夜", "雨", "巷", "灯"))
    location = _location(
        "plain-corner",
        "窄巷" if night else "普通街角",
        "石板和一处能站人的空地" if night else "一处普通的街角，地面和墙面清楚",
        "night" if night else "day",
    )
    lines = _NEWS_LINES if item["source_type"] == "news" else _STORY_LINES
    scenes = []
    for index, (scene_id, beat, heading, purpose) in enumerate(_BEATS):
        hook = hooks[index % len(hooks)]
        scenes.append(
            {
                "scene_id": scene_id,
                "heading": f"{beat} {heading}",
                "action": f"{name}{purpose}。这一场只碰「{hook}」。",
                "lines": [lines[index]],
            }
        )
    expansion = expansion_record(item, kind)
    gap_note = ""
    if expansion["gaps"]:
        gap_note = "缺口：" + "；".join(expansion["gaps"]) + "。"
    if item["source_type"] == "news":
        tone = "社会新闻只作由头。人物、机构和结果按缺口虚拟化，不写成已经发生的报道。"
    elif item["source_type"] == "touching":
        tone = "感人小品收成一个关系里的一件事，不写成连续剧。"
    else:
        tone = "有意思的小事停在今天，不往下写下一集。"
    short = hooks[0]
    return {
        "title": f"{short}这一天",
        "logline": f"虚构人物{name}用一个白天把「{short}」做成一件能看完的事。",
        "note": tone + gap_note + "时长锁在约五分钟。输入越短，补上的情节越多，但锁住的事实不改。",
        "hero": hero,
        "offscreen": [],
        "location": location,
        "scenes": scenes,
        "expansion": expansion,
    }


def film_for(item: dict) -> dict:
    return _FILMS.get(item["id"], _generic_film(item))


def _scene_indexes(shot_count: int, scene_count: int = 5) -> list[int]:
    weights = [0.16, 0.16, 0.34, 0.16, 0.18]
    counts = [max(1, round(weight * shot_count)) for weight in weights]
    while sum(counts) > shot_count:
        index = max(range(scene_count), key=lambda i: counts[i])
        if counts[index] == 1:
            break
        counts[index] -= 1
    while sum(counts) < shot_count:
        counts[2] += 1
    indexes: list[int] = []
    for scene_index, count in enumerate(counts):
        indexes.extend([scene_index] * count)
    if len(indexes) != shot_count:
        raise CineError("分场镜头数对不齐")
    return indexes


def _prop_for(blob: str, look: dict) -> dict | None:
    if "灯" in blob:
        spec = ("lamp", "一盏灯", "深色金属灯壳，玻璃罩，提手")
    elif "舟" in blob or "船" in blob:
        spec = ("paper-boat", "一只纸船", "旧报纸折成的小舟，折痕清楚")
    elif "筐" in blob:
        spec = ("basket", "一只竹筐", "竹筐，提手，筐底有压过的使用痕迹")
    else:
        return None
    prop_id, name, description = spec
    return {
        "id": prop_id,
        "name": name,
        "description": description,
        "still_prompt": compile_qwen_still(f"{name}，{description}", kind="prop", look=look),
    }


def _wardrobe_for(time_of_day: str) -> str:
    if time_of_day in {"night", "dusk"}:
        return "night"
    return "daily"


def prompt_context(bible: dict, shot: dict) -> dict:
    hero = next(character for character in bible["characters"] if character["id"] == shot["subject_ids"][0])
    location = next(place for place in bible["locations"] if place["id"] == shot["location_id"])
    wardrobe = next(item for item in hero["wardrobe_variants"] if item["id"] == shot["wardrobe_variant_id"])
    return {
        "character_name": hero["name"],
        "character_description": hero["description"],
        "location_name": location["name"],
        "location_description": location["description"],
        "wardrobe_description": wardrobe["description"],
        "time_of_day": location["time_of_day"],
        "look": bible.get("look"),
    }


def adapt_story(item: dict, duration_cfg: dict, *, backend: str = "rule") -> tuple[dict, dict, dict]:
    if backend == "llm":
        return LLMAdapter().adapt(item, duration_cfg)
    if backend != "rule":
        raise CineError(f"未知的改编后端：{backend}")
    film = film_for(item)
    hero = film["hero"]
    location = film["location"]
    frames = plan_shot_frames(
        float(duration_cfg.get("target_seconds", 300)),
        min_seconds=float(duration_cfg.get("min_seconds", 270)),
        max_seconds=float(duration_cfg.get("max_seconds", 330)),
        fps=int(duration_cfg.get("fps", FPS)),
        n_min=int(duration_cfg.get("grid_n_min", 5)),
        n_max=int(duration_cfg.get("grid_n_max", 15)),
    )
    wardrobe_id = _wardrobe_for(location["time_of_day"])
    wardrobe = next(item for item in hero["wardrobe_variants"] if item["id"] == wardrobe_id)
    # prompt_context 需要圣经；这里先用同一份字段，圣经在函数末尾组装。
    context = {
        "character_name": hero["name"],
        "character_description": hero["description"],
        "location_name": location["name"],
        "location_description": location["description"],
        "wardrobe_description": wardrobe["description"],
        "time_of_day": location["time_of_day"],
        "look": derive_look(item["source_type"], item.get("title", "") + item.get("raw_text", "")),
    }
    scene_index = _scene_indexes(len(frames))
    subject_ref = f"char-{hero['id']}-still-{wardrobe_id}"
    location_ref = f"loc-{location['id']}-still"
    shots: list[dict] = []
    local_counts = {scene["scene_id"]: 0 for scene in film["scenes"]}
    previous_by_scene: dict[str, dict] = {}
    for ordinal, frame_count in enumerate(frames, start=1):
        scene = film["scenes"][scene_index[ordinal - 1]]
        local_index = local_counts[scene["scene_id"]]
        local_counts[scene["scene_id"]] = local_index + 1
        previous = previous_by_scene.get(scene["scene_id"])
        # 尾帧只延长同一个动作，并且景别不变。换景别就是新镜头，不接尾帧。
        extend = previous is not None and local_index % 5 == 1
        framings = ["远景", "中景", "近景", "中景"]
        angles = ["平视", "轻微俯视", "平视", "轻微仰视"]
        if extend:
            action_group = previous["action_group"]
            continuation = {"mode": "same_action_extend", "from_shot_id": previous["shot_id"]}
            framing = previous["camera"]["framing"]
            angle = previous["camera"]["angle"]
            action = f"{scene['action']} 顺着上一镜的同一动作再停留，景别不变，只拍{hero['name']}一人。"
        else:
            action_group = f"{scene['scene_id']}-a{local_index}"
            continuation = None
            framing = framings[local_index % len(framings)]
            angle = angles[local_index % len(angles)]
            action = f"{scene['action']} 这一镜只拍{hero['name']}一人。"
        line = None
        if local_index % 3 == 2 and scene["lines"]:
            line = scene["lines"][(local_index // 3) % len(scene["lines"])]
        dialogue = [{"speaker_id": hero["id"], "line": line}] if line else []
        shot = {
            "shot_id": f"s{ordinal:03d}",
            "scene_id": scene["scene_id"],
            "ordinal": ordinal,
            "frame_count": frame_count,
            "grid_n": grid_n(frame_count),
            "duration_sec": round(seconds_for_frames(frame_count), 5),
            "subject_ids": [hero["id"]],
            "subject_count": 1,
            "location_id": location["id"],
            "wardrobe_variant_id": wardrobe_id,
            "reference_plan": [
                {"ref_id": subject_ref, "role": "subject"},
                {"ref_id": location_ref, "role": "location"},
            ],
            "dialogue": dialogue,
            "camera": {
                "framing": framing,
                "angle": angle,
                "forbids_hand_closeup": True,
            },
            "action": action,
            "action_group": action_group,
            "continuation": continuation,
            "first_frame": {
                "width": STILL_WIDTH,
                "height": STILL_HEIGHT,
                "latent_node": LATENT_NODE,
                "prompt": still_prompt(
                    kind="首帧",
                    text=f"{hero['name']}，{wardrobe['description']} 地点{location['name']}，{framing}",
                    look=context["look"],
                ),
            },
            "video_prompt": {},
            "regeneration": 0,
        }
        shot["video_prompt"] = build_video_prompt(shot, context)
        shots.append(shot)
        previous_by_scene[scene["scene_id"]] = shot

    note = (
        "由素材改写成约五分钟短片，不逐字复述原文，也不扩展成连续剧。"
        "16GB 竖屏优先单人镜头。视频模型没有跨镜头记忆，所以每镜只声明自己的参考图。"
        + film["note"]
    )
    screenplay = {
        "story_id": item["id"],
        "title": film["title"],
        "source_title": item["title"],
        "source_type": item["source_type"],
        "logline": film["logline"],
        "adapter": "rule-v0",
        "adaptation_note": note,
        "expansion": film.get("expansion")
        or {
            "input_kind": "prepared",
            "target_seconds": 300,
            "genre_boundary": "约五分钟的一部短片，不写成连续剧",
            "locked": ["时长约五分钟", "单人入画", "不逐字复述原文"],
            "gaps": [],
            "fictionalized": True,
        },
        "target_duration_sec": float(duration_cfg.get("target_seconds", 300)),
        "scenes": [
            {
                "scene_id": scene["scene_id"],
                "heading": scene["heading"],
                "action": scene["action"],
                "dialogue": [{"speaker_id": hero["id"], "line": line} for line in scene["lines"]],
            }
            for scene in film["scenes"]
        ],
    }
    look = context["look"]
    prop = _prop_for(" ".join(scene["action"] for scene in film["scenes"]) + film["title"], look)
    bible = {
        "story_id": item["id"],
        "look": {key: look[key] for key in ("tone", "light", "palette")},
        "characters": [hero, *film["offscreen"]],
        "locations": [location],
        "props": [prop] if prop else [],
    }
    planned = round(sum(shot["duration_sec"] for shot in shots), 5)
    storyboard = {
        "story_id": item["id"],
        "fps": FPS,
        "grid_formula": "17n+5",
        "target_duration_sec": float(duration_cfg.get("target_seconds", 300)),
        "planned_duration_sec": planned,
        "shot_count": len(shots),
        "shots": shots,
    }
    return screenplay, bible, storyboard


def regenerate_shot(shot: dict, checks: list[dict], context: dict) -> dict:
    """重拍：拉远、保持单人，语音问题则去掉台词。不保证缺陷会消失。"""
    updated = json.loads(json.dumps(shot, ensure_ascii=False))
    updated["regeneration"] = int(updated.get("regeneration", 0)) + 1
    updated["camera"]["framing"] = "远景"
    updated["camera"]["forbids_hand_closeup"] = True
    failed = {check["name"] for check in checks if not check["passed"] and check["severity"] == "error"}
    if failed & {"pronoun_collision", "dialogue_packing", "silence", "long_line"}:
        updated["dialogue"] = []
    if not updated["action"].endswith("重拍改为更远的单人构图，双手留在画外。"):
        updated["action"] = updated["action"].rstrip("。") + "。重拍改为更远的单人构图，双手留在画外。"
    updated["first_frame"]["prompt"] = still_prompt(
        kind="首帧重拍",
        text=f"{context['character_name']} 远景，{context['wardrobe_description']} 地点{context['location_name']}",
        look=context.get("look"),
    )
    updated["video_prompt"] = build_video_prompt(updated, context)
    return updated


class LLMAdapter:
    """预留。v0 不把云端视频或语言模型当成默认路径。"""

    name = "llm"

    def adapt(self, item: dict, duration_cfg: dict) -> tuple[dict, dict, dict]:
        raise CineError(
            "adapt.backend=llm 还没有客户端。v0 请使用 rule。"
            "以后可以在这一层接本地或远程模型，不必改阶段图。"
        )
