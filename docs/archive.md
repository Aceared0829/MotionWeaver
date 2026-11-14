# 文档归档

本文件登记已从 `docs/` 移除、但内容仍有追溯价值的历史文档。
被移除不等于被否定：它们记录的是当时的方案、约束和尚未实施的计划，
在后续工作中被更新的文档取代。原文仍可从提交历史完整取回：

```powershell
git show <该文件最后一次修改的提交>:<路径>
```

## 已归档

- `docs/architecture-and-repository-requirements.md` —— AIAnimationSystem 架构优化与仓库重组需求文档（2026-09-10）
- `docs/reference-guided-training-run-20260924.md` —— Root 路径与硬参考姿态：首轮条件训练记录
- `docs/soma-semantic-feature-pipeline.md` —— 骨骼语义实验历史（已退役）
- `docs/plan-a-handoff-prompt.md` —— 方案 A（关节位置损失）的一次性 agent 执行提示词（2026-11-14 移除）
- `docs/soma-processing-throughput-plan.md` —— SOMA 处理吞吐优化计划（随 refpose 主线封盘移除）
- `docs/soma-training-performance-analysis.md` —— 旧训练性能分析（随 refpose 主线封盘移除）
- `docs/soma-tokenizer-benchmark.md` —— 旧分词器四臂评测（随 refpose 主线封盘移除）
- `docs/soma-tokenizer-codebook-collapse.md` —— 码本塌缩记录（随 refpose 主线封盘移除）
- `docs/soma-tokenizer-codebook-collapse-diagnosis.md` —— 码本塌缩诊断（随 refpose 主线封盘移除）
- `docs/soma-tokenizer-codebook-init-ab.md` —— 码本初始化 A/B（随 refpose 主线封盘移除）
- `docs/soma-tokenizer-joint-error-options.md` —— 关节误差与方案 A 完整实验记录（随 refpose 主线封盘移除）

## 取代关系

| 归档文档 | 现由何处承接 |
| --- | --- |
| `architecture-and-repository-requirements.md` | 仓库结构见 [repository-layout.md](repository-layout.md)；模型路线见 [motionweaver-architecture.md](motionweaver-architecture.md) |
| `reference-guided-training-run-20260924.md` | 数据契约见 [reference-guided-data-contract-v1.md](reference-guided-data-contract-v1.md)；训练结果见 [实验记录](experiments/README.md) |
| `soma-semantic-feature-pipeline.md` | 该实验已退役，现行范围见 [soma-motion-processing.md](soma-motion-processing.md) |
| `plan-a-handoff-prompt.md` | 方案 A 已完成，结论为无有效收益，见 [refpose v3 训练与评测](refpose-v3-training-evaluation.md)；代码整理见 [refactor-plan.md](refactor-plan.md) |
| `soma-processing-throughput-plan.md` | 现行吞吐记录见 [2026-10-18 SOMA 处理速度](experiments/2026-10-18-soma-processing-speed.md) |
| `soma-training-performance-analysis.md` | 现行主线结果见 [refpose v3 训练与评测](refpose-v3-training-evaluation.md) |
| `soma-tokenizer-*.md` | 旧分词器路线已停止推进；结论摘要见 [refpose v3 训练与评测](refpose-v3-training-evaluation.md) 与技术报告 §9/§11 |

新增归档时在"已归档"末尾追加一行，并在"取代关系"里写清由谁承接；
不要让读者只能靠猜。
