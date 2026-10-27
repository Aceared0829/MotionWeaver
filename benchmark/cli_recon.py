"""命令行入口：单独跑重建评测，把结果写到 JSON。

库函数在 `benchmark.evaluate_recon`，本文件只负责参数解析与输出。
拆开的理由：`benchmark.run` 需要 import 库函数而不触发 argparse，
混在一起会让 import 也有副作用（此前 argparse 在模块顶层跑过一次）。
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, r"D:\AILocomotonSystem\MotionWeaver")

from benchmark.evaluate_recon import (
    DEFAULT_ROWS,
    RAW_ERROR_LIMIT,
    evaluate_all,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--views",
                        default=r"D:\MotionWeaverData\soma_train_v1\full-v1-f32")
    parser.add_argument("--scenes", nargs="+", default=["B", "C"])
    parser.add_argument("--clips", type=int, default=8,
                        help="每个长度档抽多少条；16 时单臂 96 次重建")
    parser.add_argument("--rows", nargs="+", type=int, default=list(DEFAULT_ROWS),
                        help="要评估的 token 档位，默认 6 8 10 12 14 16")
    parser.add_argument("--output")
    args = parser.parse_args()

    result = evaluate_all(
        args.checkpoint, rows=tuple(args.rows), scenes=args.scenes,
        clips=args.clips, views=args.views)

    print(f"自校验门槛 {RAW_ERROR_LIMIT} m；"
          f"本次最大 {result['max_self_check_m']:.6f} m")
    print(f"\n{'token':>5} {'frames':>7} {'feature L1':>12} "
          f"{'RMSE cm':>10} {'p95 cm':>10} {'max cm':>10}")
    print("-" * 60)
    for row in result["table"]:
        print(f"{row['tokens']:>5} {row['frames']:>7} "
              f"{row['feature_l1']:>12.4f} {row['joint_rmse_cm']:>10.2f} "
              f"{row['joint_p95_cm']:>10.2f} {row['joint_max_cm']:>10.2f}")
    print("-" * 60)
    print(f"{'平均':>5} {'':>7} {result['mean_feature_l1']:>12.4f} "
          f"{result['mean_joint_rmse_cm']:>10.2f}")

    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2,
                                     default=str), encoding="utf-8")
        print(f"\n已写出 {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
