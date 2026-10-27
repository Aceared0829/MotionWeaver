"""姿态精度与接触保真度的关系：全量 in-betweening 评测的二维切分。

本脚本回答三个问题：

  Q1 姿态精度与接触保真度是什么关系？同向还是无关？幅度是否可比？
  Q2 「窗口难不难」能否解释补间收益的大小？
  Q3 把姿态误差压到某个水平，脚滑是否会自动解决？

数据来源：
    D:/MotionWeaverData/eval/full-v1-fps120/report.json
    （585 MB，968,464 条逐窗口样本）

该报告由 training/evaluation/evaluate_conditioned_baselines_parallel.py 生成，
契约 contracts/fps120，test split 全部窗口。不含任何训练模型的结果，
因此本脚本的结论是「问题结构」层面的，不是「某方法好坏」层面的。

关键口径（读结论前必看）：

1. Root 轨迹是 oracle。oracle_root_condition: true，世界轨迹取真实动作，
   所以这里是离线上界评测，不是在线生成能力。
2. 接触标签是启发式的。contact_foot_speed_mps 只在来源接触标签连续两帧成立时
   统计，标签来自脚速/高度阈值，不是传感器真值，适合做相对比较。
3. 来源脚速极低（中位约 0.01-0.03 m/s），所以「倍数」会被放大；
   本脚本同时报绝对值和倍数，不单独用倍数下结论。
4. 窗口等权。每个窗口计一次，不按帧数加权，与评测器一致。

用法：
    ./.venv/Scripts/python.exe paper/analysis/contact_pose_analysis.py
    ./.venv/Scripts/python.exe paper/analysis/contact_pose_analysis.py --report X --out Y
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams.update({
    "font.sans-serif": ["SimHei", "Microsoft YaHei", "DejaVu Sans"],
    "font.family": "sans-serif",
    "axes.unicode_minus": False,
    "figure.dpi": 110,
    "savefig.dpi": 220,
    "axes.edgecolor": "#3c3c3c",
    "text.color": "#1a1a1a",
    "axes.labelcolor": "#1a1a1a",
    "xtick.color": "#3c3c3c",
    "ytick.color": "#3c3c3c",
})

C_MAIN = "#1F4E79"
C_ALT = "#C0504D"
C_GREY = "#8C8C8C"
C_SOFT = "#4F81BD"
C_GREEN = "#5B8C5A"

SCENARIOS = ["no_reference", "middle_reference", "end_reference", "middle_and_end"]
SCEN_LABEL = {
    "no_reference": "无参考",
    "middle_reference": "中间帧",
    "end_reference": "终点帧",
    "middle_and_end": "中间+终点",
}
CATS = ["A", "B", "C", "D"]
CAT_LABEL = {"A": "A 常规移动", "B": "B 大姿态", "C": "C 跑酷", "D": "D 开放交互"}

DEFAULT_REPORT = Path(r"D:\MotionWeaverData\eval\full-v1-fps120\report.json")


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return num / (dx * dy) if dx > 0 and dy > 0 else None


def spearman(xs, ys):
    """秩相关：对离群值稳健，用来复核 pearson 不是被少数极端窗口带偏。"""
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    return pearson(rank(xs), rank(ys))


def load_pairs(report_path):
    """读大报告，转成 (clip,start,scenario) -> {baseline: row}，只保留需要的字段。"""
    raw = json.loads(report_path.read_text(encoding="utf-8"))
    meta = {k: raw[k] for k in raw if k != "samples"}
    keep = ("category", "non_anchor_pose_rmse_cm", "all_pose_rmse_cm",
            "non_anchor_rotation_mean_deg", "velocity_error_cm_per_frame",
            "contact_foot_speed_mps", "source_contact_foot_speed_mps", "contact_pairs")
    pairs = defaultdict(dict)
    for row in raw["samples"]:
        key = (row["clip_index"], row["start"], row["scenario"])
        pairs[key][row["baseline"]] = {k: row.get(k) for k in keep}
    del raw
    return meta, pairs


def finite(v):
    return v is not None and isinstance(v, (int, float)) and math.isfinite(v)


# ---------------------------------------------------------------- 分析
def analyse(meta, pairs):
    out = {}

    # ---- Q1: 姿态误差 vs 脚滑 ----
    q1 = {}
    for scen in SCENARIOS:
        xs, ys = [], []
        for (_, _, sc), bases in pairs.items():
            if sc != scen or "linear_reference" not in bases:
                continue
            r = bases["linear_reference"]
            rmse, fs = r["non_anchor_pose_rmse_cm"], r["contact_foot_speed_mps"]
            if not (finite(rmse) and finite(fs)):
                continue
            if (r["contact_pairs"] or 0) < 3:
                continue
            xs.append(rmse)
            ys.append(fs)
        if len(xs) < 100:
            continue
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        k = len(order) // 5
        quintiles = []
        for q in range(5):
            idx = order[q * k:(q + 1) * k]
            quintiles.append({
                "q": q + 1,
                "rmse_mean": statistics.mean(xs[i] for i in idx),
                "foot_speed_mean": statistics.mean(ys[i] for i in idx),
            })
        lo = quintiles[0]["foot_speed_mean"]
        q1[scen] = {
            "n": len(xs),
            "pearson": pearson(xs, ys),
            "spearman": spearman(xs, ys),
            "quintiles": quintiles,
            "ratio_lowest_to_highest": (quintiles[-1]["foot_speed_mean"] / lo) if lo > 1e-9 else None,
        }
    out["q1_pose_vs_contact"] = q1

    # ---- Q1b: 分层内相关（排除「窗口难度」混杂）----
    # 总体 r 高，但 RMSE 与脚滑可能只因为「窗口难」而共同上升。若难度是唯一原因，
    # 在同一 RMSE 十分位内两者的相关性应接近 0。这里逐档、逐十分位重算。
    q1b = {}
    for scen in SCENARIOS:
        sub = []
        for (_, _, sc), bases in pairs.items():
            if sc != scen or "linear_reference" not in bases:
                continue
            r = bases["linear_reference"]
            rmse, fs = r["non_anchor_pose_rmse_cm"], r["contact_foot_speed_mps"]
            if not (finite(rmse) and finite(fs)) or (r["contact_pairs"] or 0) < 3:
                continue
            sub.append((rmse, fs))
        if len(sub) < 500:
            continue
        sub.sort()
        k = len(sub) // 10
        per_bin = []
        for q in range(10):
            chunk = sub[q * k:(q + 1) * k]
            if len(chunk) < 50:
                continue
            rr = pearson([c[0] for c in chunk], [c[1] for c in chunk])
            if rr is not None:
                per_bin.append({"q": q + 1, "r": rr,
                                "rmse_mean": statistics.mean(c[0] for c in chunk)})
        if per_bin:
            q1b[scen] = {
                "n": len(sub),
                "overall_r": pearson([x for x, _ in sub], [y for _, y in sub]),
                "by_decile": per_bin,
                "median_inner_r": statistics.median(b["r"] for b in per_bin),
            }
    out["q1b_stratified"] = q1b

    # ---- Q2: 难度是否决定补间收益 ----
    xs, ys = [], []
    by_cat = defaultdict(lambda: ([], []))
    for (_, _, sc), bases in pairs.items():
        if sc != "middle_and_end":
            continue
        if "hold" not in bases or "linear_reference" not in bases:
            continue
        h = bases["hold"]["non_anchor_pose_rmse_cm"]
        l = bases["linear_reference"]["non_anchor_pose_rmse_cm"]
        if not (finite(h) and finite(l)) or h <= 1e-6:
            continue
        gain = (l - h) / h * 100.0
        xs.append(h)
        ys.append(gain)
        cat = bases["hold"]["category"]
        if cat in CATS:
            by_cat[cat][0].append(h)
            by_cat[cat][1].append(gain)

    bins = [(0, 3), (3, 6), (6, 12), (12, 25), (25, 50), (50, float("inf"))]
    binned = []
    for lo, hi in bins:
        v = [y for x, y in zip(xs, ys) if lo <= x < hi]
        if v:
            binned.append({"lo": lo, "hi": (None if hi == float("inf") else hi),
                           "n": len(v), "gain_mean": statistics.mean(v)})

    q2 = {
        "n": len(xs),
        "pearson_difficulty_vs_gain": pearson(xs, ys),
        "spearman_difficulty_vs_gain": spearman(xs, ys),
        "bins": binned,
        "by_category": {},
    }
    for cat in CATS:
        hx, gy = by_cat[cat]
        if len(hx) > 100:
            q2["by_category"][cat] = {
                "n": len(hx),
                "hold_rmse_mean": statistics.mean(hx),
                "gain_mean": statistics.mean(gy),
            }
    out["q2_difficulty_vs_gain"] = q2

    # ---- Q3: 脚滑倍数 ----
    q3 = {}
    for scen in SCENARIOS:
        ratios, abs_speeds, src_speeds = [], [], []
        for (_, _, sc), bases in pairs.items():
            if sc != scen or "linear_reference" not in bases:
                continue
            r = bases["linear_reference"]
            g, s = r["contact_foot_speed_mps"], r["source_contact_foot_speed_mps"]
            if not (finite(g) and finite(s)) or s <= 1e-6:
                continue
            if (r["contact_pairs"] or 0) < 3:
                continue
            ratios.append(g / s)
            abs_speeds.append(g)
            src_speeds.append(s)
        if len(ratios) < 100:
            continue
        ratios.sort()
        q3[scen] = {
            "n": len(ratios),
            "ratio_median": ratios[len(ratios) // 2],
            "ratio_p25": ratios[len(ratios) // 4],
            "ratio_p75": ratios[len(ratios) * 3 // 4],
            "generated_median_mps": statistics.median(abs_speeds),
            "source_median_mps": statistics.median(src_speeds),
        }
    out["q3_foot_slip_ratio"] = q3

    # ---- 补充：分类别脚滑 ----
    by_cat_contact = {}
    for cat in CATS:
        g, s = [], []
        for (_, _, sc), bases in pairs.items():
            if sc != "middle_and_end" or "linear_reference" not in bases:
                continue
            r = bases["linear_reference"]
            if r["category"] != cat:
                continue
            if (r["contact_pairs"] or 0) < 3:
                continue
            if finite(r["contact_foot_speed_mps"]):
                g.append(r["contact_foot_speed_mps"])
            if finite(r["source_contact_foot_speed_mps"]):
                s.append(r["source_contact_foot_speed_mps"])
        if len(g) > 100:
            mg = statistics.median(g)
            ms = statistics.median(s) if s else None
            by_cat_contact[cat] = {
                "n": len(g),
                "generated_median_mps": mg,
                "source_median_mps": ms,
                "ratio_median": (mg / ms) if (ms and ms > 1e-6) else None,
            }
    out["by_category_contact"] = by_cat_contact
    return out


# ---------------------------------------------------------------- 图
def _collect_contact(pairs, scen):
    xs, ys = [], []
    for (_, _, sc), bases in pairs.items():
        if sc != scen or "linear_reference" not in bases:
            continue
        r = bases["linear_reference"]
        rmse, fs = r["non_anchor_pose_rmse_cm"], r["contact_foot_speed_mps"]
        if not (finite(rmse) and finite(fs)) or (r["contact_pairs"] or 0) < 3:
            continue
        xs.append(rmse)
        ys.append(fs)
    return xs, ys


def fig_pose_vs_contact(pairs, out_dir):
    fig, axes = plt.subplots(2, 2, figsize=(9.4, 7.6))
    for ax, scen in zip(axes.flat, SCENARIOS):
        xs, ys = _collect_contact(pairs, scen)
        if not xs:
            continue
        xs_a, ys_a = np.array(xs), np.array(ys)
        keep = (xs_a < np.percentile(xs_a, 99)) & (ys_a < np.percentile(ys_a, 99))
        ax.hexbin(xs_a[keep], ys_a[keep], gridsize=42, bins="log",
                  cmap="Blues", mincnt=1, linewidths=0)
        order = np.argsort(xs_a)
        k = len(order) // 5
        qx, qy = [], []
        for q in range(5):
            idx = order[q * k:(q + 1) * k]
            qx.append(float(np.mean(xs_a[idx])))
            qy.append(float(np.mean(ys_a[idx])))
        ax.plot(qx, qy, "-o", color=C_ALT, lw=2.0, ms=5, label="五分位均值")
        r = pearson(list(xs_a), list(ys_a))
        rho = spearman(xs, ys)
        ax.set_title("%s   r=%+.3f  \u03c1=%+.3f  n=%s" % (
            SCEN_LABEL[scen], r, rho, format(len(xs), ",")), fontsize=9.5)
        ax.set_xlabel("非参考帧关节 RMSE (cm)", fontsize=8.6)
        ax.set_ylabel("接触期脚速 (m/s)", fontsize=8.6)
        ax.tick_params(labelsize=8)
        ax.grid(alpha=0.18, lw=0.6)
        if scen == SCENARIOS[0]:
            ax.legend(frameon=False, fontsize=7.8, loc="upper left")
    fig.suptitle("姿态精度与接触保真度同向，但比例效应极不对称（Lerp 基线，121,058 窗口/档）",
                 fontsize=10.5, y=0.995)
    fig.tight_layout()
    path = out_dir / "figA-pose-vs-contact.png"
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("wrote", path.name)


def _collect_gain(pairs):
    xs, ys, cats = [], [], []
    for (_, _, sc), bases in pairs.items():
        if sc != "middle_and_end" or "hold" not in bases or "linear_reference" not in bases:
            continue
        h = bases["hold"]["non_anchor_pose_rmse_cm"]
        l = bases["linear_reference"]["non_anchor_pose_rmse_cm"]
        if not (finite(h) and finite(l)) or h <= 1e-6:
            continue
        xs.append(h)
        ys.append((l - h) / h * 100.0)
        cats.append(bases["hold"]["category"])
    return xs, ys, cats


def fig_difficulty_vs_gain(pairs, out_dir):
    xs, ys, cats = _collect_gain(pairs)
    fig, axes = plt.subplots(1, 2, figsize=(10.4, 3.9),
                             gridspec_kw={"width_ratios": [1.35, 1]})
    ax = axes[0]
    xs_a, ys_a = np.array(xs), np.array(ys)
    keep = xs_a < np.percentile(xs_a, 99)
    ax.hexbin(xs_a[keep], ys_a[keep], gridsize=40, bins="log", cmap="Greys",
              mincnt=1, linewidths=0)
    bins = [(0, 3), (3, 6), (6, 12), (12, 25), (25, 50), (50, 1e9)]
    bx, by, bn = [], [], []
    for lo, hi in bins:
        v = [y for x, y in zip(xs, ys) if lo <= x < hi]
        if v:
            bx.append((lo + min(hi, 80)) / 2 if hi < 1e8 else 70)
            by.append(statistics.mean(v))
            bn.append(len(v))
    ax.plot(bx, by, "-o", color=C_ALT, lw=2.2, ms=6, label="分箱均值")
    for x, y, n in zip(bx, by, bn):
        ax.annotate("%+.1f%%" % y, (x, y), textcoords="offset points", xytext=(0, -14),
                    fontsize=7.6, color=C_ALT, ha="center")
    r = pearson(xs, ys)
    ax.set_xlabel("窗口难度（Hold 基线 RMSE，cm）", fontsize=8.8)
    ax.set_ylabel("Lerp 相对 Hold 的改善 (%)", fontsize=8.8)
    ax.set_title("补间收益与难度几乎无关  r=%+.3f  n=%s" % (r, format(len(xs), ",")), fontsize=9.6)
    ax.tick_params(labelsize=8)
    ax.grid(alpha=0.18, lw=0.6)
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    # 极少数窗口的 Hold RMSE 接近 0，相对改善可达 +300%，会把主体压扁；
    # 因此只给主体留出上下界，并在标题里注明被裁掉的比例。
    lo_clip, hi_clip = -105, 15
    ax.set_ylim(lo_clip, hi_clip)
    outside = sum(1 for y in ys if y < lo_clip or y > hi_clip)
    if outside:
        ax.text(0.02, 0.96, "%.2f%% 的窗口落在纵轴范围外" % (100.0 * outside / len(ys)),
                transform=ax.transAxes, fontsize=7.2, color="#666666", va="top")

    ax = axes[1]
    data, labels = [], []
    for cat in CATS:
        v = [y for x, y, c in zip(xs, ys, cats) if c == cat]
        if len(v) > 100:
            data.append(v)
            labels.append("%s\nn=%s" % (CAT_LABEL[cat], format(len(v), ",")))
    bp = ax.boxplot(data, tick_labels=labels, showfliers=False, widths=0.55,
                    patch_artist=True, medianprops=dict(color=C_ALT, lw=1.6))
    for patch in bp["boxes"]:
        patch.set_facecolor("#DCE6F0")
        patch.set_edgecolor(C_MAIN)
    for i, v in enumerate(data):
        ax.text(i + 1, statistics.mean(v), "%+.1f%%" % statistics.mean(v),
                ha="center", va="bottom", fontsize=7.8, color=C_ALT)
    ax.set_ylabel("Lerp 相对 Hold 的改善 (%)", fontsize=8.8)
    ax.set_title("四场景的补间收益分布", fontsize=9.6)
    ax.tick_params(labelsize=7.4)
    ax.grid(axis="y", alpha=0.18, lw=0.6)
    fig.tight_layout()
    path = out_dir / "figB-difficulty-vs-gain.png"
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("wrote", path.name)


def fig_confound(pairs, out_dir):
    """总体相关 vs 分层内相关：说明高 r 有多少来自「窗口难度」这一混杂。"""
    fig, axes = plt.subplots(1, 4, figsize=(11.4, 3.5), sharey=True)
    for ax, scen in zip(axes, SCENARIOS):
        sub = []
        for (_, _, sc), bases in pairs.items():
            if sc != scen or "linear_reference" not in bases:
                continue
            r = bases["linear_reference"]
            rmse, fs = r["non_anchor_pose_rmse_cm"], r["contact_foot_speed_mps"]
            if not (finite(rmse) and finite(fs)) or (r["contact_pairs"] or 0) < 3:
                continue
            sub.append((rmse, fs))
        if len(sub) < 500:
            continue
        sub.sort()
        k = len(sub) // 10
        xs, ys = [], []
        for q in range(10):
            chunk = sub[q * k:(q + 1) * k]
            if len(chunk) < 50:
                continue
            rr = pearson([c[0] for c in chunk], [c[1] for c in chunk])
            if rr is not None:
                xs.append(q + 1)
                ys.append(rr)
        overall = pearson([x for x, _ in sub], [y for _, y in sub])
        ax.bar(xs, ys, color=C_SOFT, edgecolor="white", width=0.72)
        ax.axhline(overall, color=C_ALT, lw=1.8, ls="--",
                   label="总体 r=%.2f" % overall)
        ax.axhline(0, color="#999999", lw=0.8)
        ax.set_title(SCEN_LABEL[scen], fontsize=9.4)
        ax.set_xlabel("RMSE 十分位", fontsize=8.4)
        ax.set_xticks([1, 5, 10])
        ax.tick_params(labelsize=7.8)
        ax.grid(axis="y", alpha=0.18, lw=0.6)
        ax.legend(frameon=False, fontsize=7.4, loc="upper right")
        if scen == SCENARIOS[0]:
            ax.set_ylabel("十分位内的 r(RMSE, 脚滑)", fontsize=8.4)
    fig.suptitle("总体相关性高，分层后骤降：大部分相关来自「窗口难度」这一混杂，而非直接的姿态-接触对应",
                 fontsize=10.0, y=1.02)
    fig.tight_layout()
    path = out_dir / "figD-confound.png"
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("wrote", path.name)


def fig_slip_ratio(pairs, out_dir):
    fig, axes = plt.subplots(1, 2, figsize=(10.2, 3.7))
    ax = axes[0]
    for scen, color in zip(SCENARIOS, [C_GREY, C_SOFT, C_MAIN, C_ALT]):
        ratios = []
        for (_, _, sc), bases in pairs.items():
            if sc != scen or "linear_reference" not in bases:
                continue
            r = bases["linear_reference"]
            g, s = r["contact_foot_speed_mps"], r["source_contact_foot_speed_mps"]
            if not (finite(g) and finite(s)) or s <= 1e-6 or (r["contact_pairs"] or 0) < 3:
                continue
            ratios.append(g / s)
        if len(ratios) < 100:
            continue
        ratios.sort()
        med = ratios[len(ratios) // 2]
        p25 = ratios[len(ratios) // 4]
        p75 = ratios[len(ratios) * 3 // 4]
        ax.barh(SCEN_LABEL[scen], med, color=color, height=0.55)
        ax.plot([p25, p75], [SCEN_LABEL[scen]] * 2, color="#333333", lw=1.4)
        ax.text(p75 * 1.04, SCEN_LABEL[scen], "%.1f×" % med,
                va="center", fontsize=8.4, color="#333333")
        ax.text(-0.4, SCEN_LABEL[scen], "", va="center")
    ax.axvline(1.0, color=C_GREEN, lw=1.4, ls="--")
    ax.set_xlim(0, 42)
    ax.set_xlabel("接触期脚速 ÷ 来源动作脚速（条形为中位数，横线为四分位）", fontsize=8.6)
    ax.set_title("线插把接触脚速放大到来源的 8-15 倍", fontsize=9.6)
    ax.tick_params(labelsize=8)
    ax.grid(axis="x", alpha=0.18, lw=0.6)

    ax = axes[1]
    cats, gens, srcs = [], [], []
    for cat in CATS:
        g, s = [], []
        for (_, _, sc), bases in pairs.items():
            if sc != "middle_and_end" or "linear_reference" not in bases:
                continue
            r = bases["linear_reference"]
            if r["category"] != cat or (r["contact_pairs"] or 0) < 3:
                continue
            if finite(r["contact_foot_speed_mps"]):
                g.append(r["contact_foot_speed_mps"])
            if finite(r["source_contact_foot_speed_mps"]):
                s.append(r["source_contact_foot_speed_mps"])
        if len(g) > 100:
            cats.append(CAT_LABEL[cat])
            gens.append(statistics.median(g))
            srcs.append(statistics.median(s) if s else 0.0)
    x = np.arange(len(cats))
    w = 0.36
    ax.bar(x - w / 2, srcs, w, color=C_GREEN, label="来源动作")
    ax.bar(x + w / 2, gens, w, color=C_ALT, label="Lerp 生成")
    for i, (s, g) in enumerate(zip(srcs, gens)):
        ax.text(i + w / 2, g + 0.006, "%.2f" % g, ha="center", fontsize=7.4, color=C_ALT)
        ax.text(i - w / 2, s + 0.006, "%.3f" % s, ha="center", fontsize=7.0, color=C_GREEN)
    ax.set_xticks(x)
    ax.set_xticklabels(cats, fontsize=7.6)
    ax.set_ylabel("接触期脚速中位数 (m/s)", fontsize=8.6)
    ax.set_ylim(0, max(gens) * 1.22)
    ax.set_title("四场景：生成与来源的绝对脚速", fontsize=9.6)
    ax.tick_params(labelsize=8)
    ax.grid(axis="y", alpha=0.18, lw=0.6)
    ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    path = out_dir / "figC-foot-slip.png"
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("wrote", path.name)


def main():
    ap = argparse.ArgumentParser(description="姿态精度与接触保真度的二维切分")
    ap.add_argument("--report", default=str(DEFAULT_REPORT))
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "out"))
    args = ap.parse_args()

    report = Path(args.report)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not report.is_file():
        raise SystemExit("找不到评测报告：" + str(report))

    print("读取 %s (%.0f MB) ..." % (report, report.stat().st_size / 1024 / 1024))
    meta, pairs = load_pairs(report)
    print("  窗口数 %s  样本 %s" % (format(len(pairs), ","), format(sum(len(v) for v in pairs.values()), ",")))

    result = analyse(meta, pairs)
    result["_meta"] = {
        "report": str(report),
        "contract_sha256": meta.get("contract_sha256"),
        "split": meta.get("split"),
        "oracle_root_condition": meta.get("oracle_root_condition"),
        "metrics_scope": meta.get("metrics_scope"),
        "windows": len(pairs),
    }
    (out_dir / "analysis.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote analysis.json")

    fig_pose_vs_contact(pairs, out_dir)
    fig_difficulty_vs_gain(pairs, out_dir)
    fig_slip_ratio(pairs, out_dir)
    fig_confound(pairs, out_dir)

    print("")
    print("=== Q1 姿态误差 vs 脚滑 ===")
    for scen in SCENARIOS:
        d = result["q1_pose_vs_contact"].get(scen)
        if d:
            print("  %-8s r=%+.3f  rho=%+.3f  五分位最低/最高=%.1fx" % (
                SCEN_LABEL[scen], d["pearson"], d["spearman"], d["ratio_lowest_to_highest"]))
    print("")
    print("=== Q1b 分层内相关（排除难度混杂）===")
    for scen in SCENARIOS:
        d = result["q1b_stratified"].get(scen)
        if d:
            print("  %-8s 总体 r=%+.3f -> 十分位内中位 r=%+.3f" % (
                SCEN_LABEL[scen], d["overall_r"], d["median_inner_r"]))
    print("")
    print("=== Q2 难度 vs 补间收益 ===")
    d = result["q2_difficulty_vs_gain"]
    print("  r=%+.3f  rho=%+.3f" % (d["pearson_difficulty_vs_gain"], d["spearman_difficulty_vs_gain"]))
    for b in d["bins"]:
        hi = b["hi"] if b["hi"] else "inf"
        print("    [%2s,%4s) n=%6d  %+.1f%%" % (b["lo"], hi, b["n"], b["gain_mean"]))
    print("")
    print("=== Q3 脚滑倍数 ===")
    for scen in SCENARIOS:
        d = result["q3_foot_slip_ratio"].get(scen)
        if d:
            print("  %-8s 中位 %.1fx (p25 %.1f, p75 %.1f)  生成 %.3f vs 来源 %.3f m/s" % (
                SCEN_LABEL[scen], d["ratio_median"], d["ratio_p25"], d["ratio_p75"],
                d["generated_median_mps"], d["source_median_mps"]))
    print("")
    raise SystemExit(verify_doc(out_dir))


def verify_doc(out_dir):
    """把 README.md 里引用的关键数字与 analysis.json 对照，防止手抄漂移。

    改写文档或改动分析后跑一次；不一致时返回非零，便于在提交前发现。
    """
    import re
    doc_path = Path(__file__).resolve().parent / "README.md"
    json_path = out_dir / "analysis.json"
    if not (doc_path.is_file() and json_path.is_file()):
        print("[verify] 跳过：缺少 README.md 或 analysis.json")
        return 0
    data = json.loads(json_path.read_text(encoding="utf-8"))
    doc = doc_path.read_text(encoding="utf-8")
    scalars = []
    for scen, d in data["q1_pose_vs_contact"].items():
        scalars.append(("Q1 %s r" % scen, "%+.3f" % d["pearson"]))
        scalars.append(("Q1 %s rho" % scen, "%+.3f" % d["spearman"]))
    for scen, d in data.get("q1b_stratified", {}).items():
        scalars.append(("Q1b %s 层内" % scen, "%+.3f" % d["median_inner_r"]))
    scalars.append(("Q2 r", "%+.3f" % data["q2_difficulty_vs_gain"]["pearson_difficulty_vs_gain"]))
    for scen, d in data["q3_foot_slip_ratio"].items():
        scalars.append(("Q3 %s 倍数" % scen, "%.1f" % d["ratio_median"]))
    missing = [name for name, val in scalars
               if val.replace("-", "\u2212") not in doc and val not in doc]
    if missing:
        print("[verify] 文档缺少以下数字（或未同步）：")
        for name in missing:
            print("   -", name)
        return 1
    print("[verify] 文档数字与 analysis.json 一致（%d 项）" % len(scalars))
    return 0


if __name__ == "__main__":
    main()

