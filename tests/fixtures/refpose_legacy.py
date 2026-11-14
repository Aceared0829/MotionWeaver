"""整理前位于训练/工具脚本中的 refpose 模型定义，作为数值回归基线。

本文件只用于 `tests/test_refpose_numerical_equivalence.py`，不是可导入的产品代码。
修改这里等于改变历史口径，必须先确认检查点兼容性。
"""
import torch
from torch import nn


def root_world_positions(local_pose, root_plan):
    quaternion = root_plan[..., 3:]
    vector = quaternion[..., :3].unsqueeze(-2)
    translation = root_plan[..., :3].unsqueeze(-2)
    twice_cross = 2 * torch.cross(vector.expand_as(local_pose), local_pose, dim=-1)
    return (
        local_pose
        + quaternion[..., 3:4].unsqueeze(-2) * twice_cross
        + torch.cross(vector.expand_as(local_pose), twice_cross, dim=-1)
        + translation
    )


class ReferenceGuidedPose(nn.Module):
    def __init__(self, bones=79, width=256):
        super().__init__()
        self.bones = bones
        self.features = bones * 9
        self.history = nn.GRU(self.features + 7, width, batch_first=True)
        self.control = nn.Linear(self.features + 8, width)
        self.time = nn.Parameter(torch.zeros(1, 24, width))
        layer = nn.TransformerEncoderLayer(
            width, 4, width * 3, dropout=0.1, batch_first=True, norm_first=True
        )
        self.temporal = nn.TransformerEncoder(layer, 3)
        self.output = nn.Sequential(nn.LayerNorm(width), nn.Linear(width, self.features))

    def forward(self, history, root_plan, reference, mask):
        _, state = self.history(history)
        control = torch.cat((root_plan, reference, mask), dim=-1)
        hidden = self.control(control) + state[-1].unsqueeze(1) + self.time
        predicted = self.output(self.temporal(hidden))
        return torch.where(mask.bool(), reference, predicted)


class StanceGuidedPose(ReferenceGuidedPose):
    def __init__(self, bones=79, width=256):
        super().__init__(bones, width)
        self.goal_stance = nn.Embedding(2, width)
        nn.init.zeros_(self.goal_stance.weight)

    def forward(self, history, root_plan, reference, mask, goal_stance):
        if goal_stance.shape != root_plan.shape[:2]:
            raise ValueError("目标状态必须逐未来帧给出")
        _, state = self.history(history)
        control = torch.cat((root_plan, reference, mask), dim=-1)
        hidden = (
            self.control(control)
            + state[-1].unsqueeze(1)
            + self.time
            + self.goal_stance(goal_stance)
        )
        predicted = self.output(self.temporal(hidden))
        return torch.where(mask.bool(), reference, predicted)


class RootPlanHead(nn.Module):
    def __init__(self, width=256):
        super().__init__()
        self.width = width
        self.history = nn.GRU(709, width, batch_first=True)
        self.time = nn.Parameter(torch.zeros(1, 24, width))
        layer = nn.TransformerEncoderLayer(
            width, 4, width * 3, dropout=0.1, batch_first=True, norm_first=True
        )
        self.temporal = nn.TransformerEncoder(layer, 3)
        self.output = nn.Sequential(nn.LayerNorm(width), nn.Linear(width, 7))
        nn.init.zeros_(self.output[-1].weight)
        nn.init.zeros_(self.output[-1].bias)
        with torch.no_grad():
            self.output[-1].bias[6] = 1.0

    def forward(self, history):
        _, state = self.history(history)
        hidden = state[-1].unsqueeze(1) + self.time
        raw = self.output(self.temporal(hidden))
        position = raw[..., :3]
        quaternion = raw[..., 3:]
        quaternion = quaternion / quaternion.norm(dim=-1, keepdim=True).clamp_min(1e-6)
        return torch.cat((position, quaternion), dim=-1)


def losses(
    predicted,
    target,
    mask,
    pose_std,
    *,
    pose_mean=None,
    root_plan=None,
    contacts=None,
    foot_indices=None,
    contact_weight=0.0,
):
    batch, frames = target.shape[:2]
    predicted = predicted.reshape(batch, frames, -1, 9)
    target = target.reshape(batch, frames, -1, 9)
    visible = (1 - mask).reshape(batch, frames, 1, 1)
    count = visible.sum() * predicted.shape[2]
    position = (
        ((predicted[..., :3] - target[..., :3]) * pose_std) ** 2 * visible
    ).sum() / (count * 3)
    rotation = (
        ((predicted[..., 3:] - target[..., 3:]) ** 2) * visible
    ).sum() / (count * 6)
    velocity = (
        (
            predicted[:, 1:, :, :3]
            - predicted[:, :-1, :, :3]
            - target[:, 1:, :, :3]
            + target[:, :-1, :, :3]
        )
        * pose_std
    ).square().mean()
    total = 10 * position + rotation + 2 * velocity
    if contact_weight:
        if pose_mean is None or root_plan is None or contacts is None or foot_indices is None:
            raise ValueError(
                "contact loss requires pose mean, Root, contact labels, and foot indices"
            )
        pose_m = predicted[..., :3] * pose_std + pose_mean
        world_feet = root_world_positions(pose_m[:, :, foot_indices], root_plan)
        speed = torch.linalg.vector_norm(world_feet[:, 1:] - world_feet[:, :-1], dim=-1) * 30
        contact_pairs = contacts[:, 1:] * contacts[:, :-1]
        contact_loss = (speed.square() * contact_pairs).sum() / contact_pairs.sum().clamp_min(1)
        total = total + contact_weight * contact_loss
    return total, position, rotation


def root_loss(predicted, target):
    position = (predicted[..., :3] - target[..., :3]).square().mean()
    dot = (predicted[..., 3:] * target[..., 3:]).sum(dim=-1).abs().clamp(max=1.0)
    rotation = (1.0 - dot.square()).mean()
    velocity = (
        predicted[:, 1:, :3]
        - predicted[:, :-1, :3]
        - target[:, 1:, :3]
        + target[:, :-1, :3]
    ).square().mean()
    total = 10.0 * position + rotation + 2.0 * velocity
    return total, position, rotation
