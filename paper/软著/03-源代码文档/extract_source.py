#!/usr/bin/env python3
"""从 MotionWeaver 仓库提取源代码并排版为软著提交格式。

产物：
  源程序.html          60 页 × 50 行 = 3000 行，每页带页眉与行号
  选取清单.md          文件 / 行号范围 / 行数 / 对应说明书章节

规则（依据 paper/软著/00-撰写提示词.md 的 P3 一节）：
  - 前 1500 行取「数据与模型定义」，后 1500 行取「训练与推理入口」
  - 排除测试、第三方、自动生成代码；不重新格式化，保留原缩进与注释
  - 单行显示宽度不超过 100 列（CJK 记 2 列）；超长行按续行折行，不省略内容
  - 每页严格 50 行，行号 1..3000 连续，跨页不重置
"""
from __future__ import annotations

import html
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OUT_DIR = Path(__file__).resolve().parent
TITLE = "MotionWeaver 角色动画生成系统 V1.0"
LINES_PER_PAGE = 50
TOTAL_PAGES = 60
TOTAL_LINES = LINES_PER_PAGE * TOTAL_PAGES
MAX_COLS = 100
CONT_INDENT = "        "

# (相对路径, 起始行, 结束行, 说明书章节, 分组)
SELECTION = [
    ("model/MotionWeaver/geometry/quaternions.py", 1, 41, "5.2 动作数据表示", "一"),
    ("model/MotionWeaver/representation/core/utils/rotations.py", 1, 389, "5.2 动作数据表示", "一"),
    ("data/runtime/unreal_dataset.py", 1, 385, "6.2 训练数据预处理", "一"),
    ("model/MotionWeaver/tokenizer/neural_modules/vqvae.py", 1, 196, "5.3 离散姿态表示学习", "一"),
    ("model/MotionWeaver/tokenizer/neural_modules/quantize_cnn_multihead.py", 1, 230, "5.3 离散姿态表示学习", "一"),
    ("model/MotionWeaver/backbone/neural_modules/pose_backbone.py", 1, 248, "5.4 条件生成模型", "一"),
    ("training/models/vqvae/motion_vqvae.py", 1, 320, "6.4 模型训练", "二"),
    ("training/pretrain/train_unreal.py", 1, 370, "6.4 模型训练", "二"),
    ("inference/runtime/motionweaver.py", 1, 346, "6.6 姿态生成推理", "二"),
    ("inference/export/export_unreal_inference.py", 1, 84, "6.5 模型导出", "二"),
    ("inference/cli/interactive_demo_g1.py", 1, 223, "6.7 可视化交互界面", "二"),
]


def is_wide(ch: str) -> bool:
    code = ord(ch)
    return (
        0x1100 <= code <= 0x115F
        or 0x2E80 <= code <= 0xA4CF
        or 0xAC00 <= code <= 0xD7A3
        or 0xF900 <= code <= 0xFAFF
        or 0xFE30 <= code <= 0xFE4F
        or 0xFF00 <= code <= 0xFF60
        or 0xFFE0 <= code <= 0xFFE6
    )


def display_width(text: str) -> int:
    return sum(2 if is_wide(ch) else 1 for ch in text)


def wrap_line(raw: str) -> list[str]:
    """把一行折成若干显示行，保持内容完整、不加省略号。"""
    line = raw.rstrip("\r\n")
    if display_width(line) <= MAX_COLS:
        return [line]

    out: list[str] = []
    rest = line
    first = True
    while rest:
        budget = MAX_COLS if first else MAX_COLS - len(CONT_INDENT)
        cut = len(rest)
        width = 0
        for idx, ch in enumerate(rest):
            w = 2 if is_wide(ch) else 1
            if width + w > budget:
                cut = idx
                break
            width += w
        else:
            cut = len(rest)

        if cut == 0:
            cut = 1

        piece = rest[:cut]
        if cut < len(rest):
            window_start = max(0, cut - 24)
            best = -1
            for idx in range(cut, window_start, -1):
                if rest[idx - 1] in " ,)]}":
                    best = idx
                    break
            if best > 0 and display_width(rest[:best]) > 40:
                piece = rest[:best]
                cut = best

        out.append(piece if first else CONT_INDENT + piece)
        rest = rest[cut:]
        first = False
    return out


def read_range(path: Path, start: int, end: int) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    lo = max(1, start)
    hi = min(len(lines), end)
    return lines[lo - 1 : hi]


def build_stream():
    rows: list[dict] = []
    stream: list[str] = []
    for rel, start, end, chapter, group in SELECTION:
        path = REPO / rel
        if not path.is_file():
            raise SystemExit("缺少源文件：" + rel)
        raw = read_range(path, start, end)
        if not raw:
            raise SystemExit("行号范围为空：" + rel)
        wrapped: list[str] = []
        for line in raw:
            wrapped.extend(wrap_line(line))
        rows.append({
            "path": rel,
            "start": start,
            "end": start + len(raw) - 1,
            "source_lines": len(raw),
            "display_lines": len(wrapped),
            "chapter": chapter,
            "group": group,
        })
        stream.extend(wrapped)
    return rows, stream


def fit_to_total(rows, stream):
    """把总显示行数校准到 3000 行。"""
    if len(stream) > TOTAL_LINES:
        drop = len(stream) - TOTAL_LINES
        while drop > 0 and rows:
            last = rows[-1]
            take = min(drop, max(0, last["display_lines"] - 1))
            if take <= 0:
                rows.pop()
                break
            last["display_lines"] -= take
            last["source_lines"] -= take
            last["end"] -= take
            drop -= take
        return rows, stream[:TOTAL_LINES]

    if len(stream) < TOTAL_LINES:
        need = TOTAL_LINES - len(stream)
        while need > 0 and rows:
            last = rows[-1]
            path = REPO / last["path"]
            total_src = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
            nxt = last["end"] + 1
            if nxt > total_src:
                rows.pop()
                continue
            extra_raw = read_range(path, nxt, total_src)
            extra: list[str] = []
            consumed = 0
            for line in extra_raw:
                piece = wrap_line(line)
                if len(extra) + len(piece) > need:
                    break
                extra.extend(piece)
                consumed += 1
            if not extra:
                rows.pop()
                continue
            stream.extend(extra)
            need -= len(extra)
            last["end"] += consumed
            last["source_lines"] += consumed
            last["display_lines"] += len(extra)
    return rows, stream[:TOTAL_LINES]


CSS = """
  @page { size: A4; margin: 0; }
  body { margin:0; background:#e8eaed; font-family: Consolas, "Cascadia Mono", monospace; }
  .page {
    width: 210mm; height: 297mm; padding: 14mm 14mm 12mm 14mm;
    margin: 0 auto 12px; background:#fff; box-shadow:0 2px 10px rgba(0,0,0,.12);
    position: relative; overflow: hidden; page-break-after: always;
    box-sizing: border-box;
  }
  .page:last-child { page-break-after: auto; }
  .page-header {
    position:absolute; top:6mm; left:14mm; right:14mm;
    display:flex; justify-content:space-between; align-items:baseline;
    font-family:"Songti SC","SimSun",serif; font-size:8.5pt; color:#444;
    border-bottom:.5pt solid #bbb; padding-bottom:2mm;
  }
  pre {
    margin: 0;
    font-size: 7.2pt;
    line-height: 1.36;
    /* pre 内的块级 .ln 之间会有源码换行符；用 normal 让它们折叠，
       再在 .ln 上恢复 pre 语义，避免每行后面多出一个空行。 */
    white-space: normal;
    overflow: hidden;
    color: #111;
    tab-size: 4;
  }
  pre .ln { display: block; white-space: pre; }
  pre .ln::before {
    content: attr(data-n);
    display:inline-block; width:9mm;
    color:#aaa; text-align:right; margin-right:3mm;
  }
  @media print { body { background:#fff; } .page { box-shadow:none; margin:0; } }
"""


def render_html(stream):
    parts = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN">',
        "<head>",
        '<meta charset="utf-8">',
        "<title>" + TITLE + " 源程序</title>",
        "<style>" + CSS + "</style>",
        "</head>",
        "<body>",
        "",
        "<!-- 本文件由 03-源代码文档/extract_source.py 生成，不要手改。 -->",
        "<!-- 共 " + str(TOTAL_PAGES) + " 页，每页 " + str(LINES_PER_PAGE) + " 行，合计 " + str(TOTAL_LINES) + " 行。 -->",
        "",
    ]
    for page_index in range(TOTAL_PAGES):
        chunk = stream[page_index * LINES_PER_PAGE : (page_index + 1) * LINES_PER_PAGE]
        parts.append('<div class="page">')
        parts.append('  <div class="page-header">')
        parts.append("    <span>" + TITLE + "</span>")
        parts.append("    <span>第 " + str(page_index + 1) + " 页　共 " + str(TOTAL_PAGES) + " 页</span>")
        parts.append("  </div>")
        parts.append("  <pre>")
        for offset, line in enumerate(chunk):
            n = page_index * LINES_PER_PAGE + offset + 1
            text = html.escape(line) if line else " "
            parts.append('<span class="ln" data-n="' + str(n) + '">' + text + "</span>")
        parts.append("  </pre>")
        parts.append("</div>")
        parts.append("")
    parts += ["</body>", "</html>"]
    return "\n".join(parts)


def render_manifest(rows):
    out = [
        "# 源程序选取清单",
        "",
        "> 本清单由 extract_source.py 自动生成，与 源程序.html 一一对应。",
        "> 合计 " + str(TOTAL_LINES) + " 行 / " + str(TOTAL_PAGES) + " 页，每页 " + str(LINES_PER_PAGE) + " 行。",
        "",
        "## 一、选取清单",
        "",
        "| 顺序 | 文件路径 | 行号范围 | 源行数 | 显示行数 | 对应说明书章节 |",
        "|---|---|---|---|---|---|",
    ]
    for idx, row in enumerate(rows, 1):
        out.append(
            "| " + str(idx) + " | " + row["path"] + " | "
            + str(row["start"]) + "-" + str(row["end"]) + " | "
            + str(row["source_lines"]) + " | " + str(row["display_lines"]) + " | "
            + row["chapter"] + " |"
        )
    total_src = sum(r["source_lines"] for r in rows)
    total_disp = sum(r["display_lines"] for r in rows)
    out += [
        "| - | **合计** | - | **" + str(total_src) + "** | **" + str(total_disp) + "** | - |",
        "",
        "## 二、前后两半划分",
        "",
    ]
    for group, label in (("一", "第一部分　数据与模型定义"), ("二", "第二部分　训练与推理入口")):
        sub = [r for r in rows if r["group"] == group]
        n = sum(r["display_lines"] for r in sub)
        out.append("- **" + label + "**：" + str(len(sub)) + " 个文件，" + str(n) + " 行")
    out += ["", "## 三、校验结论", "", "- 总行数 3000、每页 50 行、共 60 页：见下方脚本输出。", ""]
    return "\n".join(out)


def main() -> int:
    rows, stream = build_stream()
    rows, stream = fit_to_total(rows, stream)
    longest = max((display_width(line) for line in stream), default=0)
    over = sum(1 for line in stream if display_width(line) > MAX_COLS)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "源程序.html").write_text(render_html(stream), encoding="utf-8")
    (OUT_DIR / "选取清单.md").write_text(render_manifest(rows), encoding="utf-8")

    print("页数            :", TOTAL_PAGES)
    print("总行数          :", len(stream))
    print("每页行数是否 50 :", len(stream) == TOTAL_LINES)
    print("最长显示宽度    :", longest, "（上限", MAX_COLS, "）")
    print("超宽行数        :", over)
    print("空行数          :", sum(1 for line in stream if not line.strip()))
    for row in rows:
        print("  {:<60} {:>4}-{:<4} 源 {:>4} 行 -> 显示 {:>4} 行  [{}]".format(
            row["path"], row["start"], row["end"],
            row["source_lines"], row["display_lines"], row["chapter"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
