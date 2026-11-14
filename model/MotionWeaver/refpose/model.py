"""Reference-guided pose networks used by training and inference.

This module is intentionally free of training, Lightning and CLI imports so the
inference runtime can load the same class definitions without depending on
`training`.
"""
import torch
from torch import nn


def root_world_positions(local_pose, root_plan):
    """Apply the Root quaternion (xyzw) to local positions in torch."""
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
    """Add a per-future-frame stance goal on top of the reference pose model."""

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
    """Predict the next 24 Root transforms from 24 history frames."""

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
