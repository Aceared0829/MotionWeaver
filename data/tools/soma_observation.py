"""SOMA 小样本观察：保留源时间轴，候选标注仅供人工审核。"""

import argparse
import csv
import hashlib
import json
import re
import shutil
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from data.runtime.bvh_reader import read_bvh
from data.runtime.importers.bvh import read_bvh_motion
from data.runtime.native_motion_io import (
    read_native_motion,
    read_sampled_motion,
    write_native_motion,
    write_sampled_motion,
)
from data.runtime.normalize_motion import normalize_motion
from data.runtime.skeleton_contract import load_skeleton_contract
from data.tools.native_review_store import world_positions

GROUPS = (
    ("walk", 3, r"^walk_(ff|arc).*", "Basic Locomotion Neutral"),
    ("run", 3, r"^jog_(ff|arc).*", "Basic Locomotion Neutral"),
    ("turn", 2, r"^idle_turn_.*", "Basic Locomotion Neutral"),
    ("stand", 2, r"^idle_(001|right_R_.*|hands_on_back_loop.*)__", "Basic Locomotion Neutral"),
    ("crouch", 3, r"^(crouch_idle|crouch_ff_loop|idle_crouch_loop|turn_crouch).*", "Advanced Locomotion"),
    ("supported_transition", 2, r"^h_b_w_(stand_to_crouch|crouch_to_stand).*", "Advanced Locomotion"),
    ("jump", 8, r"^(jump|high_jump).*", None),
    ("vault", 2, r"^vault_over_.*", None),
    ("climb", 5, r"^(neutral_)?come_(up|down)_.*box.*", None),
)
ROLES = ("pelvis", "left_hand", "right_hand", "left_foot", "right_foot")
LABELS = ("package", "category", "content_name", "content_type_of_movement", "content_body_position",
          "content_short_description", "content_natural_desc_1", "content_technical_description",
          "content_horizontal_move", "content_vertical_move", "content_props")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def select_clips(rows, root):
    """确定性小样本；优先不同动作族及演员，不用左右镜像凑数量。"""
    rows = [r for r in rows if r["is_mirror"].casefold() == "false"
            and 180 <= int(r["move_duration_frames"]) <= 1800]
    selected, families, actors, variants = [], set(), Counter(), Counter()
    coverage = {}
    for group, quota, pattern, category in GROUPS:
        candidates = [r for r in rows if re.match(pattern, r["filename"])
                      and (category is None or r["category"] == category)]
        coverage[group] = {"available_records": len(candidates), "requested": quota, "selected": 0}
        for slot in range(quota):
            choices = []
            for row in candidates:
                if group in {"walk", "run"} and f"_{('start', 'loop', 'stop')[slot]}_" not in row["filename"]:
                    continue
                if group == "crouch":
                    subtype = "turn" if row["filename"].startswith("turn_") else "idle" if "idle" in row["filename"] else "move"
                    if subtype != ("idle", "move", "turn")[slot]:
                        continue
                family = row["take_org_name"]
                if family in families:
                    continue
                variant = re.sub(r"_\d+$", "", family)
                choices.append((variants[variant], actors[row["actor_uid"]],
                                hashlib.sha256(row["filename"].encode()).hexdigest(), family, row))
            if not choices:
                break
            _, _, _, family, row = min(choices, key=lambda item: item[:3])
            relative = Path(row["move_soma_uniform_path"])
            source = (Path(root) / relative).resolve()
            if not source.is_relative_to(Path(root).resolve() / "soma_uniform") or not source.is_file():
                raise ValueError(f"SOMA 来源不存在或越界：{source}")
            selected.append({"group": group, "family": family, "row": row, "path": str(source)})
            families.add(family)
            variants[re.sub(r"_\d+$", "", family)] += 1
            actors[row["actor_uid"]] += 1
            coverage[group]["selected"] += 1
    return selected, coverage


def intervals(mask, fps, minimum_seconds=0.1):
    """返回闭区间帧索引；不合并中间的非候选帧。"""
    edges = np.diff(np.r_[False, mask, False].astype(int))
    return [[int(start), int(stop - 1)] for start, stop in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1))
            if stop - start >= int(np.ceil(minimum_seconds * fps))]


def observe(motion, row, group, roles, *, world=None):
    if world is None:
        world = world_positions(motion)
    names = motion.skeleton.names
    joint = {role: names.index(name) for role, name in roles.items()}
    fps = motion.fps
    speed = np.linalg.norm(np.gradient(world, motion.timestamps, axis=0), axis=2)
    stable = {}
    for role in ROLES[1:]:
        threshold = 0.1 if "hand" in role else 0.15
        stable[role] = {"joint": joint[role], "speed_threshold_m_s": threshold,
                        "intervals": intervals(speed[:, joint[role]] <= threshold, fps)}
    hips = world[:, joint["pelvis"]]
    root = world[:, joint["root"]]
    angles = []
    for side in ("left", "right"):
        a = world[:, joint[f"{side}_hip"]] - world[:, joint[f"{side}_knee"]]
        b = world[:, joint[f"{side}_foot"]] - world[:, joint[f"{side}_knee"]]
        angles.append(np.degrees(np.arccos(np.clip(np.sum(a * b, axis=1) /
                      (np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1)), -1, 1))))
    crouch = (angles[0] < 125) & (angles[1] < 125)
    straight = (angles[0] > 155) & (angles[1] > 155)
    # 只有几何证据：双膝弯曲可能是缓冲、跳跃或蹲姿，不能直接命名为 crouching 真值。
    stance = {"bilateral_knee_bend": intervals(crouch, fps), "bilateral_knee_straight": intervals(straight, fps)}
    events = [{"frame": 0, "kind": "source_start"}, {"frame": len(hips) - 1, "kind": "source_end"}]
    if np.ptp(hips[:, 1]) > 0.03:
        events += [{"frame": int(np.argmin(hips[:, 1])), "kind": "pelvis_height_min"},
                   {"frame": int(np.argmax(hips[:, 1])), "kind": "pelvis_height_max"}]
    return {"schema": "motionweaver_observation", "version": 1, "source_id": motion.source.source_id,
            "frames": len(hips), "fps": fps, "group": group, "roles": {r: joint[r] for r in ROLES},
            "labels": {key: row[key] for key in LABELS},
            "source_group": {key: row[key] for key in ("take_org_name", "take_name", "take_date", "actor_uid", "is_mirror")},
            "timing": {"policy": "declared_source_keys_no_training_resample", "frame_time_seconds": 1 / fps,
                       "declared_vs_nominal_120_end_delta_s": float(motion.timestamps[-1] - (len(hips) - 1) / 120)},
            "metrics": {"hips_displacement_m": (hips[-1] - hips[0]).tolist(),
                        "hips_range_m": np.ptp(hips, axis=0).tolist(), "root_range_m": np.ptp(root, axis=0).tolist()},
            "stance_candidates": stance, "stability_candidates": stable,
            "phase_candidates": sorted(events, key=lambda event: (event["frame"], event["kind"])),
            "contact_truth": "unknown_no_scene_geometry", "phase_truth": "unknown",
            "hard_constraints": [], "review_status": "unreviewed",
            "notes": ["几何候选不是真实站蹲、接触或起跳落地标注。", "不派生 Actor Root，不添加参考骨架旋转。",
                      "来源标签可能冲突；保留原文。", "轨迹从全段真值读取，仅供离线观察，不能直接当在线可见条件。"]}


def build_pilot(root, run_id, target):
    root = Path(root).resolve()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", run_id):
        raise ValueError("无效 run_id")
    dataset = "soma_observation"
    destinations = {part: root / part / dataset / run_id for part in ("raw", "processed", "reports")}
    if any(p.exists() for p in destinations.values()):
        raise FileExistsError("试点版本已存在，请换 run_id")
    fixed, contract = load_skeleton_contract(target)
    metadata = root / "metadata/seed_metadata_v004.csv"
    with metadata.open(encoding="utf-8-sig", newline="") as stream:
        selected, coverage = select_clips(csv.DictReader(stream), root)
    reports = destinations["reports"]
    reports.mkdir(parents=True)
    write_json(reports / "selection.json", {"coverage": coverage, "clips": selected,
               "metadata_sha256": sha(metadata), "target_sha256": sha(target),
               "limitations": ["普通移动中的无支撑站蹲过渡未得到确认；本轮过渡样本为扶墙动作。",
                               "只有两条非镜像短翻越源动作；不复制样本凑数。",
                               "原始族去重仅用于本次观察，不构成正式训练分区。"]})
    results, index_rows, audit = [], [], []
    for item in selected:
        row = item["row"]
        source_id = row["move_soma_uniform_path"].replace("\\", "/")
        identity = hashlib.sha256(source_id.encode()).hexdigest()[:24]
        raw, processed = [destinations[part] / "clips" / identity for part in ("raw", "processed")]
        profile = {"source_id": source_id, "meters_per_unit": 0.01, "basis": np.eye(3).tolist(),
                   "root_joint": "Root", "absolute_position_joints": ["Hips"]}
        native = read_bvh_motion(item["path"], profile)
        motion = normalize_motion(native, native.source.details["source_fps"])
        bvh = read_bvh(item["path"])
        if (bvh.names != fixed.names or not np.array_equal(bvh.parents, fixed.parents)
                or len(bvh.values) != int(row["move_duration_frames"])
                or len(motion.timestamps) != len(bvh.values)):
            raise ValueError(f"骨架拓扑或原始帧数不一致：{source_id}")
        np.testing.assert_allclose(motion.timestamps, np.arange(len(bvh.values)) * bvh.frame_time, atol=1e-10, rtol=0)
        expected, rotations = bvh.forward()
        hips = bvh.names.index("Hips")
        correction = np.einsum("tij,j->ti", rotations[:, 0], bvh.offsets[hips])
        expected[:, hips:] -= correction[:, None, :]
        world = world_positions(motion)
        error = float(np.max(np.abs(world - expected * 0.01)))
        if error > 1e-7:
            raise ValueError(f"BVH 绝对 Hips/FK 校验失败：{source_id}: {error}")
        write_native_motion(raw, native, destinations["raw"] / "skeletons")
        write_sampled_motion(processed, motion, destinations["processed"] / "skeletons")
        source_copy = destinations["raw"] / "sources" / (identity + ".bvh")
        source_copy.parent.mkdir(exist_ok=True)
        shutil.copyfile(item["path"], source_copy)
        if sha(source_copy) != sha(item["path"]) or sha(source_copy) != native.source.file_hashes[str(Path(item["path"]))]:
            raise ValueError("BVH 来源副本哈希不一致")
        # 实际落盘后的双向回读；不是仅检查内存中对象。
        restored_native, restored = read_native_motion(raw), read_sampled_motion(processed)
        np.testing.assert_array_equal(restored.timestamps, motion.timestamps)
        np.testing.assert_allclose(world_positions(restored), world, atol=1e-10, rtol=0)
        for original, restored_track in zip(native.tracks, restored_native.tracks):
            np.testing.assert_array_equal(restored_track.positions, original.positions)
            np.testing.assert_array_equal(restored_track.rotations, original.rotations)
        observation = observe(restored, row, item["group"], contract["roles"])
        sidecar = reports / (identity + ".observation.json")
        write_json(sidecar, observation)
        hashes = lambda folder: {name: sha(folder / name) for name in ("motion.json", "arrays.bin")}
        record = {"status": "normalized", "source_id": source_id, "raw": str(raw), "processed": str(processed),
                  "raw_sha256": hashes(raw), "processed_sha256": hashes(processed),
                  "observation": str(sidecar), "observation_sha256": sha(sidecar),
                  "source_bvh": str(source_copy), "source_bvh_sha256": sha(source_copy)}
        results.append(record)
        index_rows.append({"asset": source_id, "name": f"{item['group']} / {row['filename']}",
                           "frames": len(motion.timestamps), "joints": len(bvh.names), "fps": motion.fps,
                           "duration": motion.source_duration, "raw": str(raw), "processed": str(processed)})
        audit.append({"source_id": source_id, "frames": len(bvh.values), "fk_max_error_m": error,
                      "reference_offset_max_difference_m": float(np.max(np.abs(bvh.offsets * .01 - fixed.rest_positions))),
                      "source_copy_sha256": sha(source_copy)})
        print(f"{len(results)}/{len(selected)} {item['group']}: {row['filename']}", flush=True)
    write_json(reports / "run.json", {"scope": "observation_only_no_retarget_no_training", "results": results})
    with (reports / "export-index.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(index_rows[0]))
        writer.writeheader()
        writer.writerows(index_rows)
    write_json(reports / "acceptance.json", {"batches": [run_id], "status_counts": {"normalized": len(results)},
               "raw_and_processed_readback_errors": [], "source_endpoint_gaps": [], "audit": audit,
               "scope": "technical_readback_only_manual_review_pending", "coverage": coverage})
    return reports / "export-index.csv"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("E:/MotionWeaverData"))
    parser.add_argument("--run-id", default=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    parser.add_argument("--target", type=Path, default=Path(__file__).parents[1] / "skeletons/soma_uniform_v1.json")
    args = parser.parse_args()
    print(build_pilot(args.data_root, args.run_id, args.target))


if __name__ == "__main__":
    main()
