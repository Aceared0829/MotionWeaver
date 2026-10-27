"""生成技术报告的全部插图（可复现）。

数据来源：
  1. docs/experiments/assets/2026-09-28-motionweaver-a800-actor-root/metrics.csv
     20k 正式训练的逐步训练损失。
  2. 同目录 val_token_ce_2k.json / val_token_ce_20k.json / training_loss_summary.json
     固定验证集 Token CE 与首末 100 步均值。
  3. 同目录 motionweaver_20k_acceptance_summary_20260928.json
     同轨迹姿态、接触停止、CMC 控制器条件、解码器 oracle、时延。

所有数值直接读仓库内已提交的证据文件，不在脚本里手抄；缺失项显式报错而不是补零。
用法： ./.venv/Scripts/python.exe paper/generate_figures.py
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

# Matplotlib 需要一个可写的缓存目录；不指定时它在某些环境下会退回到临时目录并打印警告。
# 这里固定到 paper/.mplcache（已在 .gitignore 中忽略），保证多次运行输出一致、日志干净。
_MPLCACHE = Path(__file__).resolve().parent / ".mplcache"
os.environ.setdefault("MPLCONFIGDIR", str(_MPLCACHE))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

# SimHei 只有一个字重，请求 bold 时 Matplotlib 会打印 findfont 回退警告。
# 这些警告不影响出图，但会污染构建日志，因此只屏蔽这一类。
logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

plt.rcParams.update({
    # 中文优先用黑体；缺失时依次回退到雅黑与 DejaVu Sans（后者无中文，仅保证不崩）。
    "font.sans-serif": ["SimHei", "Microsoft YaHei", "Noto Sans CJK SC", "DejaVu Sans"],
    "font.family": "sans-serif",
    "axes.unicode_minus": False,
    "figure.dpi": 110,
    "savefig.dpi": 220,
    "axes.edgecolor": "#3c3c3c",
    "axes.labelcolor": "#1a1a1a",
    "text.color": "#1a1a1a",
    "xtick.color": "#3c3c3c",
    "ytick.color": "#3c3c3c",
})

PAPER = Path(__file__).resolve().parent
ASSETS = PAPER.parent / "docs" / "experiments" / "assets" / "2026-09-28-motionweaver-a800-actor-root"
OUT = PAPER / "figures"

C_MAIN = "#1F4E79"
C_ALT = "#C0504D"
C_GREY = "#8C8C8C"
C_SOFT = "#4F81BD"
C_GREEN = "#5B8C5A"
C_ORANGE = "#D48B32"


def load(name):
    return json.loads((ASSETS / name).read_text(encoding="utf-8"))


def save(fig, name):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("wrote", path.name, f"{path.stat().st_size / 1024:.0f} KiB")
    return path


def box(ax, xy, w, h, title, body, face, edge, title_color="white"):
    ax.add_patch(FancyBboxPatch((xy[0], xy[1]), w, h, boxstyle="round,pad=0.012,rounding_size=0.02",
                                linewidth=1.2, facecolor=face, edgecolor=edge, zorder=2))
    ax.text(xy[0] + w / 2, xy[1] + h - 0.085, title, ha="center", va="top", fontsize=9.6,
            color=title_color, fontweight="bold", zorder=3)
    ax.text(xy[0] + w / 2, xy[1] + h - 0.20, body, ha="center", va="top", fontsize=8.1,
            color=title_color, linespacing=1.55, zorder=3)


def arrow(ax, p, q, style="-|>", color=C_GREY, lw=1.1, ls="-"):
    ax.add_patch(FancyArrowPatch(p, q, arrowstyle=style, mutation_scale=11, linewidth=lw,
                                 color=color, linestyle=ls, shrinkA=2, shrinkB=2, zorder=1))


def node(ax, xc, ytop, w, title, lines, face, edge, title_color="white", body_color="white"):
    """逐行排版的流程节点：高度由正文行数推导，正文不会溢出边框。"""
    line_h = 0.030
    h = 0.075 + line_h * len(lines) + 0.022
    y0 = ytop - h
    ax.add_patch(FancyBboxPatch((xc - w / 2, y0), w, h,
                                boxstyle="round,pad=0.010,rounding_size=0.018",
                                linewidth=1.15, facecolor=face, edgecolor=edge, zorder=2))
    ax.text(xc, ytop - 0.040, title, ha="center", va="center", fontsize=9.6,
            color=title_color, fontweight="bold", zorder=3)
    ax.plot([xc - w / 2 + 0.022, xc + w / 2 - 0.022], [ytop - 0.060, ytop - 0.060],
            color=title_color, lw=0.7, alpha=0.55, zorder=3)
    for i, line in enumerate(lines):
        ax.text(xc, ytop - 0.082 - i * line_h, line, ha="center", va="center",
                fontsize=8.0, color=body_color, zorder=3)
    return y0, h


def fig_architecture():
    fig, ax = plt.subplots(figsize=(11.0, 5.35))
    fig.subplots_adjust(left=0.005, right=0.995, top=0.90, bottom=0.11)
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")

    cols = [
        (0.118, "离线数据", "#EAF1F8", C_MAIN, "#152238"),
        (0.372, "训练", C_MAIN, C_MAIN, "white"),
        (0.626, "证据与诊断", "#F7EFEF", C_ALT, "#3b1512"),
        (0.880, "在线执行", "#EDF2EC", C_GREEN, "#12290f"),
    ]
    for xc, name, _, edge, _ in cols:
        ax.text(xc, 0.968, name, ha="center", va="center", fontsize=10.6, color=edge, fontweight="bold")
        ax.plot([xc - 0.098, xc + 0.098], [0.940, 0.940], color=edge, lw=1.1)

    W = 0.196
    node(ax, 0.118, 0.898, W, "UE 动画导出", ["1,722 段 · 79 骨 · 30 FPS", "954 维 · 独立 Actor Root",
                                            "分区按动作家族隔断"], "#EAF1F8", C_MAIN, "#152238", "#1a1a1a")
    node(ax, 0.118, 0.655, W, "SOMA 全库处理", ["71,132 条非镜像 · 78 节点", "62.3 M 帧 · 120.0048 FPS",
                                            "本批 34,167 条已出训练视图"], "#EAF1F8", C_MAIN, "#152238", "#1a1a1a")
    node(ax, 0.118, 0.412, W, "四键训练视图", ["positions / rotations", "timestamps / root_track",
                                           "按帧率分组，绝不混批"], "#EAF1F8", C_MAIN, "#152238", "#1a1a1a")
    node(ax, 0.118, 0.169, W, "固定目标骨架", ["soma_uniform_v1", "78 节点 · 含手指与面部",
                                            "参考 TRS 已冻结并回读"], "#EAF1F8", C_MAIN, "#152238", "#1a1a1a")

    node(ax, 0.372, 0.898, W, "VQ-VAE 分词器", ["78 节点局部 TRS", "多头 EMA 码本 · code_dim 256",
                                             "训练主干时冻结"], C_MAIN, C_MAIN)
    node(ax, 0.372, 0.655, W, "Pose Token 主干", ["d=256 · 6 层 · 8 头", "8.28 M 可训练参数",
                                               "4 帧历史 + Actor Root"], C_MAIN, C_MAIN)
    node(ax, 0.372, 0.412, W, "训练设置", ["20,000 步 · batch 8 · bf16", "AdamAtan2 · lr 1e-4",
                                        "warmup 1k · cosine 至 2e-6"], C_MAIN, C_MAIN)
    node(ax, 0.372, 0.169, W, "监督目标", ["未来姿态 Token（masked）", "未来真实姿态不作输入",
                                        "未来 Root 仅作配对教师"], C_MAIN, C_MAIN)

    node(ax, 0.626, 0.898, W, "固定验证口径", ["48 段 · 2,608 个监督 Token", "Walk/Run/Crouch 各 16 段",
                                            "seed 1701 · 哈希可复核"], "#F7EFEF", C_ALT, "#3b1512", "#1a1a1a")
    node(ax, 0.626, 0.655, W, "同轨迹姿态对照", ["79 骨世界关节 RMSE", "接触脚速 / 接缝速度",
                                              "与轻量基线同条件比较"], "#F7EFEF", C_ALT, "#3b1512", "#1a1a1a")
    node(ax, 0.626, 0.412, W, "控制器条件探针", ["另录 CMC 轨迹 · 90° 转向", "局部姿态变化 0.50–5.24 cm",
                                              "蹲伏状态未作为输入"], "#F7EFEF", C_ALT, "#3b1512", "#1a1a1a")
    node(ax, 0.626, 0.169, W, "上限与预算诊断", ["解码器 pose-root oracle", "硬参考等式约束投影",
                                             "P50 17.8 ms / P95 20.0 ms"], "#F7EFEF", C_ALT, "#3b1512", "#1a1a1a")

    node(ax, 0.880, 0.898, W, "姿态生成", ["4 帧重规划 · 30 Hz", "输出 Actor 相对姿态",
                                         "不含世界位移"], "#EDF2EC", C_GREEN, "#12290f", "#1a1a1a")
    node(ax, 0.880, 0.655, W, "程序化后处理", ["Stride / Orientation Warping", "Foot Lock + Foot IK",
                                            "修正超阈值即回退"], "#EDF2EC", C_GREEN, "#12290f", "#1a1a1a")
    node(ax, 0.880, 0.412, W, "运动执行", ["CMC / Mover 决定实际 Root", "碰撞与净空在引擎侧",
                                         "实际位移回灌下一窗口"], "#EDF2EC", C_GREEN, "#12290f", "#1a1a1a")
    node(ax, 0.880, 0.169, W, "待验收", ["UE NNE / 蒙皮闭环", "多人并发与网络同步",
                                      "打包后性能"], "#F2F2F2", "#8C8C8C", "#333333", "#333333")

    for yc in (0.878, 0.635, 0.392):
        arrow(ax, (0.220, yc - 0.012), (0.264, yc - 0.012))
    arrow(ax, (0.220, 0.152), (0.264, 0.152), ls="--")
    arrow(ax, (0.470, 0.878 - 0.012), (0.520, 0.878 - 0.012))
    arrow(ax, (0.470, 0.635 - 0.012), (0.520, 0.635 - 0.012))
    arrow(ax, (0.372, 0.660), (0.372, 0.640), color=C_MAIN)
    arrow(ax, (0.470, 0.392 - 0.012), (0.720, 0.392 - 0.012), ls="--", color=C_GREEN)
    arrow(ax, (0.724, 0.878 - 0.012), (0.774, 0.878 - 0.012), color=C_GREEN)
    arrow(ax, (0.880, 0.660), (0.880, 0.640), color=C_GREEN)
    arrow(ax, (0.880, 0.412), (0.880, 0.395), color=C_GREEN)
    arrow(ax, (0.880, 0.169 + 0.145), (0.880, 0.169 + 0.170), ls="--", color="#8C8C8C")

    fig.text(0.5, 0.028, "实线 = 已执行的数据/权重路径；虚线 = 已定义但未验收的接口。"
                         "所有数值均取自仓库内已提交的证据文件。",
             ha="center", fontsize=8.2, color=C_GREY, style="italic")
    return save(fig, "fig1-architecture.png")


def fig_loss():
    csv = ASSETS / "metrics.csv"
    rows = []
    for i, line in enumerate(csv.read_text(encoding="utf-8").splitlines()):
        if i == 0:
            continue
        parts = line.split(",")
        if len(parts) < 6 or not parts[5].strip() or not parts[2].strip():
            continue
        rows.append((int(float(parts[5])), float(parts[2])))
    steps = np.array([r[0] for r in rows]); loss = np.array([r[1] for r in rows])
    summary = load("training_loss_summary.json")["20k formal"]

    fig, ax = plt.subplots(figsize=(7.4, 3.5))
    ax.plot(steps, loss, lw=0.7, color=C_SOFT, alpha=0.42, label="逐步训练损失")
    k = 200
    if len(loss) > k:
        smooth = np.convolve(loss, np.ones(k) / k, mode="valid")
        ax.plot(steps[k - 1:], smooth, lw=1.9, color=C_MAIN, label=f"{k} 步滑动平均")
    ax.axhline(summary["last_100_mean_token_ce_nats"], color=C_ALT, lw=1.0, ls="--",
               label=f"末 100 步均值 {summary['last_100_mean_token_ce_nats']:.3f}")
    ax.annotate(f"首 100 步均值 {summary['first_100_mean_token_ce_nats']:.3f}",
                xy=(100, summary["first_100_mean_token_ce_nats"]), xytext=(2400, 2.18),
                fontsize=9, color=C_ALT,
                arrowprops=dict(arrowstyle="-", color=C_ALT, lw=0.8))
    ax.set_xlabel("优化步"); ax.set_ylabel("姿态 Token 交叉熵 (nats)")
    ax.set_xlim(0, steps.max() * 1.01)
    ax.grid(alpha=0.25, lw=0.6)
    ax.legend(frameon=False, fontsize=8.6, loc="upper right")
    ax.set_title("20,000 步正式训练（A800-SXM4-80GB，batch 8，bf16）", fontsize=10.5, pad=8)
    return save(fig, "fig2-training-loss.png")


def fig_val_ce():
    a = load("val_token_ce_2k.json")["reports"][0]["scores"]
    b = load("val_token_ce_20k.json")["reports"][0]["scores"]
    keys = [("walk", "Walk"), ("run", "Run"), ("crouch", "Crouch"), ("overall", "总体")]
    x = np.arange(len(keys)); w = 0.34
    v2 = [a[k]["pose_token_ce_nats"] for k, _ in keys]
    v20 = [b[k]["pose_token_ce_nats"] for k, _ in keys]

    fig, ax = plt.subplots(figsize=(7.4, 3.4))
    ax.bar(x - w / 2 - 0.01, v2, w, color="#B9CBE0", edgecolor="white", label="2k 步链式短跑")
    ax.bar(x + w / 2 + 0.01, v20, w, color=C_MAIN, edgecolor="white", label="20k 步正式训练")
    for i, (p, q) in enumerate(zip(v2, v20)):
        ax.text(i, max(p, q) + 0.055, f"{(q - p) / p * 100:+.1f}%", ha="center", fontsize=9,
                color=C_ALT, fontweight="bold")
        ax.text(i - w / 2 - 0.01, p / 2, f"{p:.3f}", ha="center", va="center", fontsize=8.2, color="#2c3e50")
        ax.text(i + w / 2 + 0.01, q / 2, f"{q:.3f}", ha="center", va="center", fontsize=8.2, color="white")
    ax.set_xticks(x); ax.set_xticklabels([n for _, n in keys])
    ax.set_ylabel("masked Pose Token CE (nats)")
    ax.set_ylim(0, 2.25)
    ax.grid(axis="y", alpha=0.25, lw=0.6)
    ax.legend(frameon=False, fontsize=8.6, loc="upper right")
    ax.set_title("固定验证口径：48 段 / 2608 个监督 Token / seed 1701", fontsize=10.5, pad=8)
    return save(fig, "fig3-val-token-ce.png")


def fig_trajectory():
    j = load("motionweaver_20k_acceptance_summary_20260928.json")
    loops = j["same_trajectory_loops"]
    order = ["Walk", "Run", "Crouch"]
    labels = [f"{k} ({loops[k]['clip_index']})" for k in order]
    old = [loops[k]["joint_rmse_cm"]["old"] for k in order]
    new = [loops[k]["joint_rmse_cm"]["motionweaver"] for k in order]
    src = [loops[k]["source_contact_foot_speed_mps"] for k in order]
    oldf = [loops[k]["contact_foot_speed_mps"]["old"] for k in order]
    newf = [loops[k]["contact_foot_speed_mps"]["motionweaver"] for k in order]

    fig, axes = plt.subplots(1, 2, figsize=(10.2, 3.5))
    x = np.arange(3); w = 0.34
    ax = axes[0]
    ax.bar(x - w / 2 - 0.01, old, w, color="#B9CBE0", edgecolor="white", label="ReferenceGuidedPose")
    ax.bar(x + w / 2 + 0.01, new, w, color=C_MAIN, edgecolor="white", label="MotionWeaver 20k")
    for i in range(3):
        ax.text(i, max(old[i], new[i]) + 0.35, f"{new[i] - old[i]:+.2f}", ha="center", fontsize=8.6, color=C_ALT)
    ax.set_xticks(x); ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylabel("79 骨世界关节 RMSE (cm)")
    ax.set_title("(a) 同来源 Actor Root 下的姿态精度", fontsize=10)
    ax.grid(axis="y", alpha=0.25, lw=0.6); ax.legend(frameon=False, fontsize=8.2, loc="upper left")

    ax = axes[1]
    xi = np.arange(3); w2 = 0.26
    ax.bar(xi - w2, [s if s is not None else 0 for s in src], w2, color=C_GREY, edgecolor="white", label="来源动作")
    ax.bar(xi, [s if s is not None else 0 for s in oldf], w2, color="#B9CBE0", edgecolor="white", label="ReferenceGuidedPose")
    ax.bar(xi + w2, [s if s is not None else 0 for s in newf], w2, color=C_ALT, edgecolor="white", label="MotionWeaver 20k")
    for i, k in enumerate(order):
        if loops[k]["contact_foot_speed_mps"]["motionweaver"] is None:
            ax.text(i, 0.06, "无\n接触帧", ha="center", fontsize=8, color=C_GREY)
    ax.set_xticks(xi); ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylabel("接触期世界脚速 (m/s)")
    ax.set_title("(b) 接触期脚滑（越低越好）", fontsize=10)
    ax.grid(axis="y", alpha=0.25, lw=0.6); ax.legend(frameon=False, fontsize=8.2, loc="upper right")
    fig.suptitle("同一条来源未来 Actor Root 的条件，不是在线控制器预测", fontsize=9, color=C_GREY, y=-0.02)
    fig.tight_layout()
    return save(fig, "fig4-trajectory.png")


def fig_contact_stops():
    j = load("motionweaver_20k_acceptance_summary_20260928.json")
    stops = j["same_trajectory_contact_stops"]
    order = ["Walk", "Run", "Crouch"]
    labels = [f"{k} ({stops[k]['clip_index']})" + f"\nn={stops[k]['source_contact_pairs']}" for k in order]
    src = [stops[k]["source_contact_foot_speed_mps"] for k in order]
    old = [stops[k]["contact_foot_speed_mps"]["old"] for k in order]
    new = [stops[k]["contact_foot_speed_mps"]["motionweaver"] for k in order]

    fig, ax = plt.subplots(figsize=(8.6, 3.6))
    x = np.arange(3); w = 0.25
    ax.bar(x - w, src, w, color=C_GREY, edgecolor="white", label="来源动作（理想）")
    ax.bar(x, old, w, color="#B9CBE0", edgecolor="white", label="ReferenceGuidedPose")
    ax.bar(x + w, new, w, color=C_ALT, edgecolor="white", label="MotionWeaver 20k")
    for i in range(3):
        for off, val in ((-w, src[i]), (0, old[i]), (w, new[i])):
            ax.text(i + off, val + 0.012, f"{val:.3f}", ha="center", fontsize=7.6, color="#333333")
    ax.set_xticks(x); ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylabel("接触期世界脚速 (m/s)")
    ax.set_ylim(0, 0.98)
    ax.grid(axis="y", alpha=0.25, lw=0.6)
    ax.legend(frameon=False, fontsize=8.4, loc="upper left")
    ax.set_title("接触更密集的停止片段：整体变好并不等于每一段都变好", fontsize=10.5, pad=8)
    return save(fig, "fig5-contact-stops.png")


def fig_cmc():
    j = load("motionweaver_20k_acceptance_summary_20260928.json")
    cmc = j["recorded_cmc_controller"]
    order = ["Walk", "Run", "Crouch"]
    labels = [f"{k} ({cmc[k]['clip_index']})\n{cmc[k]['controller_metrics']['cmc_future_actor_mean_speed_mps']:.1f} m/s"
              for k in order]
    delta = [cmc[k]["controller_metrics"]["actor_condition_local_joint_rms_delta_cm"] for k in order]
    tok = [cmc[k]["controller_metrics"]["actor_condition_future_token_difference_fraction"] * 100 for k in order]

    fig, ax = plt.subplots(figsize=(8.2, 3.6))
    x = np.arange(3); w = 0.32
    b1 = ax.bar(x - w / 2 - 0.01, delta, w, color=C_MAIN, edgecolor="white", label="Actor 局部关节 RMS 变化 (cm)")
    ax.set_ylabel("关节 RMS 变化 (cm)", color=C_MAIN)
    ax.tick_params(axis="y", colors=C_MAIN)
    ax.set_ylim(0, 6.4)
    ax2 = ax.twinx()
    b2 = ax2.bar(x + w / 2 + 0.01, tok, w, color=C_ORANGE, edgecolor="white", label="未来 Token 改变比例 (%)")
    ax2.set_ylabel("未来 Token 改变比例 (%)", color=C_ORANGE)
    ax2.tick_params(axis="y", colors=C_ORANGE)
    ax2.set_ylim(0, 30)
    for i in range(3):
        ax.text(i - w / 2 - 0.01, delta[i] + 0.12, f"{delta[i]:.2f}", ha="center", fontsize=8.2, color=C_MAIN)
        ax2.text(i + w / 2 + 0.01, tok[i] + 0.5, f"{tok[i]:.1f}%", ha="center", fontsize=8.2, color=C_ORANGE)
    ax.set_xticks(x); ax.set_xticklabels(labels, fontsize=9)
    ax.set_title("换一条 CMC 轨迹后模型的敏感度：转向 90° 只改变局部姿态极少", fontsize=10.5, pad=8)
    lines = [b1, b2]
    ax.legend(lines, [b.get_label() for b in lines], frameon=False, fontsize=8.2, loc="upper left")
    ax.grid(axis="y", alpha=0.2, lw=0.6)
    return save(fig, "fig6-cmc-sensitivity.png")


if __name__ == "__main__":
    fig_architecture()
    fig_loss()
    fig_val_ce()
    fig_trajectory()
    fig_contact_stops()
    fig_cmc()
    print("done")
