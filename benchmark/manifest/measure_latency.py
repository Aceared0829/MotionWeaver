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


def measure(session_path, *, runs=300, warmup=20):
    session = ort.InferenceSession(str(session_path), providers=["CPUExecutionProvider"])
    shapes = {spec.name: spec.shape for spec in session.get_inputs()}
    rng = np.random.default_rng(0)
    feeds = {}
    for name, shape in shapes.items():
        dims = [int(d) for d in shape]
        feeds[name] = rng.normal(size=dims).astype(np.float32)
    # root_plan 的四元数部分归一化，mask 归到 0/1——不然测的是非典型输入
    if "root_plan" in feeds:
        quat = feeds["root_plan"][..., 3:]
        feeds["root_plan"][..., 3:] = quat / np.linalg.norm(quat, axis=-1, keepdims=True)
    if "mask" in feeds:
        feeds["mask"][...] = 0.0

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
    parser.add_argument("--output", required=True)
    parser.add_argument("--runs", type=int, default=300)
    args = parser.parse_args()
    record = measure(args.onnx, runs=args.runs)
    Path(args.output).write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: record[k] for k in ("single_stream",)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
