"""从已审计的独立运动批次中分层抽取小样本，生成只读的派生验收目录。

不修改来源批次，不复制动画文件；派生目录只保存筛选后的索引、裁剪后的回执
和观察标注副本，供 motion_review_server --native-index 快速启动审核。
"""

import argparse
import csv
import hashlib
import json
import shutil
from collections import defaultdict
from pathlib import Path


def load_selection(metadata):
    """读取非镜像元数据；键为 BVH 相对路径，用作与导出索引的连接键。"""
    rows = {}
    with metadata.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["is_mirror"].casefold() != "false":
                continue
            rows[row["move_soma_uniform_path"].replace("\\", "/")] = row
    return rows


def stratified(rows, metadata, limit):
    """按动作类别分层，每层优先未用过的演员，保证 50 条覆盖尽量多的类别。"""
    buckets = defaultdict(list)
    for row in rows:
        meta = metadata[row["asset"]]
        buckets[meta["category"] or "Other"].append((row, meta))
    for values in buckets.values():
        values.sort(key=lambda item: (item[1]["take_actor"], item[0]["asset"]))

    picked = []
    used_actors = set()
    # 轮转各层，先每层取一条不同演员的动作，再回头补足。
    while len(picked) < limit:
        advanced = False
        for key in sorted(buckets):
            values = buckets[key]
            if not values or len(picked) >= limit:
                continue
            choice = next((v for v in values if v[1]["take_actor"] not in used_actors), values[0])
            values.remove(choice)
            picked.append(choice)
            used_actors.add(choice[1]["take_actor"])
            advanced = True
        if not advanced:
            break
    return picked


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def copy_observation(record, source_report, review):
    """观察标注必须位于索引所在目录内，因此按原相对位置复制副本。"""
    if "observation" not in record:
        return record
    original = Path(record["observation"]).resolve()
    if not original.is_relative_to(source_report):
        raise ValueError("观察标注不在来源报告目录内")
    relative = original.relative_to(source_report)
    destination = review / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(original, destination)
    if sha(destination) != record["observation_sha256"]:
        raise ValueError("观察标注副本哈希不一致")
    return {**record, "observation": str(destination)}


def build(source, output, limit, metadata_path):
    source, output = source.resolve(), output.resolve()
    if output.exists():
        raise ValueError("派生目录已存在，请换一个版本名")
    metadata = load_selection(metadata_path)
    with (source / "export-index.csv").open(encoding="utf-8-sig", newline="") as stream:
        index_rows = list(csv.DictReader(stream))
    chosen = stratified(index_rows, metadata, limit)
    if len(chosen) != limit:
        raise ValueError("候选不足，无法生成完整样本")

    fields = list(index_rows[0])
    assets = {row["asset"] for row, _ in chosen}
    # 审核服务按 index.parent.parent / batch / run.json 定位回执，因此索引放在
    # 子目录、回执与验收汇总放在批次目录根部。
    output.mkdir(parents=True, exist_ok=False)
    review = output
    # 完整 run.json 很大，只保留被选中的条目。
    report = json.loads((source / "run.json").read_text(encoding="utf-8"))
    results = []
    for record in report["results"]:
        if record["source_id"] not in assets:
            continue
        results.append(copy_observation(record, source, review))
    if len(results) != limit:
        raise ValueError("回执结果与导出索引数量不一致")

    with (output / "export-index.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(row for row, _ in chosen)

    summary = json.loads((source / "acceptance.json").read_text(encoding="utf-8"))
    (output / "acceptance.json").write_text(json.dumps({
        **summary,
        # 派生目录只覆盖抽样条目，计数必须与裁剪后的索引一致。
        "batches": [output.name],
        "status_counts": {"normalized": len(chosen)},
        "derived_from": str(source),
        "derived_scope": f"stratified_sample_of_{limit}",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "run.json").write_text(json.dumps({
        **{k: v for k, v in report.items() if k != "results"},
        "results": results,
        "derived_from": str(source),
    }, ensure_ascii=False), encoding="utf-8")

    manifest = {
        "derived_from": str(source),
        "requested": limit,
        "selected": len(chosen),
        "categories": sorted({metadata[row["asset"]]["category"] for row, _ in chosen}),
        "actors": len({metadata[row["asset"]]["take_actor"] for row, _ in chosen}),
        "items": [{"asset": row["asset"], "name": row["name"],
                   "category": metadata[row["asset"]]["category"],
                   "actor": metadata[row["asset"]]["take_actor"]} for row, _ in chosen],
        "training_ready": False,
    }
    (output / "sample-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, default=Path("E:/MotionWeaverData/metadata/seed_metadata_v004.csv"))
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    if not 1 <= args.limit <= 50:
        raise ValueError("验收页面每页最多 50 条")
    manifest = build(args.source, args.output, args.limit, args.metadata)
    print(json.dumps({k: manifest[k] for k in ("selected", "actors")} |
                     {"categories": len(manifest["categories"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
