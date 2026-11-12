"""不连 GPU 的后端。任务、重拍和隔离是真的，画面是记录。"""

from __future__ import annotations

from dataclasses import dataclass

from cinecore.constants import FPS, LATENT_NODE, STILL_HEIGHT, STILL_WIDTH, VIDEO_HEIGHT, VIDEO_WIDTH
from cinecore.workflows import build_still_workflow, stable_seed


@dataclass
class Injection:
    story_id: str
    shot_ordinal: int
    defect: str
    survives_regeneration: bool = True


def injections_from_config(config: dict) -> list[Injection]:
    items = []
    for raw in config.get("dry_run", {}).get("inject", []) or []:
        items.append(
            Injection(
                story_id=str(raw["story_id"]),
                shot_ordinal=int(raw["shot_ordinal"]),
                defect=str(raw["defect"]),
                survives_regeneration=bool(raw.get("survives_regeneration", True)),
            )
        )
    return items


class DryRunBackend:
    def __init__(self, injections: list[Injection] | None = None):
        self.injections = list(injections or [])

    def render_still(self, spec: dict) -> dict:
        workflow = build_still_workflow(
            spec["prompt"],
            seed=int(spec.get("seed") or stable_seed(spec["asset_id"])),
            filename_prefix=spec["asset_id"],
            node_classes=spec.get("node_classes"),
        )
        return {
            "asset_id": spec["asset_id"],
            "width": STILL_WIDTH,
            "height": STILL_HEIGHT,
            "latent_node": LATENT_NODE,
            "face_token": spec.get("face_token"),
            "hand_closeup": False,
            "framing": spec.get("framing", ""),
            "prompt": spec["prompt"],
            "stub": True,
            "workflow": workflow,
        }

    def _defect(self, job: dict) -> str | None:
        for injection in self.injections:
            if injection.story_id != job["story_id"]:
                continue
            if injection.shot_ordinal != int(job["ordinal"]):
                continue
            if int(job["regeneration"]) > 0 and not injection.survives_regeneration:
                return None
            return injection.defect
        return None

    def render_take(self, job: dict, take: int) -> dict:
        dialogue = [dict(item) for item in job.get("dialogue") or []]
        duration = job["frame_count"] / FPS
        artifact = {
            "shot_id": job["shot_id"],
            "take": take,
            "regeneration": job["regeneration"],
            "frames": job["frame_count"],
            "fps": FPS,
            "duration_sec": duration,
            "width": VIDEO_WIDTH,
            "height": VIDEO_HEIGHT,
            "reference_ids": [item["asset_id"] for item in job["references"]] + [job["first_frame"]["asset_id"]],
            "face_token": job["character_face_token"],
            "hand_closeup": False,
            "dialogue": dialogue,
            "speech_sec": min(1.8, max(0.0, duration - 0.4)) if dialogue else 0.0,
            "rendered_as_single_narration": len({item["speaker_id"] for item in dialogue}) > 1,
            "visual_defect": None,
            "prompt_sections": job["video_prompt"],
            "uses_tail_frame": job.get("continuation") is not None,
            "renderer_exit_code": 0,
            "stub": True,
        }
        defect = self._defect(job)
        if defect == "hand_closeup":
            artifact["hand_closeup"] = True
        elif defect == "face_mismatch":
            artifact["face_token"] = "mismatch"
        elif defect == "pronoun":
            artifact["dialogue"] = [{"speaker_id": "x", "line": "他跟她说了一句话"}]
            artifact["speech_sec"] = 1.0
        elif defect == "silence":
            artifact["dialogue"] = dialogue or [{"speaker_id": "x", "line": "还有一句。"}]
            artifact["speech_sec"] = 0.0
        elif defect == "visual":
            artifact["visual_defect"] = "black_frames"
        elif defect == "duration":
            artifact["frames"] = 208
            artifact["duration_sec"] = 208 / FPS
        elif defect == "packing":
            artifact["dialogue"] = [
                {"speaker_id": "a", "line": "走吧"},
                {"speaker_id": "b", "line": "再等一下"},
            ]
            artifact["rendered_as_single_narration"] = True
            artifact["speech_sec"] = 1.2
        elif defect:
            raise ValueError(f"未知的 dry-run 缺陷：{defect}")
        return artifact
