"""非镜像 SOMA 全库：固定参考、原生来源、轻量候选、逐条回读与断点续跑。"""

import argparse
import csv
import hashlib
import json
import os
import shutil
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from data.runtime.importers.bvh import read_bvh_motion
from data.runtime.motion_kinematics import world_positions
from data.runtime.motion_processing import POLICY, process_motion
from data.runtime.native_motion_io import (
    read_native_motion,
    read_sampled_motion,
    skeleton_signature,
    write_native_motion,
    write_sampled_motion,
)
from data.runtime.normalize_motion import normalize_motion
from data.runtime.skeleton_contract import load_skeleton_contract
from data.tools.soma_observation import observe, sha, write_json

DATA = Path(__file__).resolve().parents[1]
MINIMUM_FREE_BYTES = 64 * 1024**3


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    write_json(temporary, value)
    os.replace(temporary, path)


def fixed_soma_motion(motion, fixed):
    """同名同局部轴的 SOMA 骨架固定偏移；完整录制 Root 运动合入 Hips。"""
    if motion.skeleton.names != fixed.names or not np.array_equal(motion.skeleton.parents, fixed.parents):
        raise ValueError("不是 SOMA uniform 的 78 节点拓扑")
    if not motion.track_mask[:, 1].all():
        raise ValueError("SOMA 来源缺少旋转通道，不能将参考补全冒充录制运动")
    if not np.allclose(motion.scales, 1, atol=1e-10):
        raise ValueError("SOMA BVH 意外包含非单位缩放")
    if np.max(np.ptp(motion.positions[:, 2:], axis=0)) > 1e-7:
        raise ValueError("身体关节含动画平移，不能当作静态骨长统一")
    positions = np.broadcast_to(fixed.rest_positions, motion.positions.shape).copy()
    rotations = motion.rotations.copy()
    positions[:, 0] = 0
    rotations[:, 0] = [0, 0, 0, 1]
    root = Rotation.from_quat(motion.rotations[:, 0])
    positions[:, 1] = root.apply(motion.positions[:, 1]) + motion.positions[:, 0]
    rotations[:, 1] = (root * Rotation.from_quat(motion.rotations[:, 1])).as_quat()
    mask = motion.track_mask.copy()
    mask[0] = False
    details = dict(motion.source.details, soma_uniform={
        "version": "soma_uniform_fixed_offsets_v1", "target_signature": skeleton_signature(fixed),
        "source_signature": skeleton_signature(motion.skeleton),
        "reference_offset_max_change_m": float(np.max(np.abs(motion.skeleton.rest_positions - fixed.rest_positions))),
        "rotation_policy": "same_soma_joint_axes_keep_recorded_rotations",
        "root_policy": "static_root_full_recorded_transform_in_hips",
        "time_policy": "declared_source_keys_no_resample", "training_ready": False})
    result = replace(motion, skeleton=fixed, positions=positions, rotations=rotations, track_mask=mask,
                     source=replace(motion.source, details=details), root_joint=0)
    before, after = world_positions(motion), world_positions(result)
    np.testing.assert_allclose(after[:, 1], before[:, 1], atol=1e-9, rtol=0)
    return result, float(np.max(np.abs(after[:, 1:] - before[:, 1:])))


def hashes(folder):
    return {name: sha(folder / name) for name in ("motion.json", "arrays.bin")}


def recover_partial(folder, root, report):
    """只保留本运行未写完的片段，来源文件及别的版本不移动。"""
    resolved = folder.resolve()
    if not resolved.is_relative_to(root) or folder.is_symlink() or report.is_symlink():
        raise ValueError("片段恢复路径越界或包含链接")
    destination = report / "partial" / (folder.parent.parent.parent.name + "-" + folder.name + "-" + str(time.time_ns()))
    if not destination.resolve().is_relative_to(report.resolve()):
        raise ValueError("部分输出的恢复位置越界")
    destination.parent.mkdir(parents=True, exist_ok=True)
    folder.rename(destination)


def process_clip(item):
    row, root_text, run_id = item
    root = Path(root_text)
    source_id = row["move_soma_uniform_path"].replace("\\", "/")
    identity = hashlib.sha256(source_id.encode()).hexdigest()[:24]
    report = root / "reports/soma_uniform" / run_id
    original = (root / source_id).resolve()
    if not original.is_relative_to(root / "soma_uniform/bvh"):
        raise ValueError("原始 BVH 路径越界")
    record = {"id": identity, "source_id": source_id, "name": row["filename"], "original_bvh": str(original)}
    try:
        fixed, contract = load_skeleton_contract(DATA / "skeletons/soma_uniform_v1.json")
        profile = {"source_id": source_id, "meters_per_unit": .01, "basis": np.eye(3).tolist(),
                   "root_joint": "Root", "absolute_position_joints": ["Hips"]}
        native = read_bvh_motion(original, profile)
        source = normalize_motion(native, native.source.details["source_fps"])
        if len(source.timestamps) != int(row["move_duration_frames"]):
            raise ValueError("BVH 帧数与来源元数据不一致")
        motion, adjustment = fixed_soma_motion(source, fixed)
        arrays, measures = process_motion(motion, contract["roles"])
        if measures["recomposition_max_abs_error_m"] > 1e-9:
            raise ValueError("Root/身体位置重合成误差超标")
        raw = root / "raw/soma_uniform" / run_id / "clips" / identity
        processed = root / "processed/soma_uniform" / run_id / "clips" / identity
        # 目录已有但无完整元数据，表示上次中止的本运行输出；保留到 partial。
        for folder in (raw, processed):
            if folder.exists() and not all((folder / name).is_file() for name in ("motion.json", "arrays.bin")):
                recover_partial(folder, root, report)
        if not raw.exists():
            write_native_motion(raw, native)
        restored_raw = read_native_motion(raw)
        if restored_raw.source.file_hashes != native.source.file_hashes or restored_raw.source.source_id != source_id:
            raise ValueError("已保存的原生来源与当前 BVH 身份不同")
        if not processed.exists():
            write_sampled_motion(processed, motion, processed.parent.parent / "skeletons")
        restored = read_sampled_motion(processed)
        if skeleton_signature(restored.skeleton) != contract["skeleton_signature"]:
            raise ValueError("输出不是固定 SOMA 骨架")
        for key in ("positions", "rotations", "scales", "timestamps", "track_mask"):
            np.testing.assert_array_equal(getattr(restored, key), getattr(motion, key))
        for actual, expected in zip(restored_raw.tracks, native.tracks):
            for key in ("position_times", "positions", "rotation_times", "rotations", "scale_times", "scales"):
                np.testing.assert_array_equal(getattr(actual, key), getattr(expected, key))
        path = root / "processed/soma_processing" / run_id / identity[:2] / (identity + ".npz")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".npz.tmp")
        with temporary.open("wb") as stream:
            np.savez_compressed(stream, **arrays)
        with np.load(temporary, allow_pickle=False) as loaded:
            for name, expected in arrays.items():
                np.testing.assert_array_equal(loaded[name], expected)
        os.replace(temporary, path)
        flags = []
        if measures["heading_valid_fraction"] < 1:
            flags.append("processing_heading_unknown")
        if adjustment > 1e-5:
            flags.append("processing_reference_offsets_normalized")
        observation = observe(motion, row, row["category"], contract["roles"])
        observation["processing"] = {"policy": POLICY, "arrays": str(path), "arrays_sha256": sha(path),
                                     **measures, "historical_notes": [], "review_flags": flags}
        sidecar = report / "observations" / identity[:2] / (identity + ".json")
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        write_json(sidecar, observation)
        references = [{"id": f"{identity}:{e['frame']}:{e['kind']}", "clip_id": identity,
                       "frame": e["frame"], "time_s": float(motion.timestamps[e["frame"]]),
                       "selection_reason": e["kind"], "status": "candidate_requires_selection",
                       "constraint": "none", "joint_mask": [], "source_motion": str(processed),
                       "target_signature": contract["skeleton_signature"]} for e in observation["phase_candidates"]]
        movement = {v.strip() for v in row["content_type_of_movement"].split(",")}
        route = ("interaction_candidate" if movement & {"climbing box", "vaulting", "climbing"}
                 else "locomotion_candidate" if movement & {"walking", "jogging", "turning", "standing"} else "unassigned")
        record.update(status="normalized", frames=len(motion.timestamps), joints=78, fps=motion.fps,
            duration=motion.source_duration, raw=str(raw), processed=str(processed), raw_sha256=hashes(raw),
            processed_sha256=hashes(processed), source_bvh_sha256=native.source.file_hashes[str(original)],
            observation=str(sidecar), observation_sha256=sha(sidecar), processing_flags=flags,
            processing_arrays=str(path), processing_arrays_sha256=sha(path),
            reference_candidates=references, task={"clip_id": identity, "route": route,
                "source_motion": str(processed), "processing_arrays": str(path), "reference_candidates": [r["id"] for r in references],
                "condition_reference_ids": [], "future_pose_visibility": "none_until_explicitly_selected",
                "root_condition": "offline_source_proxy_not_player_log", "split": "unassigned", "training_ready": False},
            source_to_fixed_world_adjustment_max_m=adjustment, **measures)
    except (ValueError, AssertionError) as error:
        record.update(status="needs_secondary_review", error=str(error))
    return record


def select_rows(root, limit):
    with (root / "metadata/seed_metadata_v004.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = [row for row in csv.DictReader(stream) if row["is_mirror"].casefold() == "false"]
    names = [r["move_soma_uniform_path"] for r in rows]
    if len(set(names)) != len(names):
        raise ValueError("非镜像元数据来源路径不唯一")
    if limit is not None:
        if limit < 1:
            raise ValueError("limit 必须为正")
        rows = [rows[i] for i in np.linspace(0, len(rows) - 1, min(limit, len(rows)), dtype=int)]
    return rows


def run(root, run_id, workers=4, limit=None, resume=False):
    root = Path(root).resolve()
    if not run_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in run_id):
        raise ValueError("无效 run_id")
    if not 1 <= workers <= 8:
        raise ValueError("workers 必须在 1–8 之间")
    rows = select_rows(root, limit)
    report = root / "reports/soma_uniform" / run_id
    manifest = {"run_id": run_id, "selected": len(rows), "frames": sum(int(r["move_duration_frames"]) for r in rows),
                "metadata_sha256": sha(root / "metadata/seed_metadata_v004.csv"),
                "target_sha256": sha(DATA / "skeletons/soma_uniform_v1.json"), "policy": POLICY,
                "code_sha256": {name: sha(DATA / name) for name in ("tools/process_soma_dataset.py", "runtime/motion_processing.py",
                    "runtime/motion_features.py", "runtime/native_motion.py", "runtime/native_motion_io.py", "runtime/bvh_reader.py",
                    "runtime/importers/bvh.py", "runtime/normalize_motion.py", "runtime/motion_kinematics.py", "tools/soma_observation.py")},
                "limit": limit, "scope": "all_nonmirror_soma_uniform_fixed_reference_and_candidates"}
    if resume:
        if json.loads((report / "manifest.json").read_text(encoding="utf-8")) != manifest:
            raise ValueError("元数据、代码、目标或处理规则已改变，不能续写原版本")
    else:
        report.mkdir(parents=True, exist_ok=False)
        write_json(report / "manifest.json", manifest)
        write_json(report / "target-skeleton.json", json.loads((DATA / "skeletons/soma_uniform_v1.json").read_text(encoding="utf-8")))
        with (report / "selection.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    journal = report / "clips.jsonl"
    records = {}
    if journal.exists():
        for line in journal.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            records[record["source_id"]] = record
    if resume:
        for key, record in list(records.items()):
            if record["status"] != "normalized":
                continue
            try:
                for part in ("raw", "processed"):
                    if hashes(Path(record[part])) != record[part + "_sha256"]:
                        raise ValueError("续跑前输出哈希已改变")
                for path, digest in ((record["processing_arrays"], record["processing_arrays_sha256"]),
                                     (record["observation"], record["observation_sha256"])):
                    if sha(path) != digest:
                        raise ValueError("续跑前候选数据哈希已改变")
            except (FileNotFoundError, ValueError):
                for part in ("raw", "processed"):
                    folder = Path(record[part])
                    if folder.exists():
                        recover_partial(folder, root, report)
                records.pop(key)
        completed = next((r for r in records.values() if r["status"] == "normalized"), None)
        if completed:
            verified = read_sampled_motion(completed["processed"])
            fixed, _ = load_skeleton_contract(DATA / "skeletons/soma_uniform_v1.json")
            if skeleton_signature(verified.skeleton) != skeleton_signature(fixed):
                raise ValueError("续跑前共享目标骨架与固定参考不一致")
    started = time.monotonic()
    last_progress = started

    def progress(state):
        nonlocal last_progress
        last_progress = time.monotonic()
        counts = dict(Counter(r["status"] for r in records.values()))
        value = {"state": state, "selected": len(rows), "completed": len(records), "status_counts": counts,
                 "updated_at": datetime.now(UTC).isoformat(), "pid": os.getpid(), "workers": workers,
                 "elapsed_this_session_seconds": round(time.monotonic() - started, 1)}
        atomic_json(report / "progress.json", value)
        print(json.dumps(value), flush=True)

    def save(record):
        with journal.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        records[record["source_id"]] = record
        if len(records) % 100 == 0 or time.monotonic() - last_progress >= 30:
            progress("running")
            if shutil.disk_usage(root).free < MINIMUM_FREE_BYTES:
                raise OSError("E 盘可用空间低于 64 GiB，停止写入")

    remaining_frames = sum(int(r["move_duration_frames"]) for r in rows if r["move_soma_uniform_path"].replace("\\", "/") not in records)
    if shutil.disk_usage(root).free < remaining_frames * 78 * 18 * 8 + MINIMUM_FREE_BYTES:
        raise OSError("现有可用空间不足以保留本批原生、固定运动及候选表示")
    pending = [row for row in rows if row["move_soma_uniform_path"].replace("\\", "/") not in records]
    progress("running")
    try:
        # 第一个成功输出串行创建唯一共享目标骨架，之后各进程只读它。
        while pending and not (root / "processed/soma_uniform" / run_id / "skeletons").is_dir():
            row = pending.pop(0)
            save(process_clip((row, str(root), run_id)))
        os.environ["OPENBLAS_NUM_THREADS"] = "1"
        with ProcessPoolExecutor(max_workers=workers) as pool:
            iterator = iter(pending)
            active = {}
            for _ in range(workers * 2):
                row = next(iterator, None)
                if row is not None:
                    active[pool.submit(process_clip, (row, str(root), run_id))] = row
            while active:
                done, _ = wait(active, timeout=30, return_when=FIRST_COMPLETED)
                if not done:
                    progress("running")
                for future in done:
                    active.pop(future)
                    save(future.result())
                    row = next(iterator, None)
                    if row is not None:
                        active[pool.submit(process_clip, (row, str(root), run_id))] = row
        progress("retrying_failures")
        for row in rows:
            key = row["move_soma_uniform_path"].replace("\\", "/")
            if records[key]["status"] != "normalized" and not records[key].get("retried"):
                record = process_clip((row, str(root), run_id))
                record["retried"] = True
                save(record)
    except BaseException:
        progress("stopped")
        raise
    values = list(records.values())
    good = [r for r in values if r["status"] == "normalized"]
    fields = ("asset", "name", "frames", "joints", "fps", "duration", "raw", "processed")
    with (report / "export-index.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
        for record in good:
            writer.writerow({key: record["source_id"] if key == "asset" else record[key] for key in fields})
    write_json(report / "unresolved.json", [r for r in values if r["status"] != "normalized"])
    with (report / "reference-candidates.jsonl").open("w", encoding="utf-8") as refs, (report / "task-index.jsonl").open("w", encoding="utf-8") as tasks:
        for record in good:
            for reference in record["reference_candidates"]:
                refs.write(json.dumps(reference, ensure_ascii=False) + "\n")
            tasks.write(json.dumps(record["task"], ensure_ascii=False) + "\n")
    write_json(report / "run.json", {**manifest, "results": values,
        "status_counts": dict(Counter(r["status"] for r in values)),
        "max_recomposition_error_m": max((r["recomposition_max_abs_error_m"] for r in good), default=None),
        "training_ready": False})
    write_json(report / "acceptance.json", {"batches": [run_id], "status_counts": {"normalized": len(good)},
        "raw_and_processed_readback_errors": [], "source_endpoint_gaps": [], "recomposition_errors": []})
    progress("complete")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("E:/MotionWeaverData"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    run(args.data_root, args.run_id, args.workers, args.limit, args.resume)


if __name__ == "__main__":
    main()
