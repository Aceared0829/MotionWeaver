"""把 paper/main.html 渲染为 paper/MotionWeaver-technical-report.pdf。

为什么用 Chromium（Chrome / Edge）的 headless 打印，而不是 wkhtmltopdf 或 LibreOffice：
只有它是完整的现代排版引擎，能正确处理 @page、break-inside: avoid、flex 以及
matchMedia('print') 分支。本报告的分栏、图表避断、参考文献悬挂缩进都依赖这些特性。

用法（在仓库根目录）：
    ./.venv/Scripts/python.exe paper/build_pdf.py
    ./.venv/Scripts/python.exe paper/build_pdf.py --browser "C:/path/to/chrome.exe"

脚本会：
  1. 检查 main.html 引用的所有 figures/ 图片是否存在；
  2. 用临时 user-data-dir 启动一次 headless 浏览器，避免影响日常配置；
  3. 以 A4 打印 PDF（页边距由 HTML 的 @page 控制），不带页眉页脚；
  4. 打印体积、页数与 SHA-256，并断言至少生成了 1 页。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PAPER_DIR = Path(__file__).resolve().parent
DEFAULT_HTML = PAPER_DIR / "main.html"
DEFAULT_PDF = PAPER_DIR / "MotionWeaver-technical-report.pdf"

# Chromium 打印 PDF 时的固定等待：等字体与图片就绪，避免首屏空白。
VIRTUAL_TIME_BUDGET_MS = 15000


def _env(name: str) -> str:
    """读取环境变量；Windows 的 PROGRAMFILES(x86) 名字含括号，用 get 直接取即可。"""
    return os.environ.get(name, "")


def find_browser(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise SystemExit(f"指定的浏览器不存在：{path}")
        return path

    suffixes = [
        "Google/Chrome/Application/chrome.exe",
        "Microsoft/Edge/Application/msedge.exe",
    ]
    candidates: list[Path] = []
    for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA", "PROGRAMW6432"):
        base = _env(env_name)
        if not base:
            continue
        candidates += [Path(base) / suffix for suffix in suffixes]
    # 环境变量缺失时（例如从精简环境启动），退回最常见的安装位置。
    # 注意：必须写成 "C:/Program Files"，否则 Path("C:") 会得到驱动器相对路径。
    for drive in ("C:/", "D:/"):
        for root in ("Program Files", "Program Files (x86)"):
            candidates += [Path(drive + root) / suffix for suffix in suffixes]
    # 兜底：PATH 中可用的 chrome / msedge
    for name in ("chrome.exe", "msedge.exe"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise SystemExit("未找到 Chrome 或 Edge；请用 --browser 指定可执行文件路径。")


def check_figures(html_path: Path) -> int:
    text = html_path.read_text(encoding="utf-8")
    refs = sorted(set(re.findall(r'src="(figures/[^"]+)"', text)))
    missing = [ref for ref in refs if not (PAPER_DIR / ref).is_file()]
    if missing:
        detail = "\n".join(f"  - {PAPER_DIR / m}" for m in missing)
        raise SystemExit(
            "缺少插图，请先运行 paper/generate_figures.py：\n" + detail
        )
    return len(refs)


def count_pages(pdf_path: Path) -> int:
    """粗略统计页数：数 PDF 中的 /Type /Page 对象（排除 /Pages）。"""
    raw = pdf_path.read_bytes()
    return len(re.findall(rb"/Type\s*/Page[^s]", raw))


def build(html_path: Path, pdf_path: Path, browser: Path) -> dict:
    if not html_path.is_file():
        raise SystemExit(f"找不到输入 HTML：{html_path}")

    figure_count = check_figures(html_path)
    print(f"插图检查  : {figure_count} 张，全部存在")

    if pdf_path.exists():
        pdf_path.unlink()

    profile_dir = Path(tempfile.mkdtemp(prefix="mw-paper-pdf-"))
    try:
        file_url = html_path.as_uri()
        command = [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-sync",
            "--hide-scrollbars",
            "--run-all-compositor-stages-before-draw",
            f"--virtual-time-budget={VIRTUAL_TIME_BUDGET_MS}",
            f"--user-data-dir={profile_dir}",
            "--print-to-pdf-no-header",
            f"--print-to-pdf={pdf_path}",
            file_url,
        ]
        print(f"浏览器    : {browser}")
        print(f"输入 HTML : {html_path}")
        print(f"输出 PDF  : {pdf_path}")
        # 浏览器在打印完成后退出；stderr 里的扩展注册告警不影响产物，因此只提示不中断。
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=180, errors="replace"
        )
        if completed.returncode != 0:
            print(
                f"[提示] 浏览器退出码 {completed.returncode}；Chromium 打印 PDF 时"
                "常返回非零，仍会生成文件，故继续校验产物。",
                file=sys.stderr,
            )
    finally:
        shutil.rmtree(profile_dir, ignore_errors=True)

    if not pdf_path.is_file():
        raise SystemExit(f"PDF 未生成：{pdf_path}")

    size = pdf_path.stat().st_size
    pages = count_pages(pdf_path)
    digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()

    if size < 20_000:
        raise SystemExit(f"PDF 体积异常小（{size} 字节），判定为失败产物。")
    if pages < 1:
        raise SystemExit("PDF 页数为 0，判定为失败产物。")

    print()
    print(f"页数      : {pages}")
    print(f"体积      : {size:,} 字节（{size / 1024 / 1024:.2f} MiB）")
    print(f"SHA-256   : {digest}")
    print("完成。")
    return {"pages": pages, "bytes": size, "sha256": digest}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把 main.html 渲染成 PDF")
    parser.add_argument("--input", default=str(DEFAULT_HTML), help="输入 HTML 路径")
    parser.add_argument("--output", default=str(DEFAULT_PDF), help="输出 PDF 路径")
    parser.add_argument("--browser", default=None, help="Chrome / Edge 可执行文件路径")
    args = parser.parse_args(argv)

    try:
        build(Path(args.input).resolve(), Path(args.output).resolve(), find_browser(args.browser))
    except subprocess.TimeoutExpired:
        print("浏览器打印超时（180 秒）。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
