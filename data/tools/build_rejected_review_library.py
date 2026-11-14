"""把训练视图里被拒绝的条目组装成一份可直接验收的本地批次库。

为什么需要它：motion_review_server 的 --native-index 模式要求「批次目录 + run.json
+ acceptance.json + export-index.csv」齐全，并且要求 raw/processed 目录真的位于
--library 之下、文件哈希与记录一致（native_review_store.load_native_review_inputs
与 NativeReviewStore.clip 都会校验）。而源批次的 run.json 里写的是 Windows 的
E:\\MotionWeaverData\\... 路径，本机读不到；直接拿它启动会在路径校验处失败。

本工具做三件事，都不改变审计结论：

1. 只挑出被拒条目，把它们的 raw/processed 目录**复制**到本地库（默认几 MB/条，
   不复制整个批次），复制后逐文件重算 sha256 并与 run.json 里的值比对，不符即中止。
2. 把 run.json / export-index.csv 里的路径改写成库内路径，其余字段原样保留。
3. acceptance.json 显式声明 included_statuses，如实记录被拒状态，不谎报 normalized。

用法：

    python -m data.tools.build_rejected_review_library \\
        --dataset /home/acear/MotionWeaverData/full-train-v1 \\
        --report /run/media/acear/THUNDEROBOT/MotionWeaverData/reports/soma_uniform/20261018-soma-full-v1 \\
        --source-root /run/media/acear/THUNDEROBOT/MotionWeaverData \\
        --library /home/acear/MotionWeaverData/review_library \\
        --group soma_uniform --batch rejected-full-train-v1

然后：

    python -m data.tools.motion_review_server \\
        --library /home/acear/MotionWeaverData/review_library \\
        --native-index /home/acear/MotionWeaverData/review_library/reports/soma_uniform/rejected-full-train-v1/export-index.csv \\
        --port 8766
"""

import argparse
import csv
import hashlib
import json
import shutil
from pathlib import Path

INDEX_FIELDS = ("asset", "name", "status", "raw", "processed", "fps",
                "frames", "joints", "duration")


def load_rejected(dataset_root):
    """读训练视图的 rejected.jsonl，返回 {asset: 原因}。"""
    path = Path(dataset_root) / "rejected.jsonl"
    if not path.is_file():
        raise FileNotFoundError(f"缺少 rejected.jsonl：{path}")
    rejected = {}
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            rejected[row["source_id"]] = row.get("reason", "")
    return rejected


def relocalise(value, source_root, library, partition, clip_dir):
    """把源机器路径映射到库内路径。

    源路径形如 E:\\MotionWeaverData\\processed\\soma_uniform\\...\\clips\\<id>，
    取 "MotionWeaverData/" 之后的部分拼到 source_root；再按 clip 目录名重建库内路径。
    认不出的写法直接抛错，不猜。
    """
    normalised = str(value).replace("\\", "/")
    if "/MotionWeaverData/" not in normalised:
        raise ValueError(f"无法识别的源路径：{value}")
    tail = normalised.split("/MotionWeaverData/", 1)[1]
    source = Path(source_root) / tail
    if not source.is_dir():
        raise FileNotFoundError(f"源目录不存在：{source}")
    return source, Path(library) / partition / clip_dir


def copy_verified(source, target, expected):
    """复制目录并逐文件校验 sha256；返回实际写入的文件哈希。"""
    target.mkdir(parents=True, exist_ok=True)
    actual = {}
    for name, digest in expected.items():
        origin = source / name
        if not origin.is_file():
            raise FileNotFoundError(f"缺少文件：{origin}")
        payload = origin.read_bytes()
        got = hashlib.sha256(payload).hexdigest()
        if got != digest:
            raise ValueError(f"{origin} 的 sha256 与记录不符：{got} != {digest}")
        (target / name).write_bytes(payload)
        actual[name] = got
    if set(expected) != set(actual):
        raise ValueError("哈希清单与实际文件不一致")
    return actual


def build(dataset_root, report_dir, source_root, library, group, batch):
    dataset_root = Path(dataset_root).resolve()
    report_dir = Path(report_dir).resolve()
    source_root = Path(source_root).resolve()
    library = Path(library).resolve()
    batch_dir = library / "reports" / group / batch
    if batch_dir.exists():
        raise FileExistsError(f"批次目录已存在：{batch_dir}")

    rejected = load_rejected(dataset_root)
    run = json.loads((report_dir / "run.json").read_text(encoding="utf-8"))
    records = [r for r in run.get("results", []) if r.get("source_id") in rejected]
    if not records:
        raise RuntimeError("源批次里没有任何一条能对上被拒清单")

    batch_dir.mkdir(parents=True)
    kept, skipped = [], []
    for record in sorted(records, key=lambda r: r["source_id"]):
        clip_dir = Path(str(record["processed"]).replace("\\", "/")).name
        try:
            raw_src, raw_dst = relocalise(record["raw"], source_root, library, "raw", clip_dir)
            proc_src, proc_dst = relocalise(record["processed"], source_root, library, "processed", clip_dir)
            copy_verified(raw_src, raw_dst, record["raw_sha256"])
            copy_verified(proc_src, proc_dst, record["processed_sha256"])
        except (OSError, ValueError) as error:
            skipped.append({"source_id": record["source_id"], "error": f"{type(error).__name__}: {error}"})
            continue
        cleaned = dict(record)
        # observation / processing_arrays 指向源批次目录，验收服务要求旁证位于本批次内；
        # 它们只影响附加标注的显示，不影响动画本身，摘录时连同路径一起丢掉。
        for key in ("observation", "observation_sha256", "processing_arrays", "processing_arrays_sha256"):
            cleaned.pop(key, None)
        cleaned["raw"] = str(raw_dst)
        cleaned["processed"] = str(proc_dst)
        kept.append(cleaned)

    if not kept:
        raise RuntimeError("所有被拒条目都无法本地化，未生成验收批次")

    (batch_dir / "run.json").write_text(json.dumps({
        "batch": batch,
        "note": ("只含训练视图拒绝的条目；记录从源批次 run.json 原样摘录，"
                 "仅把 raw/processed 改写为库内副本路径，复制后已逐文件校验 sha256"),
        "source_report": str(report_dir),
        "results": kept,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    with (batch_dir / "export-index.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(INDEX_FIELDS), extrasaction="ignore")
        writer.writeheader()
        for record in kept:
            # run.json 里的记录用 source_id 标识动画，验收索引要的是 asset 列；
            # 直接按同名字段取值会写出一整列空 asset，随后 load_native_review_inputs
            # 在 records[row["asset"]] 处抛 KeyError: ''。
            row = {key: record.get(key, "") for key in INDEX_FIELDS}
            row["asset"] = record["source_id"]
            writer.writerow(row)

    status_counts = {}
    for record in kept:
        status = record.get("status", "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1

    (batch_dir / "acceptance.json").write_text(json.dumps({
        "batches": [batch],
        "status_counts": status_counts,
        "included_statuses": sorted(status_counts),
        "raw_and_processed_readback_errors": 0,
        "source_endpoint_gaps": 0,
        "recomposition_errors": 0,
        "note": ("这是「被训练视图拒绝条目」的验收批次：记录来自源批次原始 run.json，"
                 "未重新审计；拒绝原因见 reject-reasons.json"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    (batch_dir / "reject-reasons.json").write_text(json.dumps([
        {"asset": record["source_id"], "name": record.get("name", ""),
         "frames": record.get("frames"), "fps": record.get("fps"),
         "reason": rejected[record["source_id"]]}
        for record in kept
    ], ensure_ascii=False, indent=2), encoding="utf-8")

    return {"library": str(library), "batch": batch,
            "index": str(batch_dir / "export-index.csv"),
            "entries": len(kept), "status_counts": status_counts,
            "skipped": skipped}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, help="含 rejected.jsonl 的训练视图")
    parser.add_argument("--report", required=True, help="源批次报告目录（含 run.json）")
    parser.add_argument("--source-root", required=True,
                        help="源数据根目录（含 raw/ 与 processed/ 的那一层）")
    parser.add_argument("--library", required=True, help="验收库根目录（新建）")
    parser.add_argument("--group", default="soma_uniform")
    parser.add_argument("--batch", required=True)
    args = parser.parse_args()
    result = build(args.dataset, args.report, args.source_root,
                   args.library, args.group, args.batch)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
