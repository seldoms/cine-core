"""从一条即时消息发起任务。回复的中心是六段提示词。

v0 的这条入口只做 dry-run。真卡渲染要数小时，不能挂在一条聊天连接上。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cinecore.constants import PROMPT_SECTIONS
from cinecore.corpus import open_corpus
from cinecore.errors import CineError
from cinecore.pipeline import Pipeline
from cinecore.prompts import compile_h3_from_colloquial, compile_qwen_still, infer_still_kind, render_prompt_text
from cinecore.schema_util import dump_json

_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_FIELD = re.compile(r"^(标题|体裁|正文|执行)\s*[:：]\s*(.*)$", re.MULTILINE)
_VERBS = {"跑", "开跑", "做", "run"}
_STILL_VERBS = {"生图", "照片", "出图"}
_CLIP_VERBS = {"生视频", "视频"}
_HELP_WORDS = {"帮助", "help", "?", "？"}
_STATUS_WORDS = {"状态", "status"}
_PROMPT_WORDS = {"提示", "prompt"}

_GENRES = {
    "news": "news",
    "新闻": "news",
    "touching": "touching",
    "感人": "touching",
    "interesting": "interesting",
    "有趣": "interesting",
    "奇闻": "interesting",
}

_LIVE_NOTE = (
    "标了「正式」也不会在 v0 里提交 GPU。"
    "即时消息发起的任务固定是 dry-run，用来先把六段提示跑出来。"
    "真卡渲染要数小时，不能占着一条聊天连接，硬件速度也不会因为换了入口变快。"
)

HELP_TEXT = """\
直接发一条消息就能发起任务，不用分步跑命令。回复的中心是六段提示词。
默认 dry-run：不连 GPU，不产出成片。16GB 卡的速度限制还在。

生图 雨里发呆的人
生视频 她把灯挂上铁钩，看了一眼
生视频 8秒 她把灯挂上铁钩

跑 <已有故事id>
跑
标题：短标题
体裁：新闻 / 感人 / 有趣
正文：
一段你有权使用的虚构或改写文本

状态
状态 <run_id>
提示 <镜头号>
提示 <run_id> <镜头号>
帮助

镜头号写成 s001 或 1。执行：正式 只会被拒绝，不会把任务推进真实队列。\
"""


@dataclass
class Intent:
    kind: str
    run_id: str | None = None
    shot_id: str | None = None
    story_id: str | None = None
    seconds: float | None = None
    title: str = ""
    source_type: str = "interesting"
    body: str = ""
    asked_live: bool = False


def parse_message(text: str) -> Intent:
    raw = (text or "").strip()
    if not raw:
        raise CineError("消息是空的。发「帮助」看用法。")
    lines = raw.splitlines()
    head = lines[0].strip()
    for prefix in ("/cine", "cine-core", "cine"):
        lowered = head.lower()
        if lowered == prefix or lowered.startswith(prefix + " "):
            head = head[len(prefix) :].strip()
            break
    body_lines = lines[1:]
    if not head and body_lines:
        head = body_lines[0].strip()
        body_lines = body_lines[1:]
    verb, _, arg = head.partition(" ")
    verb = verb.strip()
    arg = arg.strip()
    rest = "\n".join(body_lines).strip()
    if arg and rest:
        rest = arg + "\n" + rest
    elif arg:
        rest = arg

    if verb in _HELP_WORDS or (verb in _VERBS and rest in _HELP_WORDS):
        return Intent(kind="help")
    if verb in _STATUS_WORDS:
        return Intent(kind="status", run_id=rest or None)
    if verb in _PROMPT_WORDS:
        return _parse_prompt(rest)
    if verb in _STILL_VERBS:
        if len(rest) < 2:
            raise CineError("生图后面写一句口水话，比如：生图 雨里发呆的人")
        return Intent(kind="still", body=rest)
    if verb in _CLIP_VERBS:
        seconds, spoken = _leading_seconds(rest)
        if len(spoken) < 2:
            raise CineError("生视频后面写一句口水话，比如：生视频 她把灯挂上铁钩")
        return Intent(kind="clip", body=spoken, seconds=seconds)
    if verb not in _VERBS:
        raise CineError("不认识这条指令。发「帮助」看用法。")
    return _parse_run(rest)


def _leading_seconds(text: str) -> tuple[float | None, str]:
    matched = re.match(r"^(\d+(?:\.\d+)?)秒\s*", text or "")
    if not matched:
        return None, text
    return float(matched.group(1)), text[matched.end() :].strip()


def _parse_prompt(rest: str) -> Intent:
    parts = rest.split()
    if len(parts) == 1:
        return Intent(kind="prompt", shot_id=_shot_id(parts[0]))
    if len(parts) == 2:
        return Intent(kind="prompt", run_id=parts[0], shot_id=_shot_id(parts[1]))
    raise CineError("看提示请发：提示 s001，或 提示 <run_id> s001")


def _shot_id(token: str) -> str:
    text = token.strip().lower()
    if text.startswith("s") and text[1:].isdigit():
        return f"s{int(text[1:]):03d}"
    if text.isdigit():
        return f"s{int(text):03d}"
    raise CineError(f"看不懂镜头号：{token}。用 s001 或 1。")


def _parse_run(rest: str) -> Intent:
    if not rest:
        raise CineError("要跑哪一条？发故事 id，或按「帮助」里的格式写标题和正文。")
    form = _split_form(rest)
    if form is None and _ID.match(rest) and "\n" not in rest:
        return Intent(kind="run_existing", story_id=rest, asked_live=False)
    if form is None:
        if len(rest) < 20:
            raise CineError("正文太短。至少写 20 个字，说明这段文字你有权拿来改编。")
        title = rest.splitlines()[0].strip()
        if len(title) > 24:
            title = title[:12]
        return Intent(kind="run_new", title=title or "未命名", body=rest, asked_live=False)
    if form["live"] and not form["body"] and not form["story_id"]:
        return Intent(kind="live_refused", asked_live=True)
    if form["story_id"] and not form["body"]:
        return Intent(kind="run_existing", story_id=form["story_id"], asked_live=form["live"])
    genre = form["genre"] or "有趣"
    if genre not in _GENRES:
        raise CineError("体裁只能是新闻、感人、有趣。")
    body = form["body"]
    if len(body) < 20:
        raise CineError("正文太短。至少写 20 个字，说明这段文字你有权拿来改编。")
    title = form["title"] or body.splitlines()[0].strip()[:12]
    return Intent(
        kind="run_new",
        title=title,
        source_type=_GENRES[genre],
        body=body,
        asked_live=form["live"],
    )


def _split_form(text: str) -> dict | None:
    if not _FIELD.search(text):
        return None
    title = ""
    genre = ""
    story_id = ""
    live = False
    body: list[str] = []
    in_body = False
    for line in text.splitlines():
        matched = _FIELD.match(line.strip())
        if matched:
            key, value = matched.group(1), matched.group(2).strip()
            in_body = key == "正文"
            if key == "标题":
                title = value
            elif key == "体裁":
                genre = value
            elif key == "执行":
                live = value in {"正式", "gpu", "live"}
            elif value:
                body.append(value)
            continue
        if in_body:
            body.append(line)
        elif _ID.match(line.strip()):
            story_id = line.strip()
    return {
        "title": title,
        "genre": genre,
        "body": "\n".join(body).strip(),
        "story_id": story_id,
        "live": live,
    }


def handle_message(text: str, config: dict) -> str:
    intent = parse_message(text)
    if intent.kind == "help":
        return HELP_TEXT
    if intent.kind == "still":
        return "Qwen-Image 静帧提示：\n" + compile_qwen_still(intent.body, kind=infer_still_kind(intent.body))
    if intent.kind == "clip":
        sections = compile_h3_from_colloquial(intent.body, intent.seconds)
        return "MiniMax H3 视频提示：\n" + render_prompt_text(sections)
    if intent.kind == "live_refused":
        return _LIVE_NOTE
    if intent.kind == "status":
        return _status(config, intent.run_id)
    if intent.kind == "prompt":
        return _prompt(config, intent.run_id, intent.shot_id or "")
    if intent.kind == "run_existing":
        reply = _start_existing(config, intent.story_id or "")
    else:
        reply = _start_new(config, intent)
    if intent.asked_live:
        return _LIVE_NOTE + "\n\n" + reply
    return reply


def _paths(config: dict) -> dict[str, Path]:
    return {
        "corpus": Path(config["paths"]["corpus"]),
        "runs": Path(config["paths"]["runs"]),
        "ledger": Path(config["paths"]["ledger"]),
        "inbox": Path(config["im"]["inbox"]),
    }


def _latest_path(config: dict) -> Path:
    return _paths(config)["inbox"] / "latest.json"


def _remember(config: dict, *, run_id: str, run_dir: str, story_id: str) -> None:
    path = _latest_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"run_id": run_id, "run_dir": run_dir, "story_id": story_id}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _load_latest(config: dict, run_id: str | None) -> dict:
    if run_id:
        run_dir = _paths(config)["runs"] / run_id
        if not (run_dir / "run_log.json").is_file():
            raise CineError(f"找不到这次运行：{run_id}")
        log = json.loads((run_dir / "run_log.json").read_text(encoding="utf-8"))
        stories = log.get("stories") or []
        story_id = stories[-1]["story_id"] if stories else ""
        return {"run_id": run_id, "run_dir": str(run_dir), "story_id": story_id}
    path = _latest_path(config)
    if not path.is_file():
        raise CineError("还没有从即时消息发起过任务。")
    return json.loads(path.read_text(encoding="utf-8"))


def _new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%S%fZ")


def _start_existing(config: dict, story_id: str) -> str:
    paths = _paths(config)
    try:
        open_corpus(paths["corpus"]).get(story_id)
    except CineError as exc:
        raise CineError(f"{exc}。新故事请按「帮助」写标题和正文。") from exc
    return _launch(config, source=paths["corpus"], story_id=story_id)


def _start_new(config: dict, intent: Intent) -> str:
    story_id = "im-" + hashlib.sha256(f"{intent.title}\n{intent.body}".encode("utf-8")).hexdigest()[:8]
    item = {
        "id": story_id,
        "title": intent.title,
        "source_type": intent.source_type,
        "raw_text": intent.body,
        "tags": ["im"],
        "rights_notes": "从即时消息提交。请自行确认这段文字可以拿来改编。",
    }
    folder = _paths(config)["inbox"] / story_id
    dump_json(folder / f"{story_id}.json", item, "story_item.schema.json")
    return _launch(config, source=folder, story_id=story_id)


def _launch(config: dict, *, source: Path, story_id: str) -> str:
    paths = _paths(config)
    run_id = _new_run_id()
    run_dir = paths["runs"] / run_id
    result = Pipeline(
        config,
        source=source,
        run_dir=run_dir,
        ledger_path=paths["ledger"],
        mode="unattended",
        dry_run=True,
        requeue=True,
        run_id=run_id,
        only_story_ids={story_id},
    ).run()
    _remember(config, run_id=result["run_id"], run_dir=result["run_dir"], story_id=story_id)
    return _format_start(Path(result["run_dir"]), story_id)


def _format_start(run_dir: Path, story_id: str) -> str:
    log = json.loads((run_dir / "run_log.json").read_text(encoding="utf-8"))
    matched = [item for item in log["stories"] if item["story_id"] == story_id]
    story = matched[-1] if matched else None
    board_path = run_dir / "stories" / story_id / "storyboard.json"
    if story is None or not board_path.is_file():
        quarantine = run_dir / "stories" / story_id / "story_quarantine.json"
        reason = ""
        if quarantine.is_file():
            reason = json.loads(quarantine.read_text(encoding="utf-8")).get("message", "")
        return (
            f"任务没有进入提示词。运行 {run_dir.name}，故事 {story_id}。\n"
            f"{reason or '看 story_quarantine.json。'}"
        )
    screenplay = json.loads((run_dir / "stories" / story_id / "screenplay.json").read_text(encoding="utf-8"))
    board = json.loads(board_path.read_text(encoding="utf-8"))
    first = board["shots"][0]
    header = "\n".join(
        [
            "任务已发起。这是 dry-run：下面是提示词，不是成片。卡的速度不会因为从聊天发起就变快。",
            f"运行 {run_dir.name}",
            f"故事 {story_id}",
            f"片名 {screenplay['title']}",
            f"一句话 {screenplay['logline']}",
            f"计划 {board['planned_duration_sec']:.0f} 秒，{board['shot_count']} 镜。",
            f"结果 {story['status']}，通过 {story['accepted_shots']}，隔离 {story['quarantined_shots']}。",
            "",
            "第 1 镜六段提示：",
            format_shot_prompt(first),
            "",
            "看别的镜头：提示 s002",
        ]
    )
    return header


def format_shot_prompt(shot: dict) -> str:
    lines = [
        f"{shot['shot_id']}  第{shot['ordinal']}镜  {shot['frame_count']}帧  {shot['duration_sec']:.2f}秒  {shot['camera']['framing']}"
    ]
    continuation = shot.get("continuation")
    if continuation:
        lines.append(f"尾帧：同一动作延长自 {continuation['from_shot_id']}")
    else:
        lines.append("尾帧：不用")
    for name in PROMPT_SECTIONS:
        lines.append(f"\n[{name}]\n{shot['video_prompt'][name].strip()}")
    return "\n".join(lines)


def _status(config: dict, run_id: str | None) -> str:
    latest = _load_latest(config, run_id)
    run_dir = Path(latest["run_dir"])
    log = json.loads((run_dir / "run_log.json").read_text(encoding="utf-8"))
    rows = [
        f"运行 {log['run_id']}  {log['status']}  dry_run={log['dry_run']}",
    ]
    if not log["stories"]:
        rows.append("这次没有故事记录。")
    for story in log["stories"]:
        rows.append(
            f"{story['story_id']}  {story['status']}  通过 {story['accepted_shots']}  隔离 {story['quarantined_shots']}"
        )
    rows.append("看提示：提示 s001")
    return "\n".join(rows)


def _prompt(config: dict, run_id: str | None, shot_id: str) -> str:
    latest = _load_latest(config, run_id)
    story_id = latest["story_id"]
    board_path = Path(latest["run_dir"]) / "stories" / story_id / "storyboard.json"
    if not board_path.is_file():
        raise CineError(f"{latest['run_id']} 里没有 {story_id} 的分镜。")
    board = json.loads(board_path.read_text(encoding="utf-8"))
    shot = next((item for item in board["shots"] if item["shot_id"] == shot_id), None)
    if shot is None:
        raise CineError(f"没有镜头 {shot_id}。这部片子一共 {board['shot_count']} 镜。")
    return f"运行 {latest['run_id']}\n故事 {story_id}\n" + format_shot_prompt(shot)


class IMServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(config: dict, *, host: str | None = None, port: int | None = None) -> None:
    bind_host = host or str(config["im"]["host"])
    bind_port = int(port or config["im"]["port"])
    token = str(config["im"].get("token") or "")
    server = IMServer((bind_host, bind_port), _handler(config, token))
    print(
        f"cine im 监听 http://{bind_host}:{bind_port}/tasks\n"
        "把聊天工具里的用户原文 POST 成 {\"text\": \"...\"}，再把返回的 reply 发回去。\n"
        "默认只给本机。空 token 不要暴露到公网。v0 只做 dry-run。"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


def _handler(config: dict, token: str):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path.split("?", 1)[0] != "/health":
                self._json(404, {"error": "not_found"})
                return
            self._json(200, {"ok": True, "service": "cine-core", "dry_run_only": True})

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path not in {"/tasks", "/im"}:
                self._json(404, {"error": "not_found"})
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length else b""
            try:
                text, payload_token = _read_body(raw, self.headers.get("Content-Type", ""))
            except CineError as exc:
                self._json(400, {"ok": False, "reply": str(exc)})
                return
            if not _token_ok(self.headers.get("Authorization", ""), payload_token, token):
                self._json(401, {"error": "unauthorized"})
                return
            if not text.strip():
                self._json(400, {"error": "empty", "reply": "消息是空的。发「帮助」看用法。"})
                return
            try:
                reply = handle_message(text, config)
            except CineError as exc:
                self._json(200, {"ok": False, "reply": str(exc)})
                return
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"ok": False, "reply": f"任务没有发起：{type(exc).__name__}"})
                return
            self._json(200, {"ok": True, "reply": reply})

        def log_message(self, fmt: str, *args) -> None:
            return

        def _json(self, code: int, payload: dict) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def _read_body(raw: bytes, content_type: str) -> tuple[str, str]:
    if not raw:
        return "", ""
    if "application/json" in content_type or raw[:1] in (b"{", b"["):
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise CineError("请求体不是 JSON。") from exc
        if not isinstance(payload, dict):
            raise CineError("请求体必须是对象，例如 {\"text\": \"帮助\"}。")
        text = payload.get("text") or payload.get("message") or ""
        token = str(payload.get("token") or "")
        return str(text), token
    return raw.decode("utf-8"), ""


def _token_ok(header: str, payload_token: str, expected: str) -> bool:
    if not expected:
        return True
    supplied = payload_token
    if header.lower().startswith("bearer "):
        supplied = header[7:].strip()
    if len(supplied) != len(expected):
        return False
    return hmac.compare_digest(supplied, expected)
