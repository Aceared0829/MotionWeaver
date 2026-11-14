"""仅监听本机的人工验收查看器；源动作只读，人工结论独立保存，不发布或删除数据。"""

import argparse
import gzip
import hashlib
import json
import math
import secrets
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime, timezone
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
from scipy.spatial.transform import Rotation

from data.runtime.bvh_reader import read_bvh
from data.runtime.unreal_dataset import file_sha256

STATIC = Path(__file__).resolve().parent / "review_web"
MAX_REVIEW_ITEMS = 50
EXCEPTION_STATES = {
    "needs_segmentation",
    "needs_skeleton_review",
    "needs_secondary_review",
    "retarget_failed",
}


def choose_cohort(reports, seed):
    problems = []
    clean = []
    unresolved = {
        "heading_not_validated",
        "contacts_not_validated",
        "visual_quality_not_validated",
    }
    for item in reports:
        flags = [f for f in item["review_flags"] if f not in unresolved]
        entry = dict(item, flags=flags)
        (problems if flags or item["integrity_errors"] else clean).append(entry)
    if len(problems) > MAX_REVIEW_ITEMS:
        raise ValueError(
            "疑似问题超过 50 条：先修复或隔离问题再生成验收名单，不能截断异常并批准整批"
        )
    ranked = sorted(
        clean, key=lambda r: hashlib.sha256((seed + r["id"]).encode()).hexdigest()
    )
    sample = ranked[
        : min(math.ceil(len(clean) * 0.05), MAX_REVIEW_ITEMS - len(problems))
    ]
    return [dict(r, group="issue") for r in problems] + [
        dict(r, group="sample") for r in sample
    ], len(clean)


class ReviewStore:
    def __init__(
        self, library, source, exception_entries=None, exception_digest=None, page=1
    ):
        self.library = Path(library).resolve()
        self.source = Path(source).resolve()
        self.review = self.library / "reviews"
        self.review.mkdir(exist_ok=True)
        self.exception_mode = exception_entries is not None
        if exception_entries is not None:
            entries = exception_entries[
                (page - 1) * MAX_REVIEW_ITEMS : page * MAX_REVIEW_ITEMS
            ]
            page_digest = hashlib.sha256(
                json.dumps(entries, sort_keys=True).encode()
            ).hexdigest()
            self.cohort_path = self.review / f"secondary_review_live_p{page:02}.json"
            if self.cohort_path.exists():
                old = json.loads(self.cohort_path.read_text(encoding="utf-8"))
                if old["entries"] != entries[: len(old["entries"])]:
                    self.cohort_path = (
                        self.review
                        / f"exception_triage_{page_digest[:16]}_p{page:02}.json"
                    )
                elif old["entries"] != entries:
                    from data.tools.publish.close_seed_batch import write_json

                    write_json(
                        self.cohort_path,
                        dict(
                            old,
                            entries=entries,
                            total=len(exception_entries),
                            checked=len(exception_entries),
                        ),
                    )
            if not self.cohort_path.exists():
                cohort = {
                    "mode": "exception_triage",
                    "manifest_sha256": exception_digest,
                    "page": page,
                    "page_size": MAX_REVIEW_ITEMS,
                    "total": len(exception_entries),
                    "checked": len(exception_entries),
                    "basic_clean": 0,
                    "entries": entries,
                }
                self.cohort_path.write_text(
                    json.dumps(cohort, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        else:
            screen = self.library / "reports/pending_quality_screen.json"
            digest = file_sha256(screen)
            self.cohort_path = self.review / ("cohort_" + digest[:16] + ".json")
            if not self.cohort_path.exists():
                reports = json.loads(screen.read_text(encoding="utf-8"))
                seed = secrets.token_hex(16)
                entries, clean = choose_cohort(reports["reports"], seed)
                cohort = {
                    "mode": "retarget_review",
                    "screen_sha256": digest,
                    "seed": seed,
                    "checked": reports["checked"],
                    "basic_clean": clean,
                    "entries": entries,
                }
                self.cohort_path.write_text(
                    json.dumps(cohort, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        self.cohort = json.loads(self.cohort_path.read_text(encoding="utf-8"))
        if len(self.cohort["entries"]) > MAX_REVIEW_ITEMS:
            raise ValueError("历史名单超过 50 条，须保留原记录并重新安排，不能静默截断")
        self.ids = {e["id"] for e in self.cohort["entries"]}
        self.token = secrets.token_urlsafe(32)
        with closing(sqlite3.connect(self.review / "decisions.sqlite3")) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS decisions(cohort TEXT,id TEXT,status TEXT,note TEXT,updated TEXT,PRIMARY KEY(cohort,id))"
            )

    def decisions(self):
        with closing(sqlite3.connect(self.review / "decisions.sqlite3")) as db, db:
            return {
                mid: {"status": status, "note": note, "updated": updated}
                for mid, status, note, updated in db.execute(
                    "SELECT id,status,note,updated FROM decisions WHERE cohort=?",
                    (self.cohort_path.name,),
                )
            }

    def save(self, payload):
        mid, status, note = (
            payload.get("id"),
            payload.get("status"),
            payload.get("note", ""),
        )
        if (
            mid not in self.ids
            or status not in {"approved", "rejected", "unsure", "unreviewed"}
            or not isinstance(note, str)
            or len(note) > 4000
        ):
            raise ValueError("无效验收记录")
        with closing(sqlite3.connect(self.review / "decisions.sqlite3")) as db, db:
            db.execute(
                "INSERT OR REPLACE INTO decisions VALUES(?,?,?,?,?)",
                (
                    self.cohort_path.name,
                    mid,
                    status,
                    note,
                    datetime.now(UTC).isoformat(),
                ),
            )

    @lru_cache(maxsize=2)
    def clip(self, mid):
        if mid not in self.ids:
            raise ValueError("动作不在本次验收名单")
        with closing(
            sqlite3.connect(
                f'file:{self.library / "catalog.sqlite3"}?mode=ro', uri=True
            )
        ) as db, db:
            row = db.execute(
                "SELECT name,metadata_json,output_path,output_sha256,state FROM motions WHERE id=?",
                (mid,),
            ).fetchone()
            event = db.execute(
                "SELECT detail FROM events WHERE motion_id=? AND state='retargeted_pending_quality' ORDER BY id DESC LIMIT 1",
                (mid,),
            ).fetchone()
        if not row:
            raise ValueError("缺少来源回执")
        name, metadata, output, expected, state = row
        metadata = json.loads(metadata)
        source = (self.source / metadata["move_soma_uniform_path"]).resolve()
        if not source.is_relative_to(self.source / "soma_uniform"):
            raise ValueError("路径越界")
        entry = next(e for e in self.cohort["entries"] if e["id"] == mid)
        has_target = bool(
            self.exception_mode
            and entry.get("output_sha256")
            and output
            and event
            and not entry["integrity_errors"]
        )
        if self.exception_mode and (
            state not in EXCEPTION_STATES
            or file_sha256(source) != entry["source_sha256"]
        ):
            raise ValueError("二审来源或隔离状态已变化")
        if has_target:
            output = Path(output).resolve()
            if (
                not output.is_relative_to(self.library)
                or file_sha256(output) != entry["output_sha256"]
            ):
                raise ValueError("二审输出身份已变化；源文件保留，请检查报告")
        if self.exception_mode and not has_target:
            entry = next(e for e in self.cohort["entries"] if e["id"] == mid)
            if (
                state not in EXCEPTION_STATES
                or file_sha256(source) != entry["source_sha256"]
            ):
                raise ValueError("异常来源或隔离状态已变化，拒绝继续验收")
        else:
            if not event:
                raise ValueError("缺少来源回执")
            output = Path(output).resolve()
            if (
                not output.is_relative_to(self.library)
                or file_sha256(output) != expected
                or file_sha256(source) != json.loads(event[0])["source_sha256"]
            ):
                raise ValueError("文件已变化，拒绝错误对照")
        bvh = read_bvh(source)
        p, r = bvh.forward()
        hips = bvh.names.index("Hips")
        if bvh.parents[hips] != 0 or bvh.channels[hips][:3] != [
            "Xposition",
            "Yposition",
            "Zposition",
        ]:
            raise ValueError("源平移协议不支持")
        p[:, hips:] -= np.einsum("tij,j->ti", r[:, 0], bvh.offsets[hips])[:, None]
        basis = np.array([[1.0, 0, 0], [0, 0, 1], [0, 1, 0]])
        p = p @ basis.T

        def pack(positions, names, parents, pelvis, root_positions):
            if not np.isfinite(positions).all():
                raise ValueError("非有限坐标")
            if not np.isfinite(root_positions).all():
                raise ValueError("非有限 Root 坐标")
            return {
                "names": names,
                "parents": parents,
                "pelvis": pelvis,
                "positions": np.round(positions, 4).reshape(-1).tolist(),
                "root_positions": np.round(root_positions, 4).reshape(-1).tolist(),
            }

        if self.exception_mode and not has_target:
            data = {
                "id": mid,
                "name": name,
                "fps": round(1 / bvh.frame_time),
                "frames": len(p),
                "package": metadata.get("package"),
                "description": metadata.get("content_natural_desc_1", ""),
                "source": pack(p, bvh.names, bvh.parents, hips, p[:, 0]),
                "target": None,
                "target_root": None,
                "source_sha256": entry["source_sha256"],
                "output_sha256": None,
                "review_note": entry.get("reason", "")
                + "；右侧没有可验证的 UEFN 输出，源数据保留。",
            }
        else:
            with np.load(output, allow_pickle=False) as archive:
                f = archive["frames"].astype(float)
                roots = archive["root_frames"].astype(float)
                fps = float(archive["fps"])
            if len(p) != len(f) or fps != 120 or abs(1 / bvh.frame_time - fps) > 0.01:
                raise ValueError("帧数或时间不一致，不能同步")
            rr = Rotation.from_quat(roots[:, 3:]).as_matrix()
            world = np.einsum("tij,tkj->tki", rr, f[:, :, :3]) + roots[:, None, :3]
            bones = json.loads((output.parent / "skeleton.json").read_text())["bones"]
            data = {
                "id": mid,
                "name": name,
                "fps": fps,
                "frames": len(f),
                "package": metadata.get("package"),
                "description": metadata.get("content_natural_desc_1", ""),
                "source": pack(p, bvh.names, bvh.parents, hips, p[:, 0]),
                "target": pack(
                    world,
                    [b["name"] for b in bones],
                    [b["parent"] for b in bones],
                    0,
                    roots[:, :3],
                ),
                "target_root": np.round(roots[:, :3], 4).tolist(),
                "source_sha256": json.loads(event[0])["source_sha256"],
                "output_sha256": expected,
            }
            evidence = json.loads(event[0])
            if evidence.get("source_reference_normalized"):
                data["review_note"] = (
                    f"源身体参考偏移已归一化，最大修正 {evidence['max_reference_correction_cm']:.4f} cm；保留完整帧数与原绝对 Hips 运动，右侧是重新生成的 UE 输出。"
                )
        return gzip.compress(
            json.dumps(data, separators=(",", ":")).encode(), compresslevel=3
        )


def exception_manifest(library, source):
    with closing(
        sqlite3.connect(f'file:{library / "catalog.sqlite3"}?mode=ro', uri=True)
    ) as db, db:
        placeholders = ",".join("?" for _ in EXCEPTION_STATES)
        rows = db.execute(
            f"SELECT id,name,metadata_json,state,output_sha256,error FROM motions WHERE state IN ({placeholders}) ORDER BY state,name,id",
            tuple(sorted(EXCEPTION_STATES)),
        ).fetchall()
    entries = []
    for mid, name, metadata, state, output_sha256, error in rows:
        metadata = json.loads(metadata)
        path = (source / metadata["move_soma_uniform_path"]).resolve()
        if not path.is_relative_to(source / "soma_uniform") or not path.is_file():
            raise ValueError(f"异常来源路径无效：{mid}")
        frames = int(float(metadata.get("move_duration_frames", 0)))
        reason = (
            "超过先前队列的完整导出帧数预算；可提高容量后重试，不表示源数据损坏"
            if state == "needs_segmentation"
            else (
                "源骨骼头未被已验证 UEFN 映射覆盖，需要人工判定"
                if state == "needs_skeleton_review"
                else error or state
            )
        )
        details = {}
        if state == "needs_secondary_review" and error:
            try:
                details = json.loads(error)
            except (ValueError, TypeError):
                pass
        if details:
            reason = "重新处理或技术检查后待二审：" + ", ".join(
                details.get("integrity_errors", []) + details.get("review_flags", [])
            )
        entries.append(
            {
                "id": mid,
                "name": name,
                "group": "issue",
                "state": state,
                "flags": details.get("review_flags", [state]),
                "integrity_errors": details.get("integrity_errors", []),
                "frames": frames,
                "reason": reason,
                "package": metadata.get("package"),
                "description": metadata.get("content_natural_desc_1", ""),
                "source_sha256": file_sha256(path),
                "output_sha256": output_sha256,
            }
        )
    # 追加式固定次序；新增异常不能重排已完成页或改写其人工结论。
    order_path = library / "reviews/secondary_queue_order.json"
    previous = (
        json.loads(order_path.read_text(encoding="utf-8"))
        if order_path.exists()
        else []
    )
    known = set(previous)
    previous.extend(e["id"] for e in entries if e["id"] not in known)
    from data.tools.publish.close_seed_batch import write_json

    if not order_path.exists() or previous != json.loads(
        order_path.read_text(encoding="utf-8")
    ):
        write_json(order_path, previous)
    rank = {mid: index for index, mid in enumerate(previous)}
    entries.sort(key=lambda e: rank[e["id"]])
    digest = hashlib.sha256(
        json.dumps(
            entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    return entries, digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", default="D:/MotionDataLibrary")
    parser.add_argument("--source", default="D:/BONES-SEED")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--exception-triage",
        action="store_true",
        help="审核 needs_segmentation 与 needs_skeleton_review；每页最多 50 条",
    )
    parser.add_argument("--conditioned-contract", help="prepared 条件动作契约目录")
    parser.add_argument("--native-index", help="独立运动批次的 export-index.csv")
    parser.add_argument(
        "--conditioned-audit", help="对应审计目录（report.json、review_queue.json）"
    )
    args = parser.parse_args()
    prepared_mode = bool(args.conditioned_contract or args.conditioned_audit)
    native_mode = bool(args.native_index)
    if native_mode and (prepared_mode or args.exception_triage):
        parser.error("--native-index 不能与 prepared 或异常分流模式共用")
    if prepared_mode and (
        not args.conditioned_contract
        or not args.conditioned_audit
        or args.exception_triage
    ):
        parser.error(
            "prepared 模式须同时指定 --conditioned-contract 与 --conditioned-audit，且不能与 --exception-triage 共用"
        )
    library = Path(args.library).resolve()
    source = Path(args.source).resolve()
    if native_mode:
        from data.tools.review.native_review_store import (
            NativeReviewStore,
            load_native_review_inputs,
        )

        native_index, native_entries, native_digest = load_native_review_inputs(args.native_index, library)
        prepared_entries = exception_entries = exception_digest = None
    elif prepared_mode:
        from data.tools.review.conditioned_review_store import (
            ConditionedReviewStore,
            load_review_inputs,
        )

        contract, report, prepared_entries, queue_digest = load_review_inputs(
            args.conditioned_contract, args.conditioned_audit
        )
        exception_entries, exception_digest = None, None
    else:
        prepared_entries = None
        exception_entries, exception_digest = (
            exception_manifest(library, source)
            if args.exception_triage
            else (None, None)
        )
    paged_entries = native_entries if native_mode else prepared_entries if prepared_mode else exception_entries
    page_count = (
        max(1, math.ceil(len(paged_entries) / MAX_REVIEW_ITEMS))
        if paged_entries is not None
        else 1
    )
    stores = {}
    store_lock = threading.RLock()

    def store_for_unlocked(page):
        nonlocal paged_entries, exception_digest, page_count
        if args.exception_triage:
            paged_entries, exception_digest = exception_manifest(library, source)
            page_count = max(1, math.ceil(len(paged_entries) / MAX_REVIEW_ITEMS))
        if paged_entries is None:
            page = 1
        elif not 1 <= page <= page_count:
            raise ValueError(f"页码必须在 1 到 {page_count} 之间")
        key = (
            (
                page,
                hashlib.sha256(
                    json.dumps(
                        paged_entries[
                            (page - 1) * MAX_REVIEW_ITEMS : page * MAX_REVIEW_ITEMS
                        ],
                        sort_keys=True,
                    ).encode()
                ).hexdigest(),
            )
            if args.exception_triage and paged_entries is not None
            else page
        )
        if key not in stores:
            stores[key] = (
                NativeReviewStore(library, native_index, native_entries, native_digest, page)
                if native_mode else ConditionedReviewStore(
                    library, contract, report, prepared_entries, queue_digest, page
                )
                if prepared_mode
                else ReviewStore(library, source, paged_entries, exception_digest, page)
            )
        return stores[key]

    def store_for(page):
        with store_lock:
            return store_for_unlocked(page)

    class Handler(BaseHTTPRequestHandler):
        def reply(self, data, kind="application/json", code=200, gz=False):
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", kind + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if gz:
                self.send_header("Content-Encoding", "gzip")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            try:
                page = int(parse_qs(parsed.query).get("page", ["1"])[0])
                store = store_for(page)
                if path == "/api/catalog":
                    return self.reply(
                        dict(
                            store.cohort,
                            decisions=store.decisions(),
                            token=store.token,
                            page=page,
                            page_count=page_count,
                            exception_mode=args.exception_triage,
                            prepared_mode=prepared_mode,
                            native_mode=native_mode,
                        )
                    )
                if path.startswith("/api/clip/"):
                    return self.reply(store.clip(path.rsplit("/", 1)[-1]), gz=True)
                if path == "/api/export":
                    return self.reply(
                        {
                            "cohort": store.cohort,
                            "decisions": store.decisions(),
                            "note": "人工记录不会自动发布、入库或删除数据",
                        }
                    )
                files = {
                    "/": ("index.html", "text/html"),
                    "/app.js": ("app.js", "text/javascript"),
                    "/style.css": ("style.css", "text/css"),
                }
                if path in files:
                    file, kind = files[path]
                    return self.reply((STATIC / file).read_bytes(), kind)
                self.reply({"error": "not found"}, code=404)
            except Exception as exc:
                self.reply({"error": str(exc)}, code=400)

        def do_POST(self):
            try:
                parsed = urlparse(self.path)
                page = int(parse_qs(parsed.query).get("page", ["1"])[0])
                store = store_for(page)
                if (
                    parsed.path != "/api/review"
                    or self.headers.get("X-Review-Token") != store.token
                ):
                    return self.reply({"error": "forbidden"}, code=403)
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size < 20000:
                    raise ValueError("请求大小无效")
                store.save(json.loads(self.rfile.read(size)))
                self.reply({"ok": True})
            except Exception as exc:
                self.reply({"error": str(exc)}, code=400)

    first = store_for(1)
    print(
        f"Motion review: http://127.0.0.1:{args.port} | {len(paged_entries) if paged_entries is not None else len(first.ids)} clips | {page_count} pages | {first.cohort_path}",
        flush=True,
    )
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
