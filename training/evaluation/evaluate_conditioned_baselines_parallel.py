"""evaluate_conditioned_baselines 的 32 进程并行版。

原实现把「读窗口 → 算基线 → 打分」全部塞在一个串行 for 循环里，
在 32 核机器上只用到 1 个核（实测累计 CPU 9429 秒、并行度 1/32）。

这里把最外层按窗口分片：每个 worker 拿到 (contract_folder, rows 切片)，
自己读窗口、算 8 种组合（4 场景 × 2 基线）、返回 samples 列表；
主进程只负责合并与聚合。契约、骨架、脚索引等只读元数据在 worker 内重建。

用法与 evaluate_conditioned_baselines 一致，额外支持 --workers：

    python -m training.evaluation.evaluate_conditioned_baselines_parallel ^
        --contract <契约目录> --output <输出目录> --split test --per-clip 0 --workers 32
"""

import argparse
import hashlib
import json
import multiprocessing as mp
import time
from collections import defaultdict
from pathlib import Path

from data.runtime.conditioned_motion import CONTRACT_ID, load_window, with_references, read_json
from training.evaluation.evaluate_conditioned_baselines import (
    aggregate,
    predict,
    score,
    select_windows,
)


def _prepare(folder, contract):
    """只读元数据：骨架名、脚索引、场景偏移。worker 内重复构造，成本可忽略。"""
    source = Path(contract["source_dataset"])
    skeleton = read_json(source / "skeleton.json")
    names = [bone["name"] for bone in skeleton["bones"]]
    foot_indices = [names.index(skeleton["roles"][role]) for role in
                    ("left_foot", "left_toe", "right_foot", "right_toe")]
    window = contract["window"]["future_frames"]
    scenarios = {
        "no_reference": (),
        "middle_reference": (window // 2,),
        "end_reference": (window - 1,),
        "middle_and_end": (window // 2, window - 1),
    }
    return foot_indices, scenarios


def _worker(task):
    """处理一批窗口，返回 samples 明细。

    task = (folder_str, contract, rows, io_pause_ms)
    io_pause_ms > 0 时，每个窗口处理完短暂休眠，给磁盘 I/O 队列留出余量，
    避免 32 个进程同时读 NPZ 把盘压到吞吐量反而下降。
    """
    folder_str, contract, rows, io_pause_ms = task
    folder = Path(folder_str)
    foot_indices, scenarios = _prepare(folder, contract)
    pause = max(0, io_pause_ms) / 1000.0
    out = []
    for row in rows:
        base = load_window(folder, row, contract=contract)
        for scenario, offsets in scenarios.items():
            sample = with_references(base, offsets)
            for method in ("hold", "linear_reference"):
                predicted_pose, predicted_rotations = predict(sample["input"], method)
                out.append({
                    "clip_index": row["clip_index"],
                    "asset": base["metadata"]["asset"],
                    "category": base["metadata"]["category"],
                    "start": row["start"],
                    "scenario": scenario,
                    "baseline": method,
                    **score(sample, predicted_pose, predicted_rotations,
                            foot_indices, contract["fps"]),
                })
        if pause:
            time.sleep(pause)
    return out


def _chunks(rows, workers):
    """按窗口切块；块数取 workers 的 4 倍以减少尾部空转。"""
    count = max(workers * 4, 1)
    size = max(1, (len(rows) + count - 1) // count)
    return [rows[i:i + size] for i in range(0, len(rows), size)]


def evaluate(contract_folder, output, *, split="test", per_clip=0, workers=16,
             io_pause_ms=20):
    folder, output = Path(contract_folder).resolve(), Path(output).resolve()
    contract_path = folder / "contract.json"
    contract = read_json(contract_path)
    if contract.get("contract_id") != CONTRACT_ID:
        raise ValueError("条件动作契约不匹配")
    source = Path(contract["source_dataset"])
    if hashlib.sha256((source / "dataset.json").read_bytes()).hexdigest() != contract["source_manifest_sha256"]:
        raise ValueError("来源数据清单已改变")
    if hashlib.sha256((source / "skeleton.json").read_bytes()).hexdigest() != contract["skeleton_sha256"]:
        raise ValueError("骨架已改变")

    rows = select_windows(folder / "windows.jsonl", contract, split, per_clip)
    if output.exists():
        raise FileExistsError(f"目标目录已存在：{output}")

    tasks = [(str(folder), contract, chunk, io_pause_ms)
             for chunk in _chunks(rows, workers)]
    print(json.dumps({"windows": len(rows), "workers": workers,
                      "chunks": len(tasks), "io_pause_ms": io_pause_ms},
                     ensure_ascii=False), flush=True)

    samples = []
    done = 0
    # spawn 而非 fork：Windows 上 fork 不可用，且 worker 内会重建只读元数据
    context = mp.get_context("spawn")
    with context.Pool(processes=workers) as pool:
        for result in pool.imap_unordered(_worker, tasks):
            samples.extend(result)
            done += 1
            print(json.dumps({"chunk": done, "total_chunks": len(tasks),
                              "samples": len(samples)}, ensure_ascii=False), flush=True)

    groups, category_groups = defaultdict(list), defaultdict(list)
    for sample in samples:
        key = f"{sample['scenario']}/{sample['baseline']}"
        groups[key].append(sample)
        category_groups[f"{sample['category']}/{key}"].append(sample)

    report = {
        "schema_version": 1,
        "evaluation_type": "non_learning_reference_guided_pose_baselines",
        "contract": str(contract_path),
        "contract_sha256": hashlib.sha256(contract_path.read_bytes()).hexdigest(),
        "split": split,
        "selection": "all_windows" if per_clip == 0 else "middle_window_per_clip",
        "oracle_root_condition": True,
        "root_path_error": "zero_by_given_condition_not_a_model_metric",
        "metrics_scope": "per_clip_equal_weight_non_anchor_primary",
        "workers": workers,
        "aggregate": {key: aggregate(value) for key, value in sorted(groups.items())},
        "by_category": {key: aggregate(value) for key, value in sorted(category_groups.items())},
        "samples": samples,
        "limitations": [
            "Root 轨迹取自原动作，是理想控制输入；不代表玩家或 CMC 轨迹误差。",
            "硬参考直接来自目标帧，只有被选中的姿态帧作为条件公开。",
            "保持与插值只是非学习基线，不保证骨长、IK 或实际游戏可播放质量。",
            "来源分区的非 Traversal 语义变体可能跨分区，不能宣称完全独立泛化。",
        ],
    }
    output.mkdir(parents=True, exist_ok=False)
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--split", choices=("validation", "test"), default="test")
    parser.add_argument("--per-clip", type=int, choices=(0, 1), default=0)
    parser.add_argument("--workers", type=int, default=16,
                        help="并发进程数；超过磁盘 I/O 能力反而变慢")
    parser.add_argument("--io-pause-ms", type=int, default=20,
                        help="每处理完一个窗口休眠的毫秒数，用于给磁盘 I/O 留余量")
    args = parser.parse_args()
    report = evaluate(args.contract, args.output, split=args.split,
                      per_clip=args.per_clip, workers=args.workers,
                      io_pause_ms=args.io_pause_ms)
    print(json.dumps(report["aggregate"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
