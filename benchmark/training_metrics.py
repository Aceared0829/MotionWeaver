"""按 MotionBricks 原版的指标体系对比各 checkpoint。

## 原版怎么做的（training/models/vqvae/motion_vqvae.py:335-352）

原版不在训练后另做重建评测，而是**在 training_step 里直接算 10 个量**并记录：

    perplexity_pose         码本利用率（per-head，上限 = nb_code_per_head）
    l_commit_pose           编码器输出与所选码字的距离
    l_recons_pose           重建损失（主指标；SmoothL1，归一化特征空间）
    l_recons_root_global    根运动全局重建
    l_recons_root_local     根运动局部重建
    l_joint_vel             关节速度误差（归一化空间）
    l_recons_pose_global    全局姿态重建
    l_recons_pose_local     局部姿态重建
    l_skate_contact         接触期脚滑速度（m/s，可直接与基线比）
    loss                    加权总损失

并且在 L306-312 明确用 `joint_positions_from="ric_data"` 做逆变换——
这就是标准做法，不需要手工拼装。

因此本脚本只读 metrics.csv，不跑任何前向。这样得到的数字与训练时
完全同源，避免了此前手工逆变换引入的坐标错误。

## 注意

这些是**训练集**上的指标（metrics.csv 记录的是 train_*）。
它们反映收敛程度，不等价于留出集泛化。报告时必须写明这一点。
"""

import csv
import json
import math
import statistics
import sys
from pathlib import Path

RUNS = Path(r"D:\MotionWeaverData\training_runs")

# 与 motion_vqvae.py:336-344 一一对应
METRICS = {
    "loss/train_perplexity_pose_step": "perplexity_pose",
    "loss/train_l_commit_pose_step": "l_commit_pose",
    "loss/train_l_recons_pose_step": "l_recons_pose",
    "loss/train_l_recons_root_global_step": "l_recons_root_global",
    "loss/train_l_recons_root_local_step": "l_recons_root_local",
    "loss/train_l_joint_vel_step": "l_joint_vel",
    "loss/train_l_recons_pose_global_step": "l_recons_pose_global",
    "loss/train_l_recons_pose_local_step": "l_recons_pose_local",
    "loss/train_l_skate_contact_step": "l_skate_contact",
    "loss/train_loss_step": "loss",
}

ARMS = {
    "BC120 (B+C, down_t=3)": RUNS / "tok_BC120_3ep",
    "A120 (A, down_t=3)": RUNS / "tok_A120_dt3_3ep",
    "A120 (A, down_t=2, 原基线)": RUNS / "tok_A120_20k",
}


def tail_values(run, column, fraction=0.9):
    """合并所有 version，取末段 fraction 区间的值。"""
    points = {}
    for path in sorted(run.glob("metrics/version_*/metrics.csv")):
        with path.open(encoding="utf-8") as stream:
            for row in csv.DictReader(stream):
                raw = row.get(column)
                if raw in (None, ""):
                    continue
                try:
                    step = int(float(row.get("step") or 0))
                    value = float(raw)
                except (TypeError, ValueError):
                    continue
                if step > 0 and math.isfinite(value):
                    points[step] = value
    if not points:
        return None
    ordered = sorted(points)
    cut = ordered[int(len(ordered) * fraction)]
    picked = [points[s] for s in ordered if s >= cut]
    return {
        "mean": statistics.mean(picked),
        "median": statistics.median(picked),
        "count": len(picked),
    }


def main():
    available = {}
    for name, run in ARMS.items():
        if not run.exists():
            continue
        available[name] = run

    if not available:
        print("没有可用的 run")
        return 1

    # 逐臂收集
    report = {}
    for name, run in available.items():
        report[name] = {}
        for column, label in METRICS.items():
            stats = tail_values(run, column)
            if stats:
                report[name][label] = stats

    # 打印对比表
    names = list(report)
    print("MotionBricks 原版指标体系（末段 10% 均值；训练集口径）")
    print("=" * 96)
    header = f"{'指标':>22}"
    for name in names:
        header += f" {name[:22]:>24}"
    print(header)
    print("-" * 96)

    for column, label in METRICS.items():
        line = f"{label:>22}"
        for name in names:
            stats = report[name].get(label)
            line += f" {stats['mean']:>24.4f}" if stats else f" {'—':>24}"
        print(line)

    print("-" * 96)
    print("行数")
    for name in names:
        values = list(report[name].values())
        if values:
            print(f"  {name}: {values[0]['count']} 个采样点")
        else:
            print(f"  {name}: 无数据（metrics 缺失）")

    # 选型判读
    print("\n=== 判读 ===")
    print("  l_recons_pose 是原版主指标（越低越好）")
    print("  perplexity_pose 上限 = nb_code_per_head = 10（越高说明码本用得越充分）")
    print("  l_skate_contact 单位是 m/s，可与基线脚滑 0.545 m/s（Lerp）直接比")

    output = RUNS / "benchmark_original_metrics.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                 default=str), encoding="utf-8")
    print(f"\n已写出 {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


