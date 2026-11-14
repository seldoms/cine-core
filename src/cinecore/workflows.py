"""ComfyUI API 图。静帧契约：KSampler 的 latent 来自 768×1344 的 EmptySD3LatentImage。"""

from __future__ import annotations

import copy
import hashlib
from typing import Any

from cinecore.constants import (
    LATENT_NODE,
    STILL_HEIGHT,
    STILL_WIDTH,
    TEXT_ENCODE_NODE,
    VIDEO_HEIGHT,
    VIDEO_WIDTH,
)
from cinecore.duration import is_usable_shot
from cinecore.prompts import render_prompt_text


def stable_seed(*parts: str) -> int:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def default_node_classes() -> dict[str, str]:
    return {
        "still_latent": LATENT_NODE,
        "still_encode": TEXT_ENCODE_NODE,
        "still_sampler": "KSampler",
        "still_decode": "VAEDecode",
        "still_save": "SaveImage",
        "video_sampler": "H3Ref2VASampler",
        "video_save": "SaveVideo",
        "load_image": "LoadImage",
    }


def enforce_still_latent_contract(
    workflow: dict[str, Any],
    *,
    width: int = STILL_WIDTH,
    height: int = STILL_HEIGHT,
    latent_class: str = LATENT_NODE,
    sampler_class: str = "KSampler",
) -> dict[str, Any]:
    """把静帧图改到正确的 latent。缺节点就补上，并改掉错误连线。

    已知错误：有的本地脚本把 KSampler.latent_image 接到 TextEncodeQwenImage21
    带出来的 1024 方形 latent。这里强制改到 EmptySD3LatentImage 的 768×1344。
    """
    graph = copy.deepcopy(workflow)
    latent_ids = [
        node_id
        for node_id, node in graph.items()
        if isinstance(node, dict) and node.get("class_type") == latent_class
    ]
    if latent_ids:
        latent_id = latent_ids[0]
        graph[latent_id].setdefault("inputs", {})
        graph[latent_id]["inputs"]["width"] = width
        graph[latent_id]["inputs"]["height"] = height
        graph[latent_id]["inputs"]["batch_size"] = 1
    else:
        latent_id = "cinecore_latent"
        graph[latent_id] = {
            "class_type": latent_class,
            "inputs": {"width": width, "height": height, "batch_size": 1},
            "_cinecore": "补上的静帧 latent，避免使用文本编码器的方形 latent",
        }
    for node in graph.values():
        if isinstance(node, dict) and node.get("class_type") == sampler_class:
            node.setdefault("inputs", {})
            node["inputs"]["latent_image"] = [latent_id, 0]
    return graph


def build_still_workflow(
    prompt: str,
    *,
    seed: int,
    width: int = STILL_WIDTH,
    height: int = STILL_HEIGHT,
    node_classes: dict[str, str] | None = None,
    filename_prefix: str = "cinecore-still",
) -> dict[str, Any]:
    classes = {**default_node_classes(), **(node_classes or {})}
    graph = {
        "1": {
            "class_type": classes["still_latent"],
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "2": {
            "class_type": classes["still_encode"],
            "inputs": {"text": prompt},
            "_cinecore": "这个节点只提供条件，不提供 latent",
        },
        "3": {
            "class_type": classes["still_encode"],
            "inputs": {"text": "手部特写，多余手指，第二个人，低清晰度"},
        },
        "4": {
            "class_type": classes["still_sampler"],
            "inputs": {
                "seed": seed,
                "steps": 20,
                "cfg": 4.0,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1.0,
                "latent_image": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
            },
        },
        "5": {
            "class_type": classes["still_decode"],
            "inputs": {"samples": ["4", 0]},
        },
        "6": {
            "class_type": classes["still_save"],
            "inputs": {"images": ["5", 0], "filename_prefix": filename_prefix},
        },
    }
    return enforce_still_latent_contract(
        graph,
        width=width,
        height=height,
        latent_class=classes["still_latent"],
        sampler_class=classes["still_sampler"],
    )


def build_video_workflow(
    prompt_sections: dict[str, str],
    *,
    frames: int,
    seed: int,
    references: list[dict[str, str]],
    first_frame_name: str,
    width: int = VIDEO_WIDTH,
    height: int = VIDEO_HEIGHT,
    fps: int = 24,
    node_classes: dict[str, str] | None = None,
    filename_prefix: str = "cinecore-video",
) -> dict[str, Any]:
    """只为本镜头声明过的参考图建加载节点。类名是占位，本地包用配置映射。"""
    if not is_usable_shot(frames):
        raise ValueError(f"视频帧数不在 17n+5 的可用窗口里：{frames}")
    classes = {**default_node_classes(), **(node_classes or {})}
    graph: dict[str, Any] = {
        "1": {
            "class_type": classes["load_image"],
            "inputs": {"image": first_frame_name},
            "_cinecore": "首帧锁定。视频从这张已审核静帧开始。",
        }
    }
    ref_links: list[list[Any]] = []
    for index, ref in enumerate(references, start=2):
        node_id = str(index)
        graph[node_id] = {
            "class_type": classes["load_image"],
            "inputs": {"image": ref["asset_id"], "role": ref["role"]},
            "_cinecore": "仅本镜头参考，不是全片角色库",
        }
        ref_links.append([node_id, 0])
    sampler_id = str(len(references) + 2)
    save_id = str(len(references) + 3)
    graph[sampler_id] = {
        "class_type": classes["video_sampler"],
        "inputs": {
            "width": width,
            "height": height,
            "frames": frames,
            "fps": fps,
            "seed": seed,
            "prompt": render_prompt_text(prompt_sections),
            "first_frame": ["1", 0],
            "references": ref_links,
        },
        "_cinecore": "video_sampler 类名需按本地 ComfyUI 节点改映射，图内不要再加别的角色",
    }
    graph[save_id] = {
        "class_type": classes["video_save"],
        "inputs": {"video": [sampler_id, 0], "filename_prefix": filename_prefix},
    }
    return graph


def assert_still_contract(workflow: dict[str, Any], *, latent_class: str = LATENT_NODE) -> None:
    latent_ids = [
        node_id
        for node_id, node in workflow.items()
        if node.get("class_type") == latent_class
    ]
    if len(latent_ids) != 1:
        raise AssertionError("静帧图应恰好有一个 EmptySD3LatentImage")
    latent = workflow[latent_ids[0]]["inputs"]
    if latent.get("width") != STILL_WIDTH or latent.get("height") != STILL_HEIGHT:
        raise AssertionError("静帧 latent 尺寸不是 768×1344")
    encode_ids = {
        node_id
        for node_id, node in workflow.items()
        if node.get("class_type") == TEXT_ENCODE_NODE
    }
    samplers = [node for node in workflow.values() if node.get("class_type") == "KSampler"]
    if not samplers:
        raise AssertionError("静帧图缺少 KSampler")
    link = samplers[0]["inputs"].get("latent_image")
    if not link or link[0] != latent_ids[0]:
        raise AssertionError("KSampler.latent_image 没有接到 EmptySD3LatentImage")
    if link[0] in encode_ids:
        raise AssertionError("KSampler.latent_image 仍接在文本编码器上")
