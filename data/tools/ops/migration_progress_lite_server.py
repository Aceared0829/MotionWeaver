#!/usr/bin/env python3
"""Local, low-I/O observer for the E: AI animation data migration."""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

GIB = 1024**3
EXPECTED = {
    "BONES-SEED": 564.39 * GIB,
    "MotionDataLibrary": 67.56 * GIB,
    "UE export cache": 81.83 * GIB,
    "prepared datasets": 10.06 * GIB,
}


def byte_size(path: Path) -> int:
    total = 0
    if path.is_symlink() or (
        path.exists() and getattr(path.stat(), "st_file_attributes", 0) & 0x400
    ):
        return total
    for current, dirs, names in os.walk(path, followlinks=False):
        folder = Path(current)
        retained = []
        for name in dirs:
            item = folder / name
            try:
                if (
                    not item.is_symlink()
                    and not getattr(
                        item.stat(follow_symlinks=False), "st_file_attributes", 0
                    )
                    & 0x400
                ):
                    retained.append(name)
            except OSError:
                pass
        dirs[:] = retained
        for name in names:
            item = folder / name
            try:
                if (
                    not item.is_symlink()
                    and not getattr(
                        item.stat(follow_symlinks=False), "st_file_attributes", 0
                    )
                    & 0x400
                ):
                    total += item.stat().st_size
            except OSError:
                pass
    return total


class Progress:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.lock = threading.Lock()
        self.samples: list[tuple[float, int]] = []
        self.payload = {
            "state": "正在首次统计目标目录…",
            "updated_at": None,
            "jobs": [],
            "total_bytes": 0,
            "expected_bytes": sum(EXPECTED.values()),
            "speed_bps": 0,
            "eta_seconds": None,
        }

    def scan(self) -> None:
        roots = {
            "BONES-SEED": self.root / "BONES-SEED",
            "MotionDataLibrary": self.root / "MotionDataLibrary",
            "UE export cache": self.root / "UE" / "AILocomotionDataset",
            "prepared datasets": self.root / "prepared",
        }
        jobs = []
        total = 0
        for name, folder in roots.items():
            copied = byte_size(folder) if folder.exists() else 0
            expected = EXPECTED[name]
            total += copied
            jobs.append(
                {
                    "name": name,
                    "copied_bytes": copied,
                    "expected_bytes": expected,
                    "percent": min(100.0, copied * 100 / expected),
                }
            )
        now = time.time()
        with self.lock:
            self.samples.append((now, total))
            self.samples = [
                (stamp, value) for stamp, value in self.samples if now - stamp <= 180
            ]
            speed = 0.0
            if len(self.samples) >= 2:
                first_stamp, first_value = self.samples[0]
                if now > first_stamp:
                    speed = max(0.0, (total - first_value) / (now - first_stamp))
            remaining = max(0, sum(EXPECTED.values()) - total)
            self.payload = {
                "state": "迁移中（每 30 秒采样）",
                "updated_at": now,
                "jobs": jobs,
                "total_bytes": total,
                "expected_bytes": sum(EXPECTED.values()),
                "speed_bps": speed,
                "eta_seconds": (remaining / speed if speed > 0 else None),
            }

    def run(self) -> None:
        while True:
            self.scan()
            time.sleep(30)

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.payload)


HTML = r"""<!doctype html><meta charset="utf-8"><title>AI Animation System · E盘迁移</title><style>body{font:16px system-ui;background:#10151d;color:#e8eef7;margin:0;padding:34px;max-width:950px}h1{margin:0 0 6px}small{color:#9aabba}.card{background:#192330;border-radius:12px;padding:18px;margin:16px 0}.bar{height:14px;background:#0b1016;border-radius:9px;overflow:hidden}.bar i{display:block;height:100%;background:linear-gradient(90deg,#49c3ff,#7fdf9d)}.row{display:flex;justify-content:space-between;gap:16px;margin:12px 0 7px}.big{font-size:28px;font-weight:700}#status{color:#9aabba}</style><h1>AI Animation System · E盘迁移</h1><div id="status">连接中…</div><div class="card"><div class="big" id="total">—</div><div class="bar"><i id="overall" style="width:0"></i></div><div id="meta"></div></div><div id="jobs"></div><script>const gb=n=>(n/1073741824).toFixed(2)+' GiB';const time=s=>s==null?'统计中':s>86400?(s/86400).toFixed(1)+' 天':s>3600?(s/3600).toFixed(1)+' 小时':Math.ceil(s/60)+' 分钟';async function refresh(){try{let d=await (await fetch('/api')).json(),p=Math.min(100,d.total_bytes*100/d.expected_bytes);total.textContent=p.toFixed(2)+'%  · '+gb(d.total_bytes)+' / '+gb(d.expected_bytes);overall.style.width=p+'%';meta.textContent='速度：'+(d.speed_bps?gb(d.speed_bps)+' / 秒':'等待两次采样')+'　预计剩余：'+time(d.eta_seconds);document.getElementById("status").textContent=d.state+'　上次更新：'+(d.updated_at?new Date(d.updated_at*1000).toLocaleString():'—');jobs.innerHTML=d.jobs.map(j=>'<div class="card"><div class="row"><span>'+j.name+'</span><b>'+j.percent.toFixed(2)+'%</b></div><div class="bar"><i style="width:'+j.percent+'%"></i></div><small>'+gb(j.copied_bytes)+' / '+gb(j.expected_bytes)+'</small></div>').join('')}catch(e){document.getElementById("status").textContent='暂时无法读取服务：'+e}}refresh();setInterval(refresh,5000)</script>"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("E:/AIAnimationSystemData"))
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    progress = Progress(args.root)
    threading.Thread(target=progress.run, daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/api":
                body = json.dumps(progress.snapshot()).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                body = HTML.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        def log_message(self, *_: object) -> None:
            pass

    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
