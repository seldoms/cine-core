"""语料适配器。v0 提供目录 JSON 和 SQLite，接口留给以后的数据库。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from cinecore.errors import CineError
from cinecore.schema_util import validate


class FilesystemCorpus:
    def __init__(self, path: Path):
        self.path = path

    def list_items(self) -> list[dict]:
        if not self.path.is_dir():
            raise CineError(f"语料目录不存在：{self.path}")
        items: list[dict] = []
        seen: set[str] = set()
        for file in sorted(self.path.glob("*.json")):
            document = json.loads(file.read_text(encoding="utf-8"))
            validate(document, "story_item.schema.json")
            if document["id"] in seen:
                raise CineError(f"重复的故事 id：{document['id']}")
            seen.add(document["id"])
            items.append(document)
        items.sort(key=lambda item: item["id"])
        return items

    def get(self, story_id: str) -> dict:
        for item in self.list_items():
            if item["id"] == story_id:
                return item
        raise CineError(f"语料里没有 {story_id}")


class SqliteCorpus:
    def __init__(self, path: Path):
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def init(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS stories (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    raw_text TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    rights_notes TEXT
                )
                """
            )

    def upsert_many(self, items: list[dict]) -> int:
        self.init()
        with self._connect() as connection:
            for item in items:
                validate(item, "story_item.schema.json")
                connection.execute(
                    """
                    INSERT INTO stories (id, title, source_type, raw_text, tags_json, rights_notes)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        title = excluded.title,
                        source_type = excluded.source_type,
                        raw_text = excluded.raw_text,
                        tags_json = excluded.tags_json,
                        rights_notes = excluded.rights_notes
                    """,
                    (
                        item["id"],
                        item["title"],
                        item["source_type"],
                        item["raw_text"],
                        json.dumps(item.get("tags", []), ensure_ascii=False),
                        item.get("rights_notes", ""),
                    ),
                )
        return len(items)

    def list_items(self) -> list[dict]:
        if not self.path.is_file():
            raise CineError(f"找不到 SQLite 语料：{self.path}")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, title, source_type, raw_text, tags_json, rights_notes FROM stories ORDER BY id"
            ).fetchall()
        items = []
        for row in rows:
            item = {
                "id": row["id"],
                "title": row["title"],
                "source_type": row["source_type"],
                "raw_text": row["raw_text"],
                "tags": json.loads(row["tags_json"] or "[]"),
                "rights_notes": row["rights_notes"] or "",
            }
            validate(item, "story_item.schema.json")
            items.append(item)
        return items

    def get(self, story_id: str) -> dict:
        for item in self.list_items():
            if item["id"] == story_id:
                return item
        raise CineError(f"语料里没有 {story_id}")


def open_corpus(path: Path):
    if path.is_dir():
        return FilesystemCorpus(path)
    if path.suffix in {".sqlite", ".db"}:
        return SqliteCorpus(path)
    raise CineError(f"无法识别的语料路径：{path}。请使用目录或 .sqlite 文件。")


def ingest_directory(source: Path, dest: Path) -> int:
    items = FilesystemCorpus(source).list_items()
    if not items:
        raise CineError(f"语料目录里没有 JSON：{source}")
    return SqliteCorpus(dest).upsert_many(items)
