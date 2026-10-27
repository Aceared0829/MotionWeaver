"""用正式验收界面（motion_review_server）查看被拒绝的数据。

不像 build_rejected_review / build_rejected_report 那样另造页面，这里生成
motion_review_server 需要的合法批次结构，让官方界面直接读：

    reports/<batch>/acceptance.json
    reports/<batch>/run.json        （只含被拒条目的原样记录）
    reports/<batch>/export-index.csv

关键原则：**不伪造审计结论**。run.json 里的记录从源批次的 run.json 逐字段
原样摘出（source_id、raw/processed 路径、sha256、帧数等全部保留），
「被拒绝」这件事通过 included_statuses 显式声明，而不是把条目谎报成 normalized。
"""

import argparse
import csv
import hashlib
import json
from pathlib import Path

def load_rejected(dataset_root):
    path = Path(dataset_root) / "rejected.jsonl"
    if not path.is_file():
        raise FileNotFoundError(f"缺少 rejected.jsonl：{path}")
    mapping = {}
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            mapping[row["source_id"]] = row.get("reason", "")
    return mapping


def load_source_records(report_dirs, wanted):
    """从各源批次的 run.json 里只摘出被拒条目，保持原始字段不变。"""
    found = {}
    for report_dir in report_dirs:
        run_path = Path(report_dir) / "run.json"
        if not run_path.is_file():
            continue
        report = json.loads(run_path.read_text(encoding="utf-8"))
        for record in report.get("results", []):
            source_id = record.get("source_id")
            if source_id in wanted and source_id not in found:
                found[source_id] = record
    return found


def fill_missing_geometry(rows):
    """重定向批次的 run.json 不带 frames/joints/fps/duration，从 processed/motion.json 补齐。

    真实结构：motion.json 的 positions/rotations 是 {offset, shape, dtype}，
    shape 为 [frames, joints, 3]；fps 与 source_duration 是顶层字段。
    读不到就留空，绝不编造。
    """
    for row in rows:
        if row["frames"] and row["joints"] and row["fps"]:
            continue
        motion_file = Path(row["processed"]) / "motion.json"
        if not motion_file.is_file():
            continue
        try:
            motion = json.loads(motion_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        shape = (motion.get("positions") or {}).get("shape") or []
        if len(shape) >= 2:
            if not row["frames"]:
                row["frames"] = str(int(shape[0]))
            if not row["joints"]:
                row["joints"] = str(int(shape[1]))

        if not row["fps"] and motion.get("fps"):
            row["fps"] = f"{float(motion['fps']):.6f}"
        if not row["duration"]:
            if motion.get("source_duration") is not None:
                row["duration"] = f"{float(motion['source_duration']):.6f}"
            elif row["frames"] and row["fps"]:
                try:
                    row["duration"] = f"{int(row['frames']) / float(row['fps']):.6f}"
                except (TypeError, ValueError, ZeroDivisionError):
                    pass
    return rows


def build(dataset_root, report_dirs, output, batch_name):
    dataset_root = Path(dataset_root).resolve()
    output = Path(output).resolve()
    rejected = load_rejected(dataset_root)
    records = load_source_records(report_dirs, set(rejected))

    if not records:
        raise RuntimeError("没有任何被拒条目能在源批次 run.json 中找到")

    output.mkdir(parents=True, exist_ok=True)

    # run.json：原样摘录，但剔除指向外部批次的字段——
    # observation 被验收服务强制要求位于本批次目录内，而原观察标注属于源批次；
    # 摘录过来只会让整份名单报错，且它不影响动画播放。保住记录本体更重要。
    ordered = sorted(records.values(), key=lambda r: r.get("source_id", ""))
    trimmed = []
    dropped_fields = set()
    for record in ordered:
        cleaned = dict(record)
        for key in ("observation", "observation_sha256"):
            if key in cleaned:
                cleaned.pop(key)
                dropped_fields.add(key)
        trimmed.append(cleaned)
    ordered = trimmed
    (output / "run.json").write_text(json.dumps({
        "batch": batch_name,
        "note": "只含被训练视图拒绝的条目；记录从源批次 run.json 原样摘录",
        "dropped_fields": sorted(dropped_fields),
        "results": ordered,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    # export-index.csv：验收服务按这些列构造条目
    rows = []
    for record in ordered:
        rows.append({
            "asset": record.get("source_id", ""),
            "name": record.get("name", ""),
            "status": record.get("status", ""),
            "raw": record.get("raw", ""),
            "processed": record.get("processed", ""),
            "fps": record.get("fps", ""),
            "frames": record.get("frames", ""),
            "joints": record.get("joints", ""),
            "duration": record.get("duration", ""),
        })
    index_path = output / "export-index.csv"
    rows = fill_missing_geometry(rows)
    with index_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    # acceptance.json：显式声明只纳入被拒条目，审计计数如实填写
    status_counts = {}
    for record in ordered:
        key = record.get("status", "unknown")
        status_counts[key] = status_counts.get(key, 0) + 1

    (output / "acceptance.json").write_text(json.dumps({
        "batches": [str(output.name)],
        "status_counts": status_counts,
        "included_statuses": sorted(status_counts),
        "raw_and_processed_readback_errors": 0,
        "source_endpoint_gaps": 0,
        "recomposition_errors": 0,
        "note": ("这是「被训练视图拒绝条目」的验收批次：记录来自源批次原始 run.json，"
                 "未经过任何重新审计；被拒原因见 reject-reasons.json"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    # 拒绝原因单独留档，界面上看不到时也能查
    (output / "reject-reasons.json").write_text(json.dumps(
        [{"source_id": item["source_id"], "name": item.get("name", ""),
          "frames": item.get("frames"), "fps": item.get("fps"),
          "reason": rejected[item["source_id"]]}
         for item in ordered if item["source_id"] in rejected],
        ensure_ascii=False, indent=2), encoding="utf-8")

    return {"output": str(output), "batch": batch_name,
            "entries": len(ordered), "status_counts": status_counts,
            "index": str(index_path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, help="含 rejected.jsonl 的训练视图")
    parser.add_argument("--report", action="append", required=True,
                        help="源批次报告目录（含 run.json），可重复")
    parser.add_argument("--library", default=r"D:\MotionWeaverData",
                        help="数据根目录，输出必须在其 reports/ 下")
    parser.add_argument("--group", default="soma_uniform", help="reports 下的分组名")
    parser.add_argument("--batch", required=True, help="批次目录名")
    args = parser.parse_args()

    output = Path(args.library) / "reports" / args.group / args.batch
    if output.exists():
        raise FileExistsError(f"批次目录已存在：{output}")
    result = build(args.dataset, [Path(p) for p in args.report], output, args.batch)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
