"""读取 YAML，并允许环境变量覆盖主机地址。"""

from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

from cinecore.constants import (
    FPS,
    GRID_N_MAX,
    GRID_N_MIN,
    LATENT_NODE,
    MAX_SECONDS,
    MIN_SECONDS,
    STILL_HEIGHT,
    STILL_WIDTH,
    TARGET_SECONDS,
    VIDEO_HEIGHT,
    VIDEO_WIDTH,
)
from cinecore.errors import CineError
from cinecore.workflows import default_node_classes

DEFAULTS: dict = {
    "project": {"name": "cine-core"},
    "paths": {
        "corpus": "samples/corpus",
        "runs": "runs",
        "ledger": "data/ledger.json",
    },
    "im": {
        "host": "127.0.0.1",
        "port": 8787,
        "token": "",
        "inbox": "data/im-inbox",
    },
    "adapt": {"backend": "rule"},
    "autonomy": {
        "mode": "unattended",
        "takes_per_shot": 3,
        "max_regenerations": 1,
        "max_quarantined_shots": 3,
        "quarantine_shot_ratio": 0.15,
    },
    "duration": {
        "fps": FPS,
        "target_seconds": TARGET_SECONDS,
        "min_seconds": MIN_SECONDS,
        "max_seconds": MAX_SECONDS,
        "grid_n_min": GRID_N_MIN,
        "grid_n_max": GRID_N_MAX,
    },
    "render": {
        "backend": "dry_run",
        "video": {"width": VIDEO_WIDTH, "height": VIDEO_HEIGHT},
        "still": {
            "width": STILL_WIDTH,
            "height": STILL_HEIGHT,
            "latent_node": LATENT_NODE,
        },
        "upscale_720p": False,
    },
    "comfyui": {
        "host": "",
        "timeout_sec": 3600,
        "poll_sec": 2,
        "max_polls": 1800,
        "node_classes": default_node_classes(),
    },
    "audit": {
        "face_distance_max": 0.35,
        "long_line_chars": 40,
        "fail_on_long_line": False,
        "verbatim_window": 18,
    },
    "assemble": {"loudness_lufs": -16.0, "true_peak_dbtp": -1.5},
    "dry_run": {"inject": []},
}


def deep_merge(base: dict, override: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_dotenv(path: Path | None = None) -> None:
    """读取 .env。已存在的环境变量优先，避免盖掉调用方显式导出的值。"""
    env_path = path or Path(".env")
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def load_config(path: Path | None = None) -> dict:
    document: dict = {}
    if path is not None:
        if not path.is_file():
            raise CineError(f"找不到配置文件：{path}")
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise CineError("配置文件的顶层必须是映射")
        document = loaded
    config = deep_merge(DEFAULTS, document)
    host = os.environ.get("CINE_COMFYUI_HOST")
    if host:
        config["comfyui"]["host"] = host
    token = os.environ.get("CINE_IM_TOKEN")
    if token:
        config["im"]["token"] = token
    mode = config["autonomy"]["mode"]
    if mode not in {"unattended", "supervised"}:
        raise CineError(f"未知的自主模式：{mode}")
    backend = config["render"]["backend"]
    if backend not in {"dry_run", "comfyui"}:
        raise CineError(f"未知的渲染后端：{backend}")
    return config


def resolve_config_path(explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit)
    for candidate in (Path("config.yaml"), Path("config.example.yaml")):
        if candidate.is_file():
            return candidate
    return None
