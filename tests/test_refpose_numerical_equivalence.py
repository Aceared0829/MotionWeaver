"""抽离后的 refpose 模型必须与整理前实现逐元素一致。"""
import importlib.util
from pathlib import Path

import torch

from motionweaver.refpose import (
    ReferenceGuidedPose,
    RootPlanHead,
    StanceGuidedPose,
    losses,
    root_loss,
    root_world_positions,
)


def _load_legacy():
    path = Path(__file__).parent / "fixtures" / "refpose_legacy.py"
    spec = importlib.util.spec_from_file_location("refpose_legacy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


legacy = _load_legacy()


def _seeded(seed=20261114):
    torch.manual_seed(seed)


def _assert_same_state_dict(new_model, old_model):
    new_state = new_model.state_dict()
    old_state = old_model.state_dict()
    assert list(new_state) == list(old_state)
    for key in old_state:
        torch.testing.assert_close(new_state[key], old_state[key], rtol=0, atol=0)


def _assert_same_output(new_output, old_output):
    torch.testing.assert_close(new_output, old_output, rtol=0, atol=0)


def _pose_inputs(bones=3):
    generator = torch.Generator().manual_seed(7)
    history = torch.randn(2, 24, bones * 9 + 7, generator=generator)
    root = torch.randn(2, 24, 7, generator=generator)
    reference = torch.randn(2, 24, bones * 9, generator=generator)
    mask = (torch.rand(2, 24, 1, generator=generator) < 0.25).float()
    return history, root, reference, mask


def test_reference_guided_pose_matches_legacy_state_and_output():
    _seeded()
    new_model = ReferenceGuidedPose(bones=3, width=16).eval()
    _seeded()
    old_model = legacy.ReferenceGuidedPose(bones=3, width=16).eval()
    _assert_same_state_dict(new_model, old_model)
    inputs = _pose_inputs()
    with torch.no_grad():
        _assert_same_output(new_model(*inputs), old_model(*inputs))


def test_stance_guided_pose_matches_legacy_state_and_output():
    _seeded()
    new_model = StanceGuidedPose(bones=3, width=16).eval()
    _seeded()
    old_model = legacy.StanceGuidedPose(bones=3, width=16).eval()
    _assert_same_state_dict(new_model, old_model)
    history, root, reference, mask = _pose_inputs()
    goal = torch.randint(0, 2, (2, 24), generator=torch.Generator().manual_seed(11))
    with torch.no_grad():
        _assert_same_output(
            new_model(history, root, reference, mask, goal),
            old_model(history, root, reference, mask, goal),
        )


def test_root_plan_head_matches_legacy_state_and_output():
    _seeded()
    new_model = RootPlanHead(width=16).eval()
    _seeded()
    old_model = legacy.RootPlanHead(width=16).eval()
    _assert_same_state_dict(new_model, old_model)
    history = torch.randn(2, 24, 709, generator=torch.Generator().manual_seed(13))
    with torch.no_grad():
        _assert_same_output(new_model(history), old_model(history))


def test_losses_and_root_geometry_match_legacy():
    generator = torch.Generator().manual_seed(17)
    predicted = torch.randn(2, 24, 27, generator=generator)
    target = torch.randn(2, 24, 27, generator=generator)
    mask = (torch.rand(2, 24, 1, generator=generator) < 0.25).float()
    std = torch.rand(3, 3, generator=generator)

    new_values = losses(predicted, target, mask, std)
    old_values = legacy.losses(predicted, target, mask, std)
    for new_value, old_value in zip(new_values, old_values):
        torch.testing.assert_close(new_value, old_value, rtol=0, atol=0)

    root_predicted = torch.randn(2, 24, 7, generator=generator)
    root_target = torch.randn(2, 24, 7, generator=generator)
    for new_value, old_value in zip(
        root_loss(root_predicted, root_target), legacy.root_loss(root_predicted, root_target)
    ):
        torch.testing.assert_close(new_value, old_value, rtol=0, atol=0)

    local_pose = predicted.reshape(2, 24, 3, 9)[..., :3]
    _assert_same_output(
        root_world_positions(local_pose, root_predicted),
        legacy.root_world_positions(local_pose, root_predicted),
    )
