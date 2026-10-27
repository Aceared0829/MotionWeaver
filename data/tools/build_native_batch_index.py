"""从既有导入批次重建 export-index.csv 与 acceptance.json。

导入层（`import_motion_dataset.run_batch`）按设计只写 `run.json` 与 `clips.jsonl`，
而审核入口 `native_review_store.load_native_review_inputs` 要求同目录存在索引与验收汇总。
本工具补上这一段，使批次可从磁盘上已落盘并通过回读校验的产物重新生成两者，
不重新导入、不改写原始数据，也不代替人工审核结论。

索引字段沿用既有 12 列约定（与 `retarget_uefn_to_soma.retarget_batch` 相同），
使得同一份视图代码可以读取导入批次与重定向批次。
"""

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from data.runtime.native_motion_io import (
    read_native_motion,
    read_sampled_motion,
    skeleton_signature,
)

INDEX_FIELDS = ["asset", "name", "status", "joints", "frames", "duration", "fps",
                "skeleton", "skeleton_signature", "raw", "processed", "error"]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def folder_hashes(folder):
    return {name: sha256(Path(folder) / name) for name in ("motion.json", "arrays.bin")}


def asset_name(asset):
    """`Path/Asset.Asset` → `Asset`；保留原资产身份，仅用于显示。"""
    return str(asset).split(".")[-1] if "." in str(asset) else Path(str(asset)).name


def derive_row(record, root):
    """只接受已落盘且回读一致的 normalized 记录，其余原样返回为不可入索引。"""
    if record.get("status") != "normalized":
        return None, record.get("error") or record.get("status")
    missing = [key for key in ("raw", "processed", "raw_sha256", "processed_sha256") if key not in record]
    if missing:
        return None, "记录缺少字段：" + ", ".join(missing)
    for partition in ("raw", "processed"):
        folder = Path(record[partition])
        if not folder.is_relative_to(root / partition):
            return None, f"{partition} 路径越过数据分区"
        actual = folder_hashes(folder)
        expected = record[partition + "_sha256"]
        # 内容身份以磁盘现状为准：变了就不进索引，而不是把旧哈希写进新索引。
        if actual != expected:
            return None, f"{partition} 内容与批次回执不一致"
    sampled = read_sampled_motion(record["processed"])
    native = read_native_motion(record["raw"])
    if native.source.source_id != record["source_id"] or sampled.source.source_id != record["source_id"]:
        return None, "动作来源身份与回执不一致"
    if len(sampled.timestamps) != record["frames"]:
        return None, "真实帧数与回执不一致"
    # 显示用的骨架资产路径来自导入期记录；缺失时留空而不是编造。
    skeleton_asset = native.source.details.get("skeleton_asset", "")
    row = {"asset": record["source_id"], "name": asset_name(record["source_id"]), "status": "normalized",
           "joints": len(sampled.skeleton.names), "frames": len(sampled.timestamps),
           "duration": sampled.source_duration, "fps": sampled.fps,
           "skeleton": skeleton_asset, "skeleton_signature": skeleton_signature(sampled.skeleton),
           "raw": str(Path(record["raw"])), "processed": str(Path(record["processed"])), "error": ""}
    return row, None


def build(data_root, run_ids, dataset="gameanimation"):
    """按顺序合并多个批次；后一批次的同名资产替换前一批次（与补跑语义一致）。"""
    root = Path(data_root).resolve()
    if isinstance(run_ids, str):
        run_ids = [run_ids]
    if not run_ids:
        raise ValueError("至少需要一个批次 ID")
    merged, excluded, source_counts, provenance = {}, [], Counter(), {}
    for run_id in run_ids:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", run_id):
            raise ValueError(f"无效 run_id：{run_id}")
        report = root / "reports" / dataset / run_id
        report_json = report / "run.json"
        if not report_json.is_file():
            raise FileNotFoundError(f"批次缺少 run.json：{report_json}")
        batch = json.loads(report_json.read_text(encoding="utf-8"))
        results = batch.get("results") or []
        if not results:
            raise ValueError(f"批次 {run_id} 没有可重建的结果记录")
        source_counts[run_id] = dict(Counter(record.get("status") for record in results))
        provenance[run_id] = sha256(report_json)
        for record in results:
            row, reason = derive_row(record, root)
            if row is None:
                if record.get("status") == "normalized":
                    excluded.append({"source_id": record.get("source_id"), "batch": run_id,
                                     "reason": reason, "kind": "output_mismatch"})
                else:
                    excluded.append({"source_id": record.get("source_id"), "batch": run_id,
                                     "reason": reason, "kind": str(record.get("status"))})
                continue
            # 补跑批次替换先前结果：真实批次里 1844+36+91=1971，减 1 条替换得 1970。
            merged[row["asset"]] = dict(row, batch=run_id)
    if not merged:
        raise ValueError("没有任何可入索引的条目；请先检查批次产物是否仍在磁盘上")
    rows = sorted(merged.values(), key=lambda item: item["asset"])

    # 先写索引再写验收汇总：任一失败都不会留下「看起来完整」的批次。
    index_path = root / "reports" / dataset / run_ids[0] / "export-index.csv"
    with index_path.open("w", encoding="utf-8-sig", newline="") as stream:
        # 归属批次只记在验收汇总里，索引保持既有 12 列契约。
        writer = csv.DictWriter(stream, fieldnames=INDEX_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    acceptance = {
        "batches": list(run_ids),
        "status_counts": {"normalized": len(rows)},
        "raw_and_processed_readback_errors": [entry["reason"] for entry in excluded
                                              if entry["kind"] == "output_mismatch"],
        "source_endpoint_gaps": [],
        "scope": "index_rebuilt_from_landed_outputs",
        "derived_from": {run_id: str(root / "reports" / dataset / run_id / "run.json") for run_id in run_ids},
        "derived_from_sha256": provenance,
        "source_counts": source_counts,
        "clip_batches": {row["asset"]: row["batch"] for row in rows},
        "excluded": excluded,
        "rebuild": {"tool": "build_native_batch_index", "rows": len(rows), "excluded": len(excluded)},
    }
    (index_path.parent / "acceptance.json").write_text(
        json.dumps(acceptance, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"index": str(index_path), "rows": len(rows), "excluded": len(excluded),
            "status_counts": source_counts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("E:/MotionWeaverData"))
    parser.add_argument("--run-id", required=True, action="append",
                        help="既有导入批次 ID，可重复；后者替换前者同名资产")
    parser.add_argument("--dataset", default="gameanimation")
    args = parser.parse_args()
    print(json.dumps(build(args.data_root, args.run_id, args.dataset), ensure_ascii=False))


if __name__ == "__main__":
    main()
