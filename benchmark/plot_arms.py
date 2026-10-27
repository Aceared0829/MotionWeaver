"""生成 paper 图 9：四臂的 A 组（训练指标）与 B 组（重建误差）对比。

数据来自 benchmark.json。设计要点：
  - A 组的 l_recons_pose 不可跨 down_t 比较，因此左图按 down_t 分组着色，
    并在图上标注这一点，避免读者误读为「越低越好」的简单排序；
  - 右图直接给关节 RMSE（厘米），并画出 Lerp 基线的 6.59 cm 参考线，
    让「重建误差远大于补间基线」这一事实一眼可见。
"""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

REPORT = Path(r"D:\MotionWeaverData\reports\benchmark.json")
OUT = Path(r"D:\AILocomotonSystem\MotionWeaver\paper\figures\fig9-benchmark-arms.png")

# 与正文表 20/21 对齐的短标签
SHORT = {
    "BC120 (B+C, down_t=3)": "BC120\nB+C · dt3",
    "A120 (A, down_t=2)": "A120\nA · dt2",
    "A120 (A, down_t=3)": "A120\nA · dt3",
    "B120 (A, kmeans, down_t=2)": "B120\nA · kmeans · dt2",
}
COLORS = {
    "BC120 (B+C, down_t=3)": "#2ca02c",
    "A120 (A, down_t=2)": "#1f77b4",
    "A120 (A, down_t=3)": "#17becf",
    "B120 (A, kmeans, down_t=2)": "#d62728",
}


def main():
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    arms = [a for a in report["arms"] if a.get("training_metrics")]
    recon_arms = [a for a in report["arms"]
                  if a.get("reconstruction") and "error" not in a["reconstruction"]]

    figure, (left, right) = plt.subplots(1, 2, figsize=(14, 5.4))
    figure.suptitle("Tokenizer benchmark: official metrics (left) vs "
                    "reconstruction error (right)",
                    fontsize=13, fontweight="bold")

    # 左图：A 组的 l_recons_pose 与 l_skate_contact（双轴）
    labels = [SHORT.get(a["name"], a["name"]) for a in arms]
    colors = [COLORS.get(a["name"], "#888888") for a in arms]
    recons = [a["training_metrics"].get("l_recons_pose", {}).get("mean")
              for a in arms]
    skate = [a["training_metrics"].get("l_skate_contact", {}).get("mean")
             for a in arms]

    x = np.arange(len(arms))
    width = 0.38
    left.bar(x - width / 2, recons, width, color=colors, alpha=0.85,
             label="l_recons_pose")
    left.set_ylabel("l_recons_pose (normalized space)", fontsize=9)
    left.set_xticks(x)
    left.set_xticklabels(labels, fontsize=8)
    left.grid(True, alpha=0.25, linestyle="--", axis="y")

    twin = left.twinx()
    twin.bar(x + width / 2, skate, width, color=colors, alpha=0.45,
             hatch="//", edgecolor="white", label="l_skate_contact (m/s)")
    twin.set_ylabel("l_skate_contact (m/s)", fontsize=9)
    twin.axhline(0.545, color="black", linestyle=":", linewidth=1.2)
    twin.text(len(arms) - 0.5, 0.56, "Lerp baseline 0.545",
              fontsize=7, ha="right", color="black")

    left.set_title("Official metric group A — do NOT rank across down_t",
                   fontsize=10)
    handles1, labels1 = left.get_legend_handles_labels()
    handles2, labels2 = twin.get_legend_handles_labels()
    left.legend(handles1 + handles2, labels1 + labels2, fontsize=7,
                loc="upper left")

    # 右图：B 组的关节 RMSE
    rlabels = [SHORT.get(a["name"], a["name"]) for a in recon_arms]
    rc = [COLORS.get(a["name"], "#888888") for a in recon_arms]
    values = [a["reconstruction"]["mean_joint_rmse_cm"] for a in recon_arms]

    xr = np.arange(len(recon_arms))
    bars = right.bar(xr, values, 0.55, color=rc, alpha=0.85)
    right.axhline(6.59, color="black", linestyle="--", linewidth=1.3)
    right.text(len(recon_arms) - 0.55, 12, "Lerp baseline 6.59 cm",
               fontsize=8, ha="right")
    right.set_ylabel("joint RMSE (cm)", fontsize=9)
    right.set_xticks(xr)
    right.set_xticklabels(rlabels, fontsize=8)
    right.set_title("Our supplement group B — joint-level error", fontsize=10)
    right.grid(True, alpha=0.25, linestyle="--", axis="y")
    for bar, value in zip(bars, values):
        right.text(bar.get_x() + bar.get_width() / 2, value + 4,
                   f"{value:.1f}", ha="center", fontsize=8)

    figure.tight_layout(rect=(0, 0.03, 1, 0.94))
    figure.text(0.5, 0.005,
                "Self-check (inverse round-trip) is 0.000000 m for every arm, "
                "so group B differences come from the models, not the harness.",
                ha="center", fontsize=8, color="gray")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(OUT, dpi=150, bbox_inches="tight")
    print(f"已写出 {OUT}")


if __name__ == "__main__":
    main()
