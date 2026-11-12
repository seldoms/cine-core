"""命令行：ingest、plan、run、status、audit。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from cinecore.adapt import adapt_story, assert_not_verbatim
from cinecore.config import load_config, load_dotenv, resolve_config_path
from cinecore.corpus import ingest_directory, open_corpus
from cinecore.errors import CineError
from cinecore.pipeline import Pipeline
from cinecore.schema_util import dump_json, validate


def _new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%S%fZ")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cine",
        description="cine-core：约五分钟 AI 短片的无人值守研究内核。v0 是可运行的骨架，不是电影级成片器。",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="校验语料目录并写入 SQLite")
    ingest.add_argument("--source", required=True, help="JSON 语料目录")
    ingest.add_argument("--dest", required=True, help="SQLite 输出路径")

    plan = sub.add_parser("plan", help="只改编：剧本、视觉圣经、分镜")
    plan.add_argument("--story", required=True, help="故事 id")
    plan.add_argument("--source", required=True, help="语料目录或 SQLite")
    plan.add_argument("--out", required=True, help="输出目录")
    plan.add_argument("--config", default=None)

    run = sub.add_parser("run", help="跑制作循环。退出码 0 只表示循环走完，不表示成片通过。")
    run.add_argument("--mode", choices=["unattended", "supervised"], default=None)
    run.add_argument("--dry-run", action="store_true", help="不连 GPU，走完全部阶段并写模拟产物")
    run.add_argument("--config", default=None)
    run.add_argument("--source", default=None)
    run.add_argument("--out", default=None, help="运行根目录，默认取配置里的 paths.runs")
    run.add_argument("--ledger", default=None)
    run.add_argument("--run-id", default=None)
    run.add_argument("--limit", type=int, default=None)
    run.add_argument("--requeue", action="store_true", help="忽略台账，重跑已经完成或隔离的故事")
    run.add_argument("--resume", default=None, help="已有运行目录，从上次进度继续")
    run.add_argument(
        "--supervise-block",
        action="store_true",
        help="监督模式且不是 dry-run 时，停在停点直到目录里出现 CONTINUE",
    )

    status = sub.add_parser("status", help="查看一次运行的进度")
    status.add_argument("--run-dir", required=True)

    audit = sub.add_parser(
        "audit",
        help="汇总审核报告。退出码 0 只表示汇总写完，不代表镜头通过。",
    )
    audit.add_argument("--run-dir", required=True)
    return parser


def _cmd_ingest(args: argparse.Namespace) -> int:
    count = ingest_directory(Path(args.source), Path(args.dest))
    print(f"已写入 {count} 条到 {args.dest}")
    return 0


def _cmd_plan(args: argparse.Namespace) -> int:
    load_dotenv()
    config = load_config(resolve_config_path(args.config))
    item = open_corpus(Path(args.source)).get(args.story)
    screenplay, bible, storyboard = adapt_story(
        item,
        config["duration"],
        backend=config["adapt"]["backend"],
    )
    assert_not_verbatim(item, screenplay, bible, storyboard, window=int(config["audit"]["verbatim_window"]))
    out = Path(args.out)
    dump_json(out / "screenplay.json", screenplay, "screenplay.schema.json")
    dump_json(out / "bible.json", bible, "bible.schema.json")
    dump_json(out / "storyboard.json", storyboard, "storyboard.schema.json")
    print(
        f"已写出分镜 {storyboard['shot_count']} 镜，计划 {storyboard['planned_duration_sec']:.1f} 秒 → {out}"
    )
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    load_dotenv()
    config = load_config(resolve_config_path(args.config))
    if args.resume:
        run_dir = Path(args.resume)
        source = Path(args.source or config["paths"]["corpus"])
        ledger = Path(args.ledger or config["paths"]["ledger"])
        pipeline = Pipeline(
            config,
            source=source,
            run_dir=run_dir,
            ledger_path=ledger,
            mode=args.mode,
            dry_run=True if args.dry_run else None,
            requeue=args.requeue,
            limit=args.limit,
            resume=True,
            supervise_block=args.supervise_block,
        )
    else:
        run_id = args.run_id or _new_run_id()
        out = Path(args.out or config["paths"]["runs"])
        run_dir = out / run_id
        source = Path(args.source or config["paths"]["corpus"])
        ledger = Path(args.ledger or config["paths"]["ledger"])
        dry_run = True if args.dry_run else None
        pipeline = Pipeline(
            config,
            source=source,
            run_dir=run_dir,
            ledger_path=ledger,
            mode=args.mode,
            dry_run=dry_run,
            requeue=args.requeue,
            limit=args.limit,
            run_id=run_id,
            supervise_block=args.supervise_block,
        )
    result = pipeline.run()
    print(
        f"run_id={result['run_id']} status={result['status']} run_dir={result['run_dir']}\n"
        "退出码 0 只表示循环结束或进度已保存，不表示成片通过。请看 run_log.json 里每条故事的 status。"
    )
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    state_path = run_dir / "state.json"
    if not state_path.is_file():
        raise CineError(f"找不到 state.json：{run_dir}")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    print(f"run_id={state['run_id']} status={state['status']} mode={state['mode']} dry_run={state['dry_run']}")
    print(f"已完成故事：{len(state.get('completed_story_ids', []))}")
    if state.get("in_progress_story_id"):
        print(f"进行中：{state['in_progress_story_id']}")
    for result in state.get("results", []):
        print(
            f"- {result['story_id']} {result['status']} "
            f"通过 {result['accepted_shots']} 隔离 {result['quarantined_shots']} "
            f"{result['duration_sec']:.1f}s"
        )
    return 0


def _cmd_audit(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        raise CineError(f"找不到运行目录：{run_dir}")
    reports = []
    for path in sorted(run_dir.glob("stories/*/shots/*/takes/*/audit.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        validate(document, "audit_report.schema.json")
        reports.append(document)
    for path in sorted(run_dir.glob("stories/*/shots/*/keyframe_audit.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        validate(document, "audit_report.schema.json")
        reports.append(document)
    quarantined = [path for path in run_dir.glob("stories/*/quarantine/*.json")]
    failed_checks: dict[str, int] = {}
    passed = 0
    failed = 0
    for report in reports:
        if report["passed"]:
            passed += 1
        else:
            failed += 1
            for check in report["checks"]:
                if not check["passed"] and check["severity"] == "error":
                    failed_checks[check["name"]] = failed_checks.get(check["name"], 0) + 1
    summary = {
        "renderer_exit_code_is_not_acceptance": True,
        "command_exit_0_is_not_acceptance": True,
        "reports": len(reports),
        "passed": passed,
        "failed": failed,
        "quarantine_files": len(quarantined),
        "failed_checks": failed_checks,
    }
    dump_json(run_dir / "audit_summary.json", summary)
    print("审核汇总写完。退出码 0 不是通过；看 passed 字段和隔离目录。")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    commands = {
        "ingest": _cmd_ingest,
        "plan": _cmd_plan,
        "run": _cmd_run,
        "status": _cmd_status,
        "audit": _cmd_audit,
    }
    try:
        return commands[args.command](args)
    except CineError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("已中断。若进度已写入，可用 cine run --resume 继续。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
