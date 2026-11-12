"""把通过审核的镜头收成合成计划和字幕。dry-run 不写画面母带。"""

from __future__ import annotations

from pathlib import Path

from cinecore.constants import VIDEO_HEIGHT, VIDEO_WIDTH
from cinecore.schema_util import dump_json


def format_timestamp(seconds: float) -> str:
    milliseconds = max(0, int(round(seconds * 1000)))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def build_srt(accepted: list[dict], names: dict[str, str]) -> str:
    blocks: list[str] = []
    cursor = 0.0
    index = 1
    for shot in accepted:
        start = cursor
        cursor += float(shot["duration_sec"])
        if not shot.get("dialogue"):
            continue
        line = shot["dialogue"][0]
        cue_start = start + 0.3
        cue_end = min(cursor - 0.1, cue_start + 2.0)
        if cue_end <= cue_start:
            cue_end = cue_start + 0.4
        speaker = names.get(line["speaker_id"], line["speaker_id"])
        blocks.append(
            f"{index}\n{format_timestamp(cue_start)} --> {format_timestamp(cue_end)}\n{speaker}：{line['line']}\n"
        )
        index += 1
    return "\n".join(blocks)


def write_assembly(
    story_dir: Path,
    *,
    story_id: str,
    run_id: str,
    status: str,
    dry_run: bool,
    planned_duration_sec: float,
    outcomes: list[dict],
    names: dict[str, str],
    loudness_lufs: float,
    true_peak_dbtp: float,
    upscale_requested: bool,
) -> dict:
    accepted = [item for item in outcomes if item["status"] == "accepted"]
    quarantined = [item for item in outcomes if item["status"] == "quarantined"]
    accepted_duration = round(sum(float(item["duration_sec"]) for item in accepted), 5)
    segments = [
        {
            "shot_id": item["shot_id"],
            "take": item["take"],
            "regeneration": item["regeneration"],
            "duration_sec": item["duration_sec"],
            "artifact": f"shots/{item['shot_id']}/takes/r{item['regeneration']}_t{item['take']}/take.json",
        }
        for item in accepted
    ]
    assemble_dir = story_dir / "assemble"
    assemble_dir.mkdir(parents=True, exist_ok=True)
    dump_json(
        assemble_dir / "concat_plan.json",
        {"story_id": story_id, "segments": segments},
    )
    (assemble_dir / "subtitles.srt").write_text(build_srt(accepted, names), encoding="utf-8")
    sidecar = {
        "story_id": story_id,
        "run_id": run_id,
        "status": status,
        "dry_run": dry_run,
        "stub": True,
        "note": "v0 dry-run 不写画面母带。这个文件是合成契约：拼接顺序、字幕和响度目标。它不是成片。",
        "native_video": {
            "width": VIDEO_WIDTH,
            "height": VIDEO_HEIGHT,
            "note": "16GB 上的实际分辨率。不是原生 720p。",
        },
        "upscale_to_720p": {
            "requested": upscale_requested,
            "applied": False,
            "note": "即使请求放大到 720×1280，也只是后期处理，画面会更软。v0 不执行放大器。",
        },
        "planned_duration_sec": planned_duration_sec,
        "accepted_duration_sec": accepted_duration,
        "accepted_shots": len(accepted),
        "quarantined_shots": len(quarantined),
        "loudness": {
            "integrated_lufs": loudness_lufs,
            "true_peak_dbtp": true_peak_dbtp,
            "applied": False,
            "note": "响度目标已记录，dry-run 没有音频可归一化。",
        },
        "concat_plan": "concat_plan.json",
        "subtitles": "subtitles.srt",
    }
    dump_json(assemble_dir / "film_sidecar.json", sidecar)
    return sidecar
