"""自动审核。渲染进程退出码 0 不算通过。"""

from __future__ import annotations

from cinecore.constants import (
    LATENT_NODE,
    PROMPT_SECTIONS,
    STILL_HEIGHT,
    STILL_WIDTH,
    VIDEO_HEIGHT,
    VIDEO_WIDTH,
)
from cinecore.duration import is_usable_shot, seconds_for_frames


def has_pronoun_collision(text: str) -> bool:
    """他/她 在同一句里容易被语音识别撞车。先去掉复数再看。"""
    stripped = text.replace("他们", "").replace("她们", "").replace("它们", "")
    return "他" in stripped and "她" in stripped


def framing_requests_hands(framing: str) -> bool:
    """景别本身如果写成手部特写才算要求。提示里的“不给手部特写”是约束。"""
    text = framing or ""
    return "手" in text and "特写" in text and "不" not in text


def token_distance(left: str, right: str) -> float:
    """v0 的脸一致性是版本令牌是否相同，不是真实人脸嵌入。"""
    return 0.0 if left and left == right else 1.0


def _add(checks: list[dict], name: str, ok: bool, severity: str, detail: str) -> None:
    checks.append(
        {
            "name": name,
            "passed": bool(ok),
            "severity": severity,
            "detail": detail,
        }
    )


def report_passed(checks: list[dict]) -> bool:
    return all(check["passed"] for check in checks if check["severity"] == "error")


def failed_names(checks: list[dict]) -> list[str]:
    return [check["name"] for check in checks if not check["passed"] and check["severity"] == "error"]


def audit_keyframe(spec: dict, *, expected_face_token: str) -> list[dict]:
    checks: list[dict] = []
    _add(
        checks,
        "resolution",
        spec.get("width") == STILL_WIDTH and spec.get("height") == STILL_HEIGHT,
        "error",
        f"静帧应为 {STILL_WIDTH}×{STILL_HEIGHT}，实际 {spec.get('width')}×{spec.get('height')}",
    )
    _add(
        checks,
        "latent_contract",
        spec.get("latent_node") == LATENT_NODE,
        "error",
        f"latent 节点应为 {LATENT_NODE}",
    )
    _add(
        checks,
        "hand_closeup",
        not spec.get("hand_closeup", False) and not framing_requests_hands(spec.get("framing", "")),
        "error",
        "首帧禁止手部特写",
    )
    distance = token_distance(spec.get("face_token", ""), expected_face_token)
    _add(
        checks,
        "face_consistency",
        distance <= 0.35,
        "error",
        f"与角色静帧版本令牌距离 {distance:.2f}（v0 用令牌相等代替人脸嵌入）",
    )
    return checks


def audit_video(job: dict, take: dict, audit_cfg: dict) -> list[dict]:
    checks: list[dict] = []
    frames = int(take.get("frames", 0))
    fps = int(take.get("fps", 24))
    _add(
        checks,
        "resolution",
        take.get("width") == VIDEO_WIDTH and take.get("height") == VIDEO_HEIGHT,
        "error",
        f"视频应为 {VIDEO_WIDTH}×{VIDEO_HEIGHT}，这是 16GB 上的实际尺寸，不是原生 720p",
    )
    _add(
        checks,
        "duration_grid",
        is_usable_shot(frames) and frames == int(job["frame_count"]),
        "error",
        f"帧数 {frames} 必须落在 17n+5 的可用窗口，并等于计划 {job['frame_count']}",
    )
    expected = seconds_for_frames(frames, fps) if frames else -1
    actual = float(take.get("duration_sec", -1))
    _add(
        checks,
        "duration_match",
        abs(actual - expected) <= 0.05,
        "error",
        f"时长 {actual:.3f}s 与 {frames}/{fps} 不一致",
    )
    first = job["first_frame"]
    refs = set(take.get("reference_ids") or [])
    allowed = {item["asset_id"] for item in job["references"]} | {first["asset_id"]}
    _add(
        checks,
        "first_frame_lock",
        bool(first.get("approved"))
        and first.get("latent_node") == LATENT_NODE
        and first.get("width") == STILL_WIDTH
        and first.get("height") == STILL_HEIGHT
        and first["asset_id"] in refs,
        "error",
        "视频必须从已审核的 768×1344 首帧开始",
    )
    extra = sorted(refs - allowed)
    _add(
        checks,
        "reference_scope",
        not extra,
        "error",
        "参考图超出本镜头声明" if extra else "参考图只含本镜头声明的图",
    )
    distance = token_distance(take.get("face_token", ""), job.get("character_face_token", ""))
    limit = float(audit_cfg.get("face_distance_max", 0.35))
    _add(
        checks,
        "face_consistency",
        distance <= limit,
        "error",
        f"脸版本距离 {distance:.2f}，阈值 {limit:.2f}",
    )
    framing = job.get("camera", {}).get("framing", "")
    _add(
        checks,
        "hand_closeup",
        not take.get("hand_closeup", False) and not framing_requests_hands(framing),
        "error",
        "成片出现手部特写，或景别本身要求手部特写",
    )
    dialogue = take.get("dialogue") or []
    blob = "\n".join(item.get("line", "") for item in dialogue)
    _add(
        checks,
        "pronoun_collision",
        not has_pronoun_collision(blob),
        "error",
        "同一镜台词同时出现他和她，语音识别容易撞车",
    )
    speakers = {item.get("speaker_id") for item in dialogue}
    packed = len(dialogue) > 1 or (len(speakers) > 1 and take.get("rendered_as_single_narration", False))
    _add(
        checks,
        "dialogue_packing",
        not packed,
        "error",
        "一句镜头里塞了多句或多个人，容易变成旁白",
    )
    long_limit = int(audit_cfg.get("long_line_chars", 40))
    too_long = any(len(item.get("line", "")) > long_limit for item in dialogue)
    long_severity = "error" if audit_cfg.get("fail_on_long_line") else "warning"
    _add(
        checks,
        "long_line",
        not too_long,
        long_severity,
        f"单句超过 {long_limit} 字，单人长句容易读错。这是钩子，不是已解决。",
    )
    speech = float(take.get("speech_sec", 0.0))
    duration = float(take.get("duration_sec", 0.0))
    if dialogue:
        silence_ok = 0.05 < speech <= duration + 0.05
        silence_detail = "有台词但没有可听见的语音，或语音长过镜头"
    else:
        silence_ok = speech <= 0.2
        silence_detail = "无台词镜头不应出现明显人声"
    _add(checks, "silence", silence_ok, "error", silence_detail)
    defect = take.get("visual_defect")
    _add(
        checks,
        "visual_defect",
        not defect,
        "error",
        f"视觉缺陷：{defect}" if defect else "未标记黑场或其他基础缺陷",
    )
    sections = take.get("prompt_sections") or {}
    names = [name for name in PROMPT_SECTIONS if str(sections.get(name, "")).strip()]
    summary = str(sections.get("summary", ""))
    detail_text = str(sections.get("detailed_description", ""))
    prompt_ok = (
        names == list(PROMPT_SECTIONS)
        and summary.startswith("[")
        and "keyframe completion" in summary
        and "[Shot 1]" in detail_text
    )
    _add(
        checks,
        "prompt_contract",
        prompt_ok,
        "error",
        "视频提示必须是六段英文契约：summary 带 keyframe completion，正文含 [Shot 1]",
    )
    continuation = job.get("continuation")
    if take.get("uses_tail_frame"):
        legal = (
            isinstance(continuation, dict)
            and continuation.get("mode") == "same_action_extend"
            and continuation.get("same_scene") is True
            and continuation.get("same_action_group") is True
        )
        detail = "尾帧只许延长同一场里的同一动作，不许跨场景或反打"
    else:
        legal = True
        detail = "本镜头没有使用尾帧延续"
    _add(checks, "continuation_scope", legal, "error", detail)
    return checks
