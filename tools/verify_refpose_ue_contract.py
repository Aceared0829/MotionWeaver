"""RefPose 数据语义的独立判据（不含任何自证公式）。

为什么需要它：`p/r/k` 的空间语义此前在本项目里存在冲突说法。这个脚本只用
「数据自身的内禀性质」判定，而不是「用同一套公式再算一遍」。

已实现的判据：

1. 刚性（位置）：相邻骨「子-父距离」的标准差。刚体姿态下应恒为 0；
   这条不依赖任何坐标约定，也不依赖旋转字段。
2. Root 独立性：Root→Hips 距离是否恒定。恒定说明 Root 是骨架的一节；
   不恒定说明 Root 是独立辅助轨道。
3. 6D 合法性：解码后是否单位正交；这只能证明「解码方式与编码一致」，
   不能证明旋转与位置的骨轴同源（那是 U1，明确不在此断言）。
4. Root 一致性：`p_root == -R(k)ᵀ·k_pos`，manifest 有该声明，可独立复算。

命令行只打印结果，不做写盘。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from data.runtime.refpose import (  # noqa: E402
    bone_rigidity,
    decode_6d,
    load_skeleton,
    root_axis0_check,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", default=None, help="契约发布目录，默认 <MW_DATA_ROOT>/full_release_v2")
    parser.add_argument("--source", default=None, help="训练源目录，默认 <MW_DATA_ROOT>/full-train-v1")
    parser.add_argument("--mmap", default=None, help="mmap 数据目录，默认 <MW_DATA_ROOT>/full_mmap_v2")
    parser.add_argument("--clips", type=int, default=3)
    args = parser.parse_args()

    if args.release is None or args.source is None or args.mmap is None:
        data_root_value = os.environ.get("MW_DATA_ROOT", "").strip()
        if not data_root_value:
            parser.error("未设置 MW_DATA_ROOT；请设置它，或用 --release/--source/--mmap 指定目录。")
        data_root = Path(data_root_value).expanduser()
        args.release = args.release or str(data_root / "full_release_v2")
        args.source = args.source or str(data_root / "full-train-v1")
        args.mmap = args.mmap or str(data_root / "full_mmap_v2")

    contract = json.loads(
        (Path(args.release) / "contract" / "contract.json").read_text(encoding="utf-8"))
    names, parents, _, _ = load_skeleton(Path(args.source) / "skeleton.json")

    clips = [c for c in contract["clips"] if c["source_frames"] >= 96][: args.clips]
    report: dict = {"clips": [], "checks": {}}

    worst_rigidity = 0.0
    worst_axis0 = 0.0
    worst_orthonormality = 0.0
    hips_root_std = 0.0

    for clip in clips:
        folder = Path(args.mmap) / clip["mmap_file"]
        positions = np.load(folder / "p.npy").astype(np.float64)
        rotations = np.load(folder / "r.npy").astype(np.float64)
        root = np.load(folder / "k.npy").astype(np.float64)

        rigidity = bone_rigidity(positions, parents)
        # Hips 单独看：它的「父」是独立 Root，不参与骨长刚性。
        hips_index = names.index("Hips")
        hips_rigidity = float(rigidity[hips_index])
        body_rigidity = float(np.nanmax(np.delete(rigidity, hips_index)))

        decoded = decode_6d(rotations)
        gram = decoded @ decoded.transpose(0, 1, 3, 2)
        orthonormality = float(np.abs(gram - np.eye(3)).max())

        axis0 = root_axis0_check(positions, root)
        hips_to_root = np.linalg.norm(positions[:, hips_index] - positions[:, 0], axis=-1)

        worst_rigidity = max(worst_rigidity, body_rigidity)
        worst_axis0 = max(worst_axis0, axis0)
        worst_orthonormality = max(worst_orthonormality, orthonormality)
        hips_root_std = max(hips_root_std, float(hips_to_root.std()))

        report["clips"].append({
            "asset": clip["asset"],
            "frames": int(len(positions)),
            "max_body_bone_rigidity_std_m": body_rigidity,
            "hips_to_root_std_m": float(hips_to_root.std()),
            "max_6d_orthonormality_error": orthonormality,
            "max_root_axis0_error_m": axis0,
        })

    # 刚性阈值取 1 微米：真实数据是 ~5e-8 m，留两个数量级余量。
    report["checks"] = {
        "body_bones_rigid": worst_rigidity < 1e-6,
        "root_is_separate_track": hips_root_std > 1e-3,
        "rotation_6d_orthonormal": worst_orthonormality < 1e-5,
        "root_matches_axis0_identity": worst_axis0 < 1e-4,
    }
    report["limits"] = {
        "body_bone_rigidity_std_m": worst_rigidity,
        "hips_to_root_std_m": hips_root_std,
        "rotation_6d_orthonormality_error": worst_orthonormality,
        "root_axis0_error_m": worst_axis0,
    }
    report["not_asserted"] = (
        "旋转与位置的骨轴是否同源（U1）不在此判定；"
        "这两条只能分别验证：位置加刚性、旋转加正交性。")
    report["passed"] = all(report["checks"].values())

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
