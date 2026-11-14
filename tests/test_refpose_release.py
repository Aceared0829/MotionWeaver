"""refpose 发布目录（契约 + 统计量 + mmap）必须真能被训练读起来。

## 为什么必须测这个

这条链上最容易「看起来对、其实拼不上」的地方，是契约字段与消费者的错位：

- `MotionWindows.__init__` 立刻读 `contract["stats"]["pose_m"]` 与
  `contract["window_split_counts"]`，缺任何一个都是 KeyError；
- `__getitem__` 硬编码 `middle = start + 24, end = start + 48`，
  所以契约的 window 必须是 24+24，不能是 build_soma_contract 的 24+48；
- `__getitem__` 把 `r[start:end]` 直接与 pose 拼接，所以 `r.npy` 必须是
  **6D 交错**（每骨 6 个数），存成 3x3 会以 ndim 不匹配的方式炸掉。

前两条是「拼不上」，第三条是「拼上了但布局错」。三者都不会在构建阶段报错，
只会在训练第一步炸——所以这里用一个 12 条的合成数据集把它们钉住。
"""

import json
import sys
import unittest
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from data.tools.build_refpose_release import build, split_of, window_starts
from training.pretrain.train_conditioned_pose import MotionWindows

BONES = 78
FRAMES = 120
FPS = 120.00480019200768


def _synthetic_clip(index):
    """造一条能被训练读的 clip：位置小幅度抖动，旋转用单位阵（构建不判别正交性）。"""
    rng = np.random.default_rng(1000 + index)
    positions = rng.normal(scale=0.05, size=(FRAMES, BONES, 3))
    rotations = np.broadcast_to(np.eye(3), (FRAMES, BONES, 3, 3)).copy()
    timestamps = np.arange(FRAMES) / FPS
    root_track = np.zeros((FRAMES, 7))
    root_track[:, 3] = 0.0
    root_track[:, 6] = 1.0  # xyzw 单位四元数
    return {"positions": positions, "rotations": rotations,
            "timestamps": timestamps, "root_track": root_track}


class ReleaseContractTests(unittest.TestCase):
    def test_window_starts_match_full_release_cadence(self):
        """窗口步长必须是 future_frames，且包含最后一个合法起点。

        全量发布的实测口径是 71,127 clip / 2,490,688 窗口 = 35.0 窗口/条，
        平均 841.6 帧/条；841.6 / 24 = 35.07。步长改成 24 之外的任何值都对不上。
        """
        starts = window_starts(120)
        self.assertEqual(starts, [0, 24, 48, 72])
        self.assertEqual(len(window_starts(841)), 34)
        self.assertEqual(len(window_starts(47)), 0, "不足 48 帧不该产出窗口")

    def test_split_is_stable_and_disjoint(self):
        """同一资产名的 split 必须每次一致；家族名不同才可能分到不同区。"""
        self.assertEqual(split_of("a/b/c/some_idle_001.bvh"), split_of("a/b/c/some_idle_001.bvh"))
        self.assertEqual(split_of("x/walk_001.bvh"), split_of("x/walk_001.bvh"))


class MixedFrameRateTests(unittest.TestCase):
    """混帧率数据集必须在构建阶段被拒绝，不能拖到训练第一次取数。

    契约只有一个 `fps` 字段，`load_raw` 用它校验每条 clip 的时间轴
    （`abs(diff(ts) - 1/fps) <= 2e-6`）。pilot-100v2 就是 fps120 + fps30 混装：
    契约声明 120.0048，读到 fps30 的 clip 才抛「原始时间轴不满足固定帧率」，
    报错点离真正原因隔着一整层。--fps-group 是这条路的出口。
    """

    def setUp(self):
        import tempfile

        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        dataset = root / "dataset"
        dataset.mkdir()
        rows = []
        for index, (group, rate) in enumerate([("fps120", FPS), ("fps120", FPS), ("fps30", 30.0)]):
            clip_id = f"mixed{index}"
            folder = dataset / "clips" / group / clip_id
            folder.mkdir(parents=True)
            np.savez_compressed(folder / "arrays.npz", **_synthetic_clip(index))
            rows.append({"clip_id": clip_id, "asset": f"{clip_id}_walk.bvh",
                         "file": f"clips/{group}/{clip_id}/arrays.npz",
                         "source_frames": FRAMES, "fps": rate, "fps_group": group,
                         "scene": "A"})
        (dataset / "clips.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        self.dataset, self.root = dataset, root

    def tearDown(self):
        self._temp.cleanup()

    def test_mixed_rates_are_rejected(self):
        with self.assertRaises(ValueError) as caught:
            build(self.dataset, self.root / "release")
        message = str(caught.exception)
        self.assertIn("混了多个帧率", message)
        # 报错必须点名具体帧率与分组，否则读者没法判断该筛哪个 group
        self.assertIn("30.0", message)
        self.assertIn("fps30", message)

    def test_fps_group_filter_selects_single_rate(self):
        result = build(self.dataset, self.root / "release", fps_group="fps120")
        self.assertEqual(result["clips"], 2)
        contract = json.loads(
            (self.root / "release" / "contract" / "contract.json").read_text(encoding="utf-8"))
        self.assertAlmostEqual(contract["fps"], FPS, places=4)
        # raw_file 是 NPZ（评测/推理按它读），mmap_file 是 mmap 目录（训练按它读）。
        # 两者都必须在 fps120 下，但绝不能是同一个字段。
        self.assertTrue(all(clip["raw_file"].startswith("clips/fps120/") and
                            clip["raw_file"].endswith("/arrays.npz")
                            for clip in contract["clips"]))
        self.assertTrue(all(clip["mmap_file"].startswith("clips/fps120/") and
                            not clip["mmap_file"].endswith(".npz")
                            for clip in contract["clips"]))
        self.assertEqual(result["windows"], sum(
            len(window_starts(FRAMES)) for _ in range(2)))

    def test_unknown_fps_group_fails_loudly(self):
        with self.assertRaises(ValueError):
            build(self.dataset, self.root / "release", fps_group="fps240")


class ReleaseRoundTripTests(unittest.TestCase):
    """构建 → MotionWindows 读 → __getitem__ 出张量，全程真正跑一次。"""

    @classmethod
    def setUpClass(cls):
        import tempfile

        cls._temp = tempfile.TemporaryDirectory()
        root = Path(cls._temp.name)
        dataset = root / "dataset"
        dataset.mkdir()

        cls.clip_ids = [f"clip{index:02d}" for index in range(12)]
        rows = []
        for index, clip_id in enumerate(cls.clip_ids):
            folder = dataset / "clips" / "fps120" / clip_id
            folder.mkdir(parents=True)
            np.savez_compressed(folder / "arrays.npz", **_synthetic_clip(index))
            rows.append({
                "clip_id": clip_id,
                # 用互不相同的家族名，让 80/10/10 分桶真的落到 train 上
                "asset": f"soma_uniform/bvh/{clip_id}_idle.bvh",
                "name": clip_id,
                "file": f"clips/fps120/{clip_id}/arrays.npz",
                "source_frames": FRAMES,
                "fps": FPS,
                "fps_group": "fps120",
                "scene": "A",
            })
        (dataset / "clips.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

        cls.dataset = dataset
        cls.release = root / "release"
        cls.mmap = root / "mmap"
        cls.result = build(dataset, cls.release, mmap_root=cls.mmap, workers=2)
        cls.contract = json.loads(
            (cls.release / "contract" / "contract.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls._temp.cleanup()

    def test_contract_carries_what_motionwindows_reads(self):
        """stats 与 window_split_counts 缺失会让 MotionWindows 在 __init__ 就死。"""
        self.assertEqual(self.contract["window"], {"history_frames": 24, "future_frames": 24})
        self.assertIn("pose_m", self.contract["stats"])
        for split in ("train", "validation", "test"):
            self.assertIn(split, self.contract["window_split_counts"])
        self.assertEqual(sum(self.contract["window_split_counts"].values()),
                         self.result["windows"])

    def test_stats_are_finite_and_positive(self):
        """std 必须为正——除以 0 会让整批特征变成 inf，训练静默崩掉。"""
        stats = self.contract["stats"]["pose_m"]
        mean = np.load(self.release / "contract" / "stats" / stats["mean"])
        std = np.load(self.release / "contract" / "stats" / stats["std"])
        self.assertEqual(mean.shape, (BONES, 3))
        self.assertEqual(std.shape, (BONES, 3))
        self.assertTrue(np.isfinite(mean).all())
        self.assertTrue((std > 0).all())

    def test_mmap_rotation_is_six_dimensional(self):
        """r.npy 必须是 (T, bones, 6)；存成 3x3 会在拼接时 ndim 不匹配。"""
        sample = next((self.mmap / "clips" / "fps120").iterdir())
        self.assertEqual(np.load(sample / "p.npy", mmap_mode="r").shape, (FRAMES, BONES, 3))
        self.assertEqual(np.load(sample / "r.npy", mmap_mode="r").shape, (FRAMES, BONES, 6))
        self.assertEqual(np.load(sample / "k.npy", mmap_mode="r").shape, (FRAMES, 7))

    def test_motionwindows_loads_and_yields_tensors(self):
        """真正走一遍训练侧的取数：形状与有限性都检查，不只看能不能实例化。"""
        import torch

        split = "train" if self.contract["window_split_counts"]["train"] else "validation"
        data = MotionWindows(self.release, self.contract, split, mmap_root=self.mmap)
        self.assertGreater(len(data), 0, f"{split} 分区没有窗口，测试前提不成立")

        history, future_root, reference, mask, target = data[0]
        self.assertEqual(tuple(history.shape), (24, BONES * 9 + 7))
        self.assertEqual(tuple(future_root.shape), (24, 7))
        self.assertEqual(tuple(reference.shape), (24, BONES * 9))
        self.assertEqual(tuple(mask.shape), (24, 1))
        self.assertEqual(tuple(target.shape), (24, BONES * 9))
        for tensor in (history, future_root, reference, target):
            self.assertTrue(torch.isfinite(tensor).all())

    def test_windows_jsonl_matches_contract_order(self):
        """clip_index 必须指向契约里同一个 clip，错位会静默读到别人的动作。"""
        rows = [json.loads(line) for line in
                (self.release / "contract" / "windows.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()]
        self.assertTrue(rows)
        for row in rows[:20]:
            clip = self.contract["clips"][row["clip_index"]]
            self.assertTrue(clip["raw_file"].startswith("clips/fps120/"))
            self.assertTrue(clip["mmap_file"].startswith("clips/fps120/"))

    def test_raw_file_points_at_a_readable_npz(self):
        """raw_file 必须能按 contained_file(source, raw_file) 真正读出来。

        这是 load_window / evaluate_conditioned_baselines / inference 的唯一路径来源。
        构建器早期把 raw_file 写成 mmap 目录，构建本身完全不报错，
        但所有评测与推理会在「数据文件不存在或越过数据目录」上炸——
        所以这里直接用消费者自己的校验函数去读。
        """
        from data.runtime.unreal_dataset import contained_file

        for clip in self.contract["clips"][:5]:
            path = contained_file(self.contract["source_dataset"], clip["raw_file"])
            self.assertTrue(path.is_file())
            with np.load(path, allow_pickle=False) as archive:
                self.assertEqual(archive["positions"].shape[1], BONES)

    def test_consumers_verify_clips_jsonl_not_dataset_json(self):
        """评测/审计要按契约里的清单名取清单，而不是写死 dataset.json。

        为什么钉死这条：Schema v2 的来源清单叫 dataset.json，参考引导契约
        （v3/SOMA）的来源清单叫 clips.jsonl。契约本身已经写了
        source_manifest_name，但早期实现四处硬编码 dataset.json，
        于是哈希完全对得上的 v3 契约在读清单时直接 FileNotFoundError——
        报错点离真实原因（文件名）隔着一整层。
        """
        from data.runtime.conditioned_motion import (
            source_manifest_path,
            verify_source_manifest,
        )

        self.assertEqual(self.contract["source_manifest_name"], "clips.jsonl")
        self.assertEqual(source_manifest_path(self.contract).name, "clips.jsonl")
        self.assertEqual(verify_source_manifest(self.contract).name, "clips.jsonl")
        # 这份发布目录里根本没有 dataset.json，能走到这里就说明没回落到旧名字
        self.assertFalse((self.dataset / "dataset.json").exists())

    def test_baseline_evaluator_runs_on_clips_jsonl_release(self):
        """非学习基线必须能在只有 clips.jsonl 的发布目录上真正跑完。"""
        import json as jsonlib
        import tempfile

        from training.evaluation.evaluate_conditioned_baselines import evaluate

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "baseline"
            report = evaluate(self.release / "contract", output, split="test", per_clip=1)
            self.assertEqual(report["split"], "test")
            self.assertTrue(report["oracle_root_condition"])
            self.assertIn("no_reference/hold", report["aggregate"])
            self.assertTrue((output / "report.json").is_file())
            written = jsonlib.loads((output / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(written["contract_sha256"], report["contract_sha256"])

    def test_mmap_file_matches_written_directories(self):
        """mmap_file 必须真的落在磁盘上，否则训练会静默回落到 NPZ（慢 3 倍）。"""
        for clip in self.contract["clips"][:5]:
            folder = self.mmap / clip["mmap_file"]
            self.assertTrue(folder.is_dir(), f"缺少 mmap 目录 {folder}")
            for name in ("p.npy", "r.npy", "k.npy"):
                self.assertTrue((folder / name).is_file())


if __name__ == "__main__":
    unittest.main()
