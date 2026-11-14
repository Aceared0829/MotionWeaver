"""既有 Motion Review 的 prepared 数据适配器；沿用同一 UI 与结论数据库。"""

import gzip
import hashlib
import json
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np
from data.runtime.unreal_dataset import (
    apply_authoritative_root,
    contained_file,
    read_json,
)

from data.runtime.conditioned_motion import CONTRACT_ID, load_raw

PAGE_SIZE = 50


def load_review_inputs(contract_folder, audit_folder):
    contract_folder, audit_folder = Path(contract_folder).resolve(), Path(audit_folder).resolve()
    contract_path = contract_folder / "contract.json"
    contract = read_json(contract_path)
    report = read_json(audit_folder / "report.json")
    queue_path = audit_folder / "review_queue.json"
    queue = read_json(queue_path)
    if contract.get("contract_id") != CONTRACT_ID:
        raise ValueError("不是当前条件动作数据契约")
    if report.get("contract_sha256") != hashlib.sha256(contract_path.read_bytes()).hexdigest():
        raise ValueError("审计报告与数据契约不匹配")
    if report.get("review_queue_clips") != len(queue):
        raise ValueError("审计复核队列数量不匹配")
    entries = []
    for row in queue:
        index = row["clip_index"]
        if not isinstance(index, int) or not 0 <= index < len(contract["clips"]):
            raise ValueError("复核清单索引无效")
        clip = contract["clips"][index]
        if row["asset"] != clip["asset"] or row["frames"] != clip["source_frames"]:
            raise ValueError("复核清单与来源动作错位")
        reason = (f"{row['category']} · {row['split']} · 窗口 Root 均速 "
                  f"{row['diagnostic_root_speed_mean_mps']:.2f} m/s · 连续接触帧对 "
                  f"{row['diagnostic_center_contact_pairs']} · 本地高度代理额外标记 "
                  f"{row['diagnostic_local_floor_proxy_only_fraction']:.1%}"
                  if "diagnostic_center_contact_pairs" in row else
                  f"{row['category']} · {row['split']} · Root 峰值 {row.get('root_speed_max_mps', 0):.2f} m/s "
                  f"· 高度跨度 {row.get('root_height_range_m', 0):.2f} m")
        entries.append({"id": str(index), "name": row["asset"].split("/")[-1].split(".")[0],
                        "asset": row["asset"], "group": "issue" if row["flags"] else "sample",
                        "flags": row["flags"], "integrity_errors": [], "frames": row["frames"],
                        "reason": reason})
    if len({entry["id"] for entry in entries}) != len(entries):
        raise ValueError("复核清单包含重复动作")
    return contract, report, entries, hashlib.sha256(queue_path.read_bytes()).hexdigest()


class ConditionedReviewStore:
    def __init__(self, library, contract, report, entries, queue_sha256, page=1):
        self.library = Path(library).resolve()
        self.review = self.library / "reviews"
        self.review.mkdir(parents=True, exist_ok=True)
        self.contract = contract
        self.entries = entries
        self.page = page
        self.cohort_path = self.review / f"conditioned_{queue_sha256[:16]}_p{page:02}.json"
        selected = entries[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
        cohort = {"mode": "conditioned_prepared_review", "queue_sha256": queue_sha256,
                  "contract_sha256": report["contract_sha256"], "page": page,
                  "page_size": PAGE_SIZE, "total": len(entries), "checked": report["checked_clips"],
                  "entries": selected}
        if self.cohort_path.exists():
            if read_json(self.cohort_path) != cohort:
                raise ValueError("历史复核名单与当前审计输入不一致")
        else:
            self.cohort_path.write_text(json.dumps(cohort, ensure_ascii=False, indent=2), encoding="utf-8")
        self.cohort = cohort
        self.ids = {entry["id"] for entry in selected}
        self.token = secrets.token_urlsafe(32)
        with closing(sqlite3.connect(self.review / "decisions.sqlite3")) as db:
            with db:
                db.execute("CREATE TABLE IF NOT EXISTS decisions(cohort TEXT,id TEXT,status TEXT,note TEXT,updated TEXT,PRIMARY KEY(cohort,id))")

    def decisions(self):
        with closing(sqlite3.connect(self.review / "decisions.sqlite3")) as db:
            return {mid: {"status": status, "note": note, "updated": updated}
                    for mid, status, note, updated in db.execute(
                        "SELECT id,status,note,updated FROM decisions WHERE cohort=?", (self.cohort_path.name,))}

    def save(self, payload):
        mid, status, note = payload.get("id"), payload.get("status"), payload.get("note", "")
        if mid not in self.ids or status not in {"approved", "rejected", "unsure", "unreviewed"} or not isinstance(note, str) or len(note) > 4000:
            raise ValueError("无效验收记录")
        with closing(sqlite3.connect(self.review / "decisions.sqlite3")) as db:
            with db:
                db.execute("INSERT OR REPLACE INTO decisions VALUES(?,?,?,?,?)",
                           (self.cohort_path.name, mid, status, note, datetime.now(timezone.utc).isoformat()))

    @lru_cache(maxsize=2)
    def clip(self, mid):
        if mid not in self.ids:
            raise ValueError("动作不在本页验收名单")
        index = int(mid)
        item = self.contract["clips"][index]
        source = Path(self.contract["source_dataset"])
        raw_path = contained_file(source, item["raw_file"])
        raw, digest = load_raw(raw_path, item["source_frames"],
                               self.contract["bone_count"], self.contract["fps"],
                               expected_sha256=item["raw_sha256"])
        world = apply_authoritative_root(raw["positions"], raw["root_track"])
        # Motion (X 左、Y 上、Z 前，米) → UE (X 前、Y 右、Z 上，厘米)。
        ue_world = np.stack((world[..., 2], -world[..., 0], world[..., 1]), axis=-1) * 100
        motion_root = raw["root_track"][:, :3]
        ue_root = np.stack((motion_root[:, 2], -motion_root[:, 0], motion_root[:, 1]), axis=-1) * 100
        skeleton_path = source / "skeleton.json"
        if hashlib.sha256(skeleton_path.read_bytes()).hexdigest() != self.contract["skeleton_sha256"]:
            raise ValueError("来源骨架已改变，拒绝沿用旧契约")
        skeleton = read_json(skeleton_path)
        bones = skeleton["bones"]
        source_rig = {"names": [bone["name"] for bone in bones],
                      "parents": [bone["parent"] for bone in bones], "pelvis": 0,
                      "positions": np.round(ue_world, 3).reshape(-1).tolist(),
                      "root_positions": np.round(ue_root, 3).reshape(-1).tolist()}
        result = {"id": mid, "name": item["asset"], "fps": self.contract["fps"],
                  "frames": item["source_frames"], "package": item["category"],
                  "description": f"{item['split']} · {item['action']}",
                  "source": source_rig, "target": None, "target_root": None,
                  "source_sha256": digest, "output_sha256": None,
                  "review_note": "UE 导出动作的 Root 合成骨架；非蒙皮/碰撞验收。"}
        return gzip.compress(json.dumps(result, separators=(",", ":")).encode(), compresslevel=3)
