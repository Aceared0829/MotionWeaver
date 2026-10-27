"""离线 Root 代理与接触候选；不推断真实 Actor、场景或接触真值。"""

import numpy as np
from scipy.spatial.transform import Rotation

from data.runtime.motion_features import derivative, extract_features
from data.runtime.motion_kinematics import world_positions

POLICY = {
    "version": "soma_processing_v1",
    "time": "declared_source_seconds_no_retime",
    "root": "hips_xz_y_zero_anatomical_yaw",
    "vertical": "all_recorded_height_in_body",
    "invalid_heading": "identity_rotation_with_invalid_mask",
    "root_status": "offline_proxy_not_observed_actor_root",
    "contact_status": "heuristic_candidates_not_ground_truth",
    "contact_states": {"unknown": -1, "noncontact_candidate": 0, "contact_candidate": 1},
    "slow_enter_m_s": 0.12,
    "slow_leave_m_s": 0.25,
    "minimum_stable_seconds": 0.10,
    "maximum_stable_height_range_m": 0.025,
    "height_cluster_tolerance_m": 0.04,
    "swing_speed_m_s": 0.50,
    "swing_vertical_speed_m_s": 0.15,
    "swing_height_margin_m": 0.06,
}
CONTACT_ROLES = ("left_foot", "right_foot", "left_hand", "right_hand")


def runs(mask):
    """返回含首尾帧的连续区间；不改变或补齐时间轴。"""
    edges = np.diff(np.r_[False, mask, False].astype(np.int8))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1) - 1))


def stable_runs(speed, heights, times):
    result = []
    for start, end in runs(speed <= POLICY["slow_leave_m_s"]):
        if (times[end] - times[start] >= POLICY["minimum_stable_seconds"]
                and np.min(speed[start:end + 1]) <= POLICY["slow_enter_m_s"]
                and np.ptp(heights[start:end + 1]) <= POLICY["maximum_stable_height_range_m"]):
            result.append((int(start), int(end)))
    return result


def contact_candidates(world, times, names, roles):
    """脚用踝/趾代理点，手只给运动线索；稳定高度不是已知支撑面。"""
    count = len(times)
    states = np.full((count, len(CONTACT_ROLES)), -1, dtype=np.int8)
    speed = np.zeros((count, len(CONTACT_ROLES)))
    heights = np.zeros_like(speed)
    stable = np.zeros_like(states, dtype=bool)
    evidence, summaries = {}, {}
    for column, role in enumerate(CONTACT_ROLES):
        joints = [names.index(roles[role])]
        if "foot" in role:
            joints.append(names.index(roles[role.replace("foot", "toe")]))
        points = world[:, joints]
        point_velocity = derivative(points, times)
        point_speed = np.linalg.norm(point_velocity, axis=-1)
        # 允许脚掌绕一个代理点转动；这仍不是脚底网格的几何接触。
        speed[:, column] = point_speed.min(axis=1)
        heights[:, column] = points[:, :, 1].min(axis=1)
        intervals = stable_runs(speed[:, column], heights[:, column], times)
        for start, end in intervals:
            stable[start:end + 1, column] = True
        summaries[role] = {"joint_names": [names[j] for j in joints],
                           "stable_intervals": [[a, b] for a, b in intervals]}
        if "hand" in role:
            summaries[role]["contact_status"] = "unknown_without_scene_geometry"
            continue
        levels = sorted(float(np.median(heights[a:b + 1, column])) for a, b in intervals)
        clusters = []
        for level in levels:
            if not clusters or level - np.mean(clusters[-1]) > POLICY["height_cluster_tolerance_m"]:
                clusters.append([level])
            else:
                clusters[-1].append(level)
        evidence[role] = [float(np.median(cluster)) for cluster in clusters]
        states[stable[:, column], column] = 1
        if levels:
            distance = np.min(np.abs(heights[:, column, None] - np.asarray(evidence[role])), axis=1)
            vertical_speed = np.abs(derivative(heights[:, column], times))
            swing = ((speed[:, column] >= POLICY["swing_speed_m_s"])
                     & (vertical_speed >= POLICY["swing_vertical_speed_m_s"])
                     & (distance > POLICY["swing_height_margin_m"]))
            states[swing, column] = 0
        summaries[role].update(
            stable_height_levels_m=evidence[role],
            support_surface="unknown_inferred_proxy_heights_only",
            contact_intervals=[[int(a), int(b)] for a, b in runs(states[:, column] == 1)],
            noncontact_intervals=[[int(a), int(b)] for a, b in runs(states[:, column] == 0)])
    arrays = {"contact_states": states, "contact_known_mask": states >= 0,
              "limb_speed_m_s": speed, "limb_proxy_height_m": heights, "limb_stable_mask": stable}
    return arrays, summaries


def process_motion(motion, roles, *, world=None):
    """保存可逆的离线位置表示；未来 Root 与接触候选均不能冒充在线输入。"""
    if world is None:
        world = world_positions(motion)
    features = extract_features(motion, roles, world=world)
    pelvis = world[:, motion.skeleton.names.index(roles["pelvis"])]
    root_position = pelvis.copy()
    root_position[:, 1] = 0
    forward = features["heading_forward_xz"]
    yaw = np.arctan2(forward[:, 0], forward[:, 1])
    root_rotation = Rotation.from_rotvec(np.column_stack((np.zeros(len(yaw)), yaw, np.zeros(len(yaw)))))
    matrices = root_rotation.as_matrix()
    body = np.einsum("tji,tkj->tki", matrices, world - root_position[:, None])
    reconstructed = np.einsum("tij,tkj->tki", matrices, body) + root_position[:, None]
    error = float(np.max(np.abs(reconstructed - world)))
    contacts, summary = contact_candidates(world, motion.timestamps, motion.skeleton.names, roles)
    arrays = {"timestamps": motion.timestamps.copy(),
              "root_proxy": np.column_stack((root_position, root_rotation.as_quat())),
              "body_positions": body, "heading_valid": features["heading_valid"], **contacts}
    return arrays, {"recomposition_max_abs_error_m": error,
                    "heading_valid_fraction": float(features["heading_valid"].mean()),
                    "contact_roles": list(CONTACT_ROLES), "limbs": summary,
                    "contact_candidate_fraction": (contacts["contact_states"] == 1).mean(axis=0).tolist(),
                    "unknown_fraction": (contacts["contact_states"] == -1).mean(axis=0).tolist(),
                    "local_joint_trs": "unchanged_in_source_motion_not_replaced_by_body_positions",
                    "contact_truth": "unknown", "scene_geometry": "unknown",
                    "training_status": "position_representation_pilot_not_model_ready_dataset"}
