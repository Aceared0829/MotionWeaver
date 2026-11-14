"""离线评估给定真实 Root 轨迹时的非学习姿态基线；不启动模型训练或 UE。"""

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from data.runtime.conditioned_motion import (
    CONTRACT_ID,
    load_window,
    verify_source_manifest,
    with_references,
)
from data.runtime.unreal_dataset import apply_authoritative_root, read_json


def select_windows(index_file, contract, split, per_clip):
    grouped = defaultdict(list)
    with Path(index_file).open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if contract["clips"][row["clip_index"]]["split"] == split:
                grouped[row["clip_index"]].append(row)
    if not grouped:
        raise ValueError(f"分区 {split} 没有合法未来窗口")
    return [row for index in sorted(grouped) for row in
            (grouped[index] if per_clip == 0 else [grouped[index][len(grouped[index]) // 2]])]


def _interpolate_pose(visible):
    history = visible["history_pose"][-1]
    reference = visible["reference_pose"]
    mask = visible["reference_bone_mask"]
    future, bones = mask.shape
    result = np.empty_like(reference)
    for bone in range(bones):
        anchors = np.flatnonzero(mask[:, bone])
        times = np.r_[-1, anchors]
        values = np.concatenate((history[bone:bone + 1], reference[anchors, bone]), axis=0)
        for axis in range(3):
            result[:, bone, axis] = np.interp(np.arange(future), times, values[:, axis])
    return result


def _interpolate_rotations(visible):
    reference = visible["reference_rotations"]
    mask = visible["reference_bone_mask"]
    future, bones = mask.shape
    result = np.empty_like(reference)
    for bone in range(bones):
        anchors = np.flatnonzero(mask[:, bone])
        times = np.r_[-1, anchors]
        matrices = np.concatenate((visible["history_rotations"][-1:, bone], reference[anchors, bone]), axis=0)
        quaternions = Rotation.from_matrix(matrices).as_quat()
        output = np.empty((future, 4), dtype=np.float64)
        for segment in range(len(times) - 1):
            left, right = times[segment], times[segment + 1]
            first, last = quaternions[segment], quaternions[segment + 1]
            if np.dot(first, last) < 0:
                last = -last
            t = np.arange(max(0, left), right + 1)
            alpha = ((t - left) / (right - left))[:, None]
            blended = first * (1 - alpha) + last * alpha
            output[t] = blended / np.linalg.norm(blended, axis=-1, keepdims=True)
        output[times[-1] + 1:] = quaternions[-1]
        result[:, bone] = Rotation.from_quat(output).as_matrix()
    return result


def predict(visible, method):
    future, _bones = visible["reference_bone_mask"].shape
    if method == "hold":
        pose = np.repeat(visible["history_pose"][-1:], future, axis=0).copy()
        rotations = np.repeat(visible["history_rotations"][-1:], future, axis=0).copy()
        mask = visible["reference_bone_mask"]
        pose[mask] = visible["reference_pose"][mask]
        rotations[mask] = visible["reference_rotations"][mask]
        return pose, rotations
    if method == "linear_reference":
        return _interpolate_pose(visible), _interpolate_rotations(visible)
    raise ValueError(f"未知基线：{method}")


def score(sample, predicted_pose, predicted_rotations, foot_indices, fps):
    target = sample["target"]
    anchor_mask = sample["input"]["reference_bone_mask"]
    error = np.sum((predicted_pose - target["future_pose"]) ** 2, axis=-1)
    non_anchor = ~anchor_mask
    relative_rotation = np.einsum("tbij,tbjk->tbik", predicted_rotations.transpose(0, 1, 3, 2),
                                  target["future_rotations"])
    angle = np.degrees(Rotation.from_matrix(relative_rotation.reshape(-1, 3, 3)).magnitude()).reshape(error.shape)
    velocity_error = np.diff(predicted_pose, axis=0) - np.diff(target["future_pose"], axis=0)
    world = apply_authoritative_root(predicted_pose, target["future_root_track"])
    foot_speed = np.linalg.norm(np.diff(world[:, foot_indices], axis=0), axis=-1) * fps
    source_world = apply_authoritative_root(target["future_pose"], target["future_root_track"])
    source_foot_speed = np.linalg.norm(np.diff(source_world[:, foot_indices], axis=0), axis=-1) * fps
    contacts = target["future_contacts"]
    contact_pair = contacts[1:] & contacts[:-1]
    return {"non_anchor_pose_rmse_cm": float(np.sqrt(error[non_anchor].mean()) * 100) if non_anchor.any() else None,
            "all_pose_rmse_cm": float(np.sqrt(error.mean()) * 100),
            "anchor_pose_rmse_cm": float(np.sqrt(error[anchor_mask].mean()) * 100) if anchor_mask.any() else None,
            "non_anchor_rotation_mean_deg": float(angle[non_anchor].mean()) if non_anchor.any() else None,
            "velocity_error_cm_per_frame": float(np.sqrt(np.sum(velocity_error ** 2, axis=-1).mean()) * 100),
            "contact_foot_speed_mps": float(foot_speed[contact_pair].mean()) if contact_pair.any() else None,
            "source_contact_foot_speed_mps": float(source_foot_speed[contact_pair].mean()) if contact_pair.any() else None,
            "contact_pairs": int(contact_pair.sum())}


def aggregate(rows):
    by_clip = defaultdict(list)
    for row in rows:
        by_clip[row["clip_index"]].append(row)
    result = {}
    for field in ("non_anchor_pose_rmse_cm", "all_pose_rmse_cm", "anchor_pose_rmse_cm",
                  "non_anchor_rotation_mean_deg", "velocity_error_cm_per_frame", "contact_foot_speed_mps",
                  "source_contact_foot_speed_mps"):
        clip_values = [[row[field] for row in group if row[field] is not None]
                       for group in by_clip.values()]
        means = [float(np.mean(values)) for values in clip_values if values]
        result[field] = float(np.mean(means)) if means else None
    result["clips"] = len(by_clip)
    result["windows"] = len(rows)
    result["contact_pairs"] = sum(row["contact_pairs"] for row in rows)
    return result


def evaluate(contract_folder, output, *, split="test", per_clip=1):
    folder, output = Path(contract_folder).resolve(), Path(output).resolve()
    contract_path = folder / "contract.json"
    contract = read_json(contract_path)
    if contract.get("contract_id") != CONTRACT_ID:
        raise ValueError("条件动作契约不匹配")
    source = Path(contract["source_dataset"])
    verify_source_manifest(contract)
    if hashlib.sha256((source / "skeleton.json").read_bytes()).hexdigest() != contract["skeleton_sha256"]:
        raise ValueError("骨架已改变")
    skeleton = read_json(source / "skeleton.json")
    names = [bone["name"] for bone in skeleton["bones"]]
    foot_indices = [names.index(skeleton["roles"][role]) for role in
                    ("left_foot", "left_toe", "right_foot", "right_toe")]
    rows = select_windows(folder / "windows.jsonl", contract, split, per_clip)
    if output.exists():
        raise FileExistsError(f"目标目录已存在：{output}")
    scenarios = {"no_reference": (), "middle_reference": (contract["window"]["future_frames"] // 2,),
                 "end_reference": (contract["window"]["future_frames"] - 1,),
                 "middle_and_end": (contract["window"]["future_frames"] // 2,
                                    contract["window"]["future_frames"] - 1)}
    samples = []
    for index, row in enumerate(rows, 1):
        base = load_window(folder, row, contract=contract)
        for scenario, offsets in scenarios.items():
            sample = with_references(base, offsets)
            for method in ("hold", "linear_reference"):
                predicted_pose, predicted_rotations = predict(sample["input"], method)
                samples.append({"clip_index": row["clip_index"], "asset": base["metadata"]["asset"],
                                "category": base["metadata"]["category"], "start": row["start"],
                                "scenario": scenario, "baseline": method,
                                **score(sample, predicted_pose, predicted_rotations, foot_indices, contract["fps"])})
        if index % 25 == 0 or index == len(rows):
            print(f"已评估 {index}/{len(rows)} 个窗口", flush=True)
    groups = defaultdict(list)
    category_groups = defaultdict(list)
    for sample in samples:
        key = f"{sample['scenario']}/{sample['baseline']}"
        groups[key].append(sample)
        category_groups[f"{sample['category']}/{key}"].append(sample)
    report = {"schema_version": 1, "evaluation_type": "non_learning_reference_guided_pose_baselines",
              "contract": str(contract_path), "contract_sha256": hashlib.sha256(contract_path.read_bytes()).hexdigest(),
              "split": split, "selection": "all_windows" if per_clip == 0 else "middle_window_per_clip",
              "oracle_root_condition": True, "root_path_error": "zero_by_given_condition_not_a_model_metric",
              "metrics_scope": "per_clip_equal_weight_non_anchor_primary",
              "aggregate": {key: aggregate(value) for key, value in sorted(groups.items())},
              "by_category": {key: aggregate(value) for key, value in sorted(category_groups.items())},
              "samples": samples,
              "limitations": ["Root 轨迹取自原动作，是理想控制输入；不代表玩家或 CMC 轨迹误差。",
                              "硬参考直接来自目标帧，只有被选中的姿态帧作为条件公开。",
                              "保持与插值只是非学习基线，不保证骨长、IK 或实际游戏可播放质量。",
                              "来源分区的非 Traversal 语义变体可能跨分区，不能宣称完全独立泛化。"]}
    output.mkdir(parents=True, exist_ok=False)
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--split", choices=("validation", "test"), default="test")
    parser.add_argument("--per-clip", type=int, choices=(0, 1), default=1,
                        help="1 为每条动作的中心窗口；0 为全部窗口")
    args = parser.parse_args()
    report = evaluate(args.contract, args.output, split=args.split, per_clip=args.per_clip)
    print(json.dumps(report["aggregate"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
