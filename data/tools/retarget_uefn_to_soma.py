"""保留 UE 原生版本，将已核对的人体骨架离线转换为 SOMA 及可交换 BVH。"""

import argparse
import csv
import hashlib
import json
import re
import warnings
from collections import Counter
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from data.runtime.bvh_reader import read_bvh
from data.runtime.motion_kinematics import world_positions
from data.runtime.native_motion_io import (
    read_native_motion,
    read_sampled_motion,
    skeleton_signature,
    write_sampled_motion,
)
from data.runtime.normalize_motion import normalize_motion
from data.runtime.retarget_motion import SomaRetargeter
from data.runtime.skeleton_contract import load_skeleton_contract
from data.tools.native_review_store import load_native_review_inputs

DATA = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def file_hashes(folder):
    return {name: sha(Path(folder) / name) for name in ("motion.json", "arrays.bin")}


def write_bvh(path, motion):
    """厘米制、Y-up、Z-forward；偏移与旋转分别保存，位置通道为 OFFSET 增量。"""
    skeleton = motion.skeleton
    children = {i: np.flatnonzero(skeleton.parents == i).tolist() for i in range(len(skeleton.names))}
    order, lines = [], ["HIERARCHY"]

    def joint(index, depth):
        order.append(index)
        indent = "  " * depth
        lines.extend([f"{indent}{'ROOT' if depth == 0 else 'JOINT'} {skeleton.names[index]}", indent + "{"])
        offset = skeleton.rest_positions[index] * 100
        lines.append(indent + "  OFFSET " + " ".join(f"{v:.12g}" for v in offset))
        channels = "6 Xposition Yposition Zposition Zrotation Xrotation Yrotation" if index in (0, 1) else "3 Zrotation Xrotation Yrotation"
        lines.append(indent + "  CHANNELS " + channels)
        for child in children[index]:
            joint(child, depth + 1)
        if not children[index]:
            lines.extend([indent + "  End Site", indent + "  {", indent + "    OFFSET 0 0 0", indent + "  }"])
        lines.append(indent + "}")

    joint(0, 0)
    channels = []
    with warnings.catch_warnings():
        # 欧拉万向锁只影响角度表达的唯一性，随后用独立 BVH FK 验证完整运动。
        warnings.filterwarnings("ignore", message="Gimbal lock detected.*", category=UserWarning)
        for index in order:
            if index in (0, 1):
                channels.append((motion.positions[:, index] - skeleton.rest_positions[index]) * 100)
            channels.append(Rotation.from_quat(motion.rotations[:, index]).as_euler("ZXY", degrees=True))
    lines.extend(["MOTION", f"Frames: {len(motion.timestamps)}", f"Frame Time: {1 / motion.fps:.17g}"])
    with Path(path).open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(lines) + "\n")
        np.savetxt(stream, np.column_stack(channels), fmt="%.10g")
    bvh = read_bvh(path)
    positions, _ = bvh.forward()
    expected = world_positions(motion)[:, order] * 100
    error = float(np.max(np.abs(positions - expected))) / 100
    if bvh.names != [skeleton.names[i] for i in order] or error > 1e-7:
        raise ValueError(f"BVH 交换回读误差过大：{error} m")
    return error


def retarget_batch(index, data_root, run_id, target_path=None, mapping_path=None, limit=None, retry_report=None):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", run_id):
        raise ValueError("无效 run_id")
    root = Path(data_root).resolve()
    index, entries, digest = load_native_review_inputs(index, root)
    target_path = Path(target_path or DATA / "skeletons/soma_uniform_v1.json")
    mapping_path = Path(mapping_path or DATA / "skeletons/mappings/ue_humanoid_to_soma_v1.json")
    target, contract = load_skeleton_contract(target_path)
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    if retry_report is not None:
        previous = Path(retry_report).resolve()
        if not previous.is_relative_to(root / "reports"):
            raise ValueError("重试报告必须位于本数据根目录")
        previous = json.loads(previous.read_text(encoding="utf-8"))
        if previous["source_index_sha256"] != sha(index):
            raise ValueError("重试来源索引与原批次不一致")
        failed = {r["source_id"] for r in previous["results"] if r["status"] != "normalized"}
        entries = [e for e in entries if e["asset"] in failed]
        if not entries or len(entries) != len(failed):
            raise ValueError("没有可重试的来源条目，或来源条目不完整")
    if limit is not None:
        if limit <= 0:
            raise ValueError("limit 必须为正")
        # 覆盖所有来源骨架，并覆盖常见运动目录，余量均匀抽取。
        picked = {}
        for count in sorted({e["joints"] for e in entries}):
            e = next(e for e in entries if e["joints"] == count)
            picked[e["id"]] = e
        for category in ("/Walk/", "/Run/", "/Crouch/", "/Jump/", "/Traversal/", "/AimOffset/"):
            e = next((e for e in entries if category in e["asset"]), None)
            if e:
                picked[e["id"]] = e
        for i in np.linspace(0, len(entries) - 1, min(limit, len(entries)), dtype=int):
            picked[entries[i]["id"]] = entries[i]
        entries = list(picked.values())[:limit]
    report = root / "reports/gameanimation_soma" / run_id
    output = root / "processed/gameanimation_soma" / run_id
    if report.exists() or output.exists():
        raise FileExistsError("版本已存在，请使用新 run_id")
    report.mkdir(parents=True)
    (output / "clips").mkdir(parents=True)
    (output / "bvh").mkdir()
    (report / "target-skeleton.json").write_bytes(target_path.read_bytes())
    (report / "mapping.json").write_bytes(mapping_path.read_bytes())
    records, rows, converters = [], [], {}
    fields = ["asset", "name", "status", "joints", "frames", "duration", "fps", "skeleton", "skeleton_signature", "raw", "processed", "error"]
    for ordinal, entry in enumerate(entries, 1):
        record = {"source_id": entry["asset"], "raw": entry["raw"], "source_processed": entry["processed"],
                  "raw_sha256": entry["raw_sha256"], "source_processed_sha256": entry["processed_sha256"]}
        try:
            # 哈希绑定既有验收结果；不改变或重复复制来源数据。
            for partition in ("raw", "processed"):
                folder = Path(entry[partition]).resolve()
                if not folder.is_relative_to(root / partition):
                    raise ValueError("来源路径越过数据分区")
                if file_hashes(folder) != entry[partition + "_sha256"]:
                    raise ValueError("来源文件与原生审核版本不一致")
            source = (normalize_motion(read_native_motion(entry["raw"]), entry["fps"])
                      if retry_report is not None else read_sampled_motion(entry["processed"]))
            record["recomputed_from_native_raw"] = retry_report is not None
            signature = skeleton_signature(source.skeleton)
            if signature not in mapping["source_signatures"]:
                raise ValueError("来源缺少完整人体映射，保留原始数据")
            if signature not in converters:
                converters[signature] = SomaRetargeter(source, target, mapping)
            motion, metrics = converters[signature].retarget(source)
            folder = output / "clips" / entry["id"]
            write_sampled_motion(folder, motion, output / "skeletons")
            restored = read_sampled_motion(folder)
            for name in ("timestamps", "positions", "rotations", "scales", "track_mask"):
                np.testing.assert_array_equal(getattr(restored, name), getattr(motion, name))
            if skeleton_signature(restored.skeleton) != contract["skeleton_signature"]:
                raise ValueError("输出骨架签名与固定参考不一致")
            bvh = output / "bvh" / (entry["id"] + ".bvh")
            metrics["bvh_world_readback_max_error_m"] = write_bvh(bvh, restored)
            auxiliary = "/PoseAssets/" in entry["asset"] or "/Poses/" in entry["asset"] or "bake_pose" in entry["name"].lower()
            record.update(status="normalized", processed=str(folder), processed_sha256=file_hashes(folder),
                          bvh=str(bvh), bvh_sha256=sha(bvh), metrics=metrics, training_ready=False,
                          retarget=motion.source.details["retarget"], auxiliary_pose=auxiliary,
                          flags=["visual_quality_not_validated", "contacts_not_validated"] + (["auxiliary_pose_not_motion_training"] if auxiliary else []))
            rows.append(dict(zip(fields, [entry["asset"], entry["name"], "normalized", len(target.names), len(motion.timestamps),
                                         motion.source_duration, motion.fps, contract["id"], contract["skeleton_signature"],
                                         entry["raw"], str(folder), ""])))
        except ValueError as error:
            record.update(status="needs_skeleton_review", error=str(error), training_ready=False)
        records.append(record)
        with (report / "clips.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        if ordinal % 50 == 0 or ordinal == len(entries):
            print(json.dumps({"completed": ordinal, "selected": len(entries), "status": dict(Counter(r["status"] for r in records))}), flush=True)
    with (report / "export-index.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fields)
        writer.writeheader()
        writer.writerows(rows)
    report_data = {"schema_version": 1, "dataset": "gameanimation_soma", "run_id": run_id,
                   "source_index": str(index), "source_index_sha256": sha(index), "source_queue_sha256": digest,
                   "retry_report": str(retry_report) if retry_report is not None else None,
                   "mapping_sha256": sha(mapping_path), "target_signature": contract["skeleton_signature"],
                   "selected": len(entries), "status_counts": dict(Counter(r["status"] for r in records)),
                   "raw": None, "processed": str(output), "results": records}
    (report / "run.json").write_text(json.dumps(report_data, ensure_ascii=False, indent=2), encoding="utf-8")
    accepted = {"batches": [run_id], "status_counts": {"normalized": len(rows)}, "raw_and_processed_readback_errors": [],
                "source_endpoint_gaps": [], "recomposition_errors": [], "target_signature": contract["skeleton_signature"],
                "training_ready": False, "source_data_modified": False,
                "excluded": [{"source_id": r["source_id"], "reason": r["error"]} for r in records if r["status"] != "normalized"]}
    (report / "acceptance.json").write_text(json.dumps(accepted, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_data


def prepare_unresolved_review(source_index, retry_report, data_root, run_id):
    """待复核列表显示仍失败条目的真实来源骨架，不伪造 SOMA 结果。"""
    root = Path(data_root).resolve()
    _, entries, _ = load_native_review_inputs(source_index, root)
    retry_path = Path(retry_report).resolve()
    if not retry_path.is_relative_to(root / "reports"):
        raise ValueError("重试报告路径越界")
    retry = json.loads(retry_path.read_text(encoding="utf-8"))
    failures = {r["source_id"]: r["error"] for r in retry["results"] if r["status"] != "normalized"}
    if not failures:
        return None
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", run_id):
        raise ValueError("无效复核 run_id")
    report = root / "reports/gameanimation_soma" / run_id
    report.mkdir(parents=True, exist_ok=False)
    rows, records = [], []
    for entry in entries:
        if entry["asset"] not in failures:
            continue
        record = {"status": "normalized", "source_id": entry["asset"], "raw": entry["raw"],
                  "processed": entry["processed"], "raw_sha256": entry["raw_sha256"],
                  "processed_sha256": entry["processed_sha256"], "review_flags": ["needs_skeleton_review"],
                  "review_reason": "重试后仍需核对：" + failures[entry["asset"]], "retarget_status": "unresolved"}
        records.append(record)
        motion = read_sampled_motion(entry["processed"])
        rows.append({"asset": entry["asset"], "name": entry["name"], "frames": entry["frames"], "joints": entry["joints"],
                     "duration": motion.source_duration, "fps": entry["fps"], "raw": entry["raw"], "processed": entry["processed"]})
    if len(records) != len(failures):
        raise ValueError("未解决条目与来源列表不一致")
    with (report / "export-index.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (report / "run.json").write_text(json.dumps({"scope": "native_source_review_for_failed_retarget",
         "retry_report": str(retry_path), "results": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    (report / "acceptance.json").write_text(json.dumps({"batches": [run_id], "status_counts": {"normalized": len(rows)},
         "raw_and_processed_readback_errors": [], "source_endpoint_gaps": [], "scope": "native_source_review_only",
         "retargeted_clips": 0}, ensure_ascii=False, indent=2), encoding="utf-8")
    return report / "export-index.csv"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("E:/MotionWeaverData"))
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--retry-report", type=Path, help="只重试报告中的失败条目，从原生 raw 重新标准化")
    args = parser.parse_args()
    result = retarget_batch(args.index, args.data_root, args.run_id, limit=args.limit, retry_report=args.retry_report)
    if args.retry_report is not None:
        review = prepare_unresolved_review(args.index, args.data_root / "reports/gameanimation_soma" / args.run_id / "run.json",
                                           args.data_root, args.run_id + "-issues")
        if review:
            print(json.dumps({"unresolved_review_index": str(review)}, ensure_ascii=False))
    print(json.dumps({key: result[key] for key in ("run_id", "selected", "status_counts", "processed")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
