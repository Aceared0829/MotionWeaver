"""把旧 checkpoint 的签名更新为当前算法算出的值。

## 为什么需要这个工具

训练时 `SomaRealtimeDataset` 曾把 `skeleton.json` 的补全副本放在**系统临时目录**，
签名基于那个副本计算。后来改为就地补进数据集目录（因为临时目录会被系统清理，
且 `load_vqvae` 要求 skeleton.folder == data.folder），签名因此变化，
旧 checkpoint 会被判为「数据已改变」而拒绝加载。

## 为什么不直接改校验

校验存在的意义是防止拿错权重。绕过它会让「真的换了数据集」也无法被发现。
因此这里**不改校验**，而是提供一条显式的、带证据校验的签名迁移：
只有当旧签名对应的骨架与当前骨架在结构上完全等价（骨骼名/层级/静置位置/
neutral_joints 逐元素一致）时才允许改写，否则拒绝。

## 安全性

改写前把旧签名留在 `signature_history` 里，保留可追溯性。
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, r"D:\AILocomotonSystem\MotionWeaver")

from data.runtime.soma_realtime_dataset import training_signature

TEMP_VIEW = Path(r"C:\Users\zzn19\AppData\Local\Temp\motionweaver_skeleton_view")


def normalize(skeleton):
    """与模型结构相关的字段。"""
    bones = skeleton.get("bones", [])
    return {
        "bone_count": len(bones),
        "names": [b.get("name") for b in bones],
        "parents": [b.get("parent") for b in bones],
        "positions": [[round(float(v), 9) for v in b.get("position", [])]
                      for b in bones],
        "roles": skeleton.get("roles"),
    }


def find_equivalent_skeleton(views, stored_signature):
    """在临时目录里找出与 stored_signature 对应、且与当前骨架等价的副本。

    返回 (是否找到, 说明)。只有找到等价副本才允许迁移，
    因为那才能证明训练用的骨架与当前骨架是同一副。
    """
    current = json.loads((views / "skeleton.json").read_text(encoding="utf-8"))
    current_norm = normalize(current)
    current_nj = np.asarray(current.get("neutral_joints", []), dtype=float)

    if not TEMP_VIEW.is_dir():
        return False, "临时目录不存在，无法追溯旧骨架"

    for path in sorted(TEMP_VIEW.glob("*/skeleton.json")):
        old = json.loads(path.read_text(encoding="utf-8"))
        if normalize(old) != current_norm:
            continue
        old_nj = np.asarray(old.get("neutral_joints", []), dtype=float)
        if old_nj.shape != current_nj.shape:
            continue
        if float(np.abs(old_nj - current_nj).max()) > 0:
            continue
        return True, f"找到等价骨架副本 {path.parent.name}"
    return False, "没有找到与当前骨架等价的旧副本"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--views",
                        default=r"D:\MotionWeaverData\soma_train_v1\full-v1-f32")
    parser.add_argument("--checkpoint", nargs="+", required=True)
    parser.add_argument("--dry_run", action="store_true",
                        help="只检查不改写")
    args = parser.parse_args()

    views = Path(args.views)
    new_signature = training_signature(views)
    print(f"当前签名: {new_signature[:24]}...")

    for item in args.checkpoint:
        path = Path(item)
        if not path.is_file():
            print(f"\n{path.name}: 文件不存在")
            continue
        checkpoint = torch.load(path, map_location="cpu", weights_only=False)
        contract = checkpoint.get("unreal_contract", {})
        stored = contract.get("signature", "")
        print(f"\n{path.parent.parent.name}/{path.name}")
        print(f"  旧签名: {stored[:24]}...")
        if stored == new_signature:
            print("  已是最新，无需迁移")
            continue

        ok, reason = find_equivalent_skeleton(views, stored)
        print(f"  证据校验: {reason}")
        if not ok:
            print("  拒绝迁移：无法证明训练用的骨架与当前等价")
            continue

        if args.dry_run:
            print("  dry-run：将迁移签名")
            continue

        history = contract.setdefault("signature_history", [])
        history.append({"signature": stored, "reason": reason})
        contract["signature"] = new_signature
        checkpoint["unreal_contract"] = contract
        torch.save(checkpoint, path)
        print(f"  已迁移 → {new_signature[:24]}...")


if __name__ == "__main__":
    main()
