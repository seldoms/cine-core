from cinecore.duration import (
    frames_for_n,
    is_on_grid,
    is_usable_shot,
    plan_shot_frames,
    snap_frames,
)


def test_documented_checkpoints_follow_17n_plus_5():
    assert frames_for_n(5) == 90
    assert frames_for_n(7) == 124
    assert frames_for_n(15) == 260
    assert is_on_grid(90)
    assert is_on_grid(124)
    assert is_on_grid(260)
    # 208 帧约 8.67 秒，常被口头记成网格点，但 17*12+5 = 209。
    assert not is_on_grid(208)
    assert frames_for_n(12) == 209
    assert not is_usable_shot(208)
    assert not is_usable_shot(5)


def test_snap_uses_the_formula_not_the_208_frame_note():
    assert snap_frames(3.75) == 90
    assert snap_frames(5.17) == 124
    assert snap_frames(8.67) == 209
    assert snap_frames(10.83) == 260
    assert snap_frames(0.2) == 90


def test_plan_lands_near_five_minutes_on_the_grid():
    frames = plan_shot_frames(300, min_seconds=270, max_seconds=330)
    total = sum(frames) / 24
    assert 270 <= total <= 330
    assert abs(total - 300) <= 8
    assert len(frames) >= 20
    assert all(is_usable_shot(item) for item in frames)
    assert len(set(frames)) > 1
