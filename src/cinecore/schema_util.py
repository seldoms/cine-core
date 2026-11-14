"""读写 JSON，并用仓库里的 JSON Schema 校验。"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

import jsonschema

from cinecore.errors import CineError


def project_root() -> Path:
    env = os.environ.get("CINE_ROOT")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for candidate in [here.parent, *here.parents]:
        if (candidate / "pyproject.toml").is_file() and (candidate / "schemas").is_dir():
            return candidate
    raise CineError("找不到 cine-core 项目根目录。请在仓库里运行，或设置 CINE_ROOT。")


def schema_dir() -> Path:
    return project_root() / "schemas"


@lru_cache(maxsize=32)
def validator(name: str) -> jsonschema.Draft202012Validator:
    path = schema_dir() / name
    schema = json.loads(path.read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(schema)


def validate(document: dict, schema_name: str) -> None:
    errors = sorted(validator(schema_name).iter_errors(document), key=lambda err: list(err.path))
    if not errors:
        return
    first = errors[0]
    where = "/".join(str(part) for part in first.path) or "(根)"
    raise CineError(f"{schema_name} 校验失败：{where}：{first.message}")


def dump_json(path: Path, document: dict, schema_name: str | None = None) -> None:
    if schema_name:
        validate(document, schema_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
