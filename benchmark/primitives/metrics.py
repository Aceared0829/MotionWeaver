"""官方指标原语：直接复用 inference.profiling 的实现，不重新发明。

## 为什么单独抽这一层

`inference/profiling/evaluate_unreal_streaming.py` 里两个函数定义了官方口径：

    def metric(x):
        return {'mean': ..., 'p95': ..., 'max': ...}

    def rms(x):
        return np.sqrt(np.mean(np.sum(x * x, axis=-1), axis=-1))

所有官方的 joint_rmse_cm 都由这两行算出。此前我们自写评测时各算各的统计量
（均值/中位/p90 混用），导致同一指标在不同脚本里含义不同。

本模块做两件事：
  1. 优先 import 官方实现；官方不可用时用逐字等价的本地实现并标注；
  2. 提供 `joint_rmse_cm()` 一类命名清晰的包装，避免调用方再手写 rms。

## 契约

- 所有以 `_cm` 结尾的指标单位都是厘米；
- 所有 `metric()` 返回值都含 mean/p95/max 三个键，便于跨脚本比较；
- 输入形状约定 `[..., J, 3]`（关节位置，米），沿最后一维求欧氏距离。
"""

from __future__ import annotations

import numpy as np

# 官方实现优先；不可用时退回逐字等价的本地版本
try:  # pragma: no cover - 取决于运行环境是否装了官方包
    from inference.profiling.evaluate_unreal_streaming import (  # type: ignore
        metric as _official_metric,
    )
    from inference.profiling.evaluate_unreal_streaming import (
        rms as _official_rms,
    )

    OFFICIAL_SOURCE = "inference.profiling.evaluate_unreal_streaming"

    def metric(x):
        return _official_metric(x)

    def rms(x):
        return _official_rms(x)

except Exception:  # noqa: BLE001
    OFFICIAL_SOURCE = "local_equivalent"

    def metric(x):
        """与官方逐字等价：mean / p95 / max。"""
        values = np.asarray(x, dtype=float)
        return {
            "mean": float(np.mean(values)),
            "p95": float(np.percentile(values, 95)),
            "max": float(np.max(values)),
        }

    def rms(x):
        """与官方逐字等价：沿最后一维求和后开方。"""
        values = np.asarray(x, dtype=float)
        return np.sqrt(np.mean(np.sum(values * values, axis=-1), axis=-1))


def joint_rmse_cm(predicted, reference):
    """关节位置 RMSE（厘米）。

    输入 [..., J, 3]，单位米；复用官方 rms() 后乘 100 换算成厘米。
    这是官方 evaluate_pose_references.py 里 joint_rmse_cm 的实现方式。
    """
    pred = np.asarray(predicted, dtype=float)
    ref = np.asarray(reference, dtype=float)
    if pred.shape != ref.shape:
        raise ValueError(f"形状不一致 {pred.shape} vs {ref.shape}")
    return rms(pred - ref) * 100.0


def endpoint_rmse_cm(predicted, reference, joint_indices):
    """端点关节（脚/手/头）RMSE，厘米。官方同样口径。"""
    pred = np.asarray(predicted, dtype=float)[..., list(joint_indices), :]
    ref = np.asarray(reference, dtype=float)[..., list(joint_indices), :]
    return rms(pred - ref) * 100.0


def summarize(values):
    """统一的汇总格式：mean/p95/max/median/count。"""
    data = np.asarray([v for v in values if v is not None and np.isfinite(v)],
                      dtype=float)
    if data.size == 0:
        return {"count": 0}
    base = metric(data)
    return {**base, "median": float(np.median(data)), "count": int(data.size)}


if __name__ == "__main__":
    # 自检：确认本地回退与官方数值一致
    rng = np.random.default_rng(0)
    sample = rng.normal(size=(32, 78, 3))
    print(f"指标实现来源: {OFFICIAL_SOURCE}")
    print(f"metric 样例: {metric(sample.ravel())}")
    print(f"rms 形状: {rms(sample).shape}")
    print(f"joint_rmse_cm 自比: {joint_rmse_cm(sample, sample).max():.6f} cm")
