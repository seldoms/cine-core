"""运行进度、事件和跨夜台账。进程中断后可以按已完成镜头续跑。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from cinecore.errors import CineError
from cinecore.schema_util import dump_json


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Ledger:
    def __init__(self, path: Path):
        self.path = path
        if path.is_file():
            self.data = json.loads(path.read_text(encoding="utf-8"))
        else:
            self.data = {"stories": {}}

    def is_blocked(self, story_id: str) -> bool:
        return story_id in self.data.get("stories", {})

    def mark(self, story_id: str, status: str, run_id: str) -> None:
        self.data.setdefault("stories", {})[story_id] = {
            "status": status,
            "run_id": run_id,
            "at": iso_now(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class RunStore:
    def __init__(self, run_dir: Path, state: dict, events: list[dict]):
        self.run_dir = run_dir
        self.state = state
        self.events = events

    @classmethod
    def create(cls, run_dir: Path, *, run_id: str, mode: str, dry_run: bool) -> RunStore:
        if run_dir.exists() and any(run_dir.iterdir()):
            raise CineError(f"运行目录已存在：{run_dir}")
        run_dir.mkdir(parents=True, exist_ok=True)
        state = {
            "run_id": run_id,
            "mode": mode,
            "dry_run": dry_run,
            "status": "running",
            "started_at": iso_now(),
            "finished_at": None,
            "completed_story_ids": [],
            "in_progress_story_id": None,
            "results": [],
        }
        store = cls(run_dir, state, [])
        store.save_state()
        return store

    @classmethod
    def open(cls, run_dir: Path) -> RunStore:
        state_path = run_dir / "state.json"
        if not state_path.is_file():
            raise CineError(f"这个目录没有可续跑的 state.json：{run_dir}")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        events = []
        events_path = run_dir / "events.jsonl"
        if events_path.is_file():
            for line in events_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    events.append(json.loads(line))
        return cls(run_dir, state, events)

    def save_state(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "state.json").write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def event(
        self,
        message: str,
        *,
        level: str = "info",
        story_id: str | None = None,
        shot_id: str | None = None,
    ) -> None:
        record = {"at": iso_now(), "level": level, "message": message}
        if story_id:
            record["story_id"] = story_id
        if shot_id:
            record["shot_id"] = shot_id
        self.events.append(record)
        with (self.run_dir / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def write_log(self, *, status: str, finished_at: str | None) -> dict:
        self.state["status"] = status
        self.state["finished_at"] = finished_at
        document = {
            "run_id": self.state["run_id"],
            "mode": self.state["mode"],
            "dry_run": self.state["dry_run"],
            "status": status,
            "started_at": self.state["started_at"],
            "finished_at": finished_at,
            "stories": self.state["results"],
            "events": self.events,
        }
        dump_json(self.run_dir / "run_log.json", document, "run_log.schema.json")
        self.save_state()
        return document
