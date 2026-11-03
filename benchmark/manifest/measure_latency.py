"""量 ORT CPU 的延迟与多角色扩展，产出 latency.json。

**三种量法，之前只做了第一种，结论是错的。**

  A. 单 session 连跑 N 次       —— 这是「一个角色跑 N 帧」，不是 N 个角色
  B. N 个独立 session 串行     —— 这才是 N 个角色的串行代价
  C. N 个独立 session 真并发   —— 这才是 N 个角色同时存在时的实际代价

早先只测了 A，却写成「多角色性能」，还断言「顺序执行是保守估计，真并发只会更快」。
2026-11-03 实测推翻：本机默认线程池下，8 个独立 session 真并发要 163.84 ms，
是 8 角色的 60fps 预算（16.67 ms）的 10 倍，而且比串行（76.29 ms）还慢 2 倍。
原因：每个 session 自带一个 intra_op 线程池（本机 32 逻辑核），
N 个 session 就是 N× 线程在抢核，调度开销主导。

只有把每 session 的 intra_op 设成 1，并发才真的更快（8 角色 5.11 ms）。
这个开关是阶段 2/3 的关键参数，必须写进记录，不能靠猜。

**注意**：以上都是 CPU EP 的数字。UE 内走 DML，调研文档已写明 DML 强制
ORT_SEQUENTIAL 且禁用 memory pattern，多 Run 并发恰是 DML 最差场景。
这里的加速比**不可移植**到 UE。
"""
import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import onnxruntime as ort


def _real_feeds(manifest_path, session):
    """优先用真实窗口；拿不到就退回噪声。

    实测两者延迟差异在 ±3% 内且方向不稳定（真实 0.91 / 噪声 0.93 与
    真实 0.99 / 噪声 1.07 都测到过），所以选真实数据不是为了性能差异，
    而是它顺带证明「这些张量模型真的吃得进去」。
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
    # 与 export_onnx 一致：支持环境变量覆盖，并检查路径有效性后再走真实窗口，
    # 否则换机器时会在 sample_feeds 里抛 FileNotFoundError，而不是退回噪声。
    import os

    release = Path(os.environ.get("MW_RELEASE_DIR") or manifest["artifacts"]["release_dir"])
    mmap_root = Path(os.environ.get("MW_MMAP_DIR") or manifest["artifacts"]["mmap_dir"])
    if not (release / "contract" / "contract.json").is_file():
        return None, "noise"
    if not (mmap_root / "skeleton.json").is_file():
        return None, "noise"
    feeds = E.sample_feeds(manifest, mmap_root=mmap_root, release=release,
                           limit_per_split=8)
    # 多角色测试要用**各不相同**的窗口，不能同一个重放 N 次
    tensors = [{name: f["tensors"][name] for name in E.INPUT_NAMES} for f in feeds]
    return tensors, "real_window"


def _noise_feeds(session):
    rng = np.random.default_rng(0)
    feeds = {}
    for spec in session.get_inputs():
        feeds[spec.name] = rng.normal(size=[int(d) for d in spec.shape]).astype(np.float32)
    if "root_plan" in feeds:
        quat = feeds["root_plan"][..., 3:]
        feeds["root_plan"][..., 3:] = quat / np.linalg.norm(quat, axis=-1, keepdims=True)
    if "mask" in feeds:
        feeds["mask"][...] = 0.0
    # 契约要求：未选中帧整行乘 0。噪声回退也必须遵守，
    # 否则喂进去的是「mask 说不可见、reference 却有值」的违约输入。
    if "reference" in feeds:
        feeds["reference"][...] = 0.0
    return [feeds]


def _make_session(path, intra_op=None):
    options = ort.SessionOptions()
    if intra_op is not None:
        options.intra_op_num_threads = intra_op
    return ort.InferenceSession(str(path), sess_options=options,
                                providers=["CPUExecutionProvider"])


def _bench(fn, repeats):
    warm = max(1, repeats // 10)
    for _ in range(warm):
        fn()
    start = time.perf_counter()
    for _ in range(repeats):
        fn()
    return (time.perf_counter() - start) * 1000.0 / repeats


def measure(session_path, *, manifest_path=None, runs=300, repeats=10):
    session = _make_session(session_path)
    feed_list, source = _real_feeds(manifest_path, session)
    if feed_list is None:
        feed_list, source = _noise_feeds(session), "noise"
    feeds = feed_list[0]

    for _ in range(20):
        session.run(None, feeds)
    timings = []
    for _ in range(runs):
        start = time.perf_counter()
        session.run(None, feeds)
        timings.append((time.perf_counter() - start) * 1000.0)
    timings = np.asarray(timings)

    budget = 1000.0 / 60.0
    scaling = []
    for characters in (1, 2, 4, 8):
        n = min(characters, len(feed_list))
        window_feeds = feed_list[:n]
        pool = [_make_session(session_path) for _ in range(n)]
        thin = [_make_session(session_path, intra_op=1) for _ in range(n)]

        # 用默认参数把 n / pool / thin 绑死，避免闭包捕获循环变量
        # （虽然本函数在循环内立即调用，但延后绑定是实打实的坑）。
        def _single_repeat(session=session, feeds=feeds, n=n):
            for _ in range(n):
                session.run(None, feeds)

        def _serial(sessions=pool, windows=window_feeds):
            for s, w in zip(sessions, windows):
                s.run(None, w)

        def _concurrent(sessions=pool, windows=window_feeds, n=n):
            with ThreadPoolExecutor(max_workers=n) as executor:
                list(executor.map(lambda pair: pair[0].run(None, pair[1]),
                                  list(zip(sessions, windows))))

        def _thin_serial(sessions=thin, windows=window_feeds):
            for s, w in zip(sessions, windows):
                s.run(None, w)

        def _thin_concurrent(sessions=thin, windows=window_feeds, n=n):
            with ThreadPoolExecutor(max_workers=n) as executor:
                list(executor.map(lambda pair: pair[0].run(None, pair[1]),
                                  list(zip(sessions, windows))))

        single = _bench(_single_repeat, repeats)
        serial = _bench(_serial, repeats)
        concurrent = _bench(_concurrent, max(2, repeats // 2))
        thin_serial = _bench(_thin_serial, repeats)
        thin_concurrent = _bench(_thin_concurrent, max(2, repeats // 2))

        scaling.append({
            "characters": n,
            "single_session_repeat_ms": single,
            "multi_session_serial_ms": serial,
            "multi_session_concurrent_ms": concurrent,
            "thin_session_serial_ms": thin_serial,
            "thin_session_concurrent_ms": thin_concurrent,
            "concurrent_fits_60fps": concurrent < budget,
            "thin_concurrent_fits_60fps": thin_concurrent < budget,
        })

    return {
        "format": "refpose_latency_v1",
        "onnx": str(session_path),
        "providers": session.get_providers(),
        "input_source": source,
        "distinct_windows": len(feed_list),
        "runs": runs,
        "single_stream": {
            "p50_ms": float(np.percentile(timings, 50)),
            "p95_ms": float(np.percentile(timings, 95)),
            "mean_ms": float(timings.mean()),
            "max_ms": float(timings.max()),
        },
        "budget_60fps_ms": budget,
        "scaling": scaling,
        "method_note": "single_session_repeat 是「一个角色跑 N 帧」；"
                       "要评估 N 个角色必须看 multi_session_* 三项。",
        "portability_warning": "以上为 CPU EP。UE 内走 DML，调研文档已写明 DML 强制 "
                               "ORT_SEQUENTIAL 且禁用 memory pattern，多 Run 并发是 DML 最差场景，"
                               "此处加速比不可移植。",
        "environment": {
            "onnxruntime": ort.__version__,
            "cpu_count": __import__("os").cpu_count(),
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--onnx", required=True)
    parser.add_argument("--manifest", default=None, help="给了就用真实窗口测，否则用噪声")
    parser.add_argument("--output", required=True)
    parser.add_argument("--runs", type=int, default=300)
    parser.add_argument("--repeats", type=int, default=10)
    args = parser.parse_args()
    record = measure(args.onnx, manifest_path=args.manifest, runs=args.runs,
                     repeats=args.repeats)
    Path(args.output).write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "single_stream": record["single_stream"],
        "scaling": [{k: (round(v, 2) if isinstance(v, float) else v)
                     for k, v in row.items()} for row in record["scaling"]],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
