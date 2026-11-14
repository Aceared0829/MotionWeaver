"""refpose 模型抽离后的兼容性、形状与运行时不依赖训练层。"""
import os
import subprocess
import sys
import tempfile

import torch

from motionweaver.refpose import (
    ReferenceGuidedPose,
    RootPlanHead,
    StanceGuidedPose,
    losses,
)


def _inputs(bones=3):
    history = torch.randn(1, 24, bones * 9 + 7)
    root = torch.zeros(1, 24, 7)
    root[..., 6] = 1
    reference = torch.zeros(1, 24, bones * 9)
    mask = torch.zeros(1, 24, 1)
    return history, root, reference, mask


def test_legacy_training_imports_still_resolve():
    from data.runtime.refpose_windows import (
        ClipShuffleSampler as NewSampler,
        MotionWindows as NewWindows,
    )
    from training.pretrain.train_conditioned_pose import (
        ClipShuffleSampler,
        MotionWindows,
        ReferenceGuidedPose as LegacyPose,
        balanced_window_weights,
        losses as legacy_losses,
    )
    from training.pretrain.train_stance_pilot import StanceGuidedPose as LegacyStance

    assert LegacyPose is ReferenceGuidedPose
    assert legacy_losses is losses
    assert LegacyStance is StanceGuidedPose
    assert MotionWindows is NewWindows
    assert ClipShuffleSampler is NewSampler
    assert callable(balanced_window_weights)


def test_reference_guided_pose_forward_and_loss():
    history, root, reference, mask = _inputs()
    model = ReferenceGuidedPose(bones=3, width=16)
    output = model(history, root, reference, mask)
    assert output.shape == history.shape[:2] + (3 * 9,)

    target = torch.randn_like(output)
    loss, position, rotation = losses(output, target, mask, torch.ones(3, 3))
    assert torch.isfinite(loss)
    assert torch.isfinite(position) and torch.isfinite(rotation)
    loss.backward()


def test_root_plan_head_shape_and_unit_quaternion():
    model = RootPlanHead(width=16)
    output = model(torch.randn(2, 24, 709))
    assert output.shape == (2, 24, 7)
    norms = output[..., 3:].norm(dim=-1)
    torch.testing.assert_close(norms, torch.ones_like(norms), atol=1e-5, rtol=1e-5)


def test_stance_guided_pose_forward():
    history, root, reference, mask = _inputs()
    goal = torch.zeros(1, 24, dtype=torch.long)
    model = StanceGuidedPose(bones=3, width=16)
    output = model(history, root, reference, mask, goal)
    assert output.shape == history.shape[:2] + (3 * 9,)


def test_conditioned_runtime_imports_without_training_or_tools():
    code = """
import importlib.abc
import sys

class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'training', 'tools'}:
            raise AssertionError('Unexpected dependency: ' + fullname)

sys.meta_path.insert(0, Block())
from inference.runtime.conditioned_pose import ConditionedPosePredictor
print('ok')
"""
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    with tempfile.TemporaryDirectory() as cwd:
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    assert result.returncode == 0, result.stdout + result.stderr
