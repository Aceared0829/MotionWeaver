"""生成被清洗数据的只读验收页面（单文件 HTML，无需服务）。

为什么不用 motion_review_server：它要求 reports/ 下有完整 acceptance.json
与各批次 run.json 审计链，且只纳入 normalized 状态；被拒条目天然不满足。
这里直接读取 rejected.jsonl 并连到 export-index，展示帧数、时长、拒绝原因，
让「为什么被剔除」一眼可见。

用法：
    python -m data.tools.build_rejected_report ^
        --dataset D:\\MotionWeaverData\\soma_train_v1\\full-v1 ^
        --index <export-index.csv> [--index ...] ^
        --output D:\\MotionWeaverData\\reviews\\rejected.html
"""

import argparse
import csv
import html
import json
from collections import Counter
from pathlib import Path


def load_rejected(dataset_root):
    path = Path(dataset_root) / "rejected.jsonl"
    if not path.is_file():
        raise FileNotFoundError(f"缺少 rejected.jsonl：{path}")
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def load_index(index_paths):
    """asset -> 索引行；用于补帧数、fps、时长、连接状态。"""
    table = {}
    for index_path in index_paths:
        with Path(index_path).open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                asset = row.get("asset", "")
                if asset and asset not in table:
                    table[asset] = row
    return table


def _as_int(value):
    """export-index 里所有字段都是字符串，这里做安全转换。"""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def classify(reason):
    """把拒绝原因归类，供页面分组与着色。"""
    if "帧数" in reason:
        return "tool_short"
    if "非有限值" in reason or "维数错误" in reason:
        return "bad_data"
    if "token" in reason:
        return "token_budget"
    if "OSError" in reason:
        return "io_error"
    return "other"


CATEGORY_LABEL = {
    "tool_short": "片段过短（帧数不足窗口+历史）",
    "bad_data": "源数据异常（NaN/Inf 或维度错误）",
    "token_budget": "token 预算超限",
    "io_error": "I/O 读取失败（可重试）",
    "other": "其他",
}

# 只有这几类真正算「数据被丢弃」；I/O 失败本可重试，应单独醒目标注。
RETRYABLE = {"io_error"}


def build(dataset_root, index_paths, output):
    dataset_root = Path(dataset_root).resolve()
    output = Path(output).resolve()
    rejected = load_rejected(dataset_root)
    index = load_index(index_paths)

    records = []
    for item in rejected:
        asset = item.get("source_id", "")
        reason = item.get("reason", "")
        row = index.get(asset, {})
        category = classify(reason)
        frames = _as_int(row.get("frames"))
        fps = row.get("fps")
        duration = row.get("duration")
        shortfall = None
        # 从 "帧数 69 不足 72（窗口+历史）" 里取出缺口
        if category == "tool_short" and "不足" in reason:
            try:
                have = int(reason.split("帧数")[1].split("不足")[0].strip())
                need = int(reason.split("不足")[1].split("（")[0].strip())
                shortfall = need - have
                frames = have
            except (IndexError, ValueError):
                pass
        records.append({
            "asset": asset,
            "name": row.get("name") or asset.split("/")[-1],
            "frames": frames,
            "fps": fps,
            "duration": duration,
            "reason": reason,
            "category": category,
            "retryable": category in RETRYABLE,
            "shortfall": shortfall,
            "in_index": bool(row),
        })

    records.sort(key=lambda r: (r["category"], -(r["frames"] or 0)))
    counts = Counter(r["category"] for r in records)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(records, counts, dataset_root), encoding="utf-8")
    return {"output": str(output), "total": len(records),
            "by_category": dict(counts)}


def render(records, counts, dataset_root):
    total = len(records)
    cards = "".join(
        f'<div class="card"><div class="n">{counts.get(key, 0)}</div>'
        f'<div class="t">{html.escape(CATEGORY_LABEL[key])}</div>'
        f'<div class="p">{(counts.get(key, 0) / total * 100):.1f}%</div></div>'
        for key in ("tool_short", "bad_data", "io_error", "other")
        if counts.get(key)
    )

    rows = []
    for record in records:
        frames = record["frames"] if record["frames"] is not None else "—"
        fps = f'{float(record["fps"]):.1f}' if record["fps"] else "—"
        shortfall = (f'<span class="warn">差 {record["shortfall"]} 帧</span>'
                     if record["shortfall"] is not None else "")
        badge = ('<span class="badge bad">可重试</span>' if record["retryable"]
                 else '<span class="badge ok">正确剔除</span>')
        rows.append(
            "<tr>"
            f'<td class="mono">{html.escape(record["asset"])}</td>'
            f"<td class='num'>{frames}</td>"
            f"<td class='num'>{fps}</td>"
            f"<td>{shortfall}</td>"
            f'<td class="reason">{html.escape(record["reason"])}</td>'
            f"<td>{badge}</td>"
            "</tr>"
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>被清洗数据审查 · {total} 条</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;padding:28px;background:#0f1115;color:#e6e8ec;
font:14px/1.6 "Segoe UI",system-ui,sans-serif}}
h1{{font-size:20px;margin:0 0 4px;font-weight:600}}
.sub{{color:#8b93a1;font-size:13px;margin-bottom:22px}}
.cards{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:24px}}
.card{{background:#171a21;border:1px solid #232833;border-radius:10px;
padding:14px 18px;min-width:170px}}
.card .n{{font-size:26px;font-weight:600;color:#5aa9ff}}
.card .t{{font-size:12px;color:#9aa3b2;margin-top:2px}}
.card .p{{font-size:11px;color:#5f6875;margin-top:4px}}
table{{width:100%;border-collapse:collapse;font-size:12.5px}}
th{{text-align:left;padding:9px 10px;background:#171a21;color:#9aa3b2;
font-weight:500;border-bottom:1px solid #232833;position:sticky;top:0}}
td{{padding:8px 10px;border-bottom:1px solid #1b1f27;vertical-align:top}}
tr:hover td{{background:#14171e}}
.mono{{font-family:Consolas,monospace;font-size:11.5px;color:#9fb4d0;
word-break:break-all;max-width:440px}}
.num{{text-align:right;font-variant-numeric:tabular-nums;color:#c9d1dc}}
.reason{{color:#b8a06a;font-size:12px}}
.warn{{color:#e0a34a}}
.badge{{display:inline-block;padding:2px 8px;border-radius:9px;font-size:11px}}
.badge.ok{{background:#1d2f22;color:#6cc98a}}
.badge.bad{{background:#33201d;color:#e0876c}}
</style></head><body>
<h1>被清洗数据审查</h1>
<div class="sub">数据集 <span class="mono">{html.escape(str(dataset_root))}</span>
 · 共 {total} 条被剔除，占全库 {total / 73000 * 100:.2f}%</div>
<div class="cards">{cards}</div>
<table><thead><tr>
<th>源资源</th><th class="num">帧数</th><th class="num">fps</th>
<th>缺口</th><th>拒绝原因</th><th>判定</th>
</tr></thead><tbody>{''.join(rows)}</tbody></table>
</body></html>"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--index", action="append", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = build(args.dataset, [Path(p) for p in args.index], args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
