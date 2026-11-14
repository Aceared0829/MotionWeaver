"""条件生成器的硬参考约束和分区评估选择。"""

import random
import unittest

import numpy as np
import torch

from inference.runtime.conditioned_pose import rotation_6d_to_matrix
from training.evaluation.evaluate_conditioned_pose import center_indices
from training.pretrain.train_conditioned_pose import (
    MotionWindows,
    ReferenceGuidedPose,
    balanced_window_weights,
    capture_random_state,
    losses,
    restore_random_state,
    root_world_positions,
)


class ConditionedPoseTrainingTests(unittest.TestCase):
    def test_hard_references_are_exact_and_unmasked_frames_train(self):
        model = ReferenceGuidedPose(bones=2, width=32)
        history = torch.randn(2, 24, 25)
        root = torch.randn(2, 24, 7)
        reference = torch.zeros(2, 24, 18)
        reference[:, 12] = torch.randn(2, 18)
        mask = torch.zeros(2, 24, 1)
        mask[:, 12] = 1
        prediction = model(history, root, reference, mask)
        torch.testing.assert_close(prediction[:, 12], reference[:, 12])
        loss = losses(prediction, torch.randn_like(prediction), mask, torch.ones(2, 3))[0]
        loss.backward()
        self.assertTrue(torch.isfinite(loss))
        self.assertIsNotNone(model.output[-1].weight.grad)

    def test_center_window_selection_matches_clip_order(self):
        rows = [{"clip_index": 7, "start": 0}, {"clip_index": 3, "start": 0},
                {"clip_index": 7, "start": 4}, {"clip_index": 3, "start": 4},
                {"clip_index": 7, "start": 8}]
        self.assertEqual(center_indices(rows), [3, 2])

    def test_inference_rotation_is_orthonormal(self):
        values = np.array([[1, 0, 0, 1, 0, 0], [0, 0, 0, 0, 0, 0]], dtype=np.float32)
        matrices = rotation_6d_to_matrix(values)
        np.testing.assert_allclose(matrices @ matrices.transpose(0, 2, 1),
                                   np.broadcast_to(np.eye(3), (2, 3, 3)), atol=1e-6)
        np.testing.assert_allclose(np.linalg.det(matrices), 1, atol=1e-6)

    def test_balanced_sampling_reduces_long_clip_and_common_category_dominance(self):
        class Data:
            contract = {"clips": [{"category": "Walk"}, {"category": "Walk"},
                                  {"category": "Traversal"}]}
            rows = ([{"clip_index": 0}] * 8 + [{"clip_index": 1}] * 2
                    + [{"clip_index": 2}] * 2)
        weights = balanced_window_weights(Data())
        self.assertGreater(weights[10], weights[0])
        self.assertGreater(weights[8], weights[0])

    def test_random_state_restores_sampler_and_augmentations(self):
        torch.manual_seed(9)
        np.random.seed(9)
        random.seed(9)
        generator = torch.Generator().manual_seed(9)
        state = capture_random_state(generator)
        expected = (torch.rand(1), np.random.rand(), random.random(),
                    torch.rand(1, generator=generator))
        restore_random_state(state, generator)
        actual = (torch.rand(1), np.random.rand(), random.random(),
                  torch.rand(1, generator=generator))
        torch.testing.assert_close(actual[0], expected[0])
        self.assertEqual(actual[1:3], expected[1:3])
        torch.testing.assert_close(actual[3], expected[3])

    def test_contact_loss_uses_world_root_and_backpropagates(self):
        pose = torch.tensor([[[[1.0, 0.0, 0.0]], [[1.0, 0.0, 0.0]]]], requires_grad=True)
        root = torch.tensor([[[0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
                              [0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]]])
        world = root_world_positions(pose, root)
        torch.testing.assert_close(world[0, 1, 0], torch.tensor([1.1, 0.0, 0.0]))
        features = torch.cat((pose, torch.zeros(1, 2, 1, 6)), dim=-1).reshape(1, 2, 9)
        loss = losses(features, features.detach(), torch.zeros(1, 2, 1), torch.ones(1, 1, 3),
                      pose_mean=torch.zeros(1, 1, 3), root_plan=root,
                      contacts=torch.ones(1, 2, 1),
                      foot_indices=[0], contact_weight=0.01)[0]
        loss.backward()
        assert pose.grad is not None
        self.assertGreater(float(pose.grad.abs().sum()), 0)

    def test_contact_loss_scales_with_contract_fps_squared(self):
        """脚速 = 帧间位移 × 帧率，平方后整项按 (fps/30)^2 放大。

        锁定这条是因为曾经把 30 写死：full_release_v2 是 120 fps，于是
        --contact-weight 0.4 实际只生效 1/16，训练记录里的权重值全部失真。
        """
        pose = torch.tensor([[[[1.0, 0.0, 0.0]], [[1.0, 0.0, 0.0]]]])
        root = torch.tensor([[[0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
                              [0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]]])
        features = torch.cat((pose, torch.zeros(1, 2, 1, 6)), dim=-1).reshape(1, 2, 9)
        common = dict(pose_mean=torch.zeros(1, 1, 3), root_plan=root,
                      contacts=torch.ones(1, 2, 1), foot_indices=[0], contact_weight=1.0)

        def contact_only(fps):
            # 预测与目标相同 -> position/rotation/velocity 全 0，total 就是接触项本身。
            return float(losses(features, features.clone(), torch.zeros(1, 2, 1),
                                torch.ones(1, 1, 3), fps=fps, **common)[0])

        loss30, loss60, loss120 = contact_only(30.0), contact_only(60.0), contact_only(120.0)
        # 单帧位移 0.1 m，接触对只有第 1 帧 -> contact_loss = (0.1 * fps)^2
        self.assertAlmostEqual(loss30, 9.0, places=4)
        self.assertAlmostEqual(loss60 / loss30, 4.0, places=4)
        self.assertAlmostEqual(loss120 / loss30, 16.0, places=4)
        # 省略 fps 时保持旧行为（30），冻结的 legacy fixture 才对得上。
        self.assertAlmostEqual(float(losses(features, features.clone(), torch.zeros(1, 2, 1),
                                            torch.ones(1, 1, 3), **common)[0]), loss30, places=6)

    def test_hybrid_references_cover_fixed_and_off_grid_times(self):
        data = object.__new__(MotionWindows)
        data.rows = [{"clip_index": 0, "start": 0}]
        root = np.tile(np.array([0, 0, 0, 0, 0, 0, 1], dtype=np.float32), (48, 1))
        data.raw = {0: (np.zeros((48, 1, 3), dtype=np.float32),
                        np.zeros((48, 1, 6), dtype=np.float32), root)}
        data.mean = np.zeros((1, 3), dtype=np.float32)
        data.std = np.ones((1, 3), dtype=np.float32)
        data.scenario = "hybrid"
        data.seed = 42
        data.include_contacts = False
        offsets = set()
        for epoch in range(128):
            data.epoch = epoch
            mask = data[0][3].numpy()[:, 0]
            offsets.update(np.flatnonzero(mask).tolist())
        self.assertIn(12, offsets)
        self.assertIn(23, offsets)
        self.assertTrue(any(index not in (12, 23) for index in offsets))


if __name__ == "__main__":
    unittest.main()
