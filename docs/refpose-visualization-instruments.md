# inference/visualization：这批一次性仪器的现状与边界

> 这份文档解决一个具体问题：仓库里躺着 25 个 `inference/visualization/*.py`（本 PR 新增 20 个，含 paths.py），
> 它们没有测试、没有 CI、路径硬编码，谁也不敢说自己能维护。
> 本文说清它们**是什么、凭什么存在、边界在哪**，好让下一个人不用重新猜一遍。

---

## 一、定位：仪器，不是产品

这些脚本全部是**一次性测量工具**。它们做同一件事的不同切面：

> 读封盘的 `refpose_v3_71k` 权重，在真实 clip 上跑推理，
> 把结果渲染成图/GIF/网页，用来回答一个具体问题。

它们**不是**：
- 不是推理运行时（运行时在 `inference/runtime/`）
- 不是验收工具（验收在 `data/tools/review/review_web/`）
- 不是 UE 接入的承载层（承载层是新的 `FAnimNode_RefPose`，见
  `docs/refpose-ue-runtime-integration-research.md`）

因此本文档的纪律是：**冻结，不往里加功能。** 新需求进 UE，不再进这里。

---

## 二、清单

| 脚本 | 回答什么问题 |
|---|---|
| `refpose_preview.py` | 封盘权重跑一段走路补间，接触期脚还停在地上吗 |
| `refpose_oracle_root.py` | Root 给真值时的效果上限（正确用法下的天花板） |
| `refpose_closed_loop.py` | Root 自己预测时的效果（自回归，误差会累积） |
| `refpose_segment_reset.py` | 每 N 个窗口用真值历史重置，能挡多少自回归误差 |
| `refpose_chain_play.py` | 方案 B：每代只播一段，下一代从实际播过的姿态接续 |
| `refpose_matrix.py` | play × N 的完整正交矩阵 |
| `refpose_context_sweep.py` | 缩短有效上下文会掉多少精度 |
| `refpose_keyframe_fill.py` | 稀疏关键帧之间的补间质量 |
| `render_idle_fill.py` | 站立历史 + 未来 Root + 稀疏未来姿态，中间补得出来吗 |
| `render_long_fill.py` | 把 0.2 秒窗口滚成 8 秒长动画，抖动看得见吗 |
| `render_refpose_gifs.py` | 批量对照 GIF（左原动作，右生成） |
| `apply_smoothing.py` | Savitzky-Golay 平滑主链，末端保持原样，加速度降多少 |
| `dump_web3d.py` | 闭环结果导成三维关节序列给浏览器 |
| `export_viewer3d.py` | 闭环结果导成只读 3D 数据 |
| `export_closed_loop_review.py` | 导成 `motion_review_server` 能吃的结构 |
| `export_segment_reset.py` | segment_reset 结果导成查看器格式 |
| `locomotion_server.py` | 实时 Locomotion：WASD → Root 规则 → 姿态网补身体（WebSocket） |
| `ue_axis.py` | 我们的轴 → UE 轴（前 +X、右 +Y、上 +Z） |
| `serve_nocache.py` | 带 no-store 的静态服务器 |
| `paths.py` | 上面这些脚本的路径约定，集中一处 |

---

## 三、路径约定（2026-11-03 收敛）

**改动前**：每个脚本各自写死 `Path(r"D:\AILocomotonSystem\MotionWeaver")` 和
`Path(r"D:\MotionWeaverData\...")`，散在 16 个文件里。换盘符要改十几处，改漏一处不报错——
它只是读不到数据，然后抛一个毫不相干的异常。

**改动后**：全部收敛到 `inference/visualization/paths.py`。

| 常量 | 值 | 环境变量 |
|---|---|---|
| `paths.ROOT` | 仓库根（按 `__file__` 推，不再写死） | `MW_ROOT` |
| `paths.DATA` | 数据根 | `MW_DATA_ROOT` |
| `paths.MMAP` | `<DATA>/full_mmap` | — |
| `paths.CONTRACT` | `<DATA>/full_release/contract/contract.json` | — |
| `paths.STATS` | `<DATA>/full_release/contract/stats` | — |
| `paths.POSE_WEIGHT` | `<DATA>/model_weight/refpose_v3_71k/best.pt` | — |
| `paths.ROOT_WEIGHT` | `<DATA>/training_runs/refpose_root_20ep_grouped/best.pt` | — |
| `paths.ROOT_WEIGHT_ALT` | `<DATA>/training_runs/refpose_root_20ep_seq/best.pt` | — |
| `paths.RELEASE` | `<DATA>/full_release` | — |
| `paths.REVIEWS` | `<DATA>/reviews/refpose-closed-loop` | — |
| `paths.OUT_PREVIEW` | `<ROOT>/output/refpose_preview` | — |

**验收判据（已实测）**：

- `inference/visualization` 下 25 个模块从仓库根 import 全部成功
- `paths.py` 提供 11 个模块级常量
- 17 个文件 `import paths`；其中 17 个文件用 `ROOT = paths.ROOT`（**ROOT 推导式
  已彻底收进 paths.py，不再各写一份**）；`paths.*` 引用共 49 处
- **路径值对照**：这批脚本原本未进 git，所以「重构前的字面量」没有版本历史可查。
  可比对的只有 11-02 的运行产物：
  - `paths.MMAP` ← `reviews/refpose-closed-loop/export-index.csv` 记录过的
    `D:\MotionWeaverData\full_mmap\020545`
  - `paths.ROOT_WEIGHT` ← `output/refpose_preview/closed_loop/report.json` 里的
    `root_weights`
  - `paths.POSE_WEIGHT` / `STATS` / `REVIEWS` 由同批产物间接佐证

  `CONTRACT` / `RELEASE` / `OUT_PREVIEW` **没有重构前产物可对照**，
  只能算「未被证伪」，不能声称「已验证相等」。这条区别不该被含糊掉。

---

## 四、这些脚本能签什么字，不能签什么字

| 能 | 不能 |
|---|---|
| 回答"这个改动让指标变好还是变坏" | 代表引擎内视觉质量 |
| 产出可复算的 npz/png，供复核 | 作为 CI 门禁（无测试） |
| 作为 UE 侧转换器的离线对照（`ue_axis.py`） | 作为 UE 接入的代码基座 |

**已知的结构性问题（不在本次处理）**：

1. **坐标转换只有一份实现**：`ue_axis.py`。已删除的游戏模拟层里**没有**做过
   UE 轴转换（存档 html 实测 `ueToGl` / `quat` / `cont6d` / `6D` 全部 0 命中），
   所以不存在「两份实现」的问题——此前这么写是记错了。
2. **6D 旋转解码仍有两套约定**：训练侧 `matrix_to_cont6d` 是列拼接，
   数据侧 `r.npy` 是 row-major 交错 `(m00,m01,m10,m11,m20,m21)`。
   这条必须在阶段 0 的 manifest 里钉死，不能靠注释。
3. **没有任何自动化测试**。这次只做到 lint 与 import 冒烟，
   真正的数值回归要靠阶段 0 那个 `max|Δ| < 1e-6` 的重建脚本。

---

## 五、下一步（不属于本 PR）

按 `docs/refpose-ue-runtime-integration-research.md` 的排期：

- **阶段 0**：`refpose_manifest_v1.json` + 独立重建脚本，判据 `max|Δ| < 1e-6`
- **阶段 1**：ONNX 导出落盘到 `model_weight`（当前 `.onnx` 数量为 0）
- **阶段 2 起**：全部在 UE 里写，Python 只留离线 oracle

这批脚本在新阶段里的角色：**离线对照，不再演进。**
