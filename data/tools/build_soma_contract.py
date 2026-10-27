"""把 SOMA 训练视图封装成 `evaluate_conditioned_baselines` 可读的条件契约。

`load_window` 硬性要求：contract_id、source_dataset、bone_count、fps、
window.history_frames / future_frames，以及 clips[clip_index] 中的
raw_file / raw_sha256 / source_frames / split / category / contact_bits。

关键设计：

1. **按帧率组分别生成契约**。不同帧率的 history/future 帧数不同，
   混在一份契约里会让窗口越过真实边界，因此 fps120 与 fps30 各一份。
2. `contact_bits` 沿用原流程的做法，从 `world_foot_contacts` 现场派生
   （见方案 §8A.2b），并与 SOMA 自算的三态接触比对。不一致率超阈值时
   仍产出契约，但在 `contact_comparison` 里标记 `exceeds_threshold`，
   由调用方决定是否继续。
3. 同时产出 `windows.jsonl`，供 `select_windows` 按 split 读取。
"""

import base64
import hashlib
import json
from collections import Counter
from pathlib import Path

import numpy as np

from data.runtime.conditioned_motion import CONTRACT_ID
from data.runtime.unreal_dataset import world_foot_contacts

CONTACT_MISMATCH_THRESHOLD = 0.05
FOOT_ROLES = ("left_foot", "left_toe", "right_foot", "right_toe")


class _SkeletonView:
    """`world_foot_contacts` 只用到 names 与左右脚关节索引，这里给最小替身。

    约定与 `SkeletonBase` 一致：left/right_foot_joint_idx 是关节索引列表，
    顺序为 [踝, 脚掌]，对应 world_foot_contacts 返回的 4 路接触列。
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
    import re
    family = re.sub(r"(?:_?\d+)+$", "", str(asset).split(".")[0].lower())
    bucket = int(hashlib.sha256(family.encode()).hexdigest()[:8], 16) % 100
    if bucket < train_ratio:
        return "train"
    if bucket < train_ratio + validation_ratio:
        return "validation"
    return "test"


def derive_contacts(raw, names, roles, fps):
    """从合成 Root 反推世界脚速，得到 4 路布尔接触（左踝/左脚掌/右踝/右脚掌）。"""
    contacts = world_foot_contacts(raw["positions"], raw["root_track"],
                                   _SkeletonView(names, roles), fps)
    if contacts is None:
        raise ValueError("world_foot_contacts 返回 None：root_track 缺失")
    return contacts


def write_skeleton_view(dataset_root, skeleton_path, names, roles):
    """在训练视图里补一个 `skeleton.json`，格式与 UE 训练集一致（bones 为对象列表）。

    评测器用它取 `bones[*].name` 与 `roles` 来定位脚部索引；SOMA 的骨架契约
    用的是 `names/parents/rest_*`，字段名不同，因此这里做一次形状转换。
    """
    skeleton = json.loads(Path(skeleton_path).read_text(encoding="utf-8"))
    parents = skeleton["skeleton"]["parents"]
    rest_positions = skeleton["skeleton"]["rest_positions"]
    rest_rotations = skeleton["skeleton"]["rest_rotations"]

    bones = []
    for index, name in enumerate(names):
        rotation = rest_rotations[index]
        bones.append({"name": name, "parent": int(parents[index]),
                      "position": [float(v) for v in rest_positions[index]],
                      "rotation": [float(v) for v in rotation]})
    view = {"schema_version": 3, "bone_count": len(names),
            "bones": bones, "roles": {k: v for k, v in roles.items() if not k.startswith("__")}}
    (Path(dataset_root) / "skeleton.json").write_text(
        json.dumps(view, ensure_ascii=False), encoding="utf-8")
    return Path(dataset_root) / "skeleton.json"


def build_contract(dataset_root, output, roles, *, skeleton_path=None,
                   threshold=CONTACT_MISMATCH_THRESHOLD):
    dataset_root = Path(dataset_root).resolve()
    output = Path(output).resolve()
    dataset = json.loads((dataset_root / "dataset.json").read_text(encoding="utf-8"))

    names = roles.get("__names__")
    if not names:
        raise ValueError("roles 缺少 __names__；请从骨架契约传入完整关节名列表")
    missing = [key for key in FOOT_ROLES if key not in roles]
    if missing:
        raise ValueError(f"骨架缺少脚部角色：{missing}")

    # 评测器要求 source_dataset 下存在 skeleton.json，这里先补上
    if skeleton_path is not None and not (dataset_root / "skeleton.json").is_file():
        write_skeleton_view(dataset_root, skeleton_path, names, roles)
    skeleton_file = dataset_root / "skeleton.json"
    if not skeleton_file.is_file():
        raise FileNotFoundError(f"缺少 skeleton.json：{skeleton_file}")

    by_group = {}
    contact_stats = {column: {"total": 0, "mismatch": 0} for column in range(4)}

    for item in dataset["clips"]:
        npz = dataset_root / item["file"]
        if not npz.is_file():
            raise FileNotFoundError(f"缺少 NPZ：{npz}")
        with np.load(npz, allow_pickle=False) as archive:
            raw = {key: archive[key] for key in
                   ("positions", "rotations", "timestamps", "root_track")}

        contacts = derive_contacts(raw, names, roles, item["fps"])
        derived = (contacts.numpy() > 0.5)
        if derived.shape != (item["source_frames"], 4):
            raise ValueError(f"接触形状异常 {derived.shape}：{item['asset']}")
        bits = base64.b64encode(
            np.packbits(derived.astype(np.uint8).ravel()).tobytes()).decode("ascii")

        # 与 SOMA 三态接触比对（仅在数据带 somacc 时；pilot 训练视图默认不带）
        soma = item.get("soma_contacts")
        if soma:
            array = np.asarray(soma)
            if array.shape[0] == derived.shape[0]:
                for column in range(4):
                    mask = array[:, column] >= 0
                    if mask.any():
                        contact_stats[column]["total"] += int(mask.sum())
                        contact_stats[column]["mismatch"] += int(
                            (derived[mask, column] != (array[mask, column] > 0)).sum())

        group = item["fps_group"]
        by_group.setdefault(group, {"clips": [], "fps": item["fps"],
                                    "history_frames": item["history_frames"],
                                    "future_frames": item["window_frames"]})
        by_group[group]["clips"].append({
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

    comparison = {}
    worst = 0.0
    for column, value in contact_stats.items():
        if value["total"]:
            rate = value["mismatch"] / value["total"]
            comparison[str(column)] = {"total": value["total"],
                                       "mismatch": value["mismatch"],
                                       "mismatch_rate": round(rate, 6)}
            worst = max(worst, rate)

    output.mkdir(parents=True, exist_ok=True)
    written = []
    for group, info in sorted(by_group.items()):
        directory = output / group
        directory.mkdir(exist_ok=True)
        contract = {
            "schema_version": 1,
            "contract_id": CONTRACT_ID,
            "source_dataset": str(dataset_root),
            "source_manifest_sha256": sha256(dataset_root / "dataset.json"),
            "bone_count": len(names),
            "fps": info["fps"],
            "fps_group": group,
            "skeleton_sha256": sha256(skeleton_file),
            "window": {"history_frames": info["history_frames"],
                       "future_frames": info["future_frames"]},
            "clips": info["clips"],
            "contact_source": "world_foot_contacts_derived_from_derived_root",
            "root_status": "offline_proxy_not_observed_actor_root",
            "contact_comparison": {
                "by_column": comparison,
                "worst_rate": round(worst, 6),
                "threshold": threshold,
                "compared": bool(comparison),
                "exceeds_threshold": comparison and worst > threshold,
            },
            "split_counts": dict(Counter(c["split"] for c in info["clips"])),
        }
        (directory / "contract.json").write_text(
            json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")

        # windows.jsonl：clip_index 必须与 contract["clips"] 顺序一致。
        # 边界条件与 load_window 完全一致：start + history + future <= source_frames，
        # start 从 0 开始（history 取 [start, start+history)，future 紧随其后）。
        window_rows = []
        history = info["history_frames"]
        future = info["future_frames"]
        for clip_index, clip in enumerate(info["clips"]):
            start = 0
            limit = clip["source_frames"] - history - future
            while start <= limit:
                window_rows.append({"clip_index": clip_index, "start": int(start)})
                start += future
        with (directory / "windows.jsonl").open("w", encoding="utf-8") as stream:
            for row in window_rows:
                stream.write(json.dumps(row) + "\n")

        written.append({"group": group, "path": str(directory / "contract.json"),
                        "clips": len(info["clips"]), "windows": len(window_rows),
                        "fps": info["fps"],
                        "history_frames": info["history_frames"],
                        "future_frames": info["future_frames"],
                        "split_counts": dict(Counter(c["split"] for c in info["clips"]))})

    return {"dataset": str(dataset_root), "contracts": written,
            "contact_comparison": {
                "by_column": comparison,
                "worst_rate": round(worst, 6),
                "threshold": threshold,
                "compared": bool(comparison),
                "exceeds_threshold": bool(comparison) and worst > threshold,
            }}


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, help="训练视图目录（含 dataset.json）")
    parser.add_argument("--output", required=True, help="契约输出目录")
    parser.add_argument("--skeleton", default="data/skeletons/soma_uniform_v1.json")
    parser.add_argument("--threshold", type=float, default=CONTACT_MISMATCH_THRESHOLD)
    args = parser.parse_args()

    skeleton = json.loads(Path(args.skeleton).read_text(encoding="utf-8"))
    roles = dict(skeleton["roles"])
    roles["__names__"] = skeleton["skeleton"]["names"]
    result = build_contract(args.dataset, args.output, roles,
                            skeleton_path=args.skeleton, threshold=args.threshold)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
