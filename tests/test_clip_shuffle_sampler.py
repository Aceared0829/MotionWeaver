"""ClipShuffleSampler 的契约测试：完整排列、可复现、分块局部性。

为什么值得单独测：这个采样器替换的是 DataLoader 的 shuffle=True，
一旦它漏样本或重复样本，训练指标会悄悄变差而不会报错。全量 71k/199 万窗口
下人工发现不了的错误，必须在 15 个样本的小用例上钉死。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def make_rows(clips, windows_per_clip):
    return [{"clip_index": clip, "window_index": window}
            for clip in range(clips) for window in range(windows_per_clip)]


def test_sampler_yields_complete_permutation():
    from training.pretrain.train_conditioned_pose import ClipShuffleSampler

    rows = make_rows(5, 3)
    sampler = ClipShuffleSampler(rows, chunk=2, seed=7)
    order = list(sampler)
    assert len(sampler) == 15
    assert len(order) == 15
    assert sorted(order) == list(range(15)), "必须恰好遍历每个窗口一次"


def test_sampler_is_reproducible_within_epoch_and_changes_between_epochs():
    from training.pretrain.train_conditioned_pose import ClipShuffleSampler

    sampler = ClipShuffleSampler(make_rows(5, 3), chunk=2, seed=7)
    first = list(sampler)
    assert list(sampler) == first, "同一 epoch 内两次迭代必须一致"
    sampler.set_epoch(1)
    second = list(sampler)
    assert second != first, "换 epoch 必须换顺序"
    assert sorted(second) == list(range(15))


def test_sampler_keeps_windows_of_one_clip_together():
    from training.pretrain.train_conditioned_pose import ClipShuffleSampler

    rows = make_rows(6, 4)
    sampler = ClipShuffleSampler(rows, chunk=2, seed=11)
    order = list(sampler)
    # 一个 chunk 覆盖 chunk 条 clip 的全部窗口，所以任意 chunk 大小
    # 的连续前缀里出现的 clip 数不超过 chunk。
    for start in range(0, 24, 8):
        clips = {rows[index]["clip_index"] for index in order[start:start + 8]}
        assert len(clips) <= 2, f"第 {start} 段跨越了 {len(clips)} 条 clip"


def test_sampler_rejects_non_positive_chunk():
    from training.pretrain.train_conditioned_pose import ClipShuffleSampler

    for chunk in (0, -1):
        try:
            ClipShuffleSampler(make_rows(2, 2), chunk=chunk)
        except ValueError:
            continue
        raise AssertionError(f"chunk={chunk} 应该报错")


def test_sampler_length_matches_window_count_not_clip_count():
    from training.pretrain.train_conditioned_pose import ClipShuffleSampler

    rows = make_rows(4, 7)
    sampler = ClipShuffleSampler(rows, chunk=3, seed=0)
    assert len(sampler) == len(rows) == 28
    assert len(sampler.groups) == 4, "分组按 clip，长度按窗口"


def test_sampler_handles_single_clip_and_chunk_larger_than_clips():
    from training.pretrain.train_conditioned_pose import ClipShuffleSampler

    one = list(ClipShuffleSampler(make_rows(1, 5), chunk=16, seed=3))
    assert sorted(one) == list(range(5))
    wide = list(ClipShuffleSampler(make_rows(3, 2), chunk=64, seed=3))
    assert sorted(wide) == list(range(6))
