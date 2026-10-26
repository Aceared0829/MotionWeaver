"""build_unreal_training_set 的契约测试。

这些测试锁定四件事，任何一件坏了都会让训练静默出错：

1. feature_dim 必须等于 12 * 骨骼数 + 6（UnrealMotionDataset 用这个公式校验）。
2. stats 必须是「全特征维」（12*bones+6 下的完整 motion_rep 维），
   而不是取完 global_rep 子集后的列数——represention 加载 stats 时会断言
   motion_rep_dim == stats.get_dim()，维度不符会直接失败。
3. 保存的 npy 必须是二维 [frames, feature_dim] 且全为有限值。
4. 归一化后 train 分区的均值应接近 0（统计量只来自 train 分区）。

用真实的 SOMA 骨架形状（78 骨）构造最小合成数据，不依赖磁盘上的大数据集。
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data.runtime.unreal_dataset import UnrealMotionDataset, UnrealSkeleton
from data.tools.build_unreal_training_set import (
    MIN_TRAINING_FRAMES,
    build_skeleton_file,
    prepare,
)
from motionweaver.representation.core.motion_reps.dual_root_global_joints import (
    DualRootGlobalJoints,
)

BONES = 78


def _write_views(root):
    """造一个最小的四键 NPZ 训练视图，拓扑与真实 SOMA 一致但数值是合成的。"""
    root = Path(root)
    clips_dir = root / "clips" / "fps120"
    clips_dir.mkdir(parents=True)

    # 骨架：一条链 + 根节点，roles 覆盖转换器与 UnrealSkeleton 需要的全部键
    names = [f"Bone{i:02d}" for i in range(BONES)]
    parents = [-1] + [i - 1 for i in range(1, BONES)]
    rest_positions = [[float(i) * 0.01, 0.0, 0.0] for i in range(BONES)]
    bones = [
        {
            "name": names[i],
            "parent": parents[i],
            "position": rest_positions[i],
            "rotation": [0.0, 0.0, 0.0, 1.0],
        }
        for i in range(BONES)
    ]
    roles = {
        "root": names[0],
        "pelvis": names[1],
        "left_hip": names[2],
        "right_hip": names[3],
        "left_knee": names[4],
        "right_knee": names[5],
        "left_foot": names[6],
        "right_foot": names[7],
        "left_toe": names[8],
        "right_toe": names[9],
        "left_hand": names[10],
        "right_hand": names[11],
        "head": names[12],
        "left_shoulder": names[13],
        "right_shoulder": names[14],
        "jaw": names[15],
        "left_eye": names[16],
        "right_eye": names[17],
    }
    (root / "skeleton.json").write_text(
        json.dumps({"schema_version": 3, "bone_count": BONES,
                    "bones": bones, "roles": roles}, ensure_ascii=False),
        encoding="utf-8",
    )

    rng = np.random.default_rng(7)
    clips = []
    for index in range(4):
        # 必须 >= MIN_TRAINING_FRAMES（65），否则转换器会按契约拒绝，
        # 这不是测试想要的路径；短片段另有专门用例覆盖。
        frames = 80 + index * 5
        positions = rng.normal(0, 0.1, (frames, BONES, 3)).astype(np.float64)
        # 让脚部贴地，避免 foot contact 判定全为未知
        positions[:, 6:10, 1] = 0.02
        rotations = np.tile(np.eye(3, dtype=np.float64), (frames, BONES, 1, 1))
        timestamps = np.arange(frames, dtype=np.float64) / 120.0
        root_track = np.zeros((frames, 7), dtype=np.float64)
        root_track[:, 3] = 0.0
        root_track[:, 6] = 1.0
        relative = f"clips/fps120/c{index:03d}/arrays.npz"
        target = root / relative
        target.parent.mkdir(parents=True)
        np.savez_compressed(
            target,
            positions=positions,
            rotations=rotations,
            timestamps=timestamps,
            root_track=root_track,
        )
        clips.append({
            "clip_id": f"c{index:03d}",
            "asset": f"asset/family_{index}",
            "name": f"clip{index}",
            "file": relative,
            "file_sha256": "0" * 64,
            "source_frames": frames,
            "fps": 120.0048,
            "fps_group": "fps120",
            "fps_power": 0,
            "scene": "A",
            "down_t": 4,
            "frames_per_token": 16,
            "window_frames": 48,
            "history_frames": 24,
            "skeleton_signature": "sig",
            "root_policy": "merged",
            "root_status": "offline_proxy_not_observed_actor_root",
            "window_seconds": 0.4,
            "history_seconds": 0.2,
        })
    (root / "dataset.json").write_text(
        json.dumps({"clips": clips}, ensure_ascii=False), encoding="utf-8"
    )
    return root


class ConvertTrainingSetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.views = _write_views(Path(self.tmp.name) / "views")
        self.output = Path(self.tmp.name) / "train"

    def tearDown(self):
        # UnrealMotionDataset 用 mmap 持有 npy 句柄，Windows 上删除会 permission denied；
        # 主动回收后再清理，避免测试因临时目录残留而报错。
        import gc

        gc.collect()
        self.tmp.cleanup()

    def test_feature_dim_matches_unreal_formula(self):
        """feature_dim 必须等于 12 * bones + 6，否则 UnrealMotionDataset 会拒绝加载。"""
        result = prepare(self.views, self.output)
        manifest = json.loads((self.output / "dataset.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["feature_dim"], 12 * BONES + 6)
        self.assertEqual(result["feature_dim"], 12 * BONES + 6)

    def test_stats_use_full_feature_dimension(self):
        """stats 必须是全特征维，不能是取完 global_rep 子集后的列数。

        DualRootGlobalJoints 加载 stats 时断言 motion_rep_dim == stats.get_dim()，
        这里直接用它验证，等价于训练启动时的那次断言。
        """
        prepare(self.views, self.output)
        mean = np.load(self.output / "stats" / "mean.npy")
        std = np.load(self.output / "stats" / "std.npy")
        # 用真实表示类加载一次，确认维度能被接受（不触发 AssertionError）
        UnrealSkeleton(self.output)
        representation = DualRootGlobalJoints(
            fps=120.0048,
            skeleton=UnrealSkeleton(self.output),
            name="unreal_dual_root_global_joints",
        )
        from motionweaver.representation.core.utils.stats import Stats

        stats = Stats(load=True, folder=str(self.output / "stats"))
        self.assertEqual(stats.get_dim(), representation.motion_rep_dim)
        # 同时确认 mean/std 实际维度就是 motion_rep_dim，而不是 942
        self.assertEqual(mean.shape, (representation.motion_rep_dim,))
        self.assertEqual(std.shape, (representation.motion_rep_dim,))

    def test_saved_tensors_are_two_dimensional_and_finite(self):
        """每段动画必须是 [frames, feature_dim] 且全部有限。"""
        prepare(self.views, self.output)
        manifest = json.loads((self.output / "dataset.json").read_text(encoding="utf-8"))
        dim = manifest["feature_dim"]
        for item in manifest["clips"]:
            array = np.load(self.output / item["file"], allow_pickle=False)
            self.assertEqual(array.ndim, 2)
            self.assertEqual(array.shape[1], dim)
            self.assertEqual(array.shape[0], item["frames"])
            self.assertTrue(np.isfinite(array).all())

    def test_manifest_is_loadable_by_training_dataset(self):
        """转换产物必须能被真实训练用的 UnrealMotionDataset 直接读。

        注意：UnrealMotionDataset 用 mmap_mode="r" 打开 npy，Windows 上文件句柄
        会一直持有到对象释放；因此这里显式删除 dataset 再清理临时目录。
        """
        prepare(self.views, self.output)
        dataset = UnrealMotionDataset(self.output)
        try:
            self.assertGreater(len(dataset), 0)
            item = dataset[0]
            self.assertEqual(item["motion"].ndim, 2)
            self.assertTrue(torch.isfinite(item["motion"]).all())
        finally:
            del dataset

    def test_signatures_are_distinct_and_training_signature_is_bound(self):
        """skeleton_signature 只描述骨架，training_signature 额外绑定 stats。"""
        prepare(self.views, self.output)
        manifest = json.loads((self.output / "dataset.json").read_text(encoding="utf-8"))
        self.assertIn("skeleton_signature", manifest)
        self.assertIn("training_signature", manifest)
        self.assertNotEqual(manifest["skeleton_signature"], manifest["training_signature"])
        # training_signature 必须能被 UnrealMotionDataset 的校验复算出来
        from data.runtime.unreal_dataset import training_signature

        self.assertEqual(manifest["training_signature"], training_signature(self.output))

    def test_stats_only_from_train_partition(self):
        """统计量只来自 train 分区：归一化后 train 各列均值应接近 0。"""
        result = prepare(self.views, self.output)
        self.assertGreater(result["split_counts"]["train"], 0)
        manifest = json.loads((self.output / "dataset.json").read_text(encoding="utf-8"))
        train_files = [c["file"] for c in manifest["clips"] if c["split"] == "train"]
        stacked = np.concatenate(
            [np.load(self.output / name, allow_pickle=False) for name in train_files], axis=0
        )
        # 统计量来自 train，因此整体均值应贴近 0（容差给足，样本很少）
        self.assertLess(abs(float(stacked.mean())), 0.5)

    def test_clip_fps_comes_from_manifest_not_timestamps(self):
        """每条记录的 fps 必须取自 dataset.json 的权威值，不能从 timestamps 反推。

        反推会得到 120.0048 这类带噪声的值，而 world_foot_contacts 的脚接触判定
        对 fps 敏感，用错帧率会静默改变接触标签，进而影响脚滑指标。
        """
        manifest = json.loads((self.views / "dataset.json").read_text(encoding="utf-8"))
        source_fps = float(manifest["clips"][0]["fps"])
        prepare(self.views, self.output)
        converted = json.loads((self.output / "dataset.json").read_text(encoding="utf-8"))
        for item in converted["clips"]:
            self.assertEqual(float(item["fps"]), source_fps)

    def test_short_clips_are_rejected_not_silently_kept(self):
        """不足最小时长的片段必须进 rejected，不能悄悄混进训练集。"""
        manifest_path = self.views / "dataset.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        # 截断到 MIN_TRAINING_FRAMES 以下，确认转换器主动拒绝而不是放进训练集
        clip = manifest["clips"][0]
        array_path = self.views / clip["file"]
        short = MIN_TRAINING_FRAMES - 1
        with np.load(array_path, allow_pickle=False) as archive:
            payload = {key: archive[key][:short] for key in archive.files}
        np.savez_compressed(array_path, **payload)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

        result = prepare(self.views, self.output)
        self.assertGreaterEqual(result["rejected"], 1)
        rejected = json.loads((self.output / "rejected_clips.json").read_text(encoding="utf-8"))
        self.assertTrue(any("不足" in item["error"] or "帧" in item["error"] for item in rejected))


class ThresholdConsistencyTests(unittest.TestCase):
    """最小时长阈值必须与官方准备器、与训练侧加载器三方一致。

    这条用例是变异检查暴露出来的：早先把 MIN_TRAINING_FRAMES 写成 12，
    合成数据都在 80 帧以上，所以所有用例照样全绿，缺陷完全看不出来。
    直接把两个模块的常量比对，才能让「阈值不一致」当场变红。
    """

    def test_frame_threshold_matches_official_preparer(self):
        from data.tools import prepare_unreal_dataset

        self.assertEqual(
            MIN_TRAINING_FRAMES,
            prepare_unreal_dataset.MIN_TRAINING_FRAMES,
            "转换器与官方准备器的最小时长必须一致",
        )

    def test_frame_threshold_covers_training_dataset_min_frames(self):
        """转换器阈值不得低于 UnrealMotionDataset 的 min_frames，否则长片段会被静默丢弃。"""
        import inspect

        signature = inspect.signature(UnrealMotionDataset.__init__)
        loader_min = signature.parameters["min_frames"].default
        self.assertGreaterEqual(MIN_TRAINING_FRAMES, loader_min)

    def test_too_short_clip_is_rejected_by_converter_not_by_loader(self):
        """低于阈值的片段必须在转换阶段就被拒绝，而不是留到训练启动时才炸。"""
        with tempfile.TemporaryDirectory() as tmp:
            views = _write_views(Path(tmp) / "views")
            out = Path(tmp) / "train"
            manifest_path = views / "dataset.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            clip = manifest["clips"][0]
            array_path = views / clip["file"]
            with np.load(array_path, allow_pickle=False) as archive:
                payload = {
                    key: archive[key][: MIN_TRAINING_FRAMES - 1] for key in archive.files
                }
            np.savez_compressed(array_path, **payload)

            result = prepare(views, out)
            self.assertGreaterEqual(result["rejected"], 1)


class SkeletonFileTests(unittest.TestCase):
    def test_neutral_joints_present_and_shaped(self):
        """UnrealSkeleton 需要 neutral_joints；缺失会直接 KeyError。"""
        with tempfile.TemporaryDirectory() as tmp:
            views = _write_views(Path(tmp) / "views")
            out = Path(tmp) / "train"
            out.mkdir()
            payload = build_skeleton_file(out, views)
            self.assertIn("neutral_joints", payload)
            self.assertEqual(len(payload["neutral_joints"]), BONES)
            self.assertEqual(len(payload["neutral_joints"][0]), 3)
            self.assertIn("fps", payload)
            # 能被真实骨架类加载
            UnrealSkeleton(out)


if __name__ == "__main__":
    unittest.main()
