"""为既有审核页面适配独立运动数据；数据只读，人工结论独立保存。"""

import csv
import gzip
import hashlib
import json
import secrets
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from data.runtime.motion_kinematics import world_positions
from data.runtime.native_motion_io import (
    read_native_motion,
    read_sampled_motion,
    skeleton_signature,
)

PAGE_SIZE = 50


def load_native_review_inputs(index_path, data_root):
    index_path, data_root = Path(index_path).resolve(), Path(data_root).resolve()
    if not index_path.is_relative_to(data_root / "reports"):
        raise ValueError("审核索引必须位于数据根目录的 reports 中")
    summary = json.loads((index_path.parent / "acceptance.json").read_text(encoding="utf-8"))
    if summary["raw_and_processed_readback_errors"] or summary["source_endpoint_gaps"] or summary.get("recomposition_errors"):
        raise ValueError("该批次的回读审计未通过")
    records = {}
    for batch in summary["batches"]:
        path = (index_path.parent.parent / batch / "run.json").resolve()
        if not path.is_relative_to(data_root / "reports"):
            raise ValueError("批次报告路径越界")
        report = json.loads(path.read_text(encoding="utf-8"))
        records.update({r["source_id"]: r for r in report["results"] if r["status"] == "normalized"})
    payload = index_path.read_bytes()
    with index_path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows or len(rows) != summary["status_counts"]["normalized"]:
        raise ValueError("索引数量与验收汇总不一致")
    entries = []
    for row in rows:
        record = records[row["asset"]]
        for partition in ("raw", "processed"):
            # 全库名单先做路径匹配；实际文件的链接边界在按需加载时检查。
            path = Path(row[partition]).absolute()
            if not path.is_relative_to(data_root / partition) or path != Path(record[partition]).absolute():
                raise ValueError("索引路径与已审计输出不一致")
        entries.append({"id": hashlib.sha256(row["asset"].encode()).hexdigest()[:24],
                        "name": row["name"], "asset": row["asset"], "group": "sample",
                        "flags": [], "integrity_errors": [], "frames": int(row["frames"]),
                        "joints": int(row["joints"]), "fps": float(row["fps"]),
                        "reason": f"{row['joints']} 骨骼 · {row['frames']} 帧 · {float(row['duration']):.2f} 秒 · 视觉待审核",
                        "raw": row["raw"], "processed": row["processed"],
                        "raw_sha256": record["raw_sha256"], "processed_sha256": record["processed_sha256"],
                        "source_sha256": record["processed_sha256"]["arrays.bin"]})
        if "retarget" in record:
            entries[-1].update(retarget=record["retarget"], source_processed=record["source_processed"],
                               source_processed_sha256=record["source_processed_sha256"],
                               source_sha256=record["source_processed_sha256"]["arrays.bin"],
                               output_sha256=record["processed_sha256"]["arrays.bin"], flags=record["flags"])
        if "observation" in record:
            sidecar = Path(record["observation"]).resolve()
            if not sidecar.is_relative_to(index_path.parent):
                raise ValueError("观察标注必须位于本批次报告中")
            entries[-1].update(observation=str(sidecar), observation_sha256=record["observation_sha256"])
        if "processing_flags" in record:
            entries[-1].update(processing_mode=True, flags=record["processing_flags"],
                               group="issue" if record["processing_flags"] else "sample")
    if len({entry["id"] for entry in entries}) != len(entries):
        raise ValueError("审核索引包含重复动画")
    # 先展示行走动作，单帧姿态放在后面；显示顺序同样绑定到审核版本。
    entries.sort(key=lambda e: ("/Walk/" not in e["asset"], e["frames"] <= 1, e["name"].casefold()))
    digest = hashlib.sha256(payload + json.dumps(entries, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return index_path, entries, digest


class NativeReviewStore:
    def __init__(self, data_root, index_path, entries, queue_digest, page=1):
        self.data_root = Path(data_root).resolve()
        self.review = Path(data_root) / "reviews" / index_path.parent.parent.name / index_path.parent.name
        self.review.mkdir(parents=True, exist_ok=True)
        selected = entries[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
        self.cohort = {"mode": "native_motion_review", "queue_sha256": queue_digest,
                       "page": page, "page_size": PAGE_SIZE, "total": len(entries), "checked": len(entries),
                       "entries": selected, "review_directory": str(self.review)}
        if entries and entries[0].get("processing_mode"):
            self.cohort["processing_mode"] = True
        if entries and "retarget" in entries[0]:
            self.cohort["retarget_mode"] = True
        self.cohort_path = self.review / f"native_{queue_digest[:16]}_p{page:02}.json"
        if self.cohort_path.exists():
            if json.loads(self.cohort_path.read_text(encoding="utf-8")) != self.cohort:
                raise ValueError("历史审核名单与本次输入不一致")
        else:
            self.cohort_path.write_text(json.dumps(self.cohort, ensure_ascii=False, indent=2), encoding="utf-8")
        self.entries = {entry["id"]: entry for entry in selected}
        self.ids = set(self.entries)
        self.token = secrets.token_urlsafe(32)
        with closing(sqlite3.connect(self.review / "decisions.sqlite3")) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS decisions(cohort TEXT,id TEXT,status TEXT,note TEXT,updated TEXT,PRIMARY KEY(cohort,id))")

    def decisions(self):
        with closing(sqlite3.connect(self.review / "decisions.sqlite3")) as db:
            return {mid: {"status": status, "note": note, "updated": updated}
                    for mid, status, note, updated in db.execute(
                        "SELECT id,status,note,updated FROM decisions WHERE cohort=?", (self.cohort_path.name,))}

    def save(self, payload):
        mid, status, note = payload.get("id"), payload.get("status"), payload.get("note", "")
        if (mid not in self.ids or status not in {"approved", "rejected", "unsure", "unreviewed"}
                or not isinstance(note, str) or len(note) > 4000):
            raise ValueError("无效审核记录")
        with closing(sqlite3.connect(self.review / "decisions.sqlite3")) as db, db:
            db.execute("INSERT OR REPLACE INTO decisions VALUES(?,?,?,?,?)",
                       (self.cohort_path.name, mid, status, note, datetime.now(UTC).isoformat()))

    def clip(self, mid):
        if mid not in self.entries:
            raise ValueError("动画不在本页审核名单")
        entry = self.entries[mid]
        for partition in ("raw", "processed"):
            folder = Path(entry[partition]).resolve()
            if not folder.is_relative_to(self.data_root / partition):
                raise ValueError("动画文件链接越过数据分区")
            for name, expected in entry[partition + "_sha256"].items():
                if hashlib.sha256((folder / name).read_bytes()).hexdigest() != expected:
                    raise ValueError("动画内容已改变，请重新审计并创建审核名单")
        native = read_native_motion(entry["raw"])
        motion = read_sampled_motion(entry["processed"])
        if (native.source.source_id != entry["asset"] or ("retarget" not in entry and native.skeleton.names != motion.skeleton.names)
                or motion.source.source_id != entry["asset"] or len(motion.timestamps) != entry["frames"]
                or len(motion.skeleton.names) != entry["joints"] or motion.fps != entry["fps"]):
            raise ValueError("动画元信息与审核索引不一致")
        world = world_positions(motion)
        # Motion 米制 Y-up → 现有查看器的 UE 轴、厘米；仅为显示，不改动处理数据。
        ue = np.stack((world[..., 2], -world[..., 0], world[..., 1]), axis=-1) * 100
        pelvis = next((i for i, name in enumerate(motion.skeleton.names) if name.casefold() in {"pelvis", "hips"}), 0)
        rig = {"names": motion.skeleton.names, "parents": motion.skeleton.parents.tolist(), "pelvis": pelvis,
               "positions": np.round(ue, 3).reshape(-1).tolist()}
        if motion.root_joint is not None:
            rig["root_positions"] = np.round(ue[:, motion.root_joint], 3).reshape(-1).tolist()
        result = {"id": mid, "name": entry["name"], "fps": motion.fps, "frames": len(motion.timestamps),
                  "package": "独立运动数据", "description": entry["reason"],
                  "source": rig, "target": None, "target_root": None,
                  "source_sha256": entry["source_sha256"], "output_sha256": None,
                  "review_note": "保留来源骨架，尚未执行跨骨架映射；当前为骨骼线框审核。"}
        if "retarget" in entry:
            source_folder = Path(entry["source_processed"]).resolve()
            if not source_folder.is_relative_to(self.data_root / "processed"):
                raise ValueError("重定向来源链接越过数据分区")
            for name, expected in entry["source_processed_sha256"].items():
                if hashlib.sha256((source_folder / name).read_bytes()).hexdigest() != expected:
                    raise ValueError("重定向来源运动已改变")
            source_motion = read_sampled_motion(source_folder)
            np.testing.assert_array_equal(source_motion.timestamps, motion.timestamps)
            if (source_motion.source.source_id != entry["asset"]
                    or skeleton_signature(source_motion.skeleton) != entry["retarget"]["source_signature"]
                    or skeleton_signature(motion.skeleton) != entry["retarget"]["target_signature"]
                    or motion.source.details["retarget"] != entry["retarget"]):
                raise ValueError("重定向源、目标与映射身份不一致")
            source_world = world_positions(source_motion)
            alignment = np.asarray(entry["retarget"]["alignment"])
            scale = entry["retarget"]["translation_scale"]
            source_pelvis = source_motion.skeleton.names.index("pelvis")
            aligned = source_world @ alignment.T * scale
            # 仅供并排比较：同一个常量偏移对齐来源与目标的参考空间。
            aligned += (world[0, pelvis] - aligned[0, source_pelvis])[None, None]
            display = aligned[..., [2, 0, 1]] * [100, -100, 100]
            source_rig = {"names": source_motion.skeleton.names, "parents": source_motion.skeleton.parents.tolist(),
                          "pelvis": source_pelvis, "positions": np.round(display, 3).reshape(-1).tolist()}
            if source_motion.root_joint is not None:
                source_rig["root_positions"] = np.round(display[:, source_motion.root_joint], 3).reshape(-1).tolist()
            result.update(source=source_rig, target=rig, output_sha256=entry["output_sha256"],
                          package="UE → SOMA uniform", review_note="左侧来源仅在显示时校正朝向与比例，右侧为固定 SOMA 骨架。未验收蒙皮或真实接触。")
        if "observation" in entry:
            sidecar = Path(entry["observation"]).resolve()
            if not sidecar.is_relative_to(self.data_root / "reports") or hashlib.sha256(sidecar.read_bytes()).hexdigest() != entry["observation_sha256"]:
                raise ValueError("观察标注内容已改变，请重新建立审核版本")
            observation = json.loads(sidecar.read_text(encoding="utf-8"))
            if (observation["schema"] != "motionweaver_observation" or observation["version"] != 1
                    or observation["source_id"] != entry["asset"] or observation["frames"] != entry["frames"]
                    or observation["fps"] != entry["fps"]):
                raise ValueError("观察标注与动作时间身份不一致")
            result["observation"] = observation
            result["review_note"] = "SOMA 原始帧序列观察；尚未决定 Root 分解与训练时间规则。"
            if "processing" in observation:
                processing = observation["processing"]
                path = Path(processing["arrays"]).resolve()
                if not path.is_relative_to(self.data_root / "processed") or hashlib.sha256(path.read_bytes()).hexdigest() != processing["arrays_sha256"]:
                    raise ValueError("处理候选数组已改变或路径越界")
                with np.load(path, allow_pickle=False) as arrays:
                    np.testing.assert_array_equal(arrays["timestamps"], motion.timestamps)
                    proxy = arrays["root_proxy"][:, :3]
                    result["processing"] = {
                        "root_positions": np.round(proxy[:, [2, 0, 1]] * [100, -100, 100], 3).reshape(-1).tolist(),
                        "contact_states": arrays["contact_states"].tolist(),
                        "stable_mask": arrays["limb_stable_mask"].tolist(),
                        "heading_valid": arrays["heading_valid"].tolist(),
                    }
                result["review_note"] = "来源运动保留；显示可逆的离线 Root 代理与自动接触候选，均非场景真值。"
        return gzip.compress(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode(), compresslevel=3)
