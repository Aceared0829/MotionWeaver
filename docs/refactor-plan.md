# MotionWeaver 代码整理方案

> 状态：定稿，按阶段执行。创建于 2026-11-14。
> 关联文档：[仓库目录](repository-layout.md)、[架构与路线](motionweaver-architecture.md)。

## 一、结论

当前分层（`data` / `training` / `model/MotionWeaver` / `inference` / `benchmark`）方向正确，
**不做一次性大搬迁**。整理目标是收紧四条已经松掉的边界：

1. 正式评测入口不唯一，`benchmark` 的“唯一入口”地位没有落到安装与命令上。
2. 模型定义留在训练脚本和工具脚本里，导致 `inference` 反向依赖 `training` / `tools`。
3. `data/tools` 55 个模块平铺，审核、构建、运维、发布混在一起。
4. 测试发现、打包数据和路径约定存在遗漏。

执行原则：

- **先加约束，再搬代码**：每一阶段都有独立回归门禁，可单独回滚。
- **兼容优先**：旧模块路径保留 re-export 或 CLI shim，至少保留一个版本周期。
- **不碰上游内部结构**：`model/MotionWeaver` 的 tokenizer、backbone、representation 保持原样。
- **不为整齐而搬迁**：只整理有明确职责边界的目录；历史实验代码保留原处并标注冻结。

## 二、现状证据

| 指标 | 当前值 | 含义 |
| --- | ---: | --- |
| `data/tools/*.py` | 55 | 其中 50 个是 CLI，5 个是共享库，全部平铺 |
| `inference/visualization/*.py` | 26 | 一次性测量仪器，已有冻结说明但仍缺状态入口 |
| `inference/profiling/*.py` | 20 | 含 5 个 `test_*.py`，不在 `tests/`；`unittest discover` 发现不到，pytest 目前靠 `testpaths` 特判 |
| `tests/test_*.py` | 44 | 全部平铺，测试之间依赖同目录 import |
| `benchmark/*.py` | 14 | 未加入 `setup.py`，安装后不可用 |
| 带 `sys.path.insert/append` 的文件 | 41 | 安装入口仍不可靠 |
| `inference -> training` 导入 | 20 次 | 推理层依赖训练层 |
| `inference -> tools` 导入 | 3 次 | 推理层依赖工具脚本 |
| `tools -> inference` 导入 | 5 次 | 工具层反向依赖推理层 |
| `training -> inference` 导入 | 2 次 | 训练层依赖推理层 |
| `data -> training` 导入 | 1 次 | 兼容别名 `data/runtime/unreal_quality.py` |

关键代码位置：

- `ReferenceGuidedPose` 位于 `training/pretrain/train_conditioned_pose.py:288`，被
  `inference/runtime/conditioned_pose.py:11` 和 15 个可视化脚本引用。
- `RootPlanHead` 位于 `tools/train_root_plan.py:152`，被 3 个推理可视化脚本引用。
- `benchmark/README.md:1` 声明唯一评测入口，但 `setup.py:9` 的包列表没有 `benchmark`。
- `setup.py:26` 的 `package_data` 漏掉 `training/configs/unreal/*.json` 和
  `benchmark/manifest/*.json`。

## 三、目标结构

只列需要变化的部分：

```text
model/MotionWeaver/
  refpose/                      # 新增：纯 torch 模型与损失，不依赖 training
    __init__.py
    model.py                    # ReferenceGuidedPose / RootPlanHead
    losses.py
  tokenizer/                    # 不动
  backbone/                     # 不动
  representation/               # 不动
  geometry/                     # 不动

data/
  runtime/
    refpose_windows.py          # 从训练脚本抽出的 MotionWindows / sampler
  tools/
    common/                     # portable_lock、路径、IO、报告
    ingest/                     # 导入、重定向、骨架冻结
    build/                      # 训练视图、契约、索引构建
    review/                     # 审核服务、store、review_web
    audit/                      # 只读审计
    publish/                    # 冻结、发布、清理
    ops/                        # 队列、迁移、长任务、恢复
    eval/                       # 数据侧评测编排

training/
  pretrain/
    train_conditioned_pose.py   # 只保留训练循环、checkpoint 与 CLI
    train_root_plan.py          # 从 tools/ 迁入
  evaluation/                   # 标为历史实验，不再新增

benchmark/                      # 安装为包，正式评测唯一入口
  __main__.py

inference/
  runtime/                      # 只依赖 motionweaver + data
  profiling/                    # 冻结实验；测试移出
  visualization/                # 冻结仪器；标明 no-new-features

tests/                          # 接收 profiling 下的 5 个测试
tools/                          # 只留仓库维护和兼容入口
```

## 四、分阶段工作

### 阶段 0：边界守门与测试归位

目标：不改业务逻辑，先让问题可被自动发现。

- [x] 把 `inference/profiling/test_*.py` 移到 `tests/`：
  `test_hard_pose_constraints.py`、`test_phase_references.py`、
  `test_pose_reference_lab.py`、`test_pose_references.py`、
  `test_streaming_comparison.py`。
- [x] 新增 `tests/test_package_boundaries.py`：
  - 禁止 `inference/runtime`、`inference/export` 导入 `training` / `tools`。
  - 禁止 `data` 顶层导入 `training`，兼容别名单独 allowlist。
  - 允许 `tests` 导入所有第一方包。
- [x] 新增 `tests/test_packaging.py`：
  - `setup.py` 的包列表覆盖所有第一方包。
  - 从临时工作目录导入公开包。
- [x] 新增 `tests/test_test_discovery.py`：
  - `tests/` 之外不允许出现 `test_*.py`。
- [x] 新增 `docs/tools-index.md`，列出“正式入口 / 实验入口 / 已冻结入口”（初版已建立）。

验收：

```bash
.venv/bin/python -m pytest -q
.venv/bin/python tools/verify_repository_layout.py \
  --output .build/experiments/repository-layout-cli.json
```

### 阶段 1：打包与 CLI 收敛

目标：让仓库安装后，正式入口不再依赖当前工作目录。

- [x] 在 `setup.py` 的 `package_roots` 中加入 `benchmark`。
- [x] 新增 `benchmark/__main__.py`，支持 `python -m benchmark --help`。
- [x] 补全 `package_data`：
  - `training/configs/**/*.yaml`
  - `training/configs/**/*.json`
  - `benchmark/manifest/*.json`
- [x] 增加最小 `console_scripts`：
  - `mw-benchmark`
  - `mw-review`
  - `mw-train-refpose`
  - `mw-export-unreal`
- [ ] 逐步删除可被安装包替代的 `sys.path.insert`；开发态保留单一 fallback。

验收：

```bash
python -m venv /tmp/mw-check
/tmp/mw-check/bin/pip install -e .
cd /tmp && /tmp/mw-check/bin/mw-benchmark --help
cd /tmp && /tmp/mw-check/bin/mw-review --help
```

### 阶段 2：模型与数据窗口抽离

目标：让推理运行时不再依赖训练脚本和工具脚本。这是结构收益最高的一步。

- [x] 新建 `model/MotionWeaver/refpose/model.py`，迁入：
  - `ReferenceGuidedPose`
  - `RootPlanHead`
- [x] 新建 `model/MotionWeaver/refpose/losses.py`，迁入 `losses()` 与 `root_loss()`。
- [x] 新建 `data/runtime/refpose_windows.py`，迁入：
  - `MotionWindows`
  - `balanced_window_weights`
  - `ClipShuffleSampler`
- [x] 新建 `data/runtime/refpose_contract.py`，迁入 `digest` / `verify_release`，
  使窗口加载与契约校验不再经由训练脚本。
- [x] `training/pretrain/train_conditioned_pose.py` 只保留训练循环、checkpoint 与 CLI。
- [x] `tools/train_root_plan.py` 的模型类改从
  `motionweaver.refpose` 导入。
- [x] 旧路径保留 re-export，例如
  `training.pretrain.train_conditioned_pose.ReferenceGuidedPose` 继续可用。
- [x] `inference/runtime/conditioned_pose.py` 改为只依赖 `motionweaver` 与 `data`。
- [x] 新增 `tests/test_refpose_numerical_equivalence.py`，以冻结的整理前实现为
  基线，逐元素校验 `state_dict`、前向输出、损失与 Root 几何。

验收：

- `rg 'from training|from tools' inference/runtime inference/export` 为空。
- 固定 state_dict 的 PyTorch 输出与整理前逐元素一致。
- 从临时工作目录导入推理包，并阻断 `training` / `tools` 后仍可运行。
- `tests/test_repository_layout.py` 的推理隔离测试继续通过。

已知例外（不构成运行时依赖）：

- `inference/runtime/experiment/experiment.py` 仍导入 `training.common.optim.utils`，
  属于 G1 旧实验兼容路径，列入阶段 3 之后的独立清理项。
- `inference/runtime/backbone/motion_inference.py` 仅在 `TYPE_CHECKING` 下引用
  `training` 类型。
- `data/runtime/unreal_quality.py` 是 `motionbricks` 兼容别名，保留。

### 阶段 3：`data/tools` 增量分组

目标（已完成）：降低平铺模块的导航成本，不中断已有命令。

建议顺序与代表文件：

| 分组 | 代表文件 |
| --- | --- |
| `common/` | `portable_lock.py`、新增路径/IO/报告工具 |
| `review/` | `motion_review_server.py`、`native_review_store.py`、`conditioned_review_store.py`、`record_motion_review.py`、`build_rejected_*.py`、`review_web/` |
| `ingest/` | `import_motion_dataset.py`、`import_prepared_motions.py`、`retarget_uefn_to_soma.py`、`prepare_soma_ue_batch.py`、`freeze_soma_skeleton.py` |
| `build/` | `build_soma_training_set.py`、`build_soma_contract*.py`、`build_*_index.py`、`build_refpose_release.py`、`compress_training_views.py`、`process_soma_dataset.py` |
| `audit/` | `audit_*.py`、`validate_seed_training.py`、`screen_pending_quality.py` |
| `publish/` | `freeze_conditioned_dataset.py`、`close_seed_batch.py`、`promote_seed_training.py`、`cleanup_seed_batch.py` |
| `ops/` | `run_seed_queue.py`、`run_overnight_seed_pipeline.py`、`run_ue_pipeline_check.py`、迁移与恢复脚本 |
| `eval/` | `run_soma_eval.py` |

实际迁移 54 个实现模块：`common` 1、`review` 9、`ingest` 9、`build` 13、
`audit` 6、`publish` 5、`ops` 10、`eval` 1。

执行规则：

- 先做 `common/`，再做 `review/`，其余分组按实际改动频率推进。
- 旧模块路径统一由 `data.tools._compat.shim()` 转发：

```python
from data.tools._compat import shim

shim(__name__, "data.tools.review.motion_review_server")
```

导入时旧模块名指向新模块对象，保留函数、类和模块级状态的同一性；
`python -m data.tools.<旧名>` 用 `runpy` 执行新模块的 `__main__`。

- 所有 `python -m data.tools.<name>` 文档命令在 shim 删除前必须继续可用。
- 不同时移动 `data/tools` 与 `inference/`，避免一次变更跨越两个边界。

验收：

- 所有公开入口 `--help` 冒烟。
- 审核流程用临时夹具走通。
- `rg 'data\.tools\.[a-z_]+' docs tests tools` 中不存在失效路径。

完成内容：

- [x] 建立 `common` / `review` / `ingest` / `build` / `audit` / `publish` / `ops` /
  `eval` 八个子包，全部实现模块迁入。
- [x] `review_web/` 随审核服务迁入 `data/tools/review/`，打包资源与前端测试同步更新。
- [x] 交叉导入、`__file__` 相对路径、子进程脚本路径全部改为分组后的位置。
- [x] 旧路径 shim 由 `tests/test_tool_compatibility_shims.py` 参数化守卫，
  覆盖全部模块身份与一个真实 CLI 入口。
- [x] 所有带 argparse 的公开入口 `--help` 冒烟通过。

### 阶段 4：冻结实验目录与根目录清理

目标：让“正式代码”和“历史仪器”在入口处一眼可分。

- [x] 为 `inference/visualization/`、`inference/profiling/` 增加 `STATUS.md`，
  标明“实验工具，冻结新增”。
- [x] `inference/profiling/` 只留代码，4 份结果记录移到
  `docs/experiments/2026-09-23-*.md`，并更新 README、论文来源表与索引。
- [x] `MotionBricks.exe` 保持仓库根目录：启动器按自身位置推导项目根，移到
  `launcher/dist/` 会直接破坏 `.venv` 与演示脚本定位；按 Release/演示二进制管理，
  不随 Python 包发布。
- [x] `.gitignore` 已包含 `MUJOCO_LOG.TXT`（原有条目，复核通过）。
- [x] `docs/experiments/assets/` 原样保留，新增 README 标注历史边界。

## 五、执行顺序与估算

| 阶段 | 内容 | 风险 | 预计 |
| --- | --- | --- | --- |
| 0 | 边界守门与测试归位 | 低 | 0.5–1 天 |
| 1 | 打包与 CLI 收敛 | 低 | 1 天 |
| 2 | 模型与数据窗口抽离 | 中 | 3–5 天 |
| 3 | `data/tools` 分组 | 中 | 3–5 天 |
| 4 | 冻结实验目录与根目录清理 | 低 | 1–2 天 |

建议先完成阶段 0 + 1，再进入阶段 2。阶段 3 可以在模型边界稳定后单独排期。

## 六、不建议做的事

- 不移动 `model/MotionWeaver/representation/`、`backbone/`、`tokenizer/` 的内部文件。
  检查点、Hydra 配置和历史脚本依赖这些类路径。
- 不删除 `tools/compat/`。它是兼容层，不是重复代码。
- 不删除 `docs/experiments/assets/` 下的历史实验代码。
- 不一次性重命名 `data` / `inference` / `benchmark`。
- 不在没有 shim 的情况下移动 `data/tools` 模块。

## 七、执行记录

| 日期 | 阶段 | 结果 | 备注 |
| --- | --- | --- | --- |
| 2026-11-14 | 方案定稿 | 完成 | 本文创建 |
| 2026-11-14 | 阶段 0 | 完成 | 5 个测试归位；包边界、打包、测试发现守卫通过 |
| 2026-11-14 | 阶段 1 | 基本完成 | `benchmark` 可安装；4 个 console scripts 与 `python -m benchmark` 从 `/tmp` 验证通过；`sys.path` 清理待后续 |
| 2026-11-14 | 阶段 2 | 完成 | 模型、损失、窗口与契约校验已抽离；旧路径 re-export 保留；state_dict / 前向 / 损失数值等价回归通过 |
| 2026-11-14 | 阶段 3 | 完成 | 54 个实现模块迁入 8 个子包；旧路径由 `_compat` 转发并参数化守卫；CLI `--help` 冒烟通过 |
| 2026-11-14 | 阶段 4 | 完成 | 两个实验目录加冻结状态；4 份 profiling 结果迁入实验归档；根启动器保留并记录原因 |
