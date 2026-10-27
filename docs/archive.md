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

## 取代关系

| 归档文档 | 现由何处承接 |
| --- | --- |
| `architecture-and-repository-requirements.md` | 仓库结构见 [repository-layout.md](repository-layout.md)；模型路线见 [motionweaver-architecture.md](motionweaver-architecture.md) |
| `reference-guided-training-run-20260924.md` | 数据契约见 [reference-guided-data-contract-v1.md](reference-guided-data-contract-v1.md)；训练结果见 [实验记录](experiments/README.md) |
| `soma-semantic-feature-pipeline.md` | 该实验已退役，现行范围见 [soma-motion-processing.md](soma-motion-processing.md) |

新增归档时在"已归档"末尾追加一行，并在"取代关系"里写清由谁承接；
不要让读者只能靠猜。
