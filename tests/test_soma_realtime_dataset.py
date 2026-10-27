"""SomaRealtimeDataset 与 SomaRealtimeCollate 的契约测试。

这些测试锁定的都是「静默出错」的风险点——它们不会让程序报错，
但会让模型在错误的数据上训练：
  1. 帧率混批：120fps 与 30fps 的时间步长不同，混一批会让时间语义失效。
  2. 窗口长度不一致：collate 直接 stack，长度不同会崩；但若某条被静默截短，
     则会在不报错的情况下喂进长度错误的样本。
  3. 特征维度不是 942：与 UnrealMotionDataset 的 12*bones+6 公式不一致。
  4. 批量化后结果与逐条计算不一致：那就是「优化改变了效果」。
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data.runtime.soma_realtime_dataset import (  # noqa: E402
    SomaRealtimeCollate,
    SomaRealtimeDataset,
    compute_stats,
    training_signature,
)

BONES = 78
WINDOW = 48


def _write_views(root, *, fps_list=(120.0048, 120.0048), frames_list=(120, 140)):
    """造一个最小四键 NPZ 训练视图。"""
    root = Path(root)
    clips_dir = root / "clips" / "fps120"
    clips_dir.mkdir(parents=True)

    names = [f"Bone{i:02d}" for i in range(BONES)]
    parents = [-1] + [i - 1 for i in range(1, BONES)]
    bones = [
        {"name": names[i], "parent": parents[i],
         "position": [float(i) * 0.01, 0.0, 0.0],
         "rotation": [0.0, 0.0, 0.0, 1.0]}
        for i in range(BONES)
    ]
    roles = {
        "root": names[0], "pelvis": names[1],
        "left_hip": names[2], "right_hip": names[3],
        "left_knee": names[4], "right_knee": names[5],
        "left_foot": names[6], "right_foot": names[7],
        "left_toe": names[8], "right_toe": names[9],
        "left_hand": names[10], "right_hand": names[11],
        "head": names[12], "left_shoulder": names[13],
        "right_shoulder": names[14], "jaw": names[15],
        "left_eye": names[16], "right_eye": names[17],
    }
    (root / "skeleton.json").write_text(
        json.dumps({"schema_version": 3, "bone_count": BONES,
                    "bones": bones, "roles": roles}, ensure_ascii=False),
        encoding="utf-8")

    rng = np.random.default_rng(11)
    clips = []
    for index, (fps, frames) in enumerate(zip(fps_list, frames_list)):
        positions = rng.normal(0, 0.1, (frames, BONES, 3)).astype(np.float64)
        positions[:, 6:10, 1] = 0.02
        rotations = np.tile(np.eye(3, dtype=np.float64), (frames, BONES, 1, 1))
        timestamps = np.arange(frames, dtype=np.float64) / fps
        root_track = np.zeros((frames, 7), dtype=np.float64)
        root_track[:, 6] = 1.0
        relative = f"clips/fps120/c{index:03d}/arrays.npz"
        target = root / relative
        target.parent.mkdir(parents=True)
        np.savez_compressed(target, positions=positions, rotations=rotations,
                            timestamps=timestamps, root_track=root_track)
        clips.append({
            "clip_id": f"c{index:03d}", "asset": f"asset/family_{index}",
            "name": f"clip{index}", "file": relative, "file_sha256": "0" * 64,
            "source_frames": frames, "fps": fps,
            "fps_group": "fps120" if fps > 60 else "fps30",
            "fps_power": 0, "scene": "A", "down_t": 4, "frames_per_token": 16,
            "window_frames": WINDOW, "history_frames": 24,
            "skeleton_signature": "sig", "root_policy": "merged",
            "root_status": "offline_proxy_not_observed_actor_root",
            "window_seconds": 0.4, "history_seconds": 0.2,
        })
    (root / "dataset.json").write_text(
        json.dumps({"clips": clips}, ensure_ascii=False), encoding="utf-8")
    return root


class FrameRateIsolationTests(unittest.TestCase):
    def test_mixed_frame_rates_are_rejected(self):
        """含多种帧率且未指定分组时必须报错，而不是静默混批。"""
        with tempfile.TemporaryDirectory() as tmp:
            views = _write_views(Path(tmp) / "views",
                                 fps_list=(120.0048, 30.0))
            with self.assertRaises(ValueError):
                SomaRealtimeDataset(views)

    def test_fps_group_filters_to_single_rate(self):
        """指定帧率组后，留下的数据必须只有一种帧率。"""
        with tempfile.TemporaryDirectory() as tmp:
            views = _write_views(Path(tmp) / "views",
                                 fps_list=(120.0048, 30.0))
            dataset = SomaRealtimeDataset(views, fps_group="fps30")
            rates = {round(float(c["fps"]), 3) for c in dataset.clips}
            self.assertEqual(len(rates), 1)
            self.assertAlmostEqual(rates.pop(), 30.0, places=3)

    def test_unknown_fps_group_lists_available(self):
        """请求不存在的帧率组时，报错信息必须列出可用项，便于排查。"""
        with tempfile.TemporaryDirectory() as tmp:
            views = _write_views(Path(tmp) / "views", fps_list=(120.0048,))
            with self.assertRaises(ValueError) as ctx:
                SomaRealtimeDataset(views, fps_group="fps30")
            self.assertIn("fps120", str(ctx.exception))


class WindowTests(unittest.TestCase):
    def test_every_window_has_exact_length(self):
        """每条样本的窗口长度必须严格等于 window_frames。"""
        with tempfile.TemporaryDirectory() as tmp:
            views = _write_views(Path(tmp) / "views")
            dataset = SomaRealtimeDataset(views, window_frames=WINDOW)
            for i in range(len(dataset)):
                self.assertEqual(dataset[i]["frames"], WINDOW)

    def test_window_never_exceeds_clip_bounds(self):
        """窗口必须是原数据的连续切片，且不越界。"""
        with tempfile.TemporaryDirectory() as tmp:
            views = _write_views(Path(tmp) / "views")
            dataset = SomaRealtimeDataset(views, window_frames=WINDOW)
            clip = dataset.clips[0]
            with np.load(dataset.folder / clip["file"], allow_pickle=False) as archive:
                full = np.asarray(archive["positions"], dtype=np.float32)
            window = dataset[0]["positions"].numpy()
            found = False
            for start in range(len(full) - WINDOW + 1):
                if np.array_equal(full[start:start + WINDOW], window):
                    found = True
                    break
            self.assertTrue(found, "窗口不是原数据的连续切片")

    def test_short_clips_are_dropped_at_init(self):
        """短于 min_frames 的 clip 必须在初始化时剔除，而不是训练中途才失败。"""
        with tempfile.TemporaryDirectory() as tmp:
            views = _write_views(Path(tmp) / "views",
                                 frames_list=(WINDOW, WINDOW - 1))
            dataset = SomaRealtimeDataset(views, window_frames=WINDOW)
            self.assertEqual(len(dataset.clips), 1)
            for clip in dataset.clips:
                self.assertGreaterEqual(clip["source_frames"], WINDOW)


class CollateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # 造 4 条都足够长的 clip：批量对比需要多条样本
        self.views = _write_views(
            Path(self.tmp.name) / "views",
            frames_list=(120, 140, 160, 180),
            fps_list=(120.0048, 120.0048, 120.0048, 120.0048))
        self.dataset = SomaRealtimeDataset(self.views, window_frames=WINDOW)
        # 统计量：compute_stats 需要 stats 目录存在前先有数据，这里直接给常量
        stats = self.views / "stats"
        stats.mkdir(exist_ok=True)
        np.save(stats / "mean.npy", np.zeros(946, dtype=np.float32))
        np.save(stats / "std.npy", np.ones(946, dtype=np.float32))
        self.representation = self.dataset.representation
        self.indices = self.representation.indices["global_rep"]

    def tearDown(self):
        import gc

        gc.collect()
        self.tmp.cleanup()

    def test_feature_dim_is_942(self):
        """collate 输出的特征维度必须是 12*bones+6。"""
        collate = SomaRealtimeCollate(self.representation, self.indices, device="cpu")
        batch = [self.dataset[i] for i in range(2)]
        out = collate(batch)
        self.assertEqual(out["motion"].shape[-1], 12 * BONES + 6)
        self.assertEqual(out["motion"].shape[1], WINDOW)

    def test_batched_equals_single_clip(self):
        """批量结果必须与逐条计算一致——优化不能改变数值。"""
        collate = SomaRealtimeCollate(self.representation, self.indices, device="cpu")
        batch = [self.dataset[i] for i in range(3)]
        batched = collate(batch)["motion"]

        # 逐条算：一次只放 1 条，走同一条代码路径
        for index, item in enumerate(batch):
            single = collate([item])["motion"][0]
            np.testing.assert_allclose(
                batched[index].numpy(), single.numpy(), rtol=1e-5, atol=1e-5,
                err_msg="批量与逐条计算结果不一致")

    def test_mixed_length_batch_is_rejected(self):
        """同批长度不一致时必须由我们自己的断言拦下，而不是靠 np.stack 崩掉。

        这条用例原本只断言 ValueError，但即使去掉显式检查，np.stack 对不等长数组
        也会抛 ValueError——于是变异体照样「通过」，测试完全无效。
        现在改为断言错误信息里必须出现我们自己的说明文字，才能证明是那条检查在生效。
        """
        collate = SomaRealtimeCollate(self.representation, self.indices, device="cpu")
        item = self.dataset[0]
        shortened = dict(item)
        shortened["positions"] = item["positions"][: WINDOW - 5]
        shortened["rotations"] = item["rotations"][: WINDOW - 5]
        shortened["contacts"] = item["contacts"][: WINDOW - 5]
        shortened["frames"] = WINDOW - 5
        with self.assertRaises(ValueError) as ctx:
            collate([item, shortened])
        self.assertIn("长度不一致", str(ctx.exception),
                      "必须是显式长度检查拦下的，而不是下游 stack 报的形状错误")

    def test_output_has_finite_values(self):
        """输出不能含 NaN/Inf。"""
        collate = SomaRealtimeCollate(self.representation, self.indices, device="cpu")
        out = collate([self.dataset[i] for i in range(2)])
        self.assertTrue(torch.isfinite(out["motion"]).all())


class StatsAndSignatureTests(unittest.TestCase):
    def test_stats_dimension_is_motion_rep_dim(self):
        """stats 维度必须是完整 motion_rep_dim（946），不是 global_rep 的 942。"""
        with tempfile.TemporaryDirectory() as tmp:
            views = _write_views(Path(tmp) / "views")
            result = compute_stats(views, window_frames=WINDOW, sample_clips=2,
                                   device="cpu")
            mean = np.load(views / "stats" / "mean.npy")
            dataset = SomaRealtimeDataset(views, window_frames=WINDOW)
            self.assertEqual(mean.shape, (dataset.representation.motion_rep_dim,))
            self.assertEqual(result["mean_shape"], [dataset.representation.motion_rep_dim])

    def test_training_signature_changes_with_window(self):
        """窗口参数不同必须产生不同签名，防止续训时静默换窗口。"""
        with tempfile.TemporaryDirectory() as tmp:
            views = _write_views(Path(tmp) / "views")
            stats = views / "stats"
            stats.mkdir(exist_ok=True)
            np.save(stats / "mean.npy", np.zeros(946, dtype=np.float32))
            np.save(stats / "std.npy", np.ones(946, dtype=np.float32))
            first = training_signature(views, window_frames=48)
            second = training_signature(views, window_frames=72)
            self.assertNotEqual(first, second)

    def test_training_signature_requires_stats(self):
        """缺统计量时必须报错，而不是悄悄用未归一化的数据训练。"""
        with tempfile.TemporaryDirectory() as tmp:
            views = _write_views(Path(tmp) / "views")
            with self.assertRaises(FileNotFoundError):
                training_signature(views)


class SplitTests(unittest.TestCase):
    def test_split_is_deterministic(self):
        """同一条 clip 两次构造得到的划分必须一致。"""
        with tempfile.TemporaryDirectory() as tmp:
            views = _write_views(Path(tmp) / "views")
            a = SomaRealtimeDataset(views, window_frames=WINDOW)
            b = SomaRealtimeDataset(views, window_frames=WINDOW)
            self.assertEqual([c["split"] for c in a.clips],
                             [c["split"] for c in b.clips])

    def test_split_values_are_legal(self):
        """划分值必须属于 train/validation/test 三者之一。"""
        with tempfile.TemporaryDirectory() as tmp:
            views = _write_views(Path(tmp) / "views")
            dataset = SomaRealtimeDataset(views, window_frames=WINDOW)
            legal = {"train", "validation", "test"}
            for clip in dataset.clips:
                self.assertIn(clip["split"], legal)


if __name__ == "__main__":
    unittest.main()
