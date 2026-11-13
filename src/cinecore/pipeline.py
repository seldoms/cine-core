"""阶段图：摄入、改编、素材、渲染、审核、合成、循环。"""

from __future__ import annotations

import json
from pathlib import Path

from cinecore.adapt import adapt_story, assert_not_verbatim, prompt_context, regenerate_shot
from cinecore.assemble import write_assembly
from cinecore.audit import audit_keyframe, audit_video, failed_names, report_passed
from cinecore.backends.comfyui import ComfyUIClient
from cinecore.backends.dry_run import DryRunBackend, injections_from_config
from cinecore.constants import FPS, LATENT_NODE, STILL_HEIGHT, STILL_WIDTH
from cinecore.corpus import open_corpus
from cinecore.errors import CineError, ResumeInterrupt
from cinecore.library import AssetLibrary, face_token
from cinecore.policy import film_status, iter_unattended, next_decision
from cinecore.prompts import still_prompt
from cinecore.schema_util import dump_json
from cinecore.store import Ledger, RunStore
from cinecore.workflows import build_video_workflow, stable_seed


def _names(bible: dict) -> dict[str, str]:
    return {character["id"]: character["name"] for character in bible["characters"]}


def _load_progress(path: Path) -> dict:
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"finished_shot_ids": [], "results": []}


def _save_progress(path: Path, progress: dict) -> None:
    dump_json(path, progress)


class Pipeline:
    def __init__(
        self,
        config: dict,
        *,
        source: Path,
        run_dir: Path,
        ledger_path: Path,
        mode: str | None = None,
        dry_run: bool | None = None,
        requeue: bool = False,
        limit: int | None = None,
        run_id: str | None = None,
        resume: bool = False,
        supervise_block: bool = False,
        stop_after_completed_shots: int | None = None,
        fail_story_ids: set[str] | None = None,
        only_story_ids: set[str] | None = None,
    ):
        self.config = config
        self.source = source
        self.ledger = Ledger(ledger_path)
        self.requeue = requeue
        self.limit = limit
        self.supervise_block = supervise_block
        self.stop_after_completed_shots = stop_after_completed_shots
        self.fail_story_ids = fail_story_ids or set()
        self.only_story_ids = only_story_ids
        self._done_this_call = 0
        if resume:
            self.store = RunStore.open(run_dir)
            snapshot = run_dir / "config.snapshot.json"
            if snapshot.is_file():
                self.config = json.loads(snapshot.read_text(encoding="utf-8"))
            self.dry_run = self.store.state["dry_run"] if dry_run is None else dry_run
            self.mode = mode or self.store.state["mode"]
            self.config["autonomy"]["mode"] = self.mode
        else:
            self.mode = mode or config["autonomy"]["mode"]
            self.config["autonomy"]["mode"] = self.mode
            backend_name = self.config["render"]["backend"]
            self.dry_run = backend_name == "dry_run" if dry_run is None else dry_run
            self._lock_hardware(self.config)
            self._reject_live_comfy()
            chosen_id = run_id or run_dir.name
            self.store = RunStore.create(
                run_dir,
                run_id=chosen_id,
                mode=self.mode,
                dry_run=self.dry_run,
            )
            dump_json(run_dir / "config.snapshot.json", self.config)
        self.run_dir = self.store.run_dir
        self._lock_hardware(self.config)
        self._reject_live_comfy()
        self.backend = self._make_backend()

    def _reject_live_comfy(self) -> None:
        if not self.dry_run and self.config["render"]["backend"] == "comfyui":
            raise CineError(
                "v0 还不会在批处理里向 ComfyUI 提交并收回成片。请使用 --dry-run。"
                "工作流和 HTTP 客户端已经留下，供下一版接上。"
            )

    @staticmethod
    def _lock_hardware(config: dict) -> None:
        video = config["render"]["video"]
        still = config["render"]["still"]
        if (video["width"], video["height"]) != (416, 736):
            raise CineError("v0 的视频锁定在 416×736。这是 16GB 上的实测尺寸，不是画质开关。")
        if (still["width"], still["height"]) != (768, 1344) or still["latent_node"] != LATENT_NODE:
            raise CineError("v0 的静帧锁定在 768×1344，且 latent 必须来自 EmptySD3LatentImage。")

    def _make_backend(self):
        if self.dry_run or self.config["render"]["backend"] == "dry_run":
            injections = injections_from_config(self.config) if self.dry_run else []
            return DryRunBackend(injections)
        return ComfyUIClient(self.config["comfyui"]["host"])

    def run(self) -> dict:
        corpus = open_corpus(self.source)
        items = corpus.list_items()
        done = set(self.store.state["completed_story_ids"])
        pending = [item for item in items if item["id"] not in done]
        if self.only_story_ids is not None:
            missing = sorted(self.only_story_ids.difference(item["id"] for item in items))
            if missing:
                raise CineError("语料里没有：" + "、".join(missing))
            pending = [item for item in pending if item["id"] in self.only_story_ids]
        if not self.requeue:
            pending = [item for item in pending if not self.ledger.is_blocked(item["id"])]
        if self.limit is not None:
            pending = pending[: self.limit]
        self.store.event(
            f"开始循环，待处理 {len(pending)} 条，模式 {self.mode}，dry_run={self.dry_run}"
        )
        if not pending:
            self.store.event("没有待处理的故事。已完成或已隔离的条目在台账里。用 --requeue 可以重跑。")
        try:
            for result in iter_unattended(pending, self.run_story, self.quarantine_story):
                self._finish_story(result)
        except ResumeInterrupt:
            self.store.write_log(status="running", finished_at=None)
            return {
                "status": "running",
                "run_dir": str(self.run_dir),
                "run_id": self.store.state["run_id"],
            }
        self.store.write_log(status="completed", finished_at=_finished_now())
        return {
            "status": "completed",
            "run_dir": str(self.run_dir),
            "run_id": self.store.state["run_id"],
            "stories": len(self.store.state["results"]),
        }

    def _finish_story(self, result: dict) -> None:
        self.store.state["results"].append(result)
        self.store.state["completed_story_ids"].append(result["story_id"])
        self.store.state["in_progress_story_id"] = None
        self.ledger.mark(result["story_id"], result["status"], self.store.state["run_id"])
        self.store.event(
            f"故事结束：{result['status']}，通过 {result['accepted_shots']}，隔离 {result['quarantined_shots']}",
            story_id=result["story_id"],
            level="warning" if result["quarantined_shots"] else "info",
        )
        self.store.write_log(status="running", finished_at=None)

    def quarantine_story(self, item: dict, exc: Exception) -> dict:
        story_dir = self.run_dir / "stories" / item["id"]
        story_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "story_id": item["id"],
            "reason": "story_error",
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
        dump_json(story_dir / "story_quarantine.json", payload)
        self.store.event(
            f"整条故事隔离：{type(exc).__name__}：{exc}",
            level="error",
            story_id=item["id"],
        )
        return {
            "story_id": item["id"],
            "status": "quarantined",
            "accepted_shots": 0,
            "quarantined_shots": 0,
            "duration_sec": 0,
            "output": f"stories/{item['id']}/story_quarantine.json",
        }

    def run_story(self, item: dict) -> dict:
        if item["id"] in self.fail_story_ids:
            raise RuntimeError(f"按测试要求让 {item['id']} 失败")
        story_dir = self.run_dir / "stories" / item["id"]
        story_dir.mkdir(parents=True, exist_ok=True)
        self.store.state["in_progress_story_id"] = item["id"]
        self.store.save_state()
        self.store.event("开始故事", story_id=item["id"])
        dump_json(story_dir / "source.json", item, "story_item.schema.json")
        screenplay, bible, storyboard = adapt_story(
            item,
            self.config["duration"],
            backend=self.config["adapt"]["backend"],
        )
        window = int(self.config["audit"].get("verbatim_window", 18))
        assert_not_verbatim(item, screenplay, bible, storyboard, window=window)
        dump_json(story_dir / "screenplay.json", screenplay, "screenplay.schema.json")
        dump_json(story_dir / "bible.json", bible, "bible.schema.json")
        dump_json(story_dir / "storyboard.json", storyboard, "storyboard.schema.json")
        library = self._build_stills(bible, storyboard)
        progress_path = story_dir / "progress.json"
        progress = _load_progress(progress_path)
        finished = set(progress["finished_shot_ids"])
        by_id = {shot["shot_id"]: shot for shot in storyboard["shots"]}
        for shot in storyboard["shots"]:
            self._ensure_keyframe(shot, bible, library, story_dir)
        dump_json(story_dir / "library.json", library.to_dict())
        self._pause(item["id"], "after_plan_and_keyframes")
        for shot in storyboard["shots"]:
            if shot["shot_id"] in finished and shot["shot_id"] not in library.dirty_shots:
                continue
            outcome = self._render_shot(shot, bible, library, story_dir, by_id)
            self._record_shot(progress, outcome)
            finished.add(shot["shot_id"])
            library.clear_dirty(shot["shot_id"])
            _save_progress(progress_path, progress)
            self.store.save_state()
            self._done_this_call += 1
            if (
                self.stop_after_completed_shots is not None
                and self._done_this_call >= self.stop_after_completed_shots
            ):
                self.store.event("按停止点记下进度，可续跑", story_id=item["id"], shot_id=shot["shot_id"])
                raise ResumeInterrupt()
        self._pause(item["id"], "after_audit")
        results = {item["shot_id"]: item for item in progress["results"]}
        ordered = [results[shot["shot_id"]] for shot in storyboard["shots"]]
        accepted = sum(1 for item in ordered if item["status"] == "accepted")
        quarantined = sum(1 for item in ordered if item["status"] == "quarantined")
        status = film_status(
            accepted=accepted,
            quarantined=quarantined,
            policy=self.config["autonomy"],
        )
        sidecar = write_assembly(
            story_dir,
            story_id=item["id"],
            run_id=self.store.state["run_id"],
            status=status,
            dry_run=self.dry_run,
            planned_duration_sec=storyboard["planned_duration_sec"],
            outcomes=ordered,
            names=_names(bible),
            loudness_lufs=float(self.config["assemble"]["loudness_lufs"]),
            true_peak_dbtp=float(self.config["assemble"]["true_peak_dbtp"]),
            upscale_requested=bool(self.config["render"]["upscale_720p"]),
        )
        return {
            "story_id": item["id"],
            "status": status,
            "accepted_shots": accepted,
            "quarantined_shots": quarantined,
            "duration_sec": sidecar["accepted_duration_sec"],
            "output": f"stories/{item['id']}/assemble/film_sidecar.json",
        }

    def _build_stills(self, bible: dict, storyboard: dict) -> AssetLibrary:
        library = AssetLibrary(bible["story_id"])
        for character in bible["characters"]:
            if not character["on_screen"]:
                continue
            for variant in character["wardrobe_variants"]:
                asset_id = f"char-{character['id']}-still-{variant['id']}"
                dependents = [
                    shot["shot_id"]
                    for shot in storyboard["shots"]
                    if character["id"] in shot["subject_ids"]
                    and shot["wardrobe_variant_id"] == variant["id"]
                ]
                prompt = still_prompt(
                    kind="角色静帧",
                    text=f"{character['name']}。{character['description']} {variant['description']}",
                )
                token = face_token(character["id"], 1)
                still = self.backend.render_still(
                    {
                        "asset_id": asset_id,
                        "prompt": prompt,
                        "face_token": token,
                        "seed": stable_seed(bible["story_id"], asset_id),
                        "node_classes": self.config["comfyui"]["node_classes"],
                    }
                )
                library.add(
                    asset_id=asset_id,
                    kind="character_still",
                    width=still["width"],
                    height=still["height"],
                    latent_node=still["latent_node"],
                    face_token_value=token,
                    dependents=dependents,
                    prompt=prompt,
                    stub=True,
                )
                library.assets[asset_id]["workflow"] = still["workflow"]
        for location in bible["locations"]:
            asset_id = f"loc-{location['id']}-still"
            dependents = [
                shot["shot_id"] for shot in storyboard["shots"] if shot["location_id"] == location["id"]
            ]
            prompt = still_prompt(kind="场景静帧", text=f"{location['name']}。{location['description']}")
            still = self.backend.render_still(
                {
                    "asset_id": asset_id,
                    "prompt": prompt,
                    "face_token": None,
                    "seed": stable_seed(bible["story_id"], asset_id),
                    "node_classes": self.config["comfyui"]["node_classes"],
                }
            )
            library.add(
                asset_id=asset_id,
                kind="location_still",
                width=still["width"],
                height=still["height"],
                latent_node=still["latent_node"],
                face_token_value=None,
                dependents=dependents,
                prompt=prompt,
                stub=True,
            )
            library.assets[asset_id]["workflow"] = still["workflow"]
        return library

    def _ensure_keyframe(self, shot: dict, bible: dict, library: AssetLibrary, story_dir: Path) -> dict | None:
        asset_id = f"kf-{shot['shot_id']}"
        if asset_id in library.assets:
            return library.assets[asset_id]
        character_id = shot["subject_ids"][0]
        still_id = f"char-{character_id}-still-{shot['wardrobe_variant_id']}"
        expected = library.get(still_id)["face_token"]
        takes = int(self.config["autonomy"]["takes_per_shot"])
        shot_dir = story_dir / "shots" / shot["shot_id"]
        for take in range(1, takes + 1):
            still = self.backend.render_still(
                {
                    "asset_id": asset_id,
                    "prompt": shot["first_frame"]["prompt"],
                    "face_token": expected,
                    "framing": shot["camera"]["framing"],
                    "seed": stable_seed(shot["shot_id"], "keyframe", str(take)),
                    "node_classes": self.config["comfyui"]["node_classes"],
                }
            )
            checks = audit_keyframe(still, expected_face_token=expected)
            passed = report_passed(checks)
            decision = "accept" if passed else ("retry" if take < takes else "quarantine")
            dump_json(
                shot_dir / "keyframe_audit.json",
                self._audit_document(
                    shot,
                    take=take,
                    regeneration=0,
                    stage="keyframe",
                    passed=passed,
                    decision=decision,
                    checks=checks,
                    renderer_exit_code=0,
                ),
                "audit_report.schema.json",
            )
            if passed:
                library.add(
                    asset_id=asset_id,
                    kind="keyframe",
                    width=STILL_WIDTH,
                    height=STILL_HEIGHT,
                    latent_node=LATENT_NODE,
                    face_token_value=expected,
                    dependents=[shot["shot_id"]],
                    prompt=shot["first_frame"]["prompt"],
                    stub=True,
                )
                library.assets[asset_id]["workflow"] = still["workflow"]
                library.assets[asset_id]["approved"] = True
                dump_json(
                    shot_dir / "keyframe.json",
                    {
                        "asset_id": asset_id,
                        "shot_id": shot["shot_id"],
                        "version": 1,
                        "width": STILL_WIDTH,
                        "height": STILL_HEIGHT,
                        "latent_node": LATENT_NODE,
                        "face_token": expected,
                        "approved": True,
                        "stub": True,
                        "prompt": shot["first_frame"]["prompt"],
                    },
                )
                return library.assets[asset_id]
        return None

    def _render_shot(
        self,
        shot: dict,
        bible: dict,
        library: AssetLibrary,
        story_dir: Path,
        by_id: dict,
    ) -> dict:
        keyframe = library.assets.get(f"kf-{shot['shot_id']}")
        if not keyframe or not keyframe.get("approved"):
            return self._quarantine_shot(
                story_dir,
                shot,
                defects=["keyframe"],
                attempts=int(self.config["autonomy"]["takes_per_shot"]),
            )
        context = prompt_context(bible, shot)
        current = shot
        policy = self.config["autonomy"]
        takes = int(policy["takes_per_shot"])
        decision = "quarantine"
        last_checks: list[dict] = []
        attempts = 0
        while True:
            regeneration = int(current["regeneration"])
            for take in range(1, takes + 1):
                attempts += 1
                job = self._compile_job(current, library, keyframe, by_id)
                artifact = self.backend.render_take(job, take)
                checks = audit_video(job, artifact, self.config["audit"])
                passed = report_passed(checks)
                decision = next_decision(
                    passed=passed,
                    take=take,
                    regeneration=regeneration,
                    policy=policy,
                )
                report = self._audit_document(
                    current,
                    take=take,
                    regeneration=regeneration,
                    stage="video",
                    passed=passed,
                    decision=decision,
                    checks=checks,
                    renderer_exit_code=artifact.get("renderer_exit_code", 0),
                )
                take_dir = story_dir / "shots" / shot["shot_id"] / "takes" / f"r{regeneration}_t{take}"
                dump_json(take_dir / "take.json", artifact)
                dump_json(take_dir / "audit.json", report, "audit_report.schema.json")
                dump_json(story_dir / "shots" / shot["shot_id"] / "job.json", job, "shot_job.schema.json")
                last_checks = checks
                if decision == "accept":
                    return {
                        "shot_id": shot["shot_id"],
                        "ordinal": shot["ordinal"],
                        "status": "accepted",
                        "take": take,
                        "regeneration": regeneration,
                        "duration_sec": artifact["duration_sec"],
                        "dialogue": current.get("dialogue") or [],
                        "defects": [],
                    }
                if decision == "retry":
                    continue
                break
            if decision == "regenerate":
                current = regenerate_shot(current, last_checks, context)
                dump_json(
                    story_dir / "shots" / shot["shot_id"] / f"regenerated_r{current['regeneration']}.json",
                    {"shot_id": shot["shot_id"], "regeneration": current["regeneration"], "action": current["action"]},
                )
                continue
            return self._quarantine_shot(
                story_dir,
                shot,
                defects=failed_names(last_checks),
                attempts=attempts,
            )

    def _quarantine_shot(self, story_dir: Path, shot: dict, *, defects: list[str], attempts: int) -> dict:
        record = {
            "story_id": story_dir.name,
            "shot_id": shot["shot_id"],
            "ordinal": shot["ordinal"],
            "reason": "audit_failed",
            "defects": defects,
            "attempts": attempts,
        }
        dump_json(story_dir / "quarantine" / f"{shot['shot_id']}.json", record)
        self.store.event(
            f"镜头隔离：{', '.join(defects) or '未知'}",
            level="warning",
            story_id=story_dir.name,
            shot_id=shot["shot_id"],
        )
        return {
            "shot_id": shot["shot_id"],
            "ordinal": shot["ordinal"],
            "status": "quarantined",
            "take": None,
            "regeneration": int(shot.get("regeneration", 0)),
            "duration_sec": 0,
            "dialogue": [],
            "defects": defects,
        }

    def _compile_job(self, shot: dict, library: AssetLibrary, keyframe: dict, by_id: dict) -> dict:
        references = []
        for ref in shot["reference_plan"]:
            asset = library.get(ref["ref_id"])
            references.append(
                {
                    "ref_id": ref["ref_id"],
                    "role": ref["role"],
                    "asset_id": asset["asset_id"],
                    "version": asset["version"],
                }
            )
        character_id = shot["subject_ids"][0]
        still = library.get(f"char-{character_id}-still-{shot['wardrobe_variant_id']}")
        continuation = None
        if shot.get("continuation"):
            previous = by_id[shot["continuation"]["from_shot_id"]]
            continuation = {
                "mode": "same_action_extend",
                "from_shot_id": previous["shot_id"],
                "same_scene": previous["scene_id"] == shot["scene_id"],
                "same_action_group": previous["action_group"] == shot["action_group"],
            }
        workflow = build_video_workflow(
            shot["video_prompt"],
            frames=shot["frame_count"],
            seed=stable_seed(shot["shot_id"], str(shot["regeneration"])),
            references=[{"asset_id": item["asset_id"], "role": item["role"]} for item in references],
            first_frame_name=keyframe["asset_id"],
            node_classes=self.config["comfyui"]["node_classes"],
            filename_prefix=f"cinecore-{shot['shot_id']}",
        )
        return {
            "job_id": f"{shot['shot_id']}-r{shot['regeneration']}",
            "run_id": self.store.state["run_id"],
            "story_id": library.story_id,
            "shot_id": shot["shot_id"],
            "ordinal": shot["ordinal"],
            "regeneration": shot["regeneration"],
            "scene_id": shot["scene_id"],
            "action_group": shot["action_group"],
            "frame_count": shot["frame_count"],
            "fps": FPS,
            "width": self.config["render"]["video"]["width"],
            "height": self.config["render"]["video"]["height"],
            "takes_requested": int(self.config["autonomy"]["takes_per_shot"]),
            "backend": "dry_run" if self.dry_run else "comfyui",
            "dialogue": shot.get("dialogue") or [],
            "camera": shot["camera"],
            "references": references,
            "first_frame": {
                "asset_id": keyframe["asset_id"],
                "version": keyframe["version"],
                "width": STILL_WIDTH,
                "height": STILL_HEIGHT,
                "latent_node": LATENT_NODE,
                "approved": True,
            },
            "character_face_token": still["face_token"],
            "continuation": continuation,
            "video_prompt": shot["video_prompt"],
            "workflow": {"keyframe": keyframe["workflow"], "video": workflow},
        }

    def _audit_document(
        self,
        shot: dict,
        *,
        take: int,
        regeneration: int,
        stage: str,
        passed: bool,
        decision: str,
        checks: list[dict],
        renderer_exit_code: int | None,
    ) -> dict:
        return {
            "report_id": f"audit-{shot['shot_id']}-r{regeneration}-t{take}-{stage}",
            "run_id": self.store.state["run_id"],
            "story_id": self.store.state["in_progress_story_id"],
            "shot_id": shot["shot_id"],
            "take": take,
            "regeneration": regeneration,
            "stage": stage,
            "passed": passed,
            "decision": decision,
            "renderer_exit_code": renderer_exit_code,
            "renderer_exit_ignored": True,
            "checks": checks,
        }

    def _record_shot(self, progress: dict, outcome: dict) -> None:
        progress["results"] = [
            item for item in progress["results"] if item["shot_id"] != outcome["shot_id"]
        ]
        progress["results"].append(outcome)
        if outcome["shot_id"] not in progress["finished_shot_ids"]:
            progress["finished_shot_ids"].append(outcome["shot_id"])

    def _pause(self, story_id: str, stage: str) -> None:
        if self.mode != "supervised":
            return
        pause_dir = self.run_dir / "pauses"
        pause_dir.mkdir(parents=True, exist_ok=True)
        blocked = self.supervise_block and not self.dry_run
        dump_json(
            pause_dir / f"{story_id}-{stage}.json",
            {"story_id": story_id, "stage": stage, "blocked": blocked},
        )
        self.store.event(f"监督模式停点：{stage}", story_id=story_id)
        if not blocked:
            self.store.event("停点已记录，dry-run 或不阻塞时继续", story_id=story_id)
            return
        flag = self.run_dir / "CONTINUE"
        import time

        while not flag.exists():
            time.sleep(1)
        flag.unlink()


def _finished_now() -> str:
    from cinecore.store import iso_now

    return iso_now()
