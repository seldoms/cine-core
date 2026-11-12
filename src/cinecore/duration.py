"""把计划时长吸附到 17n+5 帧网格。"""

from __future__ import annotations

from cinecore.constants import FPS, GRID_N_MAX, GRID_N_MIN, GRID_OFFSET, GRID_STRIDE


def frames_for_n(n: int) -> int:
    if n < 0:
        raise ValueError(f"n 不能为负：{n}")
    return GRID_STRIDE * n + GRID_OFFSET


def grid_n(frames: int) -> int | None:
    if frames < GRID_OFFSET or (frames - GRID_OFFSET) % GRID_STRIDE != 0:
        return None
    return (frames - GRID_OFFSET) // GRID_STRIDE


def is_on_grid(frames: int) -> bool:
    return grid_n(frames) is not None


def is_usable_shot(frames: int, *, n_min: int = GRID_N_MIN, n_max: int = GRID_N_MAX) -> bool:
    n = grid_n(frames)
    return n is not None and n_min <= n <= n_max


def snap_frames(
    seconds: float,
    *,
    fps: int = FPS,
    n_min: int = GRID_N_MIN,
    n_max: int = GRID_N_MAX,
) -> int:
    """吸附到可用窗口里最近的 17n+5。并列时取较短的一档。"""
    if n_min > n_max:
        raise ValueError("n_min 不能大于 n_max")
    target = seconds * fps
    candidates = [frames_for_n(n) for n in range(n_min, n_max + 1)]
    return min(candidates, key=lambda frames: (abs(frames - target), frames))


def seconds_for_frames(frames: int, fps: int = FPS) -> float:
    return frames / fps


def plan_shot_frames(
    target_seconds: float = 300.0,
    *,
    min_seconds: float = 270.0,
    max_seconds: float = 330.0,
    fps: int = FPS,
    n_min: int = GRID_N_MIN,
    n_max: int = GRID_N_MAX,
) -> list[int]:
    """排一条接近目标时长的镜头表，每一镜都落在网格上。

    以 124 帧（约 5.17 秒）为中心上下浮动，再把总时长拉回窗口。
    """
    if min_seconds > max_seconds:
        raise ValueError("min_seconds 不能大于 max_seconds")
    cycle = [7, 6, 7, 8, 7, 5, 7, 9, 7, 6]
    cycle = [min(n_max, max(n_min, n)) for n in cycle]
    mean_frames = sum(frames_for_n(n) for n in cycle) / len(cycle)
    count = max(8, round(target_seconds * fps / mean_frames))
    frames = [frames_for_n(cycle[i % len(cycle)]) for i in range(count)]

    def total() -> float:
        return sum(frames) / fps

    def step(index: int, delta: int) -> bool:
        n = grid_n(frames[index])
        if n is None:
            return False
        new_n = n + delta
        if new_n < n_min or new_n > n_max:
            return False
        frames[index] = frames_for_n(new_n)
        return True

    guard = 0
    while total() > max_seconds and guard < 8000:
        guard += 1
        order = sorted(range(len(frames)), key=lambda i: frames[i], reverse=True)
        if not any(step(i, -1) for i in order):
            if len(frames) > 8:
                frames.pop()
            else:
                break
    while total() < min_seconds and guard < 16000:
        guard += 1
        order = sorted(range(len(frames)), key=lambda i: frames[i])
        if not any(step(i, 1) for i in order):
            frames.append(frames_for_n(min(n_max, max(n_min, 7))))
    while guard < 24000:
        guard += 1
        current = total()
        if abs(current - target_seconds) <= 8 or not (min_seconds <= current <= max_seconds):
            # 已经进窗且足够近，或已经贴边无法再动。
            if abs(current - target_seconds) <= 8 or current < min_seconds or current > max_seconds:
                if min_seconds <= current <= max_seconds and abs(current - target_seconds) <= 8:
                    break
                if current < min_seconds or current > max_seconds:
                    break
        if current > target_seconds:
            order = sorted(range(len(frames)), key=lambda i: frames[i], reverse=True)
            moved = False
            for index in order:
                before = frames[index]
                if step(index, -1) and total() >= min_seconds:
                    moved = True
                    break
                frames[index] = before
            if not moved:
                break
        else:
            order = sorted(range(len(frames)), key=lambda i: frames[i])
            moved = False
            for index in order:
                before = frames[index]
                if step(index, 1) and total() <= max_seconds:
                    moved = True
                    break
                frames[index] = before
            if not moved:
                break
    if not (min_seconds <= total() <= max_seconds):
        raise RuntimeError(f"无法把总时长放进窗口：{total():.2f}s")
    return frames
