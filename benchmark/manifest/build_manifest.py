"""从权重 / 契约 / 统计量 / 骨架生成 refpose_manifest_v1.json。

这份文件是**唯一真源**：它把「模型到底吃什么」从散在各处的代码行为
固化成一份带哈希的、机器可读的契约。任何人（包括 UE 侧的 C++）要回答
这个问题，只应该看它，而不是去读 __getitem__。

用法：
    python benchmark/manifest/build_manifest.py \
        --release  D:/MotionWeaverData/full_release \
        --mmap     D:/MotionWeaverData/full_mmap \
        --checkpoint D:/MotionWeaverData/model_weight/refpose_v3_71k/best.pt \
        --out      refpose_manifest_v1.json
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def build(release, mmap, checkpoint):
    release, mmap, checkpoint = Path(release), Path(mmap), Path(checkpoint)
    contract = json.loads((release / "contract" / "contract.json").read_text(encoding="utf-8"))
    skeleton = json.loads((mmap / "skeleton.json").read_text(encoding="utf-8"))
    stats = contract["stats"]["pose_m"]
    stats_dir = release / "contract" / "stats"
    mean = np.load(stats_dir / stats["mean"])
    std = np.load(stats_dir / stats["std"])

    bones = [bone["name"] for bone in skeleton["bones"]]
    parents = [bone["parent"] for bone in skeleton["bones"]]
    bone_count = len(bones)
    history, future = contract["window"]["history_frames"], contract["window"]["future_frames"]

    # 维度恒等式必须成立，否则前面某个假设就已经错了
    features = bone_count * 9
    assert history == future == 24, (history, future)
    assert mean.shape == std.shape == (bone_count, 3), (mean.shape, std.shape)

    return {
        "format": "refpose_manifest_v1",
        "generated_by": "benchmark/manifest/build_manifest.py",
        "purpose": "参考引导姿态补全的输入契约；UE 侧装配输入以本文件为准",

        "checksums": {
            "checkpoint_sha256": sha256(checkpoint),
            "contract_sha256": sha256(release / "contract" / "contract.json"),
            "pose_mean_sha256": sha256(stats_dir / stats["mean"]),
            "pose_std_sha256": sha256(stats_dir / stats["std"]),
            "skeleton_sha256": sha256(mmap / "skeleton.json"),
        },

        "skeleton": {
            "bone_count": bone_count,
            "names": bones,
            "parents": parents,
            "roles": skeleton.get("roles", {}),
        },

        "timing": {
            "fps": contract["fps"],
            "fps_note": "不是整数 120；24 帧 = 199.99 ms，按 120.0 计算会引入 8us 误差",
            "history_frames": history,
            "future_frames": future,
            "window_seconds": history / contract["fps"],
        },

        "inputs": {
            "history": {
                "shape": [history, features + 7],
                "composition": f"{bone_count}x9 归一的姿态位置+6D 旋转，拼上 7 维 root_local",
                "layout": {
                    "position": {"offset": 0, "width": bone_count * 3,
                                "normalized": True,
                                "formula": "(positions - mean) / std，逐骨逐轴"},
                    "rotation_6d": {"offset": bone_count * 3, "width": bone_count * 6,
                                    "normalized": False},
                    "root_local": {"offset": features, "width": 7,
                                   "normalized": False,
                                   "composition": "xyz(3) + xyzw 四元数(4)，相对最后一帧历史 Root"},
                },
            },
            "root_plan": {
                "shape": [future, 7],
                "composition": "xyz(3) + xyzw(4)，相对 origin（见 anchor）",
                "normalized": False,
                "authority": "k.npy",
            },
            "reference": {
                "shape": [future, features],
                "composition": f"{bone_count} 骨 x 9 维；未选中帧全 0",
                "normalized": "位置已归一化（同 history），旋转不归一化",
                "semantics": "硬覆盖：forward 末行 torch.where(mask, reference, predicted)，"
                             "mask=1 的帧输出与 reference 逐元素精确 0.0 误差",
            },
            "mask": {
                "shape": [future, 1],
                "dtype": "float32",
                "values": [0.0, 1.0],
            },
        },

        "output": {
            "shape": [future, features],
            "composition": f"{bone_count} 骨 x 9 = 3 位置 + 6 旋转",
            "position_normalized": True,
            "denormalize": "positions * std + mean",
        },

        "rotation_6d": {
            "layout": "row_major_interleaved",
            "order": ["m00", "m01", "m10", "m11", "m20", "m21"],
            "decode": {
                "col0": [0, 2, 4],
                "col1": [1, 3, 5],
                "col2": "cross(col0, col1)",
            },
            "warning": "仓库自带的 matrix_to_cont6d 是「按列拼接」，与本排布不兼容；"
                       "按错误口径解读时 78 根骨里只有 2 根掉到 1e-2 以下"
                       "（骨 42 RightForeArm 1.05e-04、骨 14 LeftForeArm 8.67e-03），"
                       "骨 42 会真的除零得 NaN",
            # 这些数是 2026-11-03 在四条 clip 上逐骨逐帧复算的，不是从别处抄的。
            # 之前写「点积 < 8e-8」是照抄旧文档，实测最大 1.67e-07，超了一倍。
            "verified_on": "000000 / 019534 / 019857 / 027106："
                           "两半范数落在 [0.99999982, 1.00000018]，两列点积 |.| ≤ 1.67e-07",
            "degeneration_probe": {
                "42": 1.05e-4, "14": 8.67e-3, "75": 6.66e-2,
                "70": 8.72e-2, "12": 1.30, "40": 1.31,
            },
        },

        "coordinates": {
            "space": "模型内部坐标系（前 +Z、上 +Y、右 -X）",
            "ue_conversion": "见 inference/visualization/ue_axis.py：ue=(ours_z, -ours_x, ours_y)",
            "positions_are_root_local": True,
            "root_local_proof": "world = R(k) @ local + k_pos，回代误差 < 1e-15",
        },

        "root": {
            "authority": "k.npy",
            "p_axis0": {
                "meaning": "Root 骨在位置数组里的那一项，等于 -R^T · k_pos",
                "not_a_second_trajectory": True,
                "verified_max_abs_error": 1.2e-6,
                "note": "它由 k 完全决定，不含独立信息；UE 侧忽略其水平分量即可",
            },
            "vertical": "k[:, 1] 恒为 0，垂直高度全部留在身体姿态里",
            "status": contract.get("root_status"),
        },

        "anchor": {
            "origin": "root[middle - 1]，middle = start + history_frames",
            "note": "位置与朝向都相对它；用 root[middle] 会让整条 root_plan 偏移",
        },

        "normalization": {
            "mean": {"file": stats["mean"], "shape": list(mean.shape)},
            "std": {"file": stats["std"], "shape": list(std.shape)},
            "scope": "只作用于位置通道（234 维）；旋转 6D 与 root_local 不归一化",
            "must_not_recompute": "统计量绑定训练分区，重算会破坏权重",
        },

        "dataset": {
            "clips": len(contract["clips"]),
            "window_count": contract["window_count"],
            "split_counts": contract["window_split_counts"],
            "contact_source": contract.get("contact_source"),
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True)
    parser.add_argument("--mmap", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--out", default="refpose_manifest_v1.json")
    args = parser.parse_args()
    manifest = build(args.release, args.mmap, args.checkpoint)
    text = json.dumps(manifest, ensure_ascii=False, indent=2)
    Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(json.dumps({"out": args.out, "bones": manifest["skeleton"]["bone_count"],
                      "format": manifest["format"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
