"""H3 Ref2VA 的六段提示。四段简写不能当默认。"""

from __future__ import annotations

from cinecore.constants import PROMPT_SECTIONS


def _task_prefix(shot: dict) -> str:
    kinds = ["keyframe completion", "reference generation"]
    if shot.get("continuation"):
        kinds.insert(0, "video continuation")
    if shot.get("dialogue"):
        kinds.append("audio reference")
    return "[" + " + ".join(kinds) + "]"


def build_video_prompt(shot: dict, context: dict) -> dict[str, str]:
    """按官方六段顺序写本镜头自己的提示，不把整库角色塞进来。"""
    name = context["character_name"]
    location = context["location_name"]
    wardrobe = context["wardrobe_description"]
    action = shot["action"]
    framing = shot["camera"]["framing"]
    dialogue = shot.get("dialogue") or []
    line = dialogue[0]["line"] if dialogue else ""

    definitions = [
        f"<Subject 1>：{name}。{context['character_description']} 本镜头服装：{wardrobe}。",
        f"<Picture 1>：已通过自动审核的首帧，作为这一镜的第一帧。",
        f"<Picture 2>：地点「{location}」的场景静帧，只提供空间，不额外加人。",
    ]
    retention = [
        f"<Subject 1>（本镜头）: fully_preserved - 只保留 {name} 的脸和这一套衣服，不替换成其他角色。",
        "<Picture 1>（第一帧）: fully_preserved - 视频从这张已审核首帧开始。",
        f"<Picture 2>（地点）: fully_preserved - 空间参照「{location}」，不引入参考图里没有的人群。",
    ]
    if shot.get("continuation"):
        definitions.append(
            "<Video 1>：同一动作的上一段尾巴，只许顺着这个动作延长，不是新场景，也不是反打。"
        )
        retention.append(
            "<Video 1>（同一动作延长）: partially_preserved - 只延续正在发生的动作，不跨镜头、不跨场景。"
        )
    if line:
        definitions.append(f"<Audio 1>：这一镜里 {name} 要说的一句短话，不是拿来复制的现成音轨。")
        retention.append("<Audio 1>（一句台词）: reference - 只参考这句新台词的字，不复用外部录音。")

    summary = (
        f"{_task_prefix(shot)} 这一镜只拍 {name} 一人，在「{location}」完成：{action} "
        "双手不进入近景。没有第二个入画的人。"
    )
    detail = (
        f"画面是 9:16 的单人{framing}。{name}{action} "
        "手停留在身侧或画外，不给手部特写。不要路人，不要突然加进来的第三人。"
    )
    if line:
        sound = f"环境声很小。{name} 只说一句：「{line}」。不要把它念成旁白，也不要再加第二个人的声音。"
    else:
        sound = f"这一镜没有台词。只留「{location}」里很轻的环境声，不要凭空加对白。"
    music = "这一镜不加配乐，也不用音乐去填旁白。留白。"

    sections = {
        "subject_definitions": "\n".join(definitions),
        "summary": summary,
        "retention_analysis": "\n".join(retention),
        "detailed_description": detail,
        "overall_soundscape": sound,
        "non_diegetic_music": music,
    }
    missing = [name for name in PROMPT_SECTIONS if not sections.get(name, "").strip()]
    if missing:
        raise ValueError(f"提示缺段：{missing}")
    if not sections["summary"].startswith("["):
        raise ValueError("summary 必须以任务类型前缀开头")
    return sections


def render_prompt_text(sections: dict[str, str]) -> str:
    parts = [f"{name}:\n{sections[name].strip()}" for name in PROMPT_SECTIONS]
    return "\n\n".join(parts)


def still_prompt(*, kind: str, text: str) -> str:
    return (
        f"{kind}。9:16 竖幅，单人，{text} "
        "双手不作为主体，不给手部特写，不要多余的人。"
    )
