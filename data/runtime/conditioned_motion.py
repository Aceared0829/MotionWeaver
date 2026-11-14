"""从已有 UE Root/姿态轨道构造参考引导生成任务；不依赖 UE 编辑器。"""

import base64
import hashlib
import io
import json
from collections import Counter
from pathlib import Path

import numpy as np
from data.runtime.unreal_dataset import (
    UnrealSkeleton,
    contained_file,
    read_json,
    training_signature,
    world_foot_contacts,
)
from scipy.spatial.transform import Rotation

CONTRACT_ID = "reference_guided_root_pose_v1"
SPLITS = ("train", "validation", "test")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def window_starts(length, history_frames, future_frames, stride_frames):
    """只从真实帧取窗，并包含最后一个合法窗口。"""
    if min(history_frames, future_frames, stride_frames) <= 0:
        raise ValueError("历史、未来和步长必须为正整数")
    last = length - history_frames - future_frames
    if last < 0:
        return []
    starts = list(range(0, last + 1, stride_frames))
    if starts[-1] != last:
        starts.append(last)
    return starts


def load_raw(raw_file, expected_frames, bone_count, fps, *, expected_sha256=None):
    data = Path(raw_file).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError(f"原始动作文件哈希已改变：{raw_file}")
    with np.load(io.BytesIO(data), allow_pickle=False) as archive:
        required = {"positions", "rotations", "timestamps", "root_track"}
        if not required.issubset(archive.files):
            raise ValueError(f"缺少独立 Root 或姿态轨道：{raw_file}")
        raw = {name: archive[name] for name in required}
    if (raw["positions"].shape != (expected_frames, bone_count, 3)
            or raw["rotations"].shape != (expected_frames, bone_count, 3, 3)
            or raw["root_track"].shape != (expected_frames, 7)
            or raw["timestamps"].shape != (expected_frames,)):
        raise ValueError(f"原始动作的帧数或骨骼维度不匹配：{raw_file}")
    if expected_frames < 2 or any(not np.isfinite(value).all() for value in raw.values()):
        raise ValueError(f"原始动作包含无效数值或不足两帧：{raw_file}")
    if (abs(float(raw["timestamps"][0])) > 2e-6
            or not np.allclose(np.diff(raw["timestamps"]), 1 / fps, atol=2e-6, rtol=1e-5)):
        raise ValueError(f"原始时间轴不满足固定帧率：{raw_file}")
    norms = np.linalg.norm(raw["root_track"][:, 3:], axis=-1)
    if np.any(np.abs(norms - 1) > 0.01):
        raise ValueError(f"Root 四元数未归一化：{raw_file}")
    return raw, digest


def root_local(track, origin):
    """所有 Root 条件相对最后一帧历史 Root，避免泄露世界绝对位置。"""
    reference = Rotation.from_quat(origin[3:])
    positions = reference.inv().apply(track[:, :3] - origin[:3])
    rotations = (reference.inv() * Rotation.from_quat(track[:, 3:])).as_quat()
    return np.concatenate((positions, rotations), axis=-1).astype(np.float32)


def root_velocity(track, fps):
    rotation = Rotation.from_quat(track[:, 3:])
    translation = rotation[:-1].inv().apply(np.diff(track[:, :3], axis=0)) * fps
    yaw_rate = (rotation[:-1].inv() * rotation[1:]).as_rotvec()[:, 1:2] * fps
    return np.concatenate((translation, yaw_rate), axis=-1).astype(np.float32)


def _save_stats(folder, name, total, squares, count):
    if count == 0:
        raise ValueError("训练分区没有真实帧，无法计算统计量")
    mean = total / count
    variance = np.maximum(squares / count - mean ** 2, 0)
    mean_path, std_path = folder / f"{name}_mean.npy", folder / f"{name}_std.npy"
    np.save(mean_path, mean.astype(np.float32))
    np.save(std_path, np.maximum(np.sqrt(variance), 1e-4).astype(np.float32))
    return {"mean": mean_path.name, "mean_sha256": sha256(mean_path),
            "std": std_path.name, "std_sha256": sha256(std_path), "valid_frames": count}


def source_manifest_path(contract):
    """来源清单的路径：旧契约是 dataset.json，参考引导契约是 clips.jsonl。

    为什么要显式取名字：契约里已经写了 source_manifest_name
    （build_refpose_release.py 写的是清单的真实文件名），但早期实现把所有
    校验点都硬编码成 dataset.json，导致 Schema v3 的参考引导契约虽然哈希
    完全对得上，却在读清单时直接 FileNotFoundError。
    """
    source = Path(contract["source_dataset"])
    name = contract.get("source_manifest_name", "dataset.json")
    path = source / name
    if not path.is_file():
        raise FileNotFoundError(f"来源数据清单不存在：{path}")
    return path


def verify_source_manifest(contract):
    """来源清单内容必须与契约记录一致；返回清单路径。"""
    path = source_manifest_path(contract)
    if sha256(path) != contract["source_manifest_sha256"]:
        raise ValueError("来源数据清单已改变")
    return path


def build_index(source, output, *, history_frames=24, future_frames=24, stride_frames=4,
                exclude_train_indices=(), progress=None):
    """一遍读取原始 NPZ；仅写窗口索引、派生接触掩码与训练分区统计量。"""
    source, output = Path(source).resolve(), Path(output).resolve()
    manifest_path = source / "dataset.json"
    manifest = read_json(manifest_path)
    if manifest.get("schema_version") != 2 or manifest.get("training_contract") != "pose_only_root_authoritative":
        raise ValueError("只接受独立 Root 的 Schema v2 数据集；旧模型训练契约保持不变")
    if manifest.get("training_signature") != training_signature(source):
        raise ValueError("来源数据的骨架或统计量签名不匹配")
    if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0
           for value in (history_frames, future_frames, stride_frames)):
        raise ValueError("窗口参数必须为正整数")
    excluded = set(exclude_train_indices)
    if any(not isinstance(index, int) or isinstance(index, bool) or index < 0
           or index >= len(manifest["clips"]) or manifest["clips"][index].get("split") != "train"
           for index in excluded):
        raise ValueError("只能按清单索引排除训练分区片段")
    if output.exists():
        raise FileExistsError(f"目标目录已存在：{output}")
    skeleton_json = read_json(source / "skeleton.json")
    skeleton = UnrealSkeleton(source)
    bone_count, fps = len(skeleton_json["bones"]), manifest["fps"]
    output.mkdir(parents=True, exist_ok=False)
    stats_dir = output / "stats"
    stats_dir.mkdir()
    pose_sum = pose_squares = velocity_sum = velocity_squares = None
    pose_count = velocity_count = window_count = 0
    clip_rows, split_counts, source_split_counts, window_splits = [], Counter(), Counter(), Counter()
    group_splits = {}
    with (output / "windows.jsonl").open("w", encoding="utf-8") as stream:
        for clip_index, item in enumerate(manifest["clips"]):
            split = item.get("split")
            if split not in SPLITS:
                raise ValueError(f"动画分区无效：{item['asset']}")
            source_split_counts[split] += 1
            is_excluded = clip_index in excluded
            group = item.get("group")
            if not group or (group in group_splits and group_splits[group] != split):
                raise ValueError(f"同一动作家族跨分区：{group}")
            group_splits[group] = split
            length = item.get("source_frames")
            if not isinstance(length, int) or isinstance(length, bool):
                raise ValueError(f"缺少真实帧数：{item['asset']}")
            raw_file = contained_file(source, item["raw_file"])
            raw, digest = load_raw(raw_file, length, bone_count, fps)
            contacts = world_foot_contacts(raw["positions"], raw["root_track"], skeleton, fps)
            if contacts is None or contacts.shape != (length, 4):
                raise ValueError(f"无法派生四个脚部接触标签：{item['asset']}")
            contact_bits = base64.b64encode(np.packbits((contacts.numpy() > 0.5).astype(np.uint8).ravel()).tobytes()).decode("ascii")
            starts = [] if is_excluded else window_starts(length, history_frames, future_frames, stride_frames)
            if split == "train" and not is_excluded:
                pose = raw["positions"].astype(np.float64)
                velocity = root_velocity(raw["root_track"], fps).astype(np.float64)
                pose_sum = pose.sum(0) if pose_sum is None else pose_sum + pose.sum(0)
                pose_squares = (pose ** 2).sum(0) if pose_squares is None else pose_squares + (pose ** 2).sum(0)
                velocity_sum = velocity.sum(0) if velocity_sum is None else velocity_sum + velocity.sum(0)
                velocity_squares = ((velocity ** 2).sum(0) if velocity_squares is None
                                    else velocity_squares + (velocity ** 2).sum(0))
                pose_count += length
                velocity_count += len(velocity)
            clip_rows.append({"clip_index": clip_index, "asset": item["asset"], "split": split,
                              "group": group, "category": item["labels"]["category"],
                              "action": item["labels"]["action"], "raw_file": item["raw_file"],
                              "raw_sha256": digest, "source_frames": length, "windows": len(starts),
                              "excluded_from_conditioned_dataset": is_excluded,
                              "contact_bits": contact_bits})
            if not is_excluded:
                split_counts[split] += 1
            for start in starts:
                stream.write(json.dumps({"clip_index": clip_index, "start": start}, separators=(",", ":")) + "\n")
                window_splits[split] += 1
                window_count += 1
            if progress is not None and ((clip_index + 1) % 100 == 0 or clip_index + 1 == len(manifest["clips"])):
                progress(clip_index + 1, len(manifest["clips"]))
    stats = {"pose_m": _save_stats(stats_dir, "pose", pose_sum, pose_squares, pose_count),
             "root_velocity_mps_radps": _save_stats(stats_dir, "root_velocity", velocity_sum,
                                                    velocity_squares, velocity_count)}
    contract = {"schema_version": 1, "contract_id": CONTRACT_ID,
                "source_dataset": str(source), "source_manifest_sha256": sha256(manifest_path),
                "source_manifest_name": manifest_path.name,
                "source_training_signature": manifest["training_signature"],
                "skeleton_sha256": sha256(source / "skeleton.json"), "fps": fps,
                "bone_count": bone_count,
                "source_coordinate_system": skeleton_json["coordinate_system"],
                "array_coordinate_system": "motion_y_up_meters_root_relative",
                "array_axes_from_ue": {"x": "-ue_y", "y": "ue_z", "z": "ue_x"},
                "pose_space": "root_local_meters", "root_plan_space": "last_history_root_local_meters_quaternion_xyzw",
                "window": {"history_frames": history_frames, "future_frames": future_frames,
                           "stride_frames": stride_frames, "held_padding_used": False},
                "input_contract": {"history": ["pose", "rotations", "root_local"],
                                   "control_modes": ["future_root_plan", "mean_desired_velocity"],
                                   "hard_reference": ["future_frame_offset", "bone_mask", "pose", "rotations"],
                                   "scene_context_available": False},
                "target_contract": ["future_pose", "future_rotations", "derived_foot_contacts"],
                "root_condition_source": "ground_truth_oracle_for_offline_baseline_only",
                "contact_source": "world_foot_contacts_heuristic_from_true_root_and_pose",
                "split_policy": manifest.get("split_policy"),
                "source_split_counts": dict(source_split_counts), "split_counts": dict(split_counts),
                "excluded_train_clip_indices": sorted(excluded),
                "window_split_counts": dict(window_splits), "window_count": window_count,
                "clips_without_generation_window": sum(row["windows"] == 0 and not row["excluded_from_conditioned_dataset"]
                                                       for row in clip_rows),
                "stats_scope": "train_real_frames_once_per_clip_no_held_padding_no_overlapping_window_weight",
                "stats": stats, "clips": clip_rows,
                "limitations": ["继承来源分区；非 Traversal 的语义变体可能跨分区。",
                                "未来 Root 来自源动画，离线基线不衡量玩家控制或 Root 预测。",
                                "场景几何与落点不可从动画资产推断；没有填造环境条件。"]}
    (output / "contract.json").write_text(json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")
    return contract


def with_references(sample, anchor_offsets):
    """只暴露明确指定的目标帧；未指定的未来姿态始终为零。"""
    pose = sample["target"]["future_pose"]
    rotations = sample["target"]["future_rotations"]
    offsets = sorted(set(anchor_offsets))
    if any(not isinstance(index, int) or index < 0 or index >= len(pose) for index in offsets):
        raise ValueError("硬参考帧越过未来窗口")
    frame_mask = np.zeros(len(pose), dtype=bool)
    frame_mask[offsets] = True
    joint_mask = np.repeat(frame_mask[:, None], pose.shape[1], axis=1)
    reference_pose = np.zeros_like(pose)
    reference_rotations = np.zeros_like(rotations)
    reference_pose[frame_mask] = pose[frame_mask]
    reference_rotations[frame_mask] = rotations[frame_mask]
    visible = dict(sample["input"], reference_pose=reference_pose,
                   reference_rotations=reference_rotations, reference_frame_mask=frame_mask,
                   reference_bone_mask=joint_mask)
    return dict(sample, input=visible)


def load_window(contract_folder, row, *, anchor_offsets=(), control_mode="future_root_plan", contract=None):
    """把目标与可见条件分开，只有显式指定的未来姿态帧可进入参考条件。"""
    folder = Path(contract_folder)
    contract = read_json(folder / "contract.json") if contract is None else contract
    if contract.get("contract_id") != CONTRACT_ID:
        raise ValueError("未知条件动作契约")
    source = Path(contract["source_dataset"])
    clip = contract["clips"][row["clip_index"]]
    raw, _ = load_raw(contained_file(source, clip["raw_file"]), clip["source_frames"],
                      contract["bone_count"], contract["fps"], expected_sha256=clip["raw_sha256"])
    history, future = contract["window"]["history_frames"], contract["window"]["future_frames"]
    start = row["start"]
    if not isinstance(start, int) or start < 0 or start + history + future > clip["source_frames"]:
        raise ValueError("窗口越过真实帧边界")
    middle, end = start + history, start + history + future
    origin = raw["root_track"][middle - 1]
    history_root = root_local(raw["root_track"][start:middle], origin)
    future_root = root_local(raw["root_track"][middle:end], origin)
    velocity = root_velocity(raw["root_track"][middle - 1:end], contract["fps"])
    if control_mode not in ("future_root_plan", "mean_desired_velocity"):
        raise ValueError("未知控制模式")
    pose = raw["positions"][middle:end]
    rotations = raw["rotations"][middle:end]
    bits = np.unpackbits(np.frombuffer(base64.b64decode(clip["contact_bits"]), dtype=np.uint8))
    contacts = bits[:clip["source_frames"] * 4].reshape(clip["source_frames"], 4).astype(bool)
    visible = {"history_pose": raw["positions"][start:middle],
               "history_rotations": raw["rotations"][start:middle],
               "history_root_local": history_root, "control_mode": control_mode,
               "scene_context_available": False}
    if control_mode == "future_root_plan":
        visible["future_root_plan_local"] = future_root
    else:
        visible["mean_desired_velocity"] = velocity[:, [0, 2, 3]].mean(0)
    sample = {"metadata": {"clip_index": row["clip_index"], "start": start, "asset": clip["asset"],
                           "split": clip["split"], "category": clip["category"]},
              "input": visible,
              "target": {"future_pose": pose, "future_rotations": rotations,
                         "future_root_track": raw["root_track"][middle:end],
                         "future_contacts": contacts[middle:end]}}
    return with_references(sample, anchor_offsets)
