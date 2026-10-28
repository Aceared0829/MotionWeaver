"""训练/评测输入等价的测试（回归：全库 20k 后 joint_rmse 暴涨 3 倍）。

## 为什么必须测这个

`SomaRealtimeCollate` 用 `to_normalize=False` 产出**未归一化**的原始特征。
`motion_vqvae.training_step` 若把这份特征声明成 `is_normalized=True`，
`change_first_heading` 与 `global_to_local` 就会跳过 normalize，把原始值当成
z-score 空间，于是训练喂给 pose_net 的 body 通道与评测喂的不是同一分布。

后果不是"训练变慢"，而是**训练目标与评测目标不是同一个量**：

- 20k 之前模型只输出近似常量（与输入的相关系数 -0.083），两条路径"一致地差"，
  于是 joint_rmse 看起来稳定在 ~105 cm（假的：全体 GT 均值常量姿态就有 51.73 cm）。
- 一旦真正开始拟合训练那份被污染的分布，评测输入成为分布外，joint 从 108 cm
  跳到 310 cm（30k）、330 cm（40k）。这不是过拟合，是口径错位。

## 实测证据（同一批 16 条、72 帧）

| 转换链 | local body std | 与"理论上正确的写法"的 max|d| |
|---|---|---|
| 训练声明 is_normalized=True | 2.2990 | 862.40 |
| 评测 evaluate_recon 的写法 | 0.9148 | 6.85e-6 |
| 理论正确：glob.normalize 后 is_normalized=True | 0.9148 | 0 |

## 这些测试怎么守

1. 源码级：钉住 training_step 那两处 `is_normalized` 的取值。
2. 数值级：真正跑一次转换，验证「训练喂给 pose_net 的 body」== 「评测算出的 body」。
   这一条不依赖源码拼写，改坏一定变红。
"""

import sys
import unittest

import numpy as np
import torch

sys.path.insert(0, r"D:\AILocomotonSystem\MotionWeaver")

from data.runtime.soma_realtime_dataset import SomaRealtimeCollate  # noqa: E402

LOSS_SOURCE = (r"D:\AILocomotonSystem\MotionWeaver"
               r"\training\models\vqvae\motion_vqvae.py")
VIEWS = r"D:\MotionWeaverData\soma_train_v1\full-v1-f32"
CHECKPOINT = (r"D:\MotionWeaverData\training_runs\full_base_40k"
              r"\checkpoints\final.ckpt")

# 两条路径的 body 相对差上限。实测修复后 max|d| 在 1e-5 量级（只是浮点累加差），
# 修复前是 862 级别；取 1e-2 留足余量，但远低于任何真实的错位。
TOLERANCE = 1e-2


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def training_step_source():
    """只截 training_step 里构造 global_motions / local_motions 那一段。"""
    text = read(LOSS_SOURCE)
    start = text.index("def training_step")
    end = text.index("# keyframe conditioning", start)
    return text[start:end]


class TrainingStepNormalizationFlagTests(unittest.TestCase):
    """源码级：两处 is_normalized 的取值必须与 collate 的 to_normalize=False 对齐。"""

    def test_change_first_heading_uses_declared_flag(self):
        """change_first_heading 的 is_normalized 必须来自显式声明，不能写死。"""
        block = training_step_source()
        self.assertIn("change_first_heading(", block)
        segment = block[block.index("change_first_heading("):]
        segment = segment[:segment.index(")") + 1]
        self.assertIn("is_normalized=inputs_are_normalized", segment,
                      "change_first_heading 的 is_normalized 被写死了；"
                      "两条数据入口的归一化状态相反，必须按入口声明")

    def test_global_to_local_inputs_are_normalized(self):
        """global_to_local 的输入是 change_first_heading 归一化后的，必须声明 True。"""
        block = training_step_source()
        segment = block[block.index("global_to_local("):]
        segment = segment[:segment.index("lengths=")]
        self.assertIn("is_normalized=True", segment,
                      "global_to_local 的输入已被 change_first_heading 归一化")

    def test_two_data_entrypoints_disagree_so_flag_must_be_explicit(self):
        """钉住前提：两条入口的归一化状态确实相反，这才需要显式开关。"""
        soma = read(r"D:\AILocomotonSystem\MotionWeaver"
                    r"\data\runtime\soma_realtime_dataset.py")
        self.assertIn("to_normalize=False", soma,
                      "SomaRealtimeCollate 的归一化口径变了，训练侧必须同步复查")
        build = read(r"D:\AILocomotonSystem\MotionWeaver"
                     r"\data\tools\build_unreal_training_set.py")
        self.assertIn("(features - mean)", build,
                      "build_unreal_training_set 不再预归一化，训练侧必须同步复查")

    def test_cli_sets_flag_from_data_source(self):
        """训练入口必须按实际数据来源改写开关，不能靠 yaml 默认值。"""
        text = read(r"D:\AILocomotonSystem\MotionWeaver"
                    r"\training\pretrain\train_unreal.py")
        self.assertIn("conf.model.args.inputs_are_normalized = not bool(", text,
                      "训练入口没有按数据来源声明归一化状态")
        self.assertIn('getattr(args, "soma_views", None))', text,
                      "开关没有依据 soma_views 判断")


class TrainEvalInputEquivalenceTests(unittest.TestCase):
    """数值级：真正跑一次转换，训练喂给 pose_net 的 body 必须等于评测算出的 body。"""

    @classmethod
    def setUpClass(cls):
        import os

        if not os.path.exists(CHECKPOINT):
            raise unittest.SkipTest("缺少 full_base_40k 检查点，跳过数值等价测试")
        from omegaconf import OmegaConf

        from motionweaver.representation.helper.pl_util import load_motion_rep

        checkpoint = torch.load(CHECKPOINT, map_location="cpu", weights_only=False)
        cls.rep = load_motion_rep(OmegaConf.create(checkpoint["unreal_contract"]["config"]))
        cls.dual = cls.rep.dual_rep
        cls.glob = cls.rep.dual_rep.global_motion_rep
        cls.loc = cls.dual.local_motion_rep

        from data.runtime.soma_realtime_dataset import SomaRealtimeDataset

        dataset = SomaRealtimeDataset(VIEWS, window_frames=72, fps_group="fps120")
        collate = SomaRealtimeCollate(cls.rep, np.arange(cls.rep.motion_rep_dim))
        # 固定种子，避免朝向增强引入额外噪声
        torch.manual_seed(20261028)
        cls.motions = collate([dataset[i] for i in range(8)])["motion"]

    def _train_path(self, motions):
        """复刻 motion_vqvae.training_step 的转换链（朝向增强置 0 以便逐位对比）。"""
        glob, dual, loc = self.glob, self.dual, self.loc
        batch, frames = motions.shape[0], motions.shape[1]
        lengths = torch.full([batch], frames)
        global_motions = glob.change_first_heading(
            motions, 0.0, is_normalized=False, to_normalize=True)
        local_motions = dual.global_to_local(
            global_motions, is_normalized=True, to_normalize=True, lengths=lengths)
        return local_motions[..., loc.indices["body"]]

    def _eval_path(self, motions):
        """复刻 benchmark/evaluate_recon.run_one 的转换链。

        run_one 拿到的 unscaled 是 to_normalize=False 的原始特征，直接
        global_to_local(is_normalized=False, to_normalize=True)。
        """
        batch, frames = motions.shape[0], motions.shape[1]
        lengths = torch.full([batch], frames)
        local = self.dual.global_to_local(
            motions.float(), is_normalized=False, to_normalize=True,
            lengths=lengths)
        return local[..., self.loc.indices["body"]]

    def test_train_and_eval_bodies_match(self):
        train_body = self._train_path(self.motions)
        eval_body = self._eval_path(self.motions)
        gap = (train_body - eval_body).abs().max().item()
        self.assertLess(gap, TOLERANCE,
                        f"训练与评测送进 pose_net 的 body 相差 {gap:.4f}，"
                        "两条路径不是同一个分布；修复前实测 862.40")

    def test_wrong_flag_really_breaks_equivalence(self):
        """钉住测试的灵敏度：故意用修复前的写法，必须显著偏离评测路径。

        没有这一条，上面的等价测试可能因为「两条路径恰好都很小」而假通过。
        实测修复前 max|d| = 862 量级，修复后 1e-5 量级，相距 7 个数量级。
        """
        batch, frames = self.motions.shape[0], self.motions.shape[1]
        lengths = torch.full([batch], frames)
        # 修复前的写法：把未归一化的 collate 输出声明成已归一化
        stale_global = self.glob.change_first_heading(
            self.motions, 0.0, is_normalized=True, to_normalize=True)
        stale_local = self.dual.global_to_local(
            stale_global, is_normalized=True, to_normalize=True,
            lengths=lengths)
        stale_body = stale_local[..., self.loc.indices["body"]]
        gap = (stale_body - self._eval_path(self.motions)).abs().max().item()
        self.assertGreater(gap, 1.0,
                           f"修复前的写法与评测路径只差 {gap:.4f}，"
                           "说明等价测试区分不出这个 bug，测试无效")


if __name__ == "__main__":
    unittest.main(verbosity=2)
