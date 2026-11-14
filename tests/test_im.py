import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from cinecore.cli import main
from cinecore.config import load_config
from cinecore.constants import PROMPT_SECTIONS
from cinecore.errors import CineError
from cinecore.im import IMServer, _handler, handle_message, parse_message
from cinecore.schema_util import project_root

ROOT = project_root()


def _config(tmp_path: Path) -> dict:
    path = tmp_path / "im.yaml"
    path.write_text(
        "paths:\n"
        f"  corpus: {ROOT / 'samples' / 'corpus'}\n"
        f"  runs: {tmp_path / 'runs'}\n"
        f"  ledger: {tmp_path / 'ledger.json'}\n"
        "im:\n"
        f"  inbox: {tmp_path / 'inbox'}\n"
        "duration:\n"
        "  target_seconds: 48\n"
        "  min_seconds: 40\n"
        "  max_seconds: 60\n"
        "dry_run:\n"
        "  inject: []\n",
        encoding="utf-8",
    )
    return load_config(path)


def test_help_and_unknown_command_do_not_start_a_run(tmp_path: Path):
    config = _config(tmp_path)
    reply = handle_message("帮助", config)
    assert "六段提示词" in reply
    assert "dry-run" in reply
    with pytest.raises(CineError):
        parse_message("今天天气不错")
    assert not (tmp_path / "runs").exists()


def test_message_starts_one_story_and_returns_the_six_prompts(tmp_path: Path):
    config = _config(tmp_path)
    reply = handle_message("跑 sample-paper-boat", config)
    assert "dry-run" in reply
    assert "不是成片" in reply
    assert "sample-paper-boat" in reply
    assert "盆里的小舟" in reply
    for name in PROMPT_SECTIONS:
        assert f"[{name}]" in reply
    runs = list((tmp_path / "runs").iterdir())
    assert len(runs) == 1
    log = json.loads((runs[0] / "run_log.json").read_text(encoding="utf-8"))
    assert log["dry_run"] is True
    assert [item["story_id"] for item in log["stories"]] == ["sample-paper-boat"]

    shot = handle_message("提示 2", config)
    assert "[summary]" in shot
    assert "s002" in shot
    status = handle_message("状态", config)
    assert runs[0].name in status
    assert "sample-paper-boat" in status


def test_new_text_and_live_flag(tmp_path: Path):
    config = _config(tmp_path)
    text = (
        "跑\n"
        "标题：巷口那盏灯\n"
        "体裁：有趣\n"
        "执行：正式\n"
        "正文：\n"
        "虚构小品。一个人把一盏不肯随便亮的灯挂到窄巷尽头，守到天明就收走。\n"
    )
    reply = handle_message(text, config)
    assert "不会在 v0 里提交 GPU" in reply
    assert "dry-run" in reply
    assert "[subject_definitions]" in reply
    latest = json.loads((tmp_path / "inbox" / "latest.json").read_text(encoding="utf-8"))
    log = json.loads((Path(latest["run_dir"]) / "run_log.json").read_text(encoding="utf-8"))
    assert log["dry_run"] is True
    assert log["stories"][0]["story_id"].startswith("im-")


def test_task_command_and_webhook_token(tmp_path: Path):
    config_path = tmp_path / "im.yaml"
    config_path.write_text(
        "paths:\n"
        f"  corpus: {ROOT / 'samples' / 'corpus'}\n"
        f"  runs: {tmp_path / 'runs'}\n"
        f"  ledger: {tmp_path / 'ledger.json'}\n"
        "im:\n"
        f"  inbox: {tmp_path / 'inbox'}\n"
        "  token: secret-token\n"
        "duration:\n"
        "  target_seconds: 48\n"
        "  min_seconds: 40\n"
        "  max_seconds: 60\n"
        "dry_run:\n"
        "  inject: []\n",
        encoding="utf-8",
    )
    assert main(["task", "--config", str(config_path), "--text", "帮助"]) == 0
    config = load_config(config_path)
    server = IMServer(("127.0.0.1", 0), _handler(config, "secret-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        denied = Request(
            f"http://127.0.0.1:{port}/tasks",
            data=json.dumps({"text": "跑 sample-paper-boat"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(HTTPError) as caught:
            urlopen(denied, timeout=5)
        assert caught.value.code == 401
        assert not (tmp_path / "runs").exists()

        allowed = Request(
            f"http://127.0.0.1:{port}/tasks",
            data=json.dumps({"text": "跑 sample-last-lantern"}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer secret-token",
            },
        )
        with urlopen(allowed, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["ok"] is True
        assert "sample-last-lantern" in payload["reply"]
        assert "[summary]" in payload["reply"]
    finally:
        server.shutdown()
        server.server_close()
