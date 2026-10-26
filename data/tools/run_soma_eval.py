"""训练视图 → 契约 → in-betweening 评测 → 汇总，一条命令跑完。

用法（构建完成后）：

    .\\.venv\\Scripts\\python.exe -m data.tools.run_soma_eval --dataset <训练视图> --output <报告目录>

默认行为：
1. 在 `<训练视图>/contracts/` 生成两份契约（fps120 / fps30），各带 windows.jsonl。
2. 对每个帧率组、每个 split（默认 test）跑 `evaluate_conditioned_baselines`。
3. 把四场景（no_reference / middle_reference / end_reference / middle_and_end）
   与两个基线（hold / linear_reference）的结果汇总成一份 `summary.json` 与 `summary.md`。

设计约束：
- 后续步骤失败时保留已完成产物，不删除目录，便于排查。
- 汇总明确标注 `oracle_root_condition`、split、样本量，禁止把离线结果当在线能力。
"""

import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from data.tools.build_soma_contract import build_contract


def sha256(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_evaluation(contract_dir, output_dir, split, per_clip):
    """调用现有评测器；返回解析后的 report.json。"""
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise FileExistsError(f"评测输出目录已存在：{output_dir}")
    command = [sys.executable, "-m", "training.evaluation.evaluate_conditioned_baselines",
               "--contract", str(contract_dir), "--output", str(output_dir),
               "--split", split, "--per-clip", str(per_clip)]
    print(json.dumps({"run": command}), flush=True)
    # 子进程输出显式按 UTF-8 解码：Windows 默认 GBK，遇到中文日志会抛 UnicodeDecodeError。
    environment = dict(os.environ, PYTHONIOENCODING="utf-8")
    result = subprocess.run(command, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", env=environment)
    if result.returncode != 0:
        print(result.stdout[-4000:], file=sys.stderr)
        print(result.stderr[-4000:], file=sys.stderr)
        raise RuntimeError(f"评测失败（{contract_dir.name}/{split}），返回码 {result.returncode}")
    return json.loads((output_dir / "report.json").read_text(encoding="utf-8"))


def summarize(reports, dataset_root):
    """把各组、各 split 的结果整理成可比表格。"""
    rows = []
    for group, by_split in reports.items():
        for split, report in by_split.items():
            for key, value in report.get("aggregate", {}).items():
                scenario, baseline = key.split("/", 1)
                rows.append({
                    "fps_group": group, "split": split,
                    "scenario": scenario, "baseline": baseline,
                    "non_anchor_pose_rmse_cm": value.get("non_anchor_pose_rmse_cm"),
                    "all_pose_rmse_cm": value.get("all_pose_rmse_cm"),
                    "anchor_pose_rmse_cm": value.get("anchor_pose_rmse_cm"),
                    "rotation_mean_deg": value.get("non_anchor_rotation_mean_deg"),
                    "velocity_error_cm_per_frame": value.get("velocity_error_cm_per_frame"),
                    "contact_foot_speed_mps": value.get("contact_foot_speed_mps"),
                    "source_contact_foot_speed_mps": value.get("source_contact_foot_speed_mps"),
                    "clips": value.get("clips"), "windows": value.get("windows"),
                    "contact_pairs": value.get("contact_pairs"),
                })

    scene_order = ["no_reference", "middle_reference", "end_reference", "middle_and_end"]
    baseline_order = ["hold", "linear_reference"]
    rows.sort(key=lambda r: (r["fps_group"], r["split"],
                             scene_order.index(r["scenario"]) if r["scenario"] in scene_order else 99,
                             baseline_order.index(r["baseline"]) if r["baseline"] in baseline_order else 99))

    # 关键对照：同一场景下 Lerp 相对 hold 的降幅
    comparison = []
    grouped = defaultdict(dict)
    for row in rows:
        grouped[(row["fps_group"], row["split"], row["scenario"])][row["baseline"]] = row
    for (group, split, scenario), variants in sorted(grouped.items()):
        hold, lerp = variants.get("hold"), variants.get("linear_reference")
        if not hold or not lerp:
            continue
        hold_rmse, lerp_rmse = hold["non_anchor_pose_rmse_cm"], lerp["non_anchor_pose_rmse_cm"]
        change = None
        if hold_rmse and lerp_rmse:
            change = round((lerp_rmse - hold_rmse) / hold_rmse * 100, 2)
        comparison.append({
            "fps_group": group, "split": split, "scenario": scenario,
            "hold_rmse_cm": hold_rmse, "lerp_rmse_cm": lerp_rmse,
            "lerp_change_vs_hold_pct": change,
            "lerp_foot_speed_mps": lerp["contact_foot_speed_mps"],
            "source_foot_speed_mps": lerp["source_contact_foot_speed_mps"],
            "foot_slip_ratio": (round(lerp["contact_foot_speed_mps"]
                                      / lerp["source_contact_foot_speed_mps"], 2)
                                if lerp.get("contact_foot_speed_mps") and
                                lerp.get("source_contact_foot_speed_mps") else None),
            "windows": lerp["windows"],
        })

    return {"dataset": str(dataset_root),
            "contract_manifest_sha256": sha256(Path(dataset_root) / "dataset.json"),
            "note": "离线基准：Root 使用真值，两端参考已知；不代表在线生成能力。",
            "rows": rows, "comparison": comparison}


def render_markdown(summary):
    lines = ["# SOMA in-betweening 基准报告", "",
             f"- 数据集：`{summary['dataset']}`",
             f"- dataset.json SHA256：`{summary['contract_manifest_sha256']}`",
             f"- 说明：{summary['note']}",
             "", "## 场景 × 基线（关节 RMSE，cm）", "",
             "| fps 组 | split | 场景 | hold | Lerp | Lerp 相对 hold | 脚滑倍数 | 窗口数 |",
             "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    for row in summary["comparison"]:
        lines.append("| {fps_group} | {split} | {scenario} | {hold_rmse_cm} | {lerp_rmse_cm} | "
                     "{lerp_change_vs_hold_pct}% | {foot_slip_ratio}× | {windows} |".format(**row))
    lines += ["", "## 全部结果", "",
              "| fps 组 | split | 场景 | 基线 | RMSE cm | 旋转 deg | 速度 cm/帧 | 脚速 m/s |",
              "| --- | --- | --- | --- | ---: | ---: | ---: | ---: |"]
    for row in summary["rows"]:
        lines.append("| {fps_group} | {split} | {scenario} | {baseline} | "
                     "{non_anchor_pose_rmse_cm} | {rotation_mean_deg} | "
                     "{velocity_error_cm_per_frame} | {contact_foot_speed_mps} |".format(**row))
    return "\n".join(lines) + "\n"


def run(dataset_root, output_root, *, split="test", per_clip=0, skeleton=None):
    dataset_root = Path(dataset_root).resolve()
    output_root = Path(output_root).resolve()
    if not (dataset_root / "dataset.json").is_file():
        raise FileNotFoundError(f"缺少 dataset.json：{dataset_root}")

    skeleton_path = Path(skeleton or "data/skeletons/soma_uniform_v1.json").resolve()
    skeleton = json.loads(skeleton_path.read_text(encoding="utf-8"))
    roles = dict(skeleton["roles"])
    roles["__names__"] = skeleton["skeleton"]["names"]

    contracts_root = dataset_root / "contracts"
    print(json.dumps({"stage": "contracts", "output": str(contracts_root)}), flush=True)
    contract_result = build_contract(dataset_root, contracts_root, roles,
                                     skeleton_path=str(skeleton_path))

    output_root.mkdir(parents=True, exist_ok=True)
    reports, failures = {}, []
    for group in [entry["group"] for entry in contract_result["contracts"]]:
        contract_dir = contracts_root / group
        reports[group] = {}
        try:
            print(json.dumps({"stage": "evaluate", "group": group, "split": split}), flush=True)
            reports[group][split] = run_evaluation(
                contract_dir, output_root / f"{group}-{split}", split, per_clip)
        except Exception as error:  # noqa: BLE001 - 单组失败不阻断另一组
            failures.append({"group": group, "split": split, "error": str(error)})

    if not reports or not any(reports.values()):
        raise RuntimeError(f"所有评测均失败：{failures}")

    summary = summarize({g: v for g, v in reports.items() if v}, dataset_root)
    summary["contract"] = contract_result
    summary["failures"] = failures
    (output_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_root / "summary.md").write_text(
        render_markdown(summary), encoding="utf-8")
    return {"output": str(output_root), "groups": list(summary["rows"] and
                                                       {r["fps_group"] for r in summary["rows"]}),
            "rows": len(summary["rows"]), "failures": failures,
            "summary_md": str(output_root / "summary.md")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, help="训练视图目录（含 dataset.json）")
    parser.add_argument("--output", required=True, help="报告输出目录")
    parser.add_argument("--split", default="test", choices=("validation", "test"))
    parser.add_argument("--per-clip", type=int, default=0, choices=(0, 1),
                        help="0=全部窗口；1=每条动作的中心窗口")
    parser.add_argument("--skeleton", default="data/skeletons/soma_uniform_v1.json")
    args = parser.parse_args()
    result = run(args.dataset, args.output, split=args.split,
                 per_clip=args.per_clip, skeleton=args.skeleton)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
