import json

import pytest

from cinecore.backends.comfyui import ComfyUIClient
from cinecore.constants import TEXT_ENCODE_NODE
from cinecore.errors import CineError
from cinecore.workflows import (
    assert_still_contract,
    build_still_workflow,
    build_video_workflow,
    enforce_still_latent_contract,
)


class _Response:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_still_workflow_uses_768x1344_empty_latent_not_the_text_encoder():
    graph = build_still_workflow("一个人站在巷口", seed=7)
    assert_still_contract(graph)
    encode_ids = {
        node_id for node_id, node in graph.items() if node["class_type"] == TEXT_ENCODE_NODE
    }
    sampler = next(node for node in graph.values() if node["class_type"] == "KSampler")
    assert sampler["inputs"]["latent_image"][0] not in encode_ids


def test_bad_graph_is_rewired_onto_empty_sd3_latent():
    bad = {
        "2": {"class_type": TEXT_ENCODE_NODE, "inputs": {"text": "一个人"}},
        "4": {"class_type": "KSampler", "inputs": {"latent_image": ["2", 1]}},
    }
    fixed = enforce_still_latent_contract(bad)
    assert fixed["cinecore_latent"]["class_type"] == "EmptySD3LatentImage"
    assert fixed["cinecore_latent"]["inputs"]["width"] == 768
    assert fixed["cinecore_latent"]["inputs"]["height"] == 1344
    assert fixed["4"]["inputs"]["latent_image"] == ["cinecore_latent", 0]


def test_video_workflow_only_loads_declared_references():
    sections = {
        "subject_definitions": "人",
        "summary": "[keyframe completion + reference generation] 一个人。",
        "retention_analysis": "fully_preserved",
        "detailed_description": "中景",
        "overall_soundscape": "安静",
        "non_diegetic_music": "无",
    }
    graph = build_video_workflow(
        sections,
        frames=124,
        seed=3,
        references=[{"asset_id": "char-a", "role": "subject"}, {"asset_id": "loc-a", "role": "location"}],
        first_frame_name="kf-s001",
    )
    loaders = [node for node in graph.values() if node["class_type"] == "LoadImage"]
    assert len(loaders) == 3
    sampler = next(node for node in graph.values() if node["class_type"] == "H3Ref2VASampler")
    assert sampler["inputs"]["width"] == 416
    assert sampler["inputs"]["height"] == 736
    assert sampler["inputs"]["frames"] == 124
    assert sampler["inputs"]["references"] == [["2", 0], ["3", 0]]
    with pytest.raises(ValueError):
        build_video_workflow(
            sections,
            frames=208,
            seed=3,
            references=[],
            first_frame_name="kf",
        )


def test_committed_templates_keep_the_hardware_contract():
    from cinecore.schema_util import project_root

    root = project_root() / "workflows"
    still = json.loads((root / "still_qwen_image.api.json").read_text(encoding="utf-8"))
    video = json.loads((root / "video_h3_ref2va.api.json").read_text(encoding="utf-8"))
    assert_still_contract(still)
    sampler = next(node for node in video.values() if node["class_type"] == "H3Ref2VASampler")
    assert sampler["inputs"]["width"] == 416
    assert sampler["inputs"]["height"] == 736
    assert sampler["inputs"]["frames"] == 124
    assert len([node for node in video.values() if node["class_type"] == "LoadImage"]) == 3


def test_comfy_client_submits_prompt_and_does_not_render_from_the_batch_api():
    calls = []

    def opener(request, timeout=0):
        calls.append((request.full_url, request.data, timeout))
        if request.full_url.endswith("/prompt"):
            return _Response({"prompt_id": "abc"})
        return _Response({"abc": {"status": {"completed": True}}})

    client = ComfyUIClient("http://127.0.0.1:8188", opener=opener, poll_sec=0, max_polls=1)
    assert client.submit({"1": {}}) == "abc"
    assert client.wait("abc")["status"]["completed"] is True
    assert calls[0][0] == "http://127.0.0.1:8188/prompt"
    body = json.loads(calls[0][1].decode("utf-8"))
    assert body["prompt"] == {"1": {}}

    with pytest.raises(CineError):
        ComfyUIClient("").submit({})
    before = len(calls)
    with pytest.raises(CineError):
        client.render_take({}, 1)
    assert len(calls) == before
