"""本机只读迁移进度页：显示 E 盘数据迁移的进度、速度与预计完成时间。"""

import argparse
import json
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Progress:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.folder = self.root / "migration"
        self.plan_path = self.folder / "ai_animation_system_storage_migration.json"
        self.history = []
        self.value = None
        self.lock = threading.Lock()
        self.stop = False

    def scan(self):
        plan = json.loads(self.plan_path.read_text(encoding="utf-8"))
        jobs = []
        complete_bytes = 0
        total_bytes = 0
        for job in plan["jobs"]:
            done = 0
            done_files = 0
            for item in job["files"]:
                path = Path(job["destination"]) / item["relative"]
                if path.is_file() and path.stat().st_size == item["bytes"]:
                    done += item["bytes"]
                    done_files += 1
            total = job["bytes"]
            complete_bytes += done
            total_bytes += total
            log = self.folder / (job["name"] + ".robocopy.log")
            jobs.append(
                {
                    "name": job["name"],
                    "done_bytes": done,
                    "total_bytes": total,
                    "done_files": done_files,
                    "total_files": job["file_count"],
                    "verified": bool(job.get("verified")),
                    "log_exists": log.exists(),
                    "log_size": log.stat().st_size if log.exists() else 0,
                }
            )
        now = time.time()
        self.history.append((now, complete_bytes))
        self.history = [sample for sample in self.history if now - sample[0] <= 120]
        old = self.history[0]
        speed = (complete_bytes - old[1]) / (now - old[0]) if now > old[0] else 0
        eta = (total_bytes - complete_bytes) / speed if speed > 0 else None
        with self.lock:
            self.value = {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "complete_bytes": complete_bytes,
                "total_bytes": total_bytes,
                "speed_bytes_per_sec": speed,
                "eta_seconds": eta,
                "jobs": jobs,
                "runner_active": self.runner_active(),
                "error_log": self.error_log(),
            }

    def runner_active(self):
        # 迁移脚本不写心跳；Runner 日志最近修改且未出现错误时视为活动，由页面同时呈现日志。
        log = self.folder / "runner.out.log"
        return log.exists() and time.time() - log.stat().st_mtime < 300

    def error_log(self):
        path = self.folder / "runner.err.log"
        return (
            path.read_text(encoding="utf-8", errors="replace")[-4000:]
            if path.exists()
            else ""
        )

    def loop(self):
        while not self.stop:
            try:
                self.scan()
            except Exception as exc:
                with self.lock:
                    self.value = {
                        "error": str(exc),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
            for _ in range(10):
                if self.stop:
                    break
                time.sleep(1)

    def snapshot(self):
        with self.lock:
            return self.value or {"status": "正在读取迁移清单…"}


PAGE = """<!doctype html><meta charset="utf-8"><title>AIAnimationSystem · E盘迁移进度</title><style>
body{margin:0;background:#0e1420;color:#e7edf8;font:15px system-ui,"Segoe UI",sans-serif}main{max-width:980px;margin:48px auto;padding:0 24px}.card{background:#182233;border:1px solid #2b3a50;border-radius:14px;padding:22px;margin:16px 0}.bar{height:14px;background:#0c111b;border-radius:9px;overflow:hidden}.bar i{display:block;height:100%;background:linear-gradient(90deg,#52c5d8,#809cff);transition:width .4s}table{border-collapse:collapse;width:100%;margin-top:14px}td,th{text-align:left;padding:9px;border-bottom:1px solid #2b3a50}small{color:#9fb0c8}.bad{color:#ff9a99;white-space:pre-wrap}h1{margin-bottom:6px}.stats{display:flex;gap:28px;flex-wrap:wrap}.stats b{display:block;font-size:24px;margin-top:4px}</style><main><h1>E 盘数据迁移</h1><p>只读观察器 · 每 10 秒更新 · 不启动或干预数据处理</p><div class="card"><div class="stats"><div>总进度<b id="percent">—</b></div><div>已迁移<b id="done">—</b></div><div>当前速度<b id="speed">—</b></div><div>预计剩余<b id="eta">—</b></div></div><div class="bar"><i id="bar"></i></div><small id="updated">读取中…</small></div><div class="card"><b>任务明细</b><table><thead><tr><th>任务</th><th>文件</th><th>数据量</th><th>状态</th></tr></thead><tbody id="jobs"></tbody></table></div><div class="card"><b>错误日志</b><pre id="error">无</pre></div></main><script>
const f=n=>{if(n==null)return'—';let u=['B','KiB','MiB','GiB','TiB'],i=0;while(n>=1024&&i<u.length-1){n/=1024;i++}return n.toFixed(i?2:0)+' '+u[i]};const t=n=>{if(n==null||!isFinite(n))return'计算中…';let h=Math.floor(n/3600),m=Math.floor(n%3600/60),s=Math.floor(n%60);return(h?h+'时 ':'')+(m?m+'分 ':'')+s+'秒'};async function refresh(){let x=await fetch('/api').then(r=>r.json());if(x.error||!x.jobs){error.textContent=x.error||x.status||'正在读取…';return}let p=x.total_bytes?x.complete_bytes/x.total_bytes:0;percent.textContent=(p*100).toFixed(2)+'%';done.textContent=f(x.complete_bytes)+' / '+f(x.total_bytes);speed.textContent=f(x.speed_bytes_per_sec)+'/s';eta.textContent=t(x.eta_seconds);bar.style.width=(p*100)+'%';updated.textContent='最近扫描：'+new Date(x.updated_at).toLocaleString();jobs.innerHTML=x.jobs.map(j=>{let q=j.total_bytes?j.done_bytes/j.total_bytes:0;let status=j.verified?'已验证':j.log_exists?'迁移中':'等待中';return `<tr><td>${j.name}</td><td>${j.done_files.toLocaleString()} / ${j.total_files.toLocaleString()}</td><td>${f(j.done_bytes)} / ${f(j.total_bytes)} (${(q*100).toFixed(1)}%)</td><td>${status}</td></tr>`}).join('');error.textContent=x.error_log||'无'}refresh();setInterval(refresh,5000)</script>"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="E:/AIAnimationSystemData")
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    progress = Progress(args.root)
    if not progress.plan_path.is_file():
        raise FileNotFoundError(progress.plan_path)
    thread = threading.Thread(target=progress.loop, daemon=True)
    thread.start()

    class Handler(BaseHTTPRequestHandler):
        def send(self, body, kind="application/json"):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", kind + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.send(
                progress.snapshot() if self.path == "/api" else PAGE.encode(),
                "application/json" if self.path == "/api" else "text/html",
            )

        def log_message(self, *_):
            pass

    print(f"Migration progress: http://127.0.0.1:{args.port}", flush=True)
    try:
        ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    finally:
        progress.stop = True


if __name__ == "__main__":
    main()
