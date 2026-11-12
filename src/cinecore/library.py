"""版本化的素材库。素材一改，依赖它的镜头就变脏。"""

from __future__ import annotations

import hashlib


def face_token(character_id: str, version: int) -> str:
    digest = hashlib.sha256(f"{character_id}:{version}".encode("utf-8")).hexdigest()
    return digest[:16]


class AssetLibrary:
    def __init__(self, story_id: str):
        self.story_id = story_id
        self.assets: dict[str, dict] = {}
        self.dirty_shots: list[str] = []

    def add(
        self,
        *,
        asset_id: str,
        kind: str,
        width: int,
        height: int,
        latent_node: str,
        face_token_value: str | None,
        dependents: list[str],
        prompt: str,
        stub: bool,
    ) -> dict:
        record = {
            "asset_id": asset_id,
            "kind": kind,
            "version": 1,
            "width": width,
            "height": height,
            "latent_node": latent_node,
            "face_token": face_token_value,
            "dependents": list(dependents),
            "prompt": prompt,
            "stub": stub,
        }
        self.assets[asset_id] = record
        return record

    def get(self, asset_id: str) -> dict:
        return self.assets[asset_id]

    def revise(self, asset_id: str, reason: str) -> list[str]:
        """版本加一，并把依赖镜头标脏。调用方负责重渲染这些镜头。"""
        asset = self.assets[asset_id]
        asset["version"] += 1
        asset["revise_reason"] = reason
        if asset.get("face_token") and asset["kind"] == "character_still":
            character_id = asset_id.split("-still-")[0].removeprefix("char-")
            asset["face_token"] = face_token(character_id, asset["version"])
        newly: list[str] = []
        for shot_id in asset["dependents"]:
            if shot_id not in self.dirty_shots:
                self.dirty_shots.append(shot_id)
                newly.append(shot_id)
        return newly

    def clear_dirty(self, shot_id: str) -> None:
        self.dirty_shots = [item for item in self.dirty_shots if item != shot_id]

    def to_dict(self) -> dict:
        return {
            "story_id": self.story_id,
            "assets": self.assets,
            "dirty_shots": self.dirty_shots,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AssetLibrary:
        library = cls(data["story_id"])
        library.assets = data["assets"]
        library.dirty_shots = list(data.get("dirty_shots", []))
        return library
