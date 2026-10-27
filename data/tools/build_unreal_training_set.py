"""把 soma_train_v1 的四键 NPZ 训练视图转成 UnrealMotionDataset 可读的训练格式。

## 为什么要这个工具

训练脚本 `training/pretrain/train_unreal.py` 通过 `UnrealMotionDataset` 读取数据，
要求：
  - dataset.json 含 schema_version / fps / feature_dim / training_signature
  - stats/mean.npy、stats/std.npy（训练集帧的均值方差）
  - 每条 clip 一个二维张量 `.npy`，形状 [frames, feature_dim]，feature_dim = 12*bones+6
  - skeleton.json 含 fps 与 bones

而训练视图（soma_train_v1/full-v1）用的是评测侧的四键 NPZ：
positions [F,J,3]、rotations [F,J,3,3]、timestamps [F]、root_track [F,7]。

## 做法：复用官方实现，不手写特征公式

特征必须与历史训练完全一致，否则训出来的模型学到的是错的东西。
因此这里直接调用 `DualRootGlobalJoints`（与 `prepare_unreal_dataset.py:221`
同一构造），喂进 positions/rotations/contacts，让它自己算 942 维向量；
再按 `indices["global_rep"]` 取子集，并用训练集帧的统计数据归一化。
整个流程与 `prepare_unreal_dataset.py` 的 233-245、333-346 行一一对应。
"""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import torch

from data.runtime.unreal_dataset import (
    UnrealSkeleton,
    training_signature,
    world_foot_contacts,
)
from motionweaver.representation.core.motion_reps.dual_root_global_joints import (
    DualRootGlobalJoints,
)

MIN_TRAINING_FRAMES = 65


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, payload):
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_skeleton_file(train_root, views_root):
    """把评测侧的 skeleton.json（bones 为对象列表）落成 UnrealSkeleton 可读的形状。

    评测侧格式：{"bones": [{"name","parent","position","rotation"}], "roles": {...}}
    UnrealSkeleton 期望同名文件，但需要 fps 字段；bones 列表结构一致。
    """
    source = read_json(Path(views_root) / "skeleton.json")
    bones = source["bones"]
    fps = source.get("fps")
    if fps is None:
        # 评测侧 skeleton 不带 fps；从 dataset.json 补，避免自己编造。
        manifest = read_json(Path(views_root) / "dataset.json")
        fps = manifest["clips"][0]["fps"]
    payload = {
        "schema_version": 3,
        "fps": float(fps),
        "bones": [
            {
                "name": bone["name"],
                "parent": int(bone["parent"]),
                "position": [float(v) for v in bone["position"]],
                "rotation": [float(v) for v in bone["rotation"]],
            }
            for bone in bones
        ],
        # UnrealSkeleton 需要中性姿态的关节位置来做 retarget/可视化基准。
        # 评测侧 skeleton.json 的 bones[*].position 就是骨架 rest 位置，直接取用，
        # 不另外拟合或编造。
        "neutral_joints": [[float(v) for v in bone["position"]] for bone in bones],
        "roles": {k: v for k, v in source.get("roles", {}).items() if not k.startswith("__")},
    }
    write_json(Path(train_root) / "skeleton.json", payload)
    return payload


def convert_clip(npz_path, skeleton, representation, indices, fps):
    """单条 clip：四键 NPZ → 归一化前的 [frames, feature_dim]。

    fps 由调用方从 dataset.json 传入（权威值），不从 timestamps 反推：
    反推会得到 120.0048 这类带噪声的值，而 world_foot_contacts 的速度阈值
    与脚接触判定对 fps 敏感，用错帧率会静默改变接触标签。
    """
    with np.load(npz_path, allow_pickle=False) as archive:
        positions = np.asarray(archive["positions"], dtype=np.float64)
        rotations = np.asarray(archive["rotations"], dtype=np.float64)
        root_track = np.asarray(archive["root_track"], dtype=np.float64)
    if positions.ndim != 3 or positions.shape[1] != len(skeleton.bone_order_names):
        raise ValueError(f"positions 形状异常 {positions.shape}：{npz_path.name}")
    if rotations.shape[:2] != positions.shape[:2]:
        raise ValueError(f"rotations 与 positions 帧数不一致：{npz_path.name}")

    contacts = world_foot_contacts(positions, root_track, skeleton, fps)
    with torch.no_grad():
        features = representation(
            {
                "posed_joints": torch.from_numpy(positions)[None],
                "global_joint_rots": torch.from_numpy(rotations)[None],
                "foot_contacts": contacts[None] if contacts is not None else None,
            },
            to_normalize=False,
            lengths=torch.tensor([len(positions)]),
        )[0].numpy()
    return features, len(positions)


def prepare(views_root, train_root, *, limit=None):
    views_root, train_root = Path(views_root).resolve(), Path(train_root).resolve()
    manifest = read_json(views_root / "dataset.json")
    train_root.mkdir(parents=True, exist_ok=True)

    skeleton_payload = build_skeleton_file(train_root, views_root)
    skeleton = UnrealSkeleton(train_root)
    clips = manifest["clips"]
    if limit:
        clips = clips[:limit]

    representation = DualRootGlobalJoints(
        fps=float(skeleton_payload["fps"]),
        skeleton=skeleton,
        name="unreal_dual_root_global_joints",
    )
    indices = representation.indices["global_rep"]

    records, rejected = [], []
    total = 0
    sum_x = None
    sum_x2 = None

    for order, clip in enumerate(clips):
        npz_path = views_root / clip["file"]
        clip_fps = float(clip["fps"])
        try:
            features, frames = convert_clip(
                npz_path, skeleton, representation, indices, clip_fps
            )
        except (OSError, ValueError, KeyError) as error:
            rejected.append({"file": clip["file"], "asset": clip["asset"], "error": str(error)})
            continue
        if len(features) < MIN_TRAINING_FRAMES or not np.isfinite(features).all():
            rejected.append({"file": clip["file"], "asset": clip["asset"],
                             "error": f"特征无效或不足 {MIN_TRAINING_FRAMES} 帧"})
            continue

        bucket = int(hashlib.sha256(str(clip["asset"]).encode()).hexdigest()[:8], 16) % 10
        partition = "test" if bucket == 0 else "validation" if bucket == 1 else "train"

        # 统计量必须覆盖「全特征维度」（946 = 12*78+6 之下的完整 motion_rep 维），
        # 不是取完 global_rep 子集后的 942 维：
        #   1) represention 加载 stats 时断言 motion_rep_dim == stats.get_dim()，
        #      维度不符会直接 AssertionError；
        #   2) prepare_unreal_dataset 也是先对全特征算 mean/std，再 [:, indices]。
        # 因此这里累加完整 features，归一化时先全维相减再取子集。
        if partition == "train":
            values = features.astype(np.float64)
            total += len(values)
            sum_x = values.sum(0) if sum_x is None else sum_x + values.sum(0)
            sum_x2 = (values**2).sum(0) if sum_x2 is None else sum_x2 + (values**2).sum(0)

        name = f"motion_{order:05d}.npy"
        np.save(train_root / name, features)
        records.append({
            "file": name,
            "asset": clip["asset"],
            "frames": len(features),
            "source_frames": frames,
            "split": partition,
            "group": clip.get("scene"),
            "fps": clip_fps,
            "labels": {"scene": clip.get("scene"), "fps_group": clip.get("fps_group")},
        })

    if not records:
        raise ValueError("没有任何 clip 通过转换，无法生成训练集")
    if total == 0:
        raise ValueError("train 分区为空，无法计算统计量")

    mean = (sum_x / total).astype(np.float32)
    std = np.sqrt(np.maximum(sum_x2 / total - (sum_x / total) ** 2, 0)).astype(np.float32)
    stats = train_root / "stats"
    if stats.exists():
        shutil.rmtree(stats)
    stats.mkdir()
    np.save(stats / "mean.npy", mean)
    np.save(stats / "std.npy", std)

    # 归一化并只保留 global_rep 需要的列。
    # 顺序必须是「先全维归一化，再取子集」：mean/std 是 946 维全特征统计量，
    # 直接对 942 维做 broadcasting 会失败；这也与 prepare_unreal_dataset 一致。
    for item in records:
        path = train_root / item["file"]
        features = np.load(path, allow_pickle=False)
        normalized = ((features - mean) / np.sqrt(std**2 + 1e-5))[:, indices]
        np.save(path, normalized.astype(np.float32))

    # skeleton_signature 描述骨架本身，training_signature 额外绑定 stats，
    # 两者含义不同。UnrealMotionDataset 只用 training_signature 做完整性校验。
    skeleton_only = hashlib.sha256(
        (train_root / "skeleton.json").read_bytes()).hexdigest()

    dataset = {
        "schema_version": 2,
        "fps": float(skeleton_payload["fps"]),
        "feature_dim": len(indices),
        "skeleton_signature": skeleton_only,
        "clips": records,
        "rejected_clips": len(rejected),
        "source": str(views_root),
        "split_policy": "asset_family_sha256_80_10_10",
        "stats_scope": "train_valid_frames",
        "training_contract": "pose_only_root_authoritative",
    }
    dataset["training_signature"] = training_signature(train_root)
    write_json(train_root / "dataset.json", dataset)
    if rejected:
        write_json(train_root / "rejected_clips.json", rejected)

    return {
        "train_root": str(train_root),
        "clips": len(records),
        "rejected": len(rejected),
        "feature_dim": len(indices),
        "split_counts": {
            key: sum(1 for r in records if r["split"] == key)
            for key in ("train", "validation", "test")
        },
        "frames_total": sum(r["frames"] for r in records),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--views", required=True, help="四键 NPZ 训练视图目录")
    parser.add_argument("--output", required=True, help="输出训练集目录")
    parser.add_argument("--limit", type=int, help="只转前 N 条，用于小样本验证")
    args = parser.parse_args()
    # 说明：本工具刻意不做多进程。特征转换要在单进程内累积全局 mean/std，
    # 而 DualRootGlobalJoints 与 torch 表示层在 fork/spawn 下重建成本高、
    # 且 D 盘小文件并发读会互相抢 I/O（实测 32 进程反而比串行慢）。
    # 需要更快时应当先串行转出中间张量，再做一次独立的统计与归一化。
    result = prepare(args.views, args.output, limit=args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
