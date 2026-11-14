"""把 Windows 导出的 export-index.csv 路径改写为当前机器可用路径。

export-index.csv 是 Windows 侧处理时导出的索引，processed/raw 两列写的是 E:/MotionWeaverData/... 。
USB 盘挂到 Linux 上后同一份内容位于 <mount>/MotionWeaverData/... ，所以只需替换前缀。
"""
import argparse
import csv
import sys
from pathlib import Path

PATH_COLUMNS = ("processed", "raw")


def rewrite(row, new_root):
    for name in PATH_COLUMNS:
        value = row.get(name) or ""
        normalised = value.replace("\\", "/")
        if "/MotionWeaverData/" in normalised:
            row[name] = new_root + "/" + normalised.split("/MotionWeaverData/", 1)[1]
        elif normalised.startswith("E:/"):
            # 兜底：E:/MotionWeaverData/... 形式之外的其他 E: 路径同样按根替换
            row[name] = new_root + "/" + normalised.split(":/", 1)[1].split("/", 1)[-1]
        else:
            row[name] = normalised
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", required=True, help="源 export-index.csv")
    parser.add_argument("--root", required=True, help="数据根目录（含 processed/ 的那一层）")
    parser.add_argument("--output", required=True, help="改写后的 csv")
    parser.add_argument("--check", action="store_true", help="抽查前若干条路径是否存在")
    args = parser.parse_args()

    root = args.root.rstrip("/")
    source = Path(args.index)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    missing = 0
    with source.open(encoding="utf-8-sig", newline="") as stream, \
            output.open("w", encoding="utf-8", newline="") as target:
        reader = csv.DictReader(stream)
        writer = csv.DictWriter(target, fieldnames=reader.fieldnames)
        writer.writeheader()
        for row in reader:
            row = rewrite(row, root)
            if args.check and rows < 20 and not Path(row["processed"]).is_dir():
                missing += 1
                print("MISSING", row["processed"], file=sys.stderr)
            writer.writerow(row)
            rows += 1
    print(f"rows={rows} root={root} output={output} checked_missing={missing}")


if __name__ == "__main__":
    main()
