"""把训练视图的 rejected.jsonl 转成 motion_review_server 可读的验收批次。

用途：在既有验收界面里直接查看被清洗掉的数据，判断拒绝是否合理。

做法：不改动源批次，也不复制动画文件；只生成一份过滤后的 export-index.csv，
其中只保留被拒绝的条目，并附一个 reject-reason 清单供界面显示。

用法：
    python -m data.tools.build_rejected_review ^
        --dataset D:\\MotionWeaverData\\soma_train_v1\\full-v1 ^
        --index E:\\...\\export-index.csv --index E:\\...\\export-index.csv ^
        --output D:\\MotionWeaverData\\reviews\\full-v1-rejected

随后：
    python -m data.tools.motion_review_server --native-index <output>/export-index.csv
"""

import argparse
import csv
import json
from pathlib import Path

INDEX_FIELDS = (
    "asset", "name", "status", "raw", "processed", "fps",
    "frames", "joints", "duration",
)


def load_rejected(dataset_root):
    """读取 rejected.jsonl；键为 asset，值保留原始拒绝原因。"""
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
            # 同一 asset 可能被记录多次（多轮构建），保留最后一次原因
            rejected[row["source_id"]] = row.get("reason", "")
    return rejected


def load_index_rows(index_paths):
    """把 export-index 的全部行读进内存，键为 asset。"""
    rows = {}
    for index_path in index_paths:
        with Path(index_path).open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                asset = row.get("asset", "")
                if asset and asset not in rows:
                    rows[asset] = row
    return rows


def build(dataset_root, index_paths, output):
    dataset_root = Path(dataset_root).resolve()
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(f"输出目录已存在：{output}")

    rejected = load_rejected(dataset_root)
    index_rows = load_index_rows(index_paths)

    matched, missing = [], []
    for asset, reason in rejected.items():
        row = index_rows.get(asset)
        if row is None:
            missing.append({"asset": asset, "reason": reason})
            continue
        matched.append((row, reason))

    if not matched:
        raise RuntimeError("没有任何被拒条目能在 export-index 中找到，无法生成验收批次")

    output.mkdir(parents=True)
    index_path = output / "export-index.csv"
    with index_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(INDEX_FIELDS),
                                extrasaction="ignore")
        writer.writeheader()
        for row, _ in matched:
            writer.writerow({key: row.get(key, "") for key in INDEX_FIELDS})

    # 拒绝原因单独成表，供人工对照；界面本身按索引里的 frames/status 展示
    reasons = output / "reject-reasons.json"
    reasons.write_text(json.dumps(
        [{"asset": row["asset"], "name": row.get("name", ""),
          "frames": row.get("frames"), "fps": row.get("fps"),
          "reason": reason} for row, reason in matched],
        ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "output": str(output),
        "index": str(index_path),
        "rejected_total": len(rejected),
        "matched": len(matched),
        "unmatched": len(missing),
        "unmatched_sample": missing[:5],
        "reasons": str(reasons),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, help="含 rejected.jsonl 的训练视图")
    parser.add_argument("--index", action="append", required=True,
                        help="export-index.csv，可重复")
    parser.add_argument("--output", required=True, help="验收批次输出目录")
    args = parser.parse_args()
    result = build(args.dataset, [Path(p) for p in args.index], args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
