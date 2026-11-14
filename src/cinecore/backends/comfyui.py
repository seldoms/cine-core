"""本地 ComfyUI 的 HTTP 客户端。

v0 的批处理不会调用 submit。这样不会在“还收不回成片”的情况下把任务推进真实队列。
节点类名、主机和输出目录都来自配置或环境变量。
"""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Any, Callable

from cinecore.errors import CineError


class ComfyUIClient:
    def __init__(
        self,
        host: str,
        *,
        timeout: int = 3600,
        poll_sec: float = 2.0,
        max_polls: int = 1800,
        opener: Callable[..., Any] | None = None,
    ):
        self.host = host.rstrip("/") if host else ""
        self.timeout = timeout
        self.poll_sec = poll_sec
        self.max_polls = max_polls
        self._opener = opener or urllib.request.urlopen

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        if not self.host:
            raise CineError(
                "未配置 ComfyUI 主机。请设置 CINE_COMFYUI_HOST，例如 http://127.0.0.1:8188。"
                "没有 GPU 或不想提交任务时，请使用 --dry-run。"
            )
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.host + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        with self._opener(request, timeout=self.timeout) as response:
            body = response.read().decode("utf-8")
        return json.loads(body) if body else {}

    def submit(self, workflow: dict) -> str:
        """向 /prompt 提交一张图。批处理默认不走这里。"""
        body = self._request("POST", "/prompt", {"prompt": workflow})
        prompt_id = body.get("prompt_id")
        if not prompt_id:
            raise CineError("ComfyUI 没有返回 prompt_id")
        return str(prompt_id)

    def wait(self, prompt_id: str) -> dict:
        for _ in range(self.max_polls):
            history = self._request("GET", f"/history/{prompt_id}")
            entry = history.get(prompt_id)
            if entry and entry.get("status", {}).get("completed"):
                return entry
            if self.poll_sec:
                time.sleep(self.poll_sec)
        raise CineError(f"等待 ComfyUI 超时：{prompt_id}")

    def render_still(self, spec: dict) -> dict:
        raise CineError(
            "v0 批处理还不向 ComfyUI 提交静帧。任务文件里已经有按契约接好的工作流；"
            "收文件接上之前，请使用 --dry-run。"
        )

    def render_take(self, job: dict, take: int) -> dict:
        raise CineError(
            "v0 批处理还不向 ComfyUI 提交视频。主机可配，但不会在收不回成片时把整夜循环推进真实队列。"
        )
