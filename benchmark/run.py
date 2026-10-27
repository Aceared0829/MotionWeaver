"""统一的 benchmark 入口：一次跑完所有能跑的评测，输出一份报告。

## 设计原则

**官方已有的不重写，我们只做补集，但对外是一份报告。**
因此本脚本把两类指标拼在同一张表里：

  A 组 = MotionBricks 原版指标（读 metrics.csv，训练时已算好）
        perplexity / commit / recons* / joint_vel / skate_contact / loss
  B 组 = 我们的补集（官方脚本无法覆盖 SOMA 四键 NPZ）
        关节 RMSE（厘米）、feature L1、自校验误差
  C 组 = 零成本基线（官方没有）
        Hold / Lerp 的参考值，来自既有基线报告

A、B 两组都在本进程内算；C 组只读既有报告，不重算。

## 口径

- A 组是**训练集**口径（metrics.csv 记录的是 train_*），不与留出集混报；
- B 组在同一批样本上算，长度按训练分布（tokens 6..16）；
- 所有 `_cm` 指标是厘米，全部经 `primitives.metrics` 的官方实现计算。

## 用法

    python -m benchmark.run [--checkpoints a.ckpt b.ckpt] [--report out.json]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, r"D:\AILocomotonSystem\MotionWeaver")

from benchmark import evaluate_recon, training_metrics

DEFAULT_RUNS = {
    "BC120 (B+C, down_t=3)": {
        "run": Path(r"D:\MotionWeaverData\training_runs\tok_BC120_3ep"),
        "scenes": ["B", "C"],
    },
    "A120 (A, down_t=2)": {
        "run": Path(r"D:\MotionWeaverData\training_runs\tok_A120_20k"),
        "scenes": ["A"],
    },
    "A120 (A, down_t=3)": {
        "run": Path(r"D:\MotionWeaverData\training_runs\tok_A120_dt3_3ep"),
        "scenes": ["A"],
    },
    "B120 (A, kmeans, down_t=2)": {
        "run": Path(r"D:\MotionWeaverData\training_runs\tok_B120_3ep"),
        "scenes": ["A"],
    },
}

# 既有基线（来自 docs/soma-inbetweening-baseline-full-v1.md，fps120）
BASELINE_REFERENCE = {
    "note": "来自已冻结的基线报告；不是本轮重算",
    "hold_rmse_cm": {"no_reference": 20.704, "middle_and_end": 20.465},
    "lerp_rmse_cm": {"no_reference": 20.704, "middle_and_end": 6.593},
    "lerp_foot_speed_mps": 0.545,
    "source_foot_speed_mps": 0.0261,
}


def collect_arm(name, spec, *, clips, device):
    """收集单臂的 A 组（训练指标）与 B 组（重建）。"""
    run = spec["run"]
    result = {"name": name, "run": str(run), "scenes": spec["scenes"]}

    # A 组
    training = {}
    for column, label in training_metrics.METRICS.items():
        stats = training_metrics.tail_values(run, column)
        if stats:
            training[label] = stats
    result["training_metrics"] = training

    # B 组
    checkpoint = run / "checkpoints" / "final.ckpt"
    if checkpoint.is_file():
        try:
            recon = evaluate_recon.evaluate_all(
                checkpoint, rows=(6, 8, 10, 12, 14, 16),
                scenes=spec["scenes"], clips=clips, device=device)
            result["reconstruction"] = recon
        except Exception as error:  # noqa: BLE001
            result["reconstruction"] = {
                "error": f"{type(error).__name__}: {error}"}
    else:
        result["reconstruction"] = {"error": "checkpoint 不存在"}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clips", type=int, default=4,
                        help="每个长度抽多少条做重建评测")
    parser.add_argument("--report", default=r"D:\MotionWeaverData\reports\benchmark.json")
    parser.add_argument("--only", nargs="*", help="只跑这些臂（名字前缀匹配）")
    args = parser.parse_args()

    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"

    arms = DEFAULT_RUNS
    if args.only:
        arms = {k: v for k, v in arms.items()
                if any(k.startswith(p) for p in args.only)}
    if not arms:
        print("没有匹配的臂")
        return 1

    report = {"device": device, "arms": [], "baseline_reference": BASELINE_REFERENCE}

    for name, spec in arms.items():
        if not spec["run"].exists():
            print(f"{name}: 目录不存在，跳过")
            continue
        print(f"\n=== {name} ===")
        arm = collect_arm(name, spec, clips=args.clips, device=device)
        report["arms"].append(arm)

        training = arm["training_metrics"]
        if training:
            tags = ["l_recons_pose", "l_skate_contact", "perplexity_pose",
                    "l_commit_pose", "l_joint_vel", "loss"]
            for tag in tags:
                if tag in training:
                    print(f"  {tag:20s} {training[tag]['mean']:.4f}")
        else:
            print("  训练指标: 缺失（metrics.csv 不存在）")

        recon = arm["reconstruction"]
        if "error" in recon:
            print(f"  重建: {recon['error']}")
        else:
            print(f"  重建: feature L1 {recon['mean_feature_l1']:.4f}  "
                  f"关节 {recon['mean_joint_rmse_cm']:.2f} cm  "
                  f"自校验 {recon['max_self_check_m']:.6f} m")

    target = Path(args.report)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                 default=str), encoding="utf-8")
    print(f"\n已写出 {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
