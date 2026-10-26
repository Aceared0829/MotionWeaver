"""把本地审核服务的 50 条样本导出成可静态托管的站点数据。

只读取运行中的只读审核服务，不修改源批次；输出目录用于云端部署。
"""

import argparse
import gzip
import json
import shutil
import urllib.request
from pathlib import Path

MAX_INLINE_MB = 2.0


def fetch(url, timeout=600):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8791")
    parser.add_argument("--static", type=Path, required=True, help="review_web 目录")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    output = args.output.resolve()
    if output.exists():
        raise SystemExit("输出目录已存在，请换一个版本名")
    data = output / "data"
    clips_dir = data / "clips"
    clips_dir.mkdir(parents=True)

    catalog = json.loads(fetch(f"{args.base}/api/catalog"))
    entries = catalog["entries"]
    if len(entries) != catalog["total"]:
        raise SystemExit("目录条目与服务端声明不一致")

    index = []
    for entry in entries:
        raw = fetch(f"{args.base}/api/clip/{entry['id']}")
        # 服务端返回已经 gzip 压缩的 JSON；解压后以 .json.gz 原样存储，
        # 由前端按需解压，避免浏览器自动解码带来的体积膨胀。
        payload = gzip.decompress(raw)
        sizes = json.loads(payload)
        (clips_dir / f"{entry['id']}.json.gz").write_bytes(raw)
        meta = {key: entry[key] for key in (
            "id", "name", "asset", "group", "flags", "integrity_errors",
            "frames", "joints", "fps", "reason") if key in entry}
        meta["processing_mode"] = True
        meta["bytes_gzip"] = len(raw)
        meta["inline"] = len(raw) <= MAX_INLINE_MB * 1024 * 1024
        index.append(meta)
        print(f"{entry['name'][:44]:46s} {len(raw)/1e6:7.2f} MB "
              f"{'inline' if meta['inline'] else 'lazy'}")

    shutil.copyfile(args.static / "index.html", output / "index.html")
    shutil.copyfile(args.static / "style.css", output / "style.css")
    (data / "catalog.json").write_text(json.dumps({
        "mode": "static_native_review",
        "total": len(index),
        "entries": index,
    }, ensure_ascii=False), encoding="utf-8")

    total = sum(item["bytes_gzip"] for item in index)
    print(f"\n{len(index)} clips · {total/1e6:.1f} MB gzip · "
          f"{sum(1 for i in index if not i['inline'])} lazy-loaded")


if __name__ == "__main__":
    main()
