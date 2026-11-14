# 状态：实验工具，冻结新增

本目录是 UE Schema、导出包与参考姿态的离线测量仪器，不是产品推理路径。

- 不再新增功能；只修正确性缺陷和因上游契约变化导致的失效。
- 回归测试统一放在 `tests/`，本目录不再存放 `test_*.py`。
- 实验结论与操作记录放在 `docs/experiments/`；本目录只保留代码与运行所需静态资源。
- 新正式指标以 `benchmark/` 为准，不复用这里的临时口径。

入口示例：

```bash
.venv/bin/python -m inference.profiling.evaluate_hard_constraints --help
.venv/bin/python -m inference.profiling.profile_unreal_inference --help
```
