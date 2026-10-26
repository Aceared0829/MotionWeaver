"""把 SOMA processed 库构建成训练视图（Schema v3），支持双帧率分组。

产物目录结构（与 docs/soma-training-execution-plan.md §5.1 对齐）：

    <output>/
      dataset.json          manifest + 分组统计 + token 预算
      clips/<group>/<id>/    四键 NPZ（positions/rotations/timestamps/root_track）
      rejected.json          被拒绝的条目及原因
      windows/<group>.jsonl   窗口起点索引（按秒定义推导）

设计约束：
1. 只读源数据，不改写任何 processed 内容。
2. 不同帧率写入不同 group 目录，绝不混批。
3. 失败目录不写 dataset.json（沿用 prepare_unreal_dataset 的发布屏障思路）。
"""

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from data.runtime.native_motion_io import read_sampled_motion, skeleton_signature
from data.runtime.soma_train_dataset import (
    BASE_FPS,
    HISTORY_SECONDS,
    SCENE_WINDOW_SECONDS,
    build_clip,
    down_t_for,
    frames_per_token,
    fps_group,
    history_frames,
    token_budget,
    window_frames,
    write_npz,
)

MAX_TOKENS = 16
REFERENCE_TOKENS = 4
SCENES = ("A", "B", "C", "D")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def classify_scene(asset, name="", metadata=None):
    """按动作名与来源元数据分场景。

    四个场景，全部进入训练：

        A 常规移动    walk/run/jog/idle/turn/strafe/aimoffset
        B 大姿态转换  jump/land/crouch/crawl/lying/kneel/getting up
        C 跑酷攀爬    vault/mantle/climb/traversal/slide/roll/wall
        D 开放交互    gesture/dance/object manipulation 等其余全部动作

    D 组不用于动作合成的硬约束，但参与训练以提升泛化：
    实测主库有 27,209 条（约 39%）属于此类，直接丢弃会明显损失覆盖面。
    """
    text = f"{asset} {name}".lower()
    meta = metadata or {}
    kind = (meta.get("content_type_of_movement") or "").lower()
    posture = (meta.get("content_body_position") or "").lower()
    combined = f"{text} {kind} {posture}"

    if any(k in combined for k in ("vault", "mantle", "climb", "traversal", "hurdle",
                                   "ledge", "scramble", "slide", "roll", "wall",
                                   "parkour", "wallrun", "acrobatics", "flipping")):
        return "C"
    if any(k in combined for k in ("jump", "land", "crouch", "crawling", "all fours",
                                   "lying", "kneel", "sitting on floor", "getting up",
                                   "sitting down", "fainting", "falling")):
        return "B"
    if any(k in combined for k in ("walk", "run", "jog", "sprint", "idle", "aimoffset",
                                   "turn", "strafe", "locomotion")):
        return "A"
    # 其余全部归入 D：宁可多训，不静默丢弃
    return "D"


def load_metadata(metadata_path):
    """读取非镜像元数据，键为 BVH 相对路径（与 export-index 的 asset 对齐）。"""
    if metadata_path is None:
        return {}
    import csv
    with Path(metadata_path).open(encoding="utf-8-sig", newline="") as stream:
        return {row["move_soma_uniform_path"].replace("\\", "/"): row
                for row in csv.DictReader(stream) if row["is_mirror"].casefold() == "false"}


def load_index(index_path):
    import csv
    with Path(index_path).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def stratified_sample(buckets, fps_quota):
    """按 (场景, 帧率组) 分层抽样，保证 120fps 与 30fps 都被覆盖。"""
    if fps_quota is None:
        # 全量模式：不做配额截断
        return [row for scene in SCENES for group in ("fps120", "fps30")
                for row in buckets[(scene, group)]]
    picked, seen = [], set()
    for scene in SCENES:
        for group, quota in fps_quota.items():
            pool = [r for r in buckets[(scene, group)] if r["id"] not in seen]
            if not pool:
                continue
            want = min(quota.get(scene, 0), len(pool))
            if want <= 0:
                continue
            step = max(1, len(pool) // want)
            taken = 0
            for row in pool[::step]:
                if taken >= want:
                    break
                if row["id"] in seen:
                    continue
                seen.add(row["id"])
                picked.append(row)
                taken += 1
    return picked


def _worker_convert(payload):
    """多进程 worker：在子进程里重建 roles，避免传闭包。

    payload = (item dict, roles_path, )
    """
    import json as _json
    from pathlib import Path as _Path

    item, roles_path, clips_root_arg = payload
    roles = _json.loads(_Path(roles_path).read_text(encoding="utf-8"))["roles"]
    try:
        from data.runtime.soma_train_dataset import build_clip as _build
        from data.runtime.soma_train_dataset import (
            HISTORY_SECONDS as _HIST,
            SCENE_WINDOW_SECONDS as _WIN,
            token_budget as _budget,
        )
        from data.runtime.soma_train_dataset import write_npz as _write
        record, arrays = _build(item["processed"], None, roles, scene=item["scene"])
        if not record["usable"]:
            return None, record["reject_reason"]
        check = _budget(item["scene"], record["fps"], REFERENCE_TOKENS)
        if check["total"] > MAX_TOKENS:
            return None, f"token 预算超限 {check['total']:.1f} > {MAX_TOKENS}"
        destination = (_Path(clips_root_arg) / record["fps_group"] / item["id"] / "arrays.npz")
        _write(destination, arrays)
        size_sha = hashlib.sha256(destination.read_bytes()).hexdigest()
        return {
            "clip_id": item["id"], "asset": item["asset"], "name": item["name"],
            "file": str(destination.relative_to(_Path(clips_root_arg).parent)).replace("\\", "/"),
            "file_sha256": size_sha,
            "source_frames": record["source_frames"], "fps": record["fps"],
            "fps_group": record["fps_group"], "fps_power": record["fps_power"],
            "scene": item["scene"], "down_t": record["down_t"],
            "frames_per_token": record["frames_per_token"],
            "window_frames": record["window_frames"],
            "history_frames": record["history_frames"],
            "skeleton_signature": record["skeleton_signature"],
            "root_policy": record["root_policy"], "root_status": record["root_status"],
            "window_seconds": _WIN[item["scene"]],
            "history_seconds": _HIST,
        }, None
    except Exception as error:  # noqa: BLE001
        return None, f"{type(error).__name__}: {error}"


def build(index_paths, output, roles, *, limit=None, fps_quota=None, metadata=None,
          dry_run=False, workers=1, roles_path=None, resume=False, full=False):
    output = Path(output).resolve()
    # 全量模式不设配额；小样本模式才用配额
    if not full and fps_quota is None:
        fps_quota = {"fps120": {"A": 25, "B": 15, "C": 10, "D": 15},
                     "fps30": {"A": 12, "B": 10, "C": 8, "D": 5}}
    if full:
        fps_quota = None
    metadata = metadata or {}

    buckets = defaultdict(list)
    skipped = Counter()
    seen_ids = set()
    for index_path in index_paths:
        for row in load_index(index_path):
            if row.get("status") not in (None, "normalized"):
                skipped["status_not_normalized"] += 1
                continue
            meta = metadata.get(row.get("asset", "").replace("\\", "/"))
            scene = classify_scene(row.get("asset", ""), row.get("name", ""), meta)
            try:
                group, _ = fps_group(float(row["fps"]))
            except ValueError:
                skipped["fps_unsupported"] += 1
                continue
            ident = hashlib.sha256(f"{row['asset']}|{row['processed']}".encode()).hexdigest()[:24]
            if ident in seen_ids:
                continue
            seen_ids.add(ident)
            buckets[(scene, group)].append({"id": ident, "asset": row["asset"],
                                            "name": row.get("name", ""),
                                            "processed": row["processed"],
                                            "fps": float(row["fps"]),
                                            "frames": int(row["frames"]),
                                            "scene": scene, "group": group})

    selected = stratified_sample(buckets, fps_quota)
    if limit is not None:
        selected = selected[:limit]
    if not selected:
        raise ValueError("没有可用样本；请检查索引与分类规则")

    if dry_run:
        return {"selected": len(selected),
                "by_scene": dict(Counter(r["scene"] for r in selected)),
                "by_group": dict(Counter(r["group"] for r in selected)),
                "buckets": {f"{s}/{g}": len(v) for (s, g), v in sorted(buckets.items())},
                "skipped": dict(skipped)}

    # 续跑：已有输出目录时必须显式 --resume，并复用 clips.jsonl 里已完成的条目
    finished = {}
    if output.exists() and not resume:
        raise FileExistsError(f"输出目录已存在：{output}（续跑请加 --resume）")
    if output.exists():
        existing = output / "clips.jsonl"
        if existing.is_file():
            for line in existing.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue  # 半截行：视为未完成，重做
                target = output / row["file"]
                if target.is_file():
                    finished[row["asset"]] = row
    output.mkdir(parents=True, exist_ok=True)
    clips_root = output / "clips"
    # 增量日志：逐条追加，进程中断后可从 journal 恢复已完成条目。
    journal_path = output / "clips.jsonl"
    rejected_path = output / "rejected.jsonl"

    rejected, clips, signatures = [], [], Counter()
    # 续跑时先装载已完成条目，只转换剩余部分
    for asset, row in finished.items():
        clips.append(row)
        signatures[row["skeleton_signature"]] += 1
    pending = [item for item in selected if item["asset"] not in finished]
    if finished:
        print(json.dumps({"resume": True, "already_done": len(finished),
                          "pending": len(pending)}), flush=True)

    def record_one(item):
        """转换单条并落盘；返回 (clip_record) 或 (None, reject_reason)。"""
        try:
            record, arrays = build_clip(item["processed"], None, roles, scene=item["scene"])
        except Exception as error:  # noqa: BLE001 - 逐条失败不阻断整批
            return None, f"{type(error).__name__}: {error}"
        if not record["usable"]:
            return None, record["reject_reason"]
        budget = token_budget(item["scene"], record["fps"], REFERENCE_TOKENS)
        if budget["total"] > MAX_TOKENS:
            return None, f"token 预算超限 {budget['total']:.1f} > {MAX_TOKENS}"
        destination = clips_root / record["fps_group"] / item["id"] / "arrays.npz"
        write_npz(destination, arrays)
        return {
            "clip_id": item["id"], "asset": item["asset"], "name": item["name"],
            "file": str(destination.relative_to(output)).replace("\\", "/"),
            "file_sha256": sha256(destination),
            "source_frames": record["source_frames"], "fps": record["fps"],
            "fps_group": record["fps_group"], "fps_power": record["fps_power"],
            "scene": item["scene"], "down_t": record["down_t"],
            "frames_per_token": record["frames_per_token"],
            "window_frames": record["window_frames"],
            "history_frames": record["history_frames"],
            "skeleton_signature": record["skeleton_signature"],
            "root_policy": record["root_policy"], "root_status": record["root_status"],
            "window_seconds": SCENE_WINDOW_SECONDS[item["scene"]],
            "history_seconds": HISTORY_SECONDS,
        }, None

    if workers > 1:
        # 多进程：ProcessPoolExecutor 里不能传闭包，改用模块级函数
        from concurrent.futures import ProcessPoolExecutor, as_completed
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_worker_convert, (item, roles_path, clips_root)): item for item in pending}
            done = 0
            for future in as_completed(futures):
                item = futures[future]
                done += 1
                clip, reason = future.result()
                if clip is None:
                    rejected.append({"source_id": item["asset"], "reason": reason})
                    with rejected_path.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(rejected[-1], ensure_ascii=False) + "\n")
                else:
                    clips.append(clip)
                    signatures[clip["skeleton_signature"]] += 1
                    with journal_path.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(clip, ensure_ascii=False) + "\n")
                if done % 200 == 0 or done == len(pending):
                    print(json.dumps({"converted": done, "selected": len(selected),
                                      "ok": len(clips), "rejected": len(rejected)}), flush=True)
    else:
        for index, item in enumerate(pending, 1):
            clip, reason = record_one(item)
            if clip is None:
                rejected.append({"source_id": item["asset"], "reason": reason})
                with rejected_path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(rejected[-1], ensure_ascii=False) + "\n")
            else:
                clips.append(clip)
                signatures[clip["skeleton_signature"]] += 1
                with journal_path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(clip, ensure_ascii=False) + "\n")
            if index % 200 == 0 or index == len(pending):
                print(json.dumps({"converted": index, "selected": len(selected),
                                  "ok": len(clips), "rejected": len(rejected)}), flush=True)

    if not clips:
        raise ValueError("所有样本都被拒绝，未写入 dataset.json")

    windows_dir = output / "windows"
    windows_dir.mkdir(exist_ok=True)
    window_counts = {}
    for group in sorted({c["fps_group"] for c in clips}):
        group_clips = [c for c in clips if c["fps_group"] == group]
        rows = []
        for clip in group_clips:
            start = clip["history_frames"]
            total = clip["source_frames"]
            while start + clip["window_frames"] <= total:
                rows.append({"clip_id": clip["clip_id"], "start": start,
                             "window_frames": clip["window_frames"], "scene": clip["scene"]})
                start += clip["window_frames"]
        with (windows_dir / f"{group}.jsonl").open("w", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        window_counts[group] = {"clips": len(group_clips), "windows": len(rows)}

    # 发布屏障：dataset.json 最后写
    dataset = {
        "schema_version": 3,
        "skeleton": {"id": "soma_uniform_v1", "joints": 78,
                     "signatures": {sig: n for sig, n in signatures.items()}},
        "base_fps": BASE_FPS,
        "fps_groups": sorted({c["fps_group"] for c in clips}),
        "scenes": {scene: {"window_seconds": SCENE_WINDOW_SECONDS[scene],
                           "clips": sum(1 for c in clips if c["scene"] == scene)}
                   for scene in SCENES},
        "scene_semantics": {
            "A": "常规移动",
            "B": "大姿态转换",
            "C": "跑酷攀爬",
            "D": "开放交互（手势/舞蹈/物体操作等），参与训练提升泛化，不用于硬约束",
        },
        "clips": clips,
        "window_index": window_counts,
        "rejected": len(rejected),
        "training_contract": "pose_only_root_authoritative",
        "root_status": "offline_proxy_not_observed_actor_root",
        "training_ready": False,
        "limits": {"max_tokens": MAX_TOKENS, "reference_tokens": REFERENCE_TOKENS},
    }
    (output / "rejected.json").write_text(
        json.dumps(rejected, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "dataset.json").write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"output": str(output), "clips": len(clips), "rejected": len(rejected),
            "by_scene": dict(Counter(c["scene"] for c in clips)),
            "by_group": dict(Counter(c["fps_group"] for c in clips)),
            "windows": window_counts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", action="append", required=True,
                        help="export-index.csv，可重复；多源按顺序合并且去重")
    parser.add_argument("--output", required=True, help="必须尚不存在的训练视图目录")
    parser.add_argument("--skeleton", default="data/skeletons/soma_uniform_v1.json")
    parser.add_argument("--limit", type=int, help="只取前 N 条，用于小样本验证")
    parser.add_argument("--metadata", help="seed_metadata_v004.csv，用于按来源语义分类")
    parser.add_argument("--dry-run", action="store_true", help="只统计不落盘")
    parser.add_argument("--workers", type=int, default=1, help="并行进程数")
    parser.add_argument("--resume", action="store_true", help="复用已有输出目录，跳过已完成条目")
    parser.add_argument("--full", action="store_true", help="全量构建，不设抽样配额")
    args = parser.parse_args()
    roles = json.loads(Path(args.skeleton).read_text(encoding="utf-8"))["roles"]
    metadata = load_metadata(args.metadata)
    result = build([Path(p) for p in args.index], args.output, roles,
                   limit=args.limit, metadata=metadata, dry_run=args.dry_run,
                   workers=args.workers, roles_path=str(Path(args.skeleton).resolve()),
                   resume=args.resume, full=args.full)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()




