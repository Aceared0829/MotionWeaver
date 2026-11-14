"""封存条件动作契约、离线验收与基线；原始 NPZ 仍由来源哈希引用。"""

import argparse
import hashlib
import json
import math
import shutil
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path

from data.tools.review.conditioned_review_store import PAGE_SIZE, load_review_inputs


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def freeze(contract_dir, audit_dir, baseline_path, library, output, lock_output):
    contract_dir, audit_dir = Path(contract_dir).resolve(), Path(audit_dir).resolve()
    baseline_path, library = Path(baseline_path).resolve(), Path(library).resolve()
    output, lock_output = Path(output).resolve(), Path(lock_output).resolve()
    if output.exists() or lock_output.exists():
        raise FileExistsError("封版目录或仓库锁文件已存在")
    contract, report, entries, queue_hash = load_review_inputs(contract_dir, audit_dir)
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if report["integrity_errors"] or report["checked_clips"] != len(contract["clips"]):
        raise ValueError("来源审计未全量通过")
    if report["active_exact_motion_cross_split_groups"] or report["training_exclusion_candidates"]:
        raise ValueError("仍有有效训练片段与留出集重复或待排除")
    if report["generation_windows"] != contract["window_count"]:
        raise ValueError("审计窗口数量与契约不一致")
    if baseline.get("contract_sha256") != report["contract_sha256"] or baseline.get("split") != "test":
        raise ValueError("测试基线与契约不匹配")
    if not baseline.get("oracle_root_condition"):
        raise ValueError("基线条件类型不符")

    cohort_dir = library / "reviews"
    db_path = cohort_dir / "decisions.sqlite3"
    decisions = []
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)) as db:
        for page in range(1, math.ceil(len(entries) / PAGE_SIZE) + 1):
            cohort_name = f"conditioned_{queue_hash[:16]}_p{page:02}.json"
            cohort = json.loads((cohort_dir / cohort_name).read_text(encoding="utf-8"))
            selected = entries[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
            if (cohort.get("mode") != "conditioned_prepared_review"
                    or cohort.get("queue_sha256") != queue_hash
                    or cohort.get("contract_sha256") != report["contract_sha256"]
                    or cohort.get("entries") != selected
                    or cohort.get("page") != page
                    or cohort.get("total") != len(entries)):
                raise ValueError(f"验收名单不匹配：{cohort_name}")
            rows = {mid: (status, note, updated) for mid, status, note, updated in db.execute(
                "SELECT id,status,note,updated FROM decisions WHERE cohort=?", (cohort_name,))}
            if set(rows) != {entry["id"] for entry in selected}:
                raise ValueError(f"验收结论不完整：{cohort_name}")
            for entry in selected:
                status, note, updated = rows[entry["id"]]
                if status != "approved":
                    raise ValueError(f"仍有未通过结论：{cohort_name}/{entry['id']}")
                decisions.append({"page": page, "cohort": cohort_name, "id": entry["id"],
                                  "asset": entry["asset"], "group": entry["group"],
                                  "status": status, "note": note, "updated": updated})

    files = {"contract/contract.json": contract_dir / "contract.json",
             "contract/windows.jsonl": contract_dir / "windows.jsonl",
             "audit/report.json": audit_dir / "report.json",
             "audit/review_queue.json": audit_dir / "review_queue.json",
             "audit/clips.csv": audit_dir / "clips.csv",
             "baseline/report.json": baseline_path}
    for group in contract["stats"].values():
        for key in ("mean", "std"):
            path = contract_dir / "stats" / group[key]
            if sha256(path) != group[f"{key}_sha256"]:
                raise ValueError(f"统计量哈希不匹配：{path}")
            files[f"contract/stats/{path.name}"] = path
    if sha256(audit_dir / "review_queue.json") != queue_hash:
        raise ValueError("复核清单已变化")
    with (contract_dir / "windows.jsonl").open(encoding="utf-8") as stream:
        window_count = sum(1 for _ in stream)
    if window_count != contract["window_count"]:
        raise ValueError("窗口索引数量与契约不一致")

    output.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for name, source in files.items():
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if sha256(source) != sha256(target):
            raise ValueError(f"封版复制后哈希不一致：{name}")
        hashes[name] = sha256(target)
    write_json(output / "review_decisions.json", decisions)
    hashes["review_decisions.json"] = sha256(output / "review_decisions.json")
    summary = {"schema_version": 1, "release_id": output.name,
               "source_dataset": contract["source_dataset"],
               "source_manifest_sha256": contract["source_manifest_sha256"],
               "source_training_signature": contract["source_training_signature"],
               "skeleton_sha256": contract["skeleton_sha256"],
               "raw_clip_count": len(contract["clips"]),
               "windows": contract["window_split_counts"],
               "excluded_train_clip_indices": contract["excluded_train_clip_indices"],
               "review_queue_sha256": queue_hash,
               "review": {"approved": len(decisions),
                          "groups": dict(sorted(Counter(item["group"] for item in decisions).items())),
                          "bulk_marked_notes": sum("批量通过" in item["note"] for item in decisions),
                          "scope": "review_queue_only; approval_does_not_prove_every_clip_was_individually_watched"},
               "baseline": {"split": "test", "selection": baseline["selection"],
                            "oracle_root_condition": baseline["oracle_root_condition"]},
               "artifacts_sha256": dict(sorted(hashes.items())),
               "training_authorized": False}
    write_json(output / "freeze_manifest.json", summary)
    lock_output.parent.mkdir(parents=True, exist_ok=True)
    write_json(lock_output, summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--audit", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--library", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--lock-output", required=True)
    args = parser.parse_args()
    summary = freeze(args.contract, args.audit, args.baseline, args.library,
                     args.output, args.lock_output)
    print(json.dumps({key: summary[key] for key in ("release_id", "raw_clip_count", "windows", "review")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
