"""离线运动数值与有效朝向；不生成模型请求或动作语义。"""

import numpy as np

from data.runtime.motion_kinematics import world_positions
from data.runtime.native_motion import validate_sampled_motion

FEATURE_VERSION = "soma_kinematics_v2"
ROLES = ("pelvis", "left_hip", "right_hip", "left_shoulder", "right_shoulder")
PARAMETERS = {"heading_min_width_m": 0.05, "shoulder_anchor_min_width_m": 0.01,
              "heading_torso_up_cos_min": 0.5, "hip_shoulder_alignment_cos_min": 0.5}


def derivative(values, times):
    return np.gradient(values, times, axis=0) if len(times) > 1 else np.zeros_like(values)


def extract_features(motion, roles, *, world=None):
    validate_sampled_motion(motion)
    names = motion.skeleton.names
    indices = {key: names.index(roles[key]) for key in ROLES}
    if world is None:
        world = world_positions(motion)
    p = {key: world[:, index] for key, index in indices.items()}
    times = motion.timestamps
    pelvis_v = derivative(p["pelvis"], times)
    hip_right = p["right_hip"] - p["left_hip"]
    shoulder_right = p["right_shoulder"] - p["left_shoulder"]
    torso_up = (p["left_shoulder"] + p["right_shoulder"] - p["left_hip"] - p["right_hip"]) / 2
    hip_xz, shoulder_xz = hip_right[:, [0, 2]], shoulder_right[:, [0, 2]]
    hip_width = np.linalg.norm(hip_xz, axis=1)
    shoulder_width = np.linalg.norm(shoulder_xz, axis=1)
    torso_length = np.linalg.norm(torso_up, axis=1)
    safe_hip = np.maximum(hip_width, 1e-12)
    agreement = np.sum(hip_xz * shoulder_xz, axis=1) / np.maximum(hip_width * shoulder_width, 1e-12)
    valid = ((hip_width >= PARAMETERS["heading_min_width_m"])
             & (shoulder_width >= PARAMETERS["shoulder_anchor_min_width_m"])
             & (torso_up[:, 1] / np.maximum(torso_length, 1e-12) >= PARAMETERS["heading_torso_up_cos_min"])
             & (agreement >= PARAMETERS["hip_shoulder_alignment_cos_min"]))
    # Motion空间：右侧轴为髋部Left→Right；世界up×right得到人体前向。
    forward_xz = np.column_stack((hip_xz[:, 1], -hip_xz[:, 0])) / safe_hip[:, None]
    forward_xz[~valid] = 0
    right_xz = np.column_stack((-forward_xz[:, 1], forward_xz[:, 0]))
    horizontal_v = pelvis_v[:, [0, 2]]
    body_velocity = np.column_stack((np.sum(horizontal_v * forward_xz, axis=1),
                                     np.sum(horizontal_v * right_xz, axis=1)))
    heading = np.arctan2(forward_xz[:, 0], forward_xz[:, 1])
    heading_step = np.zeros(len(times))
    pair_valid = np.r_[False, valid[1:] & valid[:-1]]
    if len(times) > 1:
        heading_step[1:] = np.arctan2(np.sin(np.diff(heading)), np.cos(np.diff(heading)))
    heading_step[~pair_valid] = 0
    angular_speed = np.zeros(len(times))
    if len(times) > 1:
        angular_speed[1:] = np.degrees(heading_step[1:]) / np.diff(times)
    return {"timestamps": times.copy(), "role_positions": world[:, [indices[key] for key in ROLES]],
            "pelvis_velocity": pelvis_v, "heading_forward_xz": forward_xz, "heading_valid": valid,
            "heading_step_deg": np.degrees(heading_step), "heading_step_valid": pair_valid,
            "heading_angular_speed_deg_s": angular_speed, "body_forward_right_speed_m_s": body_velocity}
