"""离线审计现有条件动作数据；只写新报告，不修改来源数据或分区。"""

import argparse
import base64
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from data.runtime.unreal_dataset import (
    UnrealSkeleton,
    contained_file,
    read_json,
    world_foot_contacts,
)
from scipy.spatial.transform import Rotation

from data.runtime.conditioned_motion import CONTRACT_ID, load_raw, verify_source_manifest

FOOT_ROLES = ("left_foot", "left_toe", "right_foot", "right_toe")
METRIC_FIELDS = ("root_speed_max_mps", "root_horizontal_speed_max_mps",
                 "root_vertical_speed_max_mps", "root_height_range_m",
                 "root_speed_p99_mps", "root_yaw_speed_max_degps",
                 "pelvis_world_speed_max_mps", "bone_length_deviation_max_m",
                 "contact_pair_speed_p95_mps", "contact_fraction")


def digest_arrays(raw):
    """比较真实动作内容，忽略 NPZ 压缩形式、文件名与时间戳。"""
    digest = hashlib.sha256()
    for key in ("positions", "rotations", "root_track"):
        array = np.ascontiguousarray(raw[key])
        digest.update(key.encode("ascii"))
        digest.update(str(array.shape).encode("ascii"))
        digest.update(array.tobytes())
    return digest.hexdigest()


def digest_root(track):
    digest = hashlib.sha256()
    digest.update(str(track.shape).encode("ascii"))
    digest.update(np.ascontiguousarray(track).tobytes())
    return digest.hexdigest()


def _stored_contacts(bits, frames):
    packed = np.frombuffer(base64.b64decode(bits, validate=True), dtype=np.uint8)
    unpacked = np.unpackbits(packed)
    if len(unpacked) < frames * 4:
        raise ValueError("接触掩码长度不足")
    return unpacked[:frames * 4].reshape(frames, 4).astype(bool)


def measure_clip(raw, stored_contact_bits, skeleton, parents, foot_indices, fps):
    positions, rotations, root = raw["positions"], raw["rotations"], raw["root_track"]
    frames = len(positions)
    root_rotation = Rotation.from_quat(root[:, 3:])
    root_steps = np.diff(root[:, :3], axis=0) * fps
    root_speed = np.linalg.norm(root_steps, axis=-1)
    root_horizontal_speed = np.linalg.norm(root_steps[:, [0, 2]], axis=-1)
    root_vertical_speed = np.abs(root_steps[:, 1])
    relative = root_rotation[:-1].inv() * root_rotation[1:]
    yaw_speed = np.abs(np.degrees(relative.as_rotvec()[:, 1])) * fps

    joints = np.r_[0, foot_indices]
    world = np.einsum("tij,tkj->tki", root_rotation.as_matrix(), positions[:, joints])
    world += root[:, None, :3]
    joint_speed = np.linalg.norm(np.diff(world, axis=0), axis=-1) * fps

    child_indices = np.flatnonzero(parents >= 0)
    lengths = np.linalg.norm(positions[:, child_indices] - positions[:, parents[child_indices]], axis=-1)
    medians = np.median(lengths, axis=0)
    # 很短的辅助骨容易受浮点和伸缩影响；只统计长度至少 10 cm 的实体骨段。
    substantial = medians >= 0.10
    deviation = np.abs(lengths[:, substantial] - medians[substantial])
    worst_bone = None
    worst_frame = None
    max_deviation = 0.0
    if deviation.size:
        frame, bone = np.unravel_index(np.argmax(deviation), deviation.shape)
        worst_frame = int(frame)
        worst_bone = int(child_indices[substantial][bone])
        max_deviation = float(deviation[frame, bone])

    matrices = rotations.astype(np.float64)
    orthogonality = np.einsum("...ji,...jk->...ik", matrices, matrices)
    identity = np.eye(3)
    max_orthogonality_error = float(np.max(np.abs(orthogonality - identity)))
    max_determinant_error = float(np.max(np.abs(np.linalg.det(matrices) - 1)))

    contact_tensor = world_foot_contacts(positions, root, skeleton, fps)
    if contact_tensor is None:
        raise ValueError("A root track is required to audit contacts")
    actual_contacts = contact_tensor.numpy() > 0.5
    stored_contacts = _stored_contacts(stored_contact_bits, frames)
    contact_mismatch = int(np.count_nonzero(actual_contacts != stored_contacts))
    contact_pairs = actual_contacts[1:] & actual_contacts[:-1]
    foot_speed = joint_speed[:, 1:]
    contact_speeds = foot_speed[contact_pairs]
    return {
        "frames": frames,
        "root_speed_max_mps": float(root_speed.max()),
        "root_horizontal_speed_max_mps": float(root_horizontal_speed.max()),
        "root_vertical_speed_max_mps": float(root_vertical_speed.max()),
        "root_height_range_m": float(np.ptp(root[:, 1])),
        "root_speed_p99_mps": float(np.quantile(root_speed, 0.99)),
        "root_speed_max_frame": int(np.argmax(root_speed) + 1),
        "root_yaw_speed_max_degps": float(yaw_speed.max()),
        "root_yaw_speed_max_frame": int(np.argmax(yaw_speed) + 1),
        "pelvis_world_speed_max_mps": float(joint_speed[:, 0].max()),
        "pelvis_world_speed_max_frame": int(np.argmax(joint_speed[:, 0]) + 1),
        "bone_length_deviation_max_m": max_deviation,
        "bone_length_worst_index": worst_bone,
        "bone_length_worst_frame": worst_frame,
        "rotation_orthogonality_max": max_orthogonality_error,
        "rotation_determinant_error_max": max_determinant_error,
        "contact_fraction": float(actual_contacts.mean()),
        "contact_pair_count": int(contact_pairs.sum()),
        "contact_pair_speed_p95_mps": (float(np.quantile(contact_speeds, 0.95))
                                         if len(contact_speeds) else None),
        "contact_mismatch_labels": contact_mismatch,
    }


def _quantiles(rows, field):
    values = [row[field] for row in rows if row.get(field) is not None]
    return ({"median": float(np.median(values)), "p95": float(np.quantile(values, 0.95)),
             "max": float(max(values))} if values else None)


def audit(contract_folder, output, progress=None):
    folder, output = Path(contract_folder).resolve(), Path(output).resolve()
    contract_path = folder / "contract.json"
    contract = read_json(contract_path)
    if contract.get("contract_id") != CONTRACT_ID:
        raise ValueError("只审计当前 Root/姿态条件契约")
    if output.exists():
        raise FileExistsError(f"报告目录已存在：{output}")
    source = Path(contract["source_dataset"])
    # 清单文件名取自契约（旧契约 dataset.json，参考引导契约 clips.jsonl）。
    manifest_path = verify_source_manifest(contract)
    manifest = read_json(manifest_path)
    skeleton_json = read_json(source / "skeleton.json")
    if hashlib.sha256((source / "skeleton.json").read_bytes()).hexdigest() != contract["skeleton_sha256"]:
        raise ValueError("来源骨架已改变，拒绝沿用旧契约")
    skeleton = UnrealSkeleton(source)
    names = [bone["name"] for bone in skeleton_json["bones"]]
    parents = np.array([bone["parent"] for bone in skeleton_json["bones"]], dtype=np.int32)
    foot_indices = np.array([names.index(skeleton_json["roles"][role]) for role in FOOT_ROLES])
    if len(manifest["clips"]) != len(contract["clips"]):
        raise ValueError("清单与条件契约片段数不一致")

    rows, errors = [], []
    raw_hashes, content_hashes, root_hashes = defaultdict(list), defaultdict(list), defaultdict(list)
    groups = defaultdict(set)
    for index, (item, clip) in enumerate(zip(manifest["clips"], contract["clips"])):
        if (clip["clip_index"] != index or clip["asset"] != item["asset"]
                or clip["split"] != item["split"] or clip["source_frames"] != item["source_frames"]):
            raise ValueError(f"来源片段与契约错位：{index}")
        groups[clip["group"]].add(clip["split"])
        row = {"clip_index": index, "asset": clip["asset"], "split": clip["split"],
               "category": clip["category"], "frames": clip["source_frames"],
               "generation_windows": clip["windows"],
               "excluded_from_conditioned_dataset": clip.get("excluded_from_conditioned_dataset", False),
               "flags": []}
        try:
            raw, digest = load_raw(contained_file(source, clip["raw_file"]), clip["source_frames"],
                                   contract["bone_count"], contract["fps"],
                                   expected_sha256=clip["raw_sha256"])
            raw_hashes[digest].append(index)
            content_hashes[digest_arrays(raw)].append(index)
            root_hashes[digest_root(raw["root_track"])].append(index)
            row.update(measure_clip(raw, clip["contact_bits"], skeleton, parents, foot_indices,
                                    contract["fps"]))
            if row["contact_mismatch_labels"]:
                row["flags"].append("contact_index_mismatch")
            if row["rotation_orthogonality_max"] > 0.01 or row["rotation_determinant_error_max"] > 0.01:
                row["flags"].append("invalid_rotation_matrix")
            if row["root_speed_max_mps"] > 15:
                row["flags"].append("root_speed_over_15_mps_review")
            if row["root_height_range_m"] > 10:
                row["flags"].append("root_height_range_over_10_m_review")
            if row["root_yaw_speed_max_degps"] > 900:
                row["flags"].append("root_yaw_over_900_degps_review")
            if row["pelvis_world_speed_max_mps"] > 20:
                row["flags"].append("pelvis_speed_over_20_mps_review")
            if row["bone_length_deviation_max_m"] > 0.10:
                row["flags"].append("bone_length_deviation_over_10_cm_review")
            if clip["category"] in ("Walk", "Run", "Crouch", "Sprint", "Idle") and row["contact_fraction"] == 0:
                row["flags"].append("locomotion_without_derived_foot_contact_review")
        except (OSError, ValueError, KeyError) as exc:
            row["flags"].append("integrity_error")
            errors.append({"clip_index": index, "asset": clip["asset"], "error": str(exc)})
        rows.append(row)
        if progress and ((index + 1) % 100 == 0 or index + 1 == len(contract["clips"])):
            progress(index + 1, len(contract["clips"]))

    duplicate_groups = []
    training_exclusion_candidates = []
    for kind, mapping in (("raw_npz", raw_hashes), ("motion_arrays", content_hashes)):
        for indices in mapping.values():
            if len(indices) > 1:
                splits = sorted({rows[i]["split"] for i in indices})
                active = [i for i in indices if not rows[i]["excluded_from_conditioned_dataset"]]
                active_splits = sorted({rows[i]["split"] for i in active})
                duplicate_groups.append({"kind": kind, "clip_indices": indices, "splits": splits,
                                         "cross_split": len(splits) > 1,
                                         "active_cross_split": len(active_splits) > 1})
                if kind == "motion_arrays" and len(active_splits) > 1:
                    for index in active:
                        rows[index]["flags"].append("exact_motion_cross_split")
                    training_exclusion_candidates.extend(
                        {"clip_index": index, "asset": rows[index]["asset"],
                         "reason": "exact_motion_also_in_holdout"}
                        for index in active if rows[index]["split"] == "train")
                elif kind == "motion_arrays" and len(active_splits) == 1 and active_splits[0] == "train":
                    training_exclusion_candidates.extend(
                        {"clip_index": index, "asset": rows[index]["asset"],
                         "reason": "exact_motion_duplicate_in_train"}
                        for index in active[1:])
    cross_split_groups = {group: sorted(splits) for group, splits in groups.items() if len(splits) > 1}
    root_duplicates = [indices for indices in root_hashes.values() if len(indices) > 1]
    root_cross_split = [indices for indices in root_duplicates
                        if len({rows[i]["split"] for i in indices}) > 1]
    flagged = [row for row in rows if row["flags"]]
    # 自动异常全收；另外每个类别抽取一个确定性的中位片段，便于轻量骨架复核。
    by_category = defaultdict(list)
    for row in rows:
        if not row["flags"] and row["generation_windows"]:
            by_category[row["category"]].append(row)
    sample = [sorted(group, key=lambda row: row["asset"])[len(group) // 2]
              for _, group in sorted(by_category.items()) if group]
    review = sorted({row["clip_index"]: row for row in flagged + sample}.values(),
                    key=lambda row: (not bool(row["flags"]), row["category"], row["asset"]))
    category_counts = Counter(row["category"] for row in rows)
    flag_counts = Counter(flag for row in rows for flag in row["flags"])
    report = {
        "schema_version": 1, "audit_type": "offline_conditioned_motion_quality_v1",
        "source_dataset": str(source), "contract": str(contract_path),
        "contract_sha256": hashlib.sha256(contract_path.read_bytes()).hexdigest(),
        "source_manifest_sha256": contract["source_manifest_sha256"],
        "clips": len(rows), "checked_clips": len(rows) - len(errors), "integrity_errors": errors,
        "split_counts": dict(Counter(row["split"] for row in rows)),
        "category_counts": dict(sorted(category_counts.items())),
        "generation_windows": sum(row["generation_windows"] for row in rows),
        "clips_without_generation_window": sum(row["generation_windows"] == 0 for row in rows),
        "cross_split_contract_groups": cross_split_groups,
        "duplicate_motion_groups": duplicate_groups,
        "active_exact_motion_cross_split_groups": sum(
            row["kind"] == "motion_arrays" and row["active_cross_split"] for row in duplicate_groups),
        "training_exclusion_candidates": training_exclusion_candidates,
        "identical_root_track_groups": len(root_duplicates),
        "identical_root_track_cross_split_groups": len(root_cross_split),
        "identical_root_track_cross_split_examples": root_cross_split[:20],
        "flag_counts": dict(sorted(flag_counts.items())),
        "zero_derived_contact_clips": sum(row.get("contact_fraction") == 0 for row in rows),
        "zero_derived_contact_by_category": dict(sorted(Counter(
            row["category"] for row in rows if row.get("contact_fraction") == 0).items())),
        "flagged_clips": len(flagged), "review_queue_clips": len(review),
        "metric_distributions": {field: _quantiles(rows, field) for field in METRIC_FIELDS},
        "thresholds": {"root_speed_review_mps": 15, "root_height_range_review_m": 10,
                       "root_yaw_review_degps": 900,
                       "pelvis_speed_review_mps": 20, "bone_length_deviation_review_m": 0.10,
                       "rotation_matrix_error": 0.01},
        "conclusion_boundary": "flags_are_review_candidates_not_automatic_exclusions",
        "limitations": ["无场景几何与蒙皮画面；几何阈值只能筛疑似异常，不能自动判定动作无效。",
                        "接触标签来自脚速/高度启发式，复算一致不等于物理接触真值。",
                        "精确数组重复可查；非 Traversal 语义近似动作跨分区仍需人工核查。"],
    }
    output.mkdir(parents=True, exist_ok=False)
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "review_queue.json").write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with (output / "clips.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value
                             for key, value in row.items()})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True, help="现有轻量条件索引目录")
    parser.add_argument("--output", required=True, help="尚不存在的报告目录")
    args = parser.parse_args()
    report = audit(args.contract, args.output,
                   progress=lambda done, total: print(f"已审计 {done}/{total} 段", flush=True))
    print(json.dumps({key: report[key] for key in
                      ("clips", "checked_clips", "flagged_clips", "review_queue_clips", "flag_counts")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
