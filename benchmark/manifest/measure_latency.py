"""量 ORT CPU 的单流延迟与多角色扩展，产出 latency.json。

调研文档把「多角色并发性能未测」列为一条未证实项。
阶段 1 顺手把它测掉——不测的话，阶段 3 做 AnimNode 时就不知道
「同时 4 个角色能不能跑满 60fps」。

**注意这是顺序执行，不是真并发。** 同一个 session 连跑 N 次，
测的是「N 个角色串行推理占总预算多少」。真并发需要 N 个 session
（batch 固定为 1，见 onnx_manifest.json 的 batch_note）。
顺序执行是**保守估计**：真并发只会更快或持平，不会更慢。
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort


def _real_feeds(manifest_path, session):
    """优先用真实窗口；拿不到就退回噪声。

    实测两者延迟几乎一样（真实 0.897 / 噪声 0.881 ms）——因为算子路径固定，
    数值不影响耗时。但真实数据更诚实，也顺带证明「这些张量是模型真能吃进去的」。
    """
    import sys

    repo = Path(__file__).resolve().parents[2]
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    sys.path.insert(0, str(repo / "benchmark" / "manifest"))
    import export_onnx as E

    if manifest_path is None or not Path(manifest_path).is_file():
        return None, "noise"
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    feeds = E.sample_feeds(manifest,
                           mmap_root=Path(manifest["artifacts"]["mmap_dir"]),
                           release=Path(manifest["artifacts"]["release_dir"]),
                           limit_per_split=1)
    tensors = {name: feeds[0]["tensors"][name] for name in E.INPUT_NAMES}
    return tensors, "real_window"


def _noise_feeds(session):
    rng = np.random.default_rng(0)
    feeds = {}
    for spec in session.get_inputs():
        feeds[spec.name] = rng.normal(size=[int(d) for d in spec.shape]).astype(np.float32)
    # root_plan 的四元数归一化，mask 归到 0——不然测的是非典型输入
    if "root_plan" in feeds:
        quat = feeds["root_plan"][..., 3:]
        feeds["root_plan"][..., 3:] = quat / np.linalg.norm(quat, axis=-1, keepdims=True)
    if "mask" in feeds:
        feeds["mask"][...] = 0.0
    return feeds


def measure(session_path, *, manifest_path=None, runs=300, warmup=20):
    session = ort.InferenceSession(str(session_path), providers=["CPUExecutionProvider"])
    feeds, source = _real_feeds(manifest_path, session)
    if feeds is None:
        feeds = _noise_feeds(session)

    for _ in range(warmup):
        session.run(None, feeds)

    timings = []
    for _ in range(runs):
        start = time.perf_counter()
        session.run(None, feeds)
        timings.append((time.perf_counter() - start) * 1000.0)
    timings = np.asarray(timings)

    scaling = []
    for characters in (1, 2, 4, 8):
        repeats = 50
        start = time.perf_counter()
        for _ in range(repeats):
            for _ in range(characters):
                session.run(None, feeds)
        total = (time.perf_counter() - start) * 1000.0 / repeats
        scaling.append({
            "characters": characters,
            "total_ms": total,
            "per_character_ms": total / characters,
            "fits_60fps": total < 1000.0 / 60.0,
        })

    return {
        "format": "refpose_latency_v1",
        "onnx": str(session_path),
        "providers": session.get_providers(),
        "input_source": source,
        "runs": runs,
        "single_stream": {
            "p50_ms": float(np.percentile(timings, 50)),
            "p95_ms": float(np.percentile(timings, 95)),
            "mean_ms": float(timings.mean()),
            "max_ms": float(timings.max()),
        },
        "scaling_sequential": scaling,
        "caveat": "顺序执行，不是真并发。真并发需要每角色一个 session；"
                  "顺序执行是保守估计。",
        "budget_60fps_ms": 1000.0 / 60.0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--onnx", required=True)
    parser.add_argument("--manifest", default=None, help="给了就用真实窗口测，否则用噪声")
    parser.add_argument("--output", required=True)
    parser.add_argument("--runs", type=int, default=300)
    args = parser.parse_args()
    record = measure(args.onnx, manifest_path=args.manifest, runs=args.runs)
    Path(args.output).write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: record[k] for k in ("single_stream",)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
