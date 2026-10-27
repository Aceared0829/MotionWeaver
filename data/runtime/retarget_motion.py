"""显式人体映射：骨段方向与扭转迁移到固定 SOMA 骨长，不执行接触 IK。"""

from dataclasses import replace

import numpy as np
from scipy.spatial.transform import Rotation

from data.runtime.motion_kinematics import world_positions
from data.runtime.native_motion import SampledMotion, validate_sampled_motion
from data.runtime.native_motion_io import skeleton_signature


def rest_motion(skeleton, source):
    return SampledMotion(skeleton, np.array([0.]), skeleton.rest_positions[None],
                         skeleton.rest_rotations[None], skeleton.rest_scales[None],
                         np.zeros((len(skeleton.names), 3), dtype=bool), source, 0., 30., 0)


def world_rotations(motion, affine=False):
    matrices = Rotation.from_quat(motion.rotations.reshape(-1, 4)).as_matrix().reshape(*motion.rotations.shape[:2], 3, 3)
    matrices *= motion.scales[..., None, :]
    for joint, parent in enumerate(motion.skeleton.parents):
        if parent >= 0:
            matrices[:, joint] = matrices[:, parent] @ matrices[:, joint]
    if affine:
        return matrices
    left, _, right = np.linalg.svd(matrices)
    return left @ right


def frame_matrix(primary, secondary):
    """第一轴沿骨段，第二轴由显式扭转锚点投影确定。"""
    lengths = np.linalg.norm(primary, axis=-1, keepdims=True)
    if np.any(lengths < 1e-8):
        raise ValueError("重定向骨段长度为零")
    first = primary / lengths
    second = secondary - np.sum(secondary * first, axis=-1, keepdims=True) * first
    lengths = np.linalg.norm(second, axis=-1, keepdims=True)
    if np.any(lengths < 1e-6):
        raise ValueError("重定向扭转锚点退化")
    second /= lengths
    return np.stack((first, second, np.cross(first, second)), axis=-1)


class SomaRetargeter:
    def __init__(self, source, target, mapping):
        if skeleton_signature(source.skeleton) not in mapping["source_signatures"]:
            raise ValueError("来源骨架签名没有经过映射核对")
        if skeleton_signature(target) != mapping["target_signature"]:
            raise ValueError("目标骨架不是已冻结的 SOMA 参考")
        self.target, self.mapping = target, mapping
        self.source_signature = skeleton_signature(source.skeleton)
        self.pairs = {target.names.index(t): source.skeleton.names.index(s) for t, s in mapping["joints"].items()}
        self.pelvis = target.names.index("Hips")
        self.source_pelvis = self.pairs[self.pelvis]
        sr = rest_motion(source.skeleton, source.source)
        tr = rest_motion(target, source.source)
        self.source_rest = world_positions(sr)[0]
        self.target_rest = world_positions(tr)[0]
        self.source_rest_rotations = world_rotations(sr)[0]
        self.source_rest_linear = world_rotations(sr, affine=True)[0]
        self.target_rest_rotations = world_rotations(tr)[0]
        left = source.skeleton.names.index(mapping["joints"]["LeftLeg"])
        right = source.skeleton.names.index(mapping["joints"]["RightLeg"])
        lateral = self.source_rest[left] - self.source_rest[right]
        if np.linalg.norm(lateral[[0, 2]]) < 0.01:
            raise ValueError("来源参考髋部不能确定人体朝向")
        target_lateral = self.target_rest[target.names.index("LeftLeg")] - self.target_rest[target.names.index("RightLeg")]
        yaw = np.arctan2(lateral[2], lateral[0]) - np.arctan2(target_lateral[2], target_lateral[0])
        self.alignment = Rotation.from_euler("y", yaw).as_matrix()
        # 统一参考骨盆高度，按真实左右腿骨长的均值缩放来源位移。
        lengths = []
        for prefix in ("Left", "Right"):
            ti = [target.names.index(prefix + name) for name in ("Leg", "Shin", "Foot")]
            si = [self.pairs[i] for i in ti]
            lengths.append(np.linalg.norm(np.diff(self.target_rest[ti], axis=0), axis=1).sum()
                           / np.linalg.norm(np.diff(self.source_rest[si], axis=0), axis=1).sum())
        self.translation_scale = float(np.mean(lengths))
        self.aims = {}
        for name, child_name in mapping["aim_children"].items():
            joint, child = target.names.index(name), target.names.index(child_name)
            si, ci = self.pairs[joint], self.pairs[child]
            primary = self.source_rest[ci] - self.source_rest[si]
            primary /= np.linalg.norm(primary)
            # 轴选择在参考姿态中固定，动画时随来源关节旋转，避免逐帧切轴。
            canonical_primary = self.alignment @ primary
            target_direction = self.target_rest[child] - self.target_rest[joint]
            target_direction /= np.linalg.norm(target_direction)
            anchor = np.eye(3)[np.argmin(np.maximum(np.abs(canonical_primary), np.abs(target_direction)))]
            source_anchor = np.linalg.solve(self.source_rest_linear[si], self.alignment.T @ anchor)
            target_primary = self.target_rest_rotations[joint].T @ (self.target_rest[child] - self.target_rest[joint])
            target_anchor = self.target_rest_rotations[joint].T @ anchor
            self.aims[joint] = (ci, source_anchor, frame_matrix(target_primary, target_anchor))

    def retarget(self, motion):
        validate_sampled_motion(motion)
        if skeleton_signature(motion.skeleton) != self.source_signature:
            raise ValueError("不能复用其他来源骨架的重定向校正")
        source_positions = world_positions(motion)
        source_rotations = world_rotations(motion)
        source_linear = world_rotations(motion, affine=True)
        count, target = len(motion.timestamps), self.target
        positions = np.broadcast_to(target.rest_positions, (count, *target.rest_positions.shape)).copy()
        rotations = np.broadcast_to(target.rest_rotations, (count, *target.rest_rotations.shape)).copy()
        scales = np.broadcast_to(target.rest_scales, (count, *target.rest_scales.shape)).copy()
        global_rotations = np.empty((count, len(target.names), 3, 3))
        mask = np.zeros((len(target.names), 3), dtype=bool)
        source_valid = motion.track_mask[:, 1].copy()
        for joint, parent in enumerate(motion.skeleton.parents):
            if parent >= 0:
                source_valid[joint] |= source_valid[parent]
        for joint, parent in enumerate(target.parents):
            parent_rotation = global_rotations[:, parent] if parent >= 0 else np.eye(3)
            if joint in self.pairs:
                si = self.pairs[joint]
                if joint in self.aims:
                    ci, anchor, target_frame = self.aims[joint]
                    primary = (source_positions[:, ci] - source_positions[:, si]) @ self.alignment.T
                    secondary = np.einsum("tij,j->ti", self.alignment @ source_linear[:, si], anchor)
                    desired = frame_matrix(primary, secondary) @ target_frame.T
                else:
                    correction = self.source_rest_rotations[si].T @ self.alignment.T @ self.target_rest_rotations[joint]
                    desired = self.alignment @ source_rotations[:, si] @ correction
                local = np.swapaxes(parent_rotation, -1, -2) @ desired
                rotations[:, joint] = Rotation.from_matrix(local).as_quat()
                mask[joint, 1] = source_valid[si]
            else:
                local = Rotation.from_quat(rotations[:, joint]).as_matrix()
                desired = parent_rotation @ local
            global_rotations[:, joint] = desired
        positions[:, self.pelvis] = (source_positions[:, self.source_pelvis] - self.source_rest[self.source_pelvis]) @ self.alignment.T * self.translation_scale + self.target_rest[self.pelvis]
        mask[self.pelvis, 0] = True
        details = dict(motion.source.details, retarget={
            "version": self.mapping["id"], "source_signature": self.source_signature,
            "target_signature": skeleton_signature(target), "alignment": self.alignment.tolist(),
            "translation_scale": self.translation_scale, "root_policy": "static_soma_root_full_recorded_motion_in_hips",
            "scale_policy": "source_affine_fk_target_fixed_lengths_no_stretch_transfer",
            "unmapped_joints": [name for i, name in enumerate(target.names) if i not in self.pairs],
            "rotation_supervision_mask": mask[:, 1].tolist(), "training_ready": False,
            "quality": "geometric_candidate_no_contact_ik_or_skin_acceptance"})
        result = SampledMotion(target, motion.timestamps.copy(), positions, rotations, scales, mask,
                               replace(motion.source, details=details), motion.source_duration, motion.fps, 0)
        validate_sampled_motion(result)
        world = world_positions(result)
        errors = []
        for joint, (ci, _, _) in self.aims.items():
            child = target.names.index(self.mapping["aim_children"][target.names[joint]])
            a = world[:, child] - world[:, joint]
            b = (source_positions[:, ci] - source_positions[:, self.pairs[joint]]) @ self.alignment.T
            errors.append(np.linalg.norm(np.cross(a / np.linalg.norm(a, axis=1)[:, None],
                                                   b / np.linalg.norm(b, axis=1)[:, None]), axis=1).max())
        # 不能用仅有叉积的误差放过反向骨段。
        cosines = []
        for name, child_name in self.mapping["aim_children"].items():
            joint, child = target.names.index(name), target.names.index(child_name)
            a = world[:, child] - world[:, joint]
            b = (source_positions[:, self.pairs[child]] - source_positions[:, self.pairs[joint]]) @ self.alignment.T
            cosines.append(np.min(np.sum(a * b, axis=1) / (np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1))))
        metrics = {"max_bone_direction_sine_error": float(max(errors)), "min_bone_direction_cosine": float(min(cosines)),
                   "pelvis_path_max_error_m": float(np.max(np.abs(world[:, self.pelvis] - positions[:, self.pelvis]))),
                   "translation_scale": self.translation_scale,
                   "source_max_scale_deviation": float(np.max(np.abs(motion.scales - 1))),
                   "mapped_joints": len(self.pairs), "reference_only_joints": len(target.names) - len(self.pairs)}
        if metrics["max_bone_direction_sine_error"] > 1e-6 or metrics["min_bone_direction_cosine"] < 1 - 1e-6:
            raise ValueError("骨段方向重合成校验失败")
        return result, metrics
