"""把模型四档评测与非学习基线合并成一份可直接对表的对照报告。

为什么要单独一步：两份报告的选窗口径相同（middle_window_per_clip、
per_clip_equal_weight），但结构不同——模型报告按 scenario 平铺，
基线报告把四场景 × 两基线塞进 aggregate 的 "scenario/baseline" 键里。
人工对表容易看错行，所以在这里一次性对齐、算倍数、写 markdown。

用法：
  .venv/bin/python tools/merge_evaluation_report.py \
      --model <model.json> --baselines <baselines/report.json> \
      --output <comparison.json> --markdown <comparison.md>
"""

import argparse
import json
from pathlib import Path

SCENARIOS = ("no_reference", "middle_reference", "end_reference", "middle_and_end")
LABELS = {
    "no_reference": "无参考帧",
    "middle_reference": "中间 1 帧参考",
    "end_reference": "末帧参考",
    "middle_and_end": "中间+末帧参考",
}


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def merge(model_report, baseline_report):
    rows = []
    for scenario in SCENARIOS:
        mine = model_report["scenarios"][scenario]
        base = {name: baseline_report["aggregate"].get(f"{scenario}/{name}", {})
                for name in ("hold", "linear_reference")}
        rmse = mine["non_anchor_pose_rmse_cm"]
        entry = {
            "scenario": scenario,
            "label": LABELS[scenario],
            "model_rmse_cm": rmse,
            "model_median_rmse_cm": mine.get("median_non_anchor_pose_rmse_cm"),
            "model_worst_clip_rmse_cm": mine.get("worst_clip_rmse_cm"),
            "model_foot_slip_ratio": mine.get("foot_slip_ratio_mean"),
            "model_contact_foot_speed_mps": mine.get("contact_foot_speed_mps"),
            "clips": mine.get("clips"),
        }
        for name, values in base.items():
            value = values.get("non_anchor_pose_rmse_cm")
            entry[f"{name}_rmse_cm"] = value
            # 倍数 = 基线 / 模型；越大说明模型越省。基线或模型缺失时留 None，
            # 不要写成 0 或 NaN——下游要能区分「没测」和「测出 0」。
            entry[f"gain_vs_{name}"] = (value / rmse) if value and rmse else None
        rows.append(entry)
    return rows


def to_markdown(rows, model_report, baseline_report):
    lines = ["# refpose 评测对照", ""]
    lines.append(f"- 契约：`{model_report.get('release')}`")
    lines.append(f"- 权重：`{model_report.get('checkpoint')}`")
    lines.append(f"- 分区：{model_report.get('split')}，"
                 f"每条 clip 取中心窗口，共 {rows[0]['clips']} 条")
    lines.append("- 指标：非锚点关节逐关节欧氏距离 RMSE（cm），"
                 "每条 clip 先求值再等权平均")
    lines.append("")
    lines.append("| 参考档位 | 模型 | Lerp 插值 | Hold | 相对 Lerp | 脚滑倍数 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for row in rows:
        def cell(key, digits=2):
            value = row.get(key)
            return "—" if value is None else f"{value:.{digits}f}"
        gain = row.get("gain_vs_linear_reference")
        gain_text = "—" if gain is None else f"{gain:.2f}×"
        slip = row.get("model_foot_slip_ratio")
        slip_text = "—" if slip is None else f"{slip:.2f}×"
        lines.append(f"| {row['label']} | **{cell('model_rmse_cm')}** | "
                     f"{cell('linear_reference_rmse_cm')} | {cell('hold_rmse_cm')} | "
                     f"{gain_text} | {slip_text} |")
    lines.append("")
    lines.append("## 口径说明")
    lines.append("")
    lines.append("- Root 轨迹取自原动作（oracle），不代表玩家或 CMC 的轨迹误差。")
    lines.append("- 硬参考帧直接来自目标帧，只有被选中的姿态帧作为条件公开。")
    lines.append("- 脚滑倍数 = 接触期预测脚速 ÷ 源动作同期脚速；1.0 表示与源动作同速。")
    lines.append("- 相对 Lerp 的倍数越大越好；Lerp 是用两侧已知帧线性插值得到的非学习基线。")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", required=True)
    parser.add_argument("--baselines", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--markdown", default=None)
    args = parser.parse_args()

    model_report = load(args.model)
    baseline_report = load(args.baselines)
    rows = merge(model_report, baseline_report)

    report = {
        "schema_version": 1,
        "model": model_report,
        "baselines": {key: value for key, value in baseline_report.items() if key != "samples"},
        "comparison": rows,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    text = to_markdown(rows, model_report, baseline_report)
    if args.markdown:
        Path(args.markdown).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
