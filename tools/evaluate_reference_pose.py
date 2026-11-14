"""用四条硬参考档位评估参考引导姿态模型的 test 分区指标。

为什么要单独一个入口：仓库里的 training/evaluation/evaluate_conditioned_pose.py
只跑 no_reference 与 two_refs 两档，而且要 --lock 指向封版 freeze_manifest.json；
本机重建的 full_release_v2 没有那份清单（见 data/tools/build/build_refpose_release.py），
所以那条路走不通。这里复刻非学习基线
（training/evaluation/evaluate_conditioned_baselines.py）的选窗与聚合口径，
让模型指标能和 hold / linear_reference 直接对表：

  - 选窗：每条 clip 取 windows.jsonl 里该 clip 的中心窗口（per-clip=1 口径）；
  - 指标：非锚点关节的逐关节欧氏距离 RMSE（cm），先按 clip 求值再等权平均；
  - 四档硬参考：no_reference / middle_reference / end_reference / middle_and_end，
    与封盘记录的 5.28 / 3.22 / 3.22 / 2.17 cm 一一对应。

用法：
  .venv/bin/python tools/evaluate_reference_pose.py \
      --release /home/acear/MotionWeaverData/full_release_v2 \
      --mmap-root /home/acear/MotionWeaverData/full_mmap_v2 \
      --checkpoint /home/acear/MotionWeaverData/runs/refpose_v2_20ep/best.pt \
      --output /home/acear/MotionWeaverData/reports/refpose_v2_test.json
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader, Subset

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from training.pretrain.train_conditioned_pose import (
    MotionWindows,
    ReferenceGuidedPose,
    root_world_positions,
)

# 与封盘记录对齐的四档；顺序即报告顺序。
SCENARIOS = (
    ("no_reference", ()),
    ("middle_reference", (12,)),
    ("end_reference", (23,)),
    ("middle_and_end", (12, 23)),
)


def center_indices(rows):
    """每条 clip 取中心窗口，与非学习基线的 per-clip=1 选窗完全一致。"""
    grouped = defaultdict(list)
    for index, row in enumerate(rows):
        grouped[row["clip_index"]].append(index)
    return [indices[len(indices) // 2] for _, indices in sorted(grouped.items())]


def offsets_to_scenario(offsets, dataset):
    """把参考帧下标写进数据集，避免依赖 scenario 字符串的隐式约定。"""
    if not offsets:
        dataset.scenario = "no_refs"
    elif offsets == (12,):
        dataset.scenario = "one_ref"
    elif offsets == (23,):
        dataset.scenario = "end_ref"
    elif offsets == (12, 23):
        dataset.scenario = "two_refs"
    else:
        raise ValueError(f"未知参考档位：{offsets}")


@torch.no_grad()
def evaluate_scenario(model, dataset, chosen, std, mean, foot_indices, fps, device, batch_size):
    loader = DataLoader(Subset(dataset, chosen), batch_size=batch_size, num_workers=0)
    per_clip, slip_ratio = [], []
    pred_windows, tgt_windows = [], []
    for batch in loader:
        history, root, reference, mask, target, contacts = (part.to(device) for part in batch)
        with torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=device.type == "cuda"):
            predicted = model(history, root, reference, mask)
        predicted = predicted.float().reshape(len(history), 24, -1, 9)
        target = target.float().reshape(len(history), 24, -1, 9)
        # 反标准化：pose 在数据集里已经 (p - mean) / std，误差要回到米再转厘米。
        squared_cm = (((predicted[..., :3] - target[..., :3]) * std) ** 2).sum(-1) * 10000
        visible = (1 - mask).reshape(len(history), 24, 1).bool().expand_as(squared_cm)
        for values, include in zip(squared_cm, visible):
            per_clip.append(float(torch.sqrt(values[include].mean()).item()))

        # 脚滑：与封盘口径一致——接触期（相邻两帧都着地）脚速 ÷ 源动作同期脚速。
        # 位置是 Root 局部坐标下的米，必须先用权威 Root 轨迹合到世界系再求差
        # （纯局部坐标里 Root 自身的位移被扣掉了，脚速会偏小）。
        pose_m = predicted[..., :3] * std + mean
        target_m = target[..., :3] * std + mean
        pred_world = root_world_positions(pose_m[:, :, foot_indices], root)
        tgt_world = root_world_positions(target_m[:, :, foot_indices], root)
        pred_speed = torch.linalg.vector_norm(pred_world[:, 1:] - pred_world[:, :-1], dim=-1) * fps
        tgt_speed = torch.linalg.vector_norm(tgt_world[:, 1:] - tgt_world[:, :-1], dim=-1) * fps
        pair_mask = contacts[:, 1:] * contacts[:, :-1] > 0
        for index in range(len(history)):
            if not bool(pair_mask[index].any()):
                continue
            # 按窗口求接触期脚速的均值，再跨窗口等权平均——这正是
            # evaluate_conditioned_baselines.py 的 aggregate() 口径
            # （per_clip_equal_weight）；池化所有帧会得到完全不同的数。
            pred_clip = float(pred_speed[index][pair_mask[index]].mean())
            tgt_clip = float(tgt_speed[index][pair_mask[index]].mean())
            pred_windows.append(pred_clip)
            tgt_windows.append(tgt_clip)
            if tgt_clip > 0:
                slip_ratio.append(pred_clip / tgt_clip)
    pred_mean = float(np.mean(pred_windows)) if pred_windows else None
    tgt_mean = float(np.mean(tgt_windows)) if tgt_windows else None
    return {"clips": len(per_clip),
            "non_anchor_pose_rmse_cm": float(np.mean(per_clip)) if per_clip else None,
            "median_non_anchor_pose_rmse_cm": float(np.median(per_clip)) if per_clip else None,
            "worst_clip_rmse_cm": float(np.max(per_clip)) if per_clip else None,
            # 与 evaluate_conditioned_baselines.py 的 aggregate() 同口径，可直接对表
            "contact_foot_speed_mps": pred_mean,
            "source_contact_foot_speed_mps": tgt_mean,
            "foot_slip_ratio": (pred_mean / tgt_mean) if pred_mean and tgt_mean else None,
            "contact_windows": len(pred_windows),
            # 每条 clip 先算比值再等权平均，避免长动作主导
            "foot_slip_ratio_mean": float(np.mean(slip_ratio)) if slip_ratio else None,
            "foot_slip_ratio_median": float(np.median(slip_ratio)) if slip_ratio else None,
            "_per_clip": per_clip}


def foot_joint_indices(contract):
    """按 skeleton.json 的 roles 取 [LeftFoot, LeftToeBase, RightFoot, RightToeBase]。"""
    skeleton = json.loads((Path(contract["source_dataset"]) / "skeleton.json").read_text(encoding="utf-8"))
    names = [bone["name"] for bone in skeleton["bones"]]
    return [names.index(skeleton["roles"][role]) for role in
            ("left_foot", "left_toe", "right_foot", "right_toe")]


def evaluate(release, checkpoint, output, *, mmap_root=None, batch_size=256, split="test",
             limit_clips=0):
    contract = json.loads((Path(release) / "contract" / "contract.json").read_text(encoding="utf-8"))
    state = torch.load(checkpoint, map_location="cpu", weights_only=False)
    width = state.get("width", 256)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = ReferenceGuidedPose(contract["bone_count"], width).to(device)
    model.load_state_dict(state["model"])
    model.eval()

    # include_contacts 必须开：脚滑倍数要用接触标签，关掉就拿不到第 6 个 batch 元素。
    dataset = MotionWindows(release, contract, split, seed=42, scenario="no_refs",
                            include_contacts=True, mmap_root=mmap_root)
    chosen = center_indices(dataset.rows)
    if limit_clips:
        chosen = chosen[:limit_clips]
    std = torch.from_numpy(dataset.std).float().to(device).view(1, 1, -1, 3)
    mean = torch.from_numpy(dataset.mean).float().to(device).view(1, 1, -1, 3)
    feet = foot_joint_indices(contract)
    fps = float(contract["fps"])
    print(f"契约 {release}")
    print(f"{split}: {len(dataset)} 窗口 / {len(chosen)} 条 clip（每条取中心窗口）  脚部关节 {feet}", flush=True)

    result = {}
    for name, offsets in SCENARIOS:
        offsets_to_scenario(offsets, dataset)
        result[name] = evaluate_scenario(model, dataset, chosen, std, mean, feet, fps, device, batch_size)
        print(f"  {name:18s} {result[name]['non_anchor_pose_rmse_cm']:6.2f} cm   "
              f"脚滑 {result[name]['foot_slip_ratio_mean']:.2f}×  "
              f"({result[name]['clips']} 条 clip)", flush=True)

    per_clip = {name: result[name].pop("_per_clip") for name, _ in SCENARIOS}
    report = {"checkpoint": str(Path(checkpoint).resolve()), "release": str(Path(release).resolve()),
              "split": split, "selection": "middle_window_per_clip", "oracle_root_condition": True,
              "metric": "mean_of_per_clip_non_anchor_joint_euclidean_rmse_cm",
              "epoch": state.get("epoch"), "width": width,
              "checkpoint_val_pose_rmse_cm": state.get("val_pose_rmse_cm"),
              "scenarios": result}
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # 逐 clip 明细单独存：报告要能追到是哪条动作拖后腿，又不把主报告撑到几十兆。
    np.savez_compressed(out.with_suffix(".per_clip.npz"),
                        **{name: np.asarray(values) for name, values in per_clip.items()})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--release", required=True, help="含 contract/contract.json 的契约目录")
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--mmap-root", default=None)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--split", choices=("validation", "test"), default="test")
    parser.add_argument("--limit-clips", type=int, default=0,
                        help="只评估前 N 条 clip（冒烟用；0 表示全量）")
    args = parser.parse_args()
    report = evaluate(args.release, args.checkpoint, args.output,
                      mmap_root=args.mmap_root, batch_size=args.batch_size, split=args.split,
                      limit_clips=args.limit_clips)
    print(json.dumps({k: v["non_anchor_pose_rmse_cm"] for k, v in report["scenarios"].items()},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
