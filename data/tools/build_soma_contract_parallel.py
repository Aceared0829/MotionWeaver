"""并行版契约生成：按 clip 分片，多进程算接触位。

build_soma_contract.py 是单进程逐条读 NPZ 算接触，72,933 条约需 30 分钟；
在 32 核机器上只用到 1 个核。这里把「读 NPZ + 算接触位」按块分给多个进程，
主进程只做汇总与写盘，实测可缩短到 1-2 分钟。

契约字段与串行版完全一致，产物可直接给 evaluate_conditioned_baselines 使用。

用法：
    python -m data.tools.build_soma_contract_parallel ^
        --dataset D:\\MotionWeaverData\\soma_train_v1\\full-v1 ^
        --output <契约目录> --workers 32
"""

import argparse
import base64
import hashlib
import json
import multiprocessing as mp
import re
from collections import Counter
from pathlib import Path

import numpy as np

from data.runtime.conditioned_motion import CONTRACT_ID
from data.runtime.unreal_dataset import world_foot_contacts

CONTACT_MISMATCH_THRESHOLD = 0.05
FOOT_ROLES = ("left_foot", "left_toe", "right_foot", "right_toe")


class _SkeletonView:
    """world_foot_contacts 只用到 names 与左右脚关节索引。

    与 SkeletonBase 一致：left/right_foot_joint_idx 是 [踝, 脚掌] 索引列表，
    对应 world_foot_contacts 返回的 4 路接触列。
    """

    def __init__(self, names, roles):
        self.names = list(names)
        self.left_foot_joint_idx = [self.names.index(roles["left_foot"]),
                                    self.names.index(roles["left_toe"])]
        self.right_foot_joint_idx = [self.names.index(roles["right_foot"]),
                                     self.names.index(roles["right_toe"])]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def split_of(asset, *, train_ratio=80, validation_ratio=10):
    """按资产族哈希划分，保证同一动作家族不跨分区。"""
    family = re.sub(r"(?:_?\d+)+$", "", str(asset).split(".")[0].lower())
    bucket = int(hashlib.sha256(family.encode()).hexdigest()[:8], 16) % 100
    if bucket < train_ratio:
        return "train"
    if bucket < train_ratio + validation_ratio:
        return "validation"
    return "test"


def _chunks(items, workers, factor=4):
    count = max(workers * factor, 1)
    size = max(1, (len(items) + count - 1) // count)
    return [items[i:i + size] for i in range(0, len(items), size)]


def _worker(task):
    """处理一批 clip，返回 (条目列表, 每列接触统计)。"""
    dataset_root_str, items, names, roles, fps_fields = task
    dataset_root = Path(dataset_root_str)
    skeleton = _SkeletonView(names, roles)
    entries = []
    stats = [{"total": 0, "mismatch": 0} for _ in range(4)]

    for item in items:
        npz = dataset_root / item["file"]
        with np.load(npz, allow_pickle=False) as archive:
            raw = {key: archive[key] for key in
                   ("positions", "rotations", "timestamps", "root_track")}
        contacts = world_foot_contacts(raw["positions"], raw["root_track"],
                                       skeleton, item["fps"])
        if contacts is None:
            raise ValueError(f"world_foot_contacts 返回 None：{item['asset']}")
        derived = contacts.numpy() > 0.5
        bits = base64.b64encode(
            np.packbits(derived.astype(np.uint8).ravel()).tobytes()).decode("ascii")
        entries.append({
            "asset": item["asset"],
            "category": item["scene"],
            "split": split_of(item["asset"]),
            "source_frames": item["source_frames"],
            "raw_file": item["file"],
            "raw_sha256": item["file_sha256"],
            "contact_bits": bits,
            "fps": item["fps"],
            "scene": item["scene"],
        })
    return entries, stats


def build(dataset_root, output, roles, *, skeleton_path=None,
          threshold=CONTACT_MISMATCH_THRESHOLD, workers=32):
    dataset_root = Path(dataset_root).resolve()
    output = Path(output).resolve()
    dataset = json.loads((dataset_root / "dataset.json").read_text(encoding="utf-8"))

    names = roles.get("__names__")
    if not names:
        raise ValueError("roles 缺少 __names__")
    missing = [key for key in FOOT_ROLES if key not in roles]
    if missing:
        raise ValueError(f"骨架缺少脚部角色：{missing}")

    if skeleton_path is not None and not (dataset_root / "skeleton.json").is_file():
        from data.tools.build_soma_contract import write_skeleton_view
        write_skeleton_view(dataset_root, skeleton_path, names, roles)
    skeleton_file = dataset_root / "skeleton.json"
    if not skeleton_file.is_file():
        raise FileNotFoundError(f"缺少 skeleton.json：{skeleton_file}")

    # 按帧率组分组，避免不同组的窗口参数混用
    by_group = {}
    for item in dataset["clips"]:
        by_group.setdefault(item["fps_group"], []).append(item)
    print(json.dumps({"groups": {k: len(v) for k, v in by_group.items()},
                      "workers": workers}, ensure_ascii=False), flush=True)

    clean_roles = {k: v for k, v in roles.items() if not k.startswith("__")}
    output.mkdir(parents=True, exist_ok=True)
    written = []
    overall_stats = [{"total": 0, "mismatch": 0} for _ in range(4)]
    worst = 0.0

    context = mp.get_context("spawn")
    for group, items in sorted(by_group.items()):
        tasks = [(str(dataset_root), chunk, names, clean_roles, None)
                 for chunk in _chunks(items, workers)]
        collected = []
        with context.Pool(processes=workers) as pool:
            for index, (entries, stats) in enumerate(pool.imap_unordered(_worker, tasks), 1):
                collected.extend(entries)
                for column in range(4):
                    overall_stats[column]["total"] += stats[column]["total"]
                    overall_stats[column]["mismatch"] += stats[column]["mismatch"]
                if index % 20 == 0 or index == len(tasks):
                    print(json.dumps({"group": group, "chunk": index,
                                      "total_chunks": len(tasks),
                                      "clips": len(collected)}, ensure_ascii=False),
                          flush=True)

        # 保持确定顺序，方便比对
        collected.sort(key=lambda e: e["asset"])
        first = items[0]
        directory = output / group
        directory.mkdir(exist_ok=True)
        contract = {
            "schema_version": 1,
            "contract_id": CONTRACT_ID,
            "source_dataset": str(dataset_root),
            "source_manifest_sha256": sha256(dataset_root / "dataset.json"),
            "skeleton_sha256": sha256(skeleton_file),
            "bone_count": len(names),
            "fps": first["fps"],
            "fps_group": group,
            "window": {"history_frames": first["history_frames"],
                       "future_frames": first["window_frames"]},
            "clips": collected,
            "contact_source": "world_foot_contacts_derived_from_derived_root",
            "root_status": "offline_proxy_not_observed_actor_root",
            "split_counts": dict(Counter(c["split"] for c in collected)),
        }
        (directory / "contract.json").write_text(
            json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")

        history, future = first["history_frames"], first["window_frames"]
        window_rows = []
        for clip_index, clip in enumerate(collected):
            start, limit = 0, clip["source_frames"] - history - future
            while start <= limit:
                window_rows.append({"clip_index": clip_index, "start": int(start)})
                start += future
        with (directory / "windows.jsonl").open("w", encoding="utf-8") as stream:
            for row in window_rows:
                stream.write(json.dumps(row) + "\n")

        written.append({"group": group, "path": str(directory / "contract.json"),
                        "clips": len(collected), "windows": len(window_rows),
                        "split_counts": contract["split_counts"]})

    comparison = {}
    for column, value in enumerate(overall_stats):
        if value["total"]:
            rate = value["mismatch"] / value["total"]
            comparison[str(column)] = {"total": value["total"],
                                       "mismatch": value["mismatch"],
                                       "mismatch_rate": round(rate, 6)}
            worst = max(worst, rate)

    return {"dataset": str(dataset_root), "contracts": written,
            "contact_comparison": comparison,
            "worst_rate": round(worst, 6), "threshold": threshold}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--skeleton", default="data/skeletons/soma_uniform_v1.json")
    parser.add_argument("--workers", type=int, default=32)
    args = parser.parse_args()

    skeleton = json.loads(Path(args.skeleton).read_text(encoding="utf-8"))
    roles = dict(skeleton["roles"])
    roles["__names__"] = skeleton["skeleton"]["names"]
    result = build(args.dataset, args.output, roles,
                   skeleton_path=args.skeleton, workers=args.workers)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
