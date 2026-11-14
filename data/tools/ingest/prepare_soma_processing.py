"""复用来源粗语义，生成小样本 Root/接触候选与独立审核批次；不调用模型。"""

import argparse
import csv
import json
import re
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path

import numpy as np

from data.runtime.motion_processing import POLICY, process_motion
from data.runtime.native_motion_io import read_sampled_motion, skeleton_signature
from data.runtime.skeleton_contract import load_skeleton_contract
from data.tools.review.native_review_store import load_native_review_inputs
from data.tools.build.soma_observation import sha, write_json

SEMANTIC_FIELDS = ("filename", "move_soma_uniform_path", "is_mirror", "package", "category",
                   "content_type_of_movement", "content_body_position", "content_uniform_style",
                   "content_short_description", "content_natural_desc_1")


def source_semantics(root, destination, wanted):
    metadata = root / "metadata/seed_metadata_v004.csv"
    paths = {p.relative_to(root).as_posix() for p in (root / "soma_uniform/bvh").rglob("*.bvh")}
    selected, names, missing, blanks = {}, set(), [], Counter()
    count = 0
    with metadata.open(encoding="utf-8-sig", newline="") as stream, destination.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=SEMANTIC_FIELDS)
        writer.writeheader()
        for row in csv.DictReader(stream):
            source = row["move_soma_uniform_path"].replace("\\", "/")
            if source in wanted:
                if source in selected:
                    raise ValueError("试点动作元数据关联不唯一")
                selected[source] = row
            if row["is_mirror"].lower() != "false":
                continue
            if row["filename"] in names:
                raise ValueError("来源粗语义包含重复非镜像文件名")
            names.add(row["filename"])
            count += 1
            writer.writerow({key: row[key] for key in SEMANTIC_FIELDS})
            if source not in paths:
                missing.append(source)
            for key in ("content_type_of_movement", "content_body_position", "content_short_description", "content_natural_desc_1"):
                if not row[key].strip():
                    blanks[key] += 1
    if set(selected) != wanted:
        raise ValueError("来源元数据未完整覆盖试点")
    return selected, {"metadata": str(metadata), "metadata_sha256": sha(metadata),
                       "nonmirror_records": count, "missing_motion_paths": missing,
                       "blank_fields": dict(blanks), "status": "source_labels_not_verified_truth"}


def historical_notes(root, index, identities):
    """保留旧备注及原审核身份，不将旧通过/拒绝继承为本批次决定。"""
    database = root / "reviews" / index.parent.parent.name / index.parent.name / "decisions.sqlite3"
    result = {identity: [] for identity in identities}
    if database.is_file():
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
            for cohort, identity, status, note, updated in db.execute("SELECT cohort,id,status,note,updated FROM decisions ORDER BY updated"):
                if identity in result and note.strip():
                    result[identity].append({"cohort": cohort, "status": status, "note": note, "updated": updated})
    return result


def prepare(root, index_path, run_id, target):
    root = Path(root).resolve()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", run_id):
        raise ValueError("无效 run_id")
    index, entries, digest = load_native_review_inputs(index_path, root)
    target_skeleton, contract = load_skeleton_contract(target)
    report = root / "reports/soma_observation" / run_id
    processed = root / "processed/soma_processing" / run_id
    if report.exists() or processed.exists():
        raise FileExistsError("处理批次已存在，请使用新的 run_id")
    report.mkdir(parents=True)
    processed.mkdir(parents=True)
    selected, coverage = source_semantics(root, report / "source-semantics.csv", {e["asset"] for e in entries})
    notes = historical_notes(root, index, {e["id"] for e in entries})
    references, tasks, records, errors = [], [], [], []
    for entry in entries:
        for partition in ("raw", "processed"):
            folder = Path(entry[partition]).resolve()
            if not folder.is_relative_to(root / partition):
                raise ValueError("来源运动分区路径越界")
            for name, expected in entry[partition + "_sha256"].items():
                if sha(folder / name) != expected:
                    raise ValueError("来源运动已改变")
        sidecar = Path(entry["observation"])
        if sha(sidecar) != entry["observation_sha256"]:
            raise ValueError("来源观察记录已改变")
        observation = json.loads(sidecar.read_text(encoding="utf-8"))
        motion = read_sampled_motion(entry["processed"])
        if (motion.skeleton.names != target_skeleton.names or not np.array_equal(motion.skeleton.parents, target_skeleton.parents)
                or motion.source.source_id != entry["asset"] or len(motion.timestamps) != entry["frames"]):
            raise ValueError("动作身份、拓扑或帧数不匹配")
        arrays, measures = process_motion(motion, contract["roles"])
        path = processed / (entry["id"] + ".npz")
        np.savez_compressed(path, **arrays)
        with np.load(path, allow_pickle=False) as loaded:
            for key, expected in arrays.items():
                np.testing.assert_array_equal(loaded[key], expected)
        if measures["recomposition_max_abs_error_m"] > 1e-9:
            errors.append(entry["id"])
        source_row = selected[entry["asset"]]
        movement = {v.strip() for v in source_row["content_type_of_movement"].split(",")}
        route = ("interaction_candidate" if movement & {"climbing box", "vaulting", "climbing"}
                 else "locomotion_candidate" if movement & {"walking", "jogging", "turning", "standing"} else "unassigned")
        flags = []
        if measures["heading_valid_fraction"] < 1:
            flags.append("processing_heading_unknown")
        if any(len(limb.get("stable_height_levels_m", [])) > 1 for limb in measures["limbs"].values()):
            flags.append("processing_multiple_heights")
        if observation.get("semantic_reading", {}).get("issues"):
            flags.append("processing_source_conflict")
        if notes[entry["id"]]:
            flags.append("processing_historical_note")
        ref_ids = []
        for event in observation["phase_candidates"]:
            reference_id = f"{entry['id']}:{event['frame']}:{event['kind']}"
            ref_ids.append(reference_id)
            references.append({"id": reference_id, "source_motion": entry["processed"], "source_sha256": entry["processed_sha256"],
                "frame": event["frame"], "time_s": float(motion.timestamps[event["frame"]]), "selection_reason": event["kind"],
                "space": "recording_motion_m_y_up", "status": "candidate_requires_selection",
                "constraint": "none", "joint_mask": [], "target_contract_sha256": sha(target),
                "source_skeleton_signature": skeleton_signature(motion.skeleton), "retargeted_to_fixed_rest": False})
        tasks.append({"clip_id": entry["id"], "route": route, "route_status": "source_label_candidate",
            "source_motion": entry["processed"], "processing_arrays": str(path), "reference_candidates": ref_ids,
            "condition_reference_ids": [], "future_pose_visibility": "none_until_explicitly_selected",
            "root_condition": "offline_source_proxy_not_player_log", "split": "unassigned",
            "training_ready": False, "window_policy": "retain_transitions_no_class_based_cutting"})
        observation["processing"] = {"policy": POLICY, "arrays": str(path), "arrays_sha256": sha(path),
                                     **measures, "historical_notes": notes[entry["id"]], "review_flags": flags}
        output_sidecar = report / (entry["id"] + ".json")
        write_json(output_sidecar, observation)
        records.append({"source_id": entry["asset"], "status": "normalized", "raw": entry["raw"], "processed": entry["processed"],
            "raw_sha256": entry["raw_sha256"], "processed_sha256": entry["processed_sha256"],
            "observation": str(output_sidecar), "observation_sha256": sha(output_sidecar), "processing_flags": flags,
            "processing_arrays": str(path), "processing_arrays_sha256": sha(path),
            "source_skeleton_signature": skeleton_signature(motion.skeleton), **measures})
        print(f"{len(records)}/{len(entries)} {entry['id']} error={measures['recomposition_max_abs_error_m']:.3g}m", flush=True)
    for filename, values in (("reference-candidates.json", references), ("task-index.json", tasks)):
        write_json(report / filename, values)
    (report / "export-index.csv").write_bytes(index.read_bytes())
    acceptance = {"batches": [run_id], "status_counts": {"normalized": len(records)},
                  "raw_and_processed_readback_errors": [], "source_endpoint_gaps": [], "recomposition_errors": errors}
    write_json(report / "acceptance.json", acceptance)
    summary = {"schema": "motionweaver_processing_pilot", "version": 1, "policy": POLICY,
        "source_index": str(index), "source_index_sha256": sha(index), "source_queue_sha256": digest,
        "target_contract_sha256": sha(target), "coverage": coverage, "clips": len(records),
        "max_recomposition_error_m": max(r["recomposition_max_abs_error_m"] for r in records),
        "recomposition_errors": errors, "source_skeleton_signatures": dict(Counter(r["source_skeleton_signature"] for r in records)),
        "historical_note_count": sum(len(v) for v in notes.values()), "results": records,
        "artifacts": {name: sha(report / name) for name in ("source-semantics.csv", "reference-candidates.json", "task-index.json")},
        "manual_contact_truth": 0, "selected_references": 0, "api_calls": 0,
        "scope": "offline_proxy_and_candidates_not_fixed_rest_retargeting_or_model_training"}
    write_json(report / "run.json", summary)
    return report / "run.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("E:/MotionWeaverData"))
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--target", type=Path, default=Path(__file__).parents[2] / "skeletons/soma_uniform_v1.json")
    args = parser.parse_args()
    print(prepare(args.data_root, args.index, args.run_id, args.target))


if __name__ == "__main__":
    main()
