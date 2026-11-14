"""Losses for reference-guided pose and future Root heads."""
import torch

from .model import root_world_positions


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
    fps=30.0,
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
        # fps 必须跟数据契约走：脚速是「帧间位移 × 帧率」，帧率填错不是量纲装饰，
        # 它进平方后按 (fps_真 / fps_填)^2 放大或缩小整项。2026-11-14 之前的实现把
        # 30 写死，而 full_release_v2 是 120 fps，于是 contact_weight=0.4 实际只生效
        # 1/16（实测 2.916e-04 vs 4.666e-03，比值 16.00）。默认 30 只是兼容旧契约与
        # 冻结的 legacy fixture，新调用方一律显式传契约里的 fps。
        speed = torch.linalg.vector_norm(world_feet[:, 1:] - world_feet[:, :-1], dim=-1) * fps
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

