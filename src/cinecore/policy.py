"""无人值守：通过就收下，失败就重拍，反复失败就隔离，循环不停。"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import TypeVar

from cinecore.errors import ResumeInterrupt

T = TypeVar("T")


def next_decision(*, passed: bool, take: int, regeneration: int, policy: dict) -> str:
    if passed:
        return "accept"
    if take < int(policy["takes_per_shot"]):
        return "retry"
    if regeneration < int(policy["max_regenerations"]):
        return "regenerate"
    return "quarantine"


def film_status(*, accepted: int, quarantined: int, policy: dict, crashed: bool = False) -> str:
    if crashed:
        return "quarantined"
    total = accepted + quarantined
    if total == 0 or accepted == 0 and quarantined == 0:
        return "quarantined"
    if quarantined == 0:
        return "completed"
    over_count = quarantined >= int(policy["max_quarantined_shots"])
    over_ratio = quarantined / total >= float(policy["quarantine_shot_ratio"])
    if over_count or over_ratio:
        return "quarantined"
    return "completed_with_quarantine"


def iter_unattended(
    items: list[T],
    run_one: Callable[[T], dict],
    on_error: Callable[[T, Exception], dict],
) -> Iterator[dict]:
    """一条故事抛错只隔离这一条，不让整夜循环停住。"""
    for item in items:
        try:
            yield run_one(item)
        except ResumeInterrupt:
            raise
        except Exception as exc:
            yield on_error(item, exc)
