# RefPose v3 → UE 导出/推理管线调研

> 调研范围：只读源码 + 在 `D:\tmp\refpose_probe2\` 下写一次性脚本做实测，未改动仓库任何源码。
> 权重：`D:\MotionWeaverData\model_weight\refpose_v3_71k\best.pt`（SHA-256 `6d10d358…5ffc`，epoch 19，val_pose_rmse_cm 3.6539）
> 契约：`D:\MotionWeaverData\full_release\contract\contract.json`
> 数据：`D:\MotionWeaverData\full_mmap`（71,127 clips，2,490,688 窗口，176.7 GB）

---

## 0. 结论摘要

1. **模型输入契约已完全确定并实测**：4 个输入 `history[1,24,709]` / `root_plan[1,24,7]` / `reference[1,24,702]` / `mask[1,24,1]`，输出 `joints[1,24,702]`，全部 float32、静态 batch=1。ONNX（opset 17 / IR 8 / 12.4 MB / 257 节点）与 torch 最大绝对差 **3.33e-06**，可放心作为 UE 侧唯一数值真源。
2. **与旧 AIAnimation 的 4D `pose_history` 契约是两套完全不同的接口**：旧的是单输入 `[1,W+1,J,12]`（xyz + 行优先 3x3）、调用方填未来 Root、模型只重建身体姿态；新的是**四输入、模型自己吃未来 Root 计划、输出 6D 旋转**。旧节点不能靠改窗口参数复用，必须重写输入装配与输出反解。
3. **一个必须写进代码的硬约束**：`r.npy` 的 6D 是 **`m00,m01,m10,m11,m20,m21`（前两列按行交织）**。仓库自带的 `matrix_to_cont6d`（`model/MotionWeaver/representation/core/utils/rotations.py:62-64`）是「按列拼接」，**两者不兼容**；误用会在骨骼 12/14/40/42/70/75（LeftShoulder、LeftForeArm、RightShoulder、RightForeArm、LeftFoot、RightFoot）直接产生单位向量退化 → NaN。
4. **归一化统计量必须原样打包，不能重算**：`pose_m.npy` / `pose_s.npy` 是 `(78,3) float32`，仅覆盖位置，旋转不归一化。实测用 3 条 clip 反推的经验统计量与契约统计量最大差 **43.0 cm（均值）/ 146.8 cm（标准差）**，说明它绑定全量训练分区，任何「重新统计」都会破坏权重。
5. **语义上 `mask` 是硬覆盖而非软条件**：`forward` 末行 `torch.where(mask.bool(), reference, predicted)`（`train_conditioned_pose.py:253`），实测 mask=1 的帧输出与 reference **逐元素 0.0 误差**。这决定了 UE 侧「硬参考帧」的实现方式，也决定了门禁不能只比连续帧。
6. **延迟完全可接受**：ORT CPU 4 线程 p50 **0.638 ms / p95 0.705 ms**；1 线程 p50 1.443 / p95 1.508 ms。按 120 fps、24 帧窗口、4 帧步长推理，单次推理占一个 8.33 ms 帧周期的 **7.7 %–18 %**（4 线程 / 1 线程），留给皮肤与图求值的预算充裕。**注意：ONNX 是静态 batch=1，batch=8 会被 ORT 拒绝**，多角色必须每角色一个 session 或重新导出动态 batch。
7. **离线→UE 一致性门禁应沿用旧脚本的 0.01 绝对差，但要补两级更严的内部门限**：torch↔ONNX **1e-4**、ONNX↔DML **1e-3**、DML↔UE 侧回读 **1e-2**。理由是：torch↔ONNX 实测只有 3.33e-06，给到 1e-4 已留 30 倍余量；DML 因 GRU/Softmax 的算子实现差异放大一档；UE NNE 的 float→FTransform 往返再放大一档。
8. **没有 UE 也能先看到动画**：`inference/visualization/` 已有一套 PIL 1280×720 火柴人渲染与指标框架，**渲染层可直接复用**，但 `motionweaver_preview.py`（575 行）整体绑死 VQ-VAE 的 `motion_rep / global_rep / local_rep` 接口，不能直接跑 refpose。估一版约 200–250 行新脚本，工作量 0.5–1 人日，零额外费用（纯 CPU + PIL）。
9. **最需要警惕的未证实事项**：`p.npy`（位置）与 `r.npy`（6D 旋转）之间的 **FK 闭合关系没能在离线数据上验证出来**（最佳候选约定仍有 253 mm 平均重建误差）。这不影响模型推理（模型直接吃/吐同一表示），但**影响任何「用旋转重建位置」或「用位置反推旋转」的 UE 侧反解**，必须先解决（见 §6 未证实事项 U1）。

---

## 1. Q1：静态形状与输入契约

### 1.1 实测的精确形状（证据：`D:\tmp\refpose_probe2\probe5.py` 输出 + ONNX 图内省）

| 输入名 | 形状 | dtype | 构成 | 归一化 |
|---|---|---|---|---|
| `history` | `[1, 24, 709]` | float32 | `234` 位置 + `468` 旋转6D + `7` root_local | 位置减 mean 除 std；旋转与 root 不归一化 |
| `root_plan` | `[1, 24, 7]` | float32 | `3` 位置 + `4` 四元数 xyzw | 不归一化 |
| `reference` | `[1, 24, 702]` | float32 | 78×9，未选中帧全 0 | 位置已归一化（同 history） |
| `mask` | `[1, 24, 1]` | float32 | 24×1，0/1 | — |
| 输出 `joints` | `[1, 24, 702]` | float32 | 78×9 = 3 位置 + 6 旋转 | 位置在归一化域，需反归一化 |

数值等式（已验证）：
- `709 = 78*9 + 7 = 702 + 7`（`train_conditioned_pose.py:239-240`：`self.features = bones*9`，`nn.GRU(self.features + 7, width)`）
- `702 = 78*9`；`234 = 78*3`；`468 = 78*6`

ONNX 图内省（`onnx.load()`）：
- opset **ai.onnx 17**，IR **8**，12,395,366 字节，257 节点
- 算子集合：`Add, Cast, Concat, Constant, Div, Expand, GRU, Gather, Gemm, LayerNormalization, MatMul, Mod, Mul, Relu, Reshape, Shape, Slice, Softmax, Sqrt, Squeeze, Transpose, Unsqueeze, Where`
- 输入名顺序：`['history', 'root_plan', 'reference', 'mask']`，输出名 `'joints'`
- **`GRU` 是 ONNX 标准算子**（非自定义），这是 DirectML 能跑起来的关键。

### 1.2 与旧 AIAnimation 4D 契约的差异

证据：`inference/export/export_unreal_animgraph.py:192`、`unreal-script/AIAnimation/Source/AIAnimationEditor/Private/AIAnimationPrepareCommandlet.cpp:195`

| 维度 | 旧 AIAnimation（VQ-VAE） | 新 RefPose v3 |
|---|---|---|
| 输入个数 | **1**（`pose_history`） | **4**（history / root_plan / reference / mask） |
| 输入形状 | `[1, W+1, J, 12]` | `[1,24,709] / [1,24,7] / [1,24,702] / [1,24,1]` |
| 12 的含义 | xyz(3) + **行优先 3x3**(9) | 6D 旋转（`m00,m01,m10,m11,m20,m21`） |
| 窗口 | 16（默认）或 24，W+1 帧（末帧复制补 1） | 历史 24 / 未来 24，无 +1 |
| 未来 Root | **调用方**填，模型不预测 | **模型条件输入**，必须给 24 帧 |
| Root 权威性 | 输出不产生位移，Root 沿用源动画 | 输出只有相对姿态，Root 完全由调用方决定 |
| 旋转 → UE | 反解 `cont6d_to_matrix` + `basis/reference` 双乘（`:89, :98`） | 无现成代码，需新写（见 §2） |
| 语义 | 重建已知姿态 | **补间生成**（参考引导） |

结论：**旧节点不能「改个窗口数」复用**。`Session->Run(History, …)`（`AnimNode_AIAnimation.cpp:367`）只有一个 buffer，新的必须改成四 buffer；`ToLocal`（`:348-356`）把 Root 从姿态里剔除，而新模型的输出本身就是根相对位置，这段逻辑对新模型是错的。

### 1.3 建议的 UE 侧 manifest 字段清单

以旧 `ai_animation_ue_pose_onnx_v1`（`export_unreal_animgraph.py:186-195`）为骨架，新增 `format = "refpose_ue_pose_onnx_v1"`：

`JSON
{
  "format": "refpose_ue_pose_onnx_v1",
  "purpose": "reference_guided_pose_inbetweening",
  "training_contract": "reference_guided_root_pose_v1",
  "checkpoint_sha256": "<best.pt sha256>",
  "onnx_sha256": "<model.onnx sha256>",
  "contract_sha256": "<full_release/contract/contract.json sha256>",
  "stats_sha256": { "mean": "<pose_m.npy sha256>", "std": "<pose_s.npy sha256>" },

  "num_bones": 78,
  "fps": 120.00480019200768,
  "history_frames": 24,
  "future_frames": 24,
  "root_bone": "Root",
  "pelvis_bone": "Hips",

  "inputs": [
    { "name": "history",   "shape": [1,24,709], "dtype": "float32" },
    { "name": "root_plan", "shape": [1,24,7],   "dtype": "float32" },
    { "name": "reference", "shape": [1,24,702], "dtype": "float32" },
    { "name": "mask",      "shape": [1,24,1],   "dtype": "float32" }
  ],
  "outputs": [ { "name": "joints", "shape": [1,24,702], "dtype": "float32" } ],

  "feature_layout": {
    "per_bone_slots": 9,
    "order": ["position_xyz_normalized", "rotation_6d"],
    "position_space": "root_local_meters_y_up",
    "rotation_6d_packing": "m00,m01,m10,m11,m20,m21",
    "rotation_space": "reference_pose_removed",
    "reference_pose_removal": "U @ (R_ue @ R_ref_ue^T) @ U^T"
  },
  "normalization": {
    "scope": "positions_only",
    "mean": "stats/pose_m.npy", "mean_shape": [78,3],
    "std":  "stats/pose_s.npy", "std_shape": [78,3],
    "apply": "denormalized_m = output[..., :234] * std + mean"
  },
  "axis": { "ue_to_motion": [[0,-1,0],[0,0,1],[1,0,0]], "source": "data/runtime/unreal_dataset.py:18" },
  "mask_semantics": "hard_override; output[frame] == reference[frame] when mask==1",

  "streaming": {
    "inference_stride_frames": 4,
    "playback_delay_frames": 24,
    "window_mode": "rolling_24_history_24_future",
    "reference_default": "end_frame_only"
  },

  "bones": [ { "name": "Root", "parent": -1, "position": [0,0,0], "rotation": [0,0,0,1] }, "..." ],
  "validation": [ { "asset": "...", "torch_max_abs_error": 0.0,
                    "onnx_max_abs_error": 3.33e-06, "dml_max_abs_error": 4.77e-06 } ],
  "limitations": [ "Root 由调用方提供，不是玩家意图；窗口边界质量需实测；ONNX CPU 只用于导出校验，UE 用 DirectML。" ]
}
`

**`bones` 必须是 UE 参考骨架的 78 骨（含 `Root`、去掉 `VB ` 虚拟骨）**，`parent` 用父先子后的整数索引——`AIAnimationPrepareCommandlet.cpp:141-153` 已强制校验「父索引 < 自身索引」。

---

## 2. Q2：归一化与统计量，cm 尺度与坐标基

### 2.1 统计量从哪来

证据：`contract.json` 的 `stats` 块 + `data/runtime/conditioned_motion.py:145-154, 169-171`

`contract.json` 实际内容（**注意与任务描述的字段名不同**）：

`JSON
"stats": { "pose_m": { "mean": "pose_m.npy", "std": "pose_s.npy" } }
`

即：合约键叫 `pose_m`，里面 mean 文件是 `pose_m.npy`、std 文件是 `pose_s.npy`（**不是 `pose_m_std.npy`**）。构建逻辑：只累加 `split == "train"` 的真实帧，`stats_scope = "train_real_frames_once_per_clip_no_held_padding_no_overlapping_window_weight"`。

实测：
- 形状 **`(78,3)`**，dtype **float32**，各 1,064 字节
- `std` 范围 `[0.0238 cm, 149.76 cm]`，均值 26.95 cm；**恰好等于 1.0 m（100 cm）的有 3 个槽位**（位于 bone 0 与 bone 1）
- `mean` 绝对值最大 **161.35 cm**
- 用 clip 000000/000001/000002 反推经验统计量，与契约统计量最大差：均值 **43.02 cm**、标准差 **146.83 cm**（中位 21.21 cm）
- 训练侧读取（`train_conditioned_pose.py:88-90`）：`stats = contract["stats"]["pose_m"]` → `np.load(release/"contract"/"stats"/stats["mean"])`

**结论：统计量绑定全量训练分区，无法从少量样本复现，必须原样随导出包携带。**

### 2.2 导出包必须携带什么

| 形式 | 建议 | 理由 |
|---|---|---|
| `.npy` 原文件 | **必须**（`stats/pose_m.npy`、`stats/pose_s.npy`） | 可作为第三方复核的数值真源；1 KB 级别，零成本 |
| `.bin` 小端 f32 | **必须**（`stats.bin`，78*3*2*4 = 1872 字节） | UE 侧 `FFileHelper::LoadFileToArray` 直接读，免 JSON 浮点解析 |
| 内嵌 ONNX constant | **不要** | 会把归一化烧进图里：换统计量必须重新导出模型，且 DML 侧多 Constant 节点 |
| JSON 内嵌数值 | 可选（仅审计） | 78×3 个 float 的 JSON 可读性差且易受解析器精度影响 |

**对旧脚本的参照**：`export_unreal_animgraph.py` 把 mean/scale 做成 `register_buffer`（`:58-59`）烧进图里，因为它要做「包装器 vs 训练实现」双路校验。新管线**不要沿用**——refpose 的归一化只覆盖位置 234 维，且必须可替换（见 U6）。

### 2.3 cm 尺度与坐标基变换在哪做

**旧脚本**（`export_unreal_animgraph.py`）：
- `:53` `register_buffer("basis", torch.tensor(UE_TO_MOTION, dtype=torch.float32))`
- `:64` `positions = pose_history[..., :3] @ self.basis.T * .01` ← **cm→m 用 `*0.01`，基变换用右乘 `basis.T`**
- `:65` `rotations = matrix_product(matrix_product(self.basis, ... @ self.reference.transpose(-1,-2)), self.basis.T)`
- `:97` `ue_positions = positions @ self.basis * 100` ← 反向
- `:98` `ue_rotations = basis.T @ global_rotations @ basis @ reference`
- `:102-106` `pack_raw_pose`：`positions = raw["positions"] @ UE_TO_MOTION * 100`，`rotations = UE_TO_MOTION.T @ raw["rotations"] @ UE_TO_MOTION @ reference`

**`UE_TO_MOTION` 定义**（`data/runtime/unreal_dataset.py:18`）：
`Python
# UE X 前、Y 右、Z 上 → 动作空间 Z 前、X 左、Y 上；同时改变手性。
UE_TO_MOTION = np.array([[0, -1, 0], [0, 0, 1], [1, 0, 0]], dtype=np.float64)
`

**refpose 的 positions / rotations 是什么坐标系**：
- 构建代码自述（`conditioned_motion.py:177-180`，**这一份 full_release 契约里没有这些字段**，只有 `source_dataset/bone_count/fps/window/clips/stats/contact_source/root_status`）：
  `array_coordinate_system = "motion_y_up_meters_root_relative"`、`array_axes_from_ue = {"x": "-ue_y", "y": "ue_z", "z": "ue_x"}`、`pose_space = "root_local_meters"`、`root_plan_space = "last_history_root_local_meters_quaternion_xyzw"`
- 位置：`convert_clip`（`unreal_dataset.py:311`）`positions = frames[..., :3] @ UE_TO_MOTION.T * 0.01` → **米制、Y 上、root-local（pelvis 子树相对 Root）**
- 旋转：`convert_clip:316` `rotations = UE_TO_MOTION @ (rotations @ reference.transpose(0, 2, 1)) @ UE_TO_MOTION.T` → **去参考骨轴后的旋转矩阵**，基同为 motion 基
- 实测佐证：`|pos[:,0]|` 最大 0.076 m（Root 接近原点）、Hips 的 y ≈ 0.991 m、全身 y 最高 1.742 m → 确实是「米制 Y 上、根相对」

### 2.4 「UE 输入 → 模型输入 → UE 输出」完整数值链路

`
【UE 侧 · 每帧采样】
  FTransform(局部空间, cm, X前 Y右 Z上)
    │
    ├─(A) 位置：经 ToPositions() 得 Component 空间位置（cm）
    │     p_motion_m   = p_ue_cm @ UE_TO_MOTION.T * 0.01
    │     p_rootlocal  = p_motion_m - root_motion_m      ← root 为回填的 root track
    │     p_norm       = (p_rootlocal - pose_m.npy) / pose_s.npy    ← 78×3 逐元素
    │
    ├─(B) 旋转：从 Component 变换取 FMatrix
    │     R_motion = UE_TO_MOTION @ (R_ue @ R_ref_ue^T) @ UE_TO_MOTION^T
    │       R_ref_ue 由 manifest 里每骨参考姿态四元数离线构建（常量）
    │     rot6d = [R[0][0], R[0][1], R[1][0], R[1][1], R[2][0], R[2][1]]
    │       ⚠ 前两列按行交织；不许用 rotations.py:62 的 matrix_to_cont6d
    │
    ├─(C) root_local(7) = 相对「最后一帧历史 Root」的 位置3 + 四元数xyzw4
    │       同构复算 conditioned_motion.py:67-72 的 root_local
    │
    ├─(D) 拼装
    │     history[709]   = concat(p_norm.flatten(234), rot6d.flatten(468), root_local(7))
    │       24 帧堆成 [1,24,709]
    │     root_plan[1,24,7]   = 未来 24 帧 root_local（同样相对最后一帧历史 Root）
    │     reference[1,24,702] = 目标帧 9 维特征 × mask，未选帧填 0
    │     mask[1,24,1]        = 0/1
    │
【模型】ReferenceGuidedPose.forward → joints[1,24,702]（位置仍在归一化域）
    │
【UE 侧 · 反解】
    ├─(E) p_norm(234) → p_motion_m = p_norm * pose_s.npy + pose_m.npy
    ├─(F) 回填 Root：p_world_m = root_world_positions(p_motion_m, root_plan)
    │       即 train_conditioned_pose.py:225-232 的 torch 实现，UE 侧用 FQuat 等价复算
    ├─(G) rot6d(468) → R_motion（Gram-Schmidt，见下）
    ├─(H) p_ue_cm = p_world_m @ UE_TO_MOTION * 100
    └─(I) R_ue = UE_TO_MOTION^T @ R_motion @ UE_TO_MOTION @ R_ref_ue
          → FQuat 归一化 → FTransform(FQuat, FVector(p_ue_cm))
`

**6D → 矩阵的正确 Gram-Schmidt**（`model/MotionWeaver/representation/core/utils/rotations.py:43-59`，可直接照抄成 C++）：
`
x = normalize(c[0:3]);  z = normalize(cross(x, c[3:6]));  y = cross(z, x)
mat = [x | y | z]      # 三列为 R 的基
`

**极简实现建议**：UE 侧不要在运行时算 `UE_TO_MOTION` 三重积。把 (B) 与 (I) 的基变换合并成每骨常量，导出时预计算 78 组 `FQuat Q_pre[i]` / `Q_post[i]` 存进资产，运行时每骨只做 2 次四元数乘。这样既省算力，又把最易错的基变换冻结为离线常量（与旧脚本把 `reference` 做成 `register_buffer` 同一思路）。

---

## 3. Q3：窗口调度策略

### 3.1 事实基线

- 历史 24 帧 @ 120.0048 fps = **0.20001 s**；未来 24 帧同样 **0.20001 s**
- 推理成本（实测 `D:\tmp\refpose_probe2\lat.py`，300 次）：

| 后端 | p50 | p95 | max |
|---|---:|---:|---:|
| ORT CPU，1 线程 | 1.443 ms | 1.508 ms | 1.636 ms |
| ORT CPU，4 线程 | **0.638 ms** | **0.705 ms** | 1.139 ms |
| torch CPU | 1.740 ms | 2.309 ms | — |

（此前已实测的 DirectML：单次 1.066 ms，与 CPU 最大差 4.77e-06，GRU 落在 DmlExecutionProvider。本次未重复跑 DML。）

- 120 fps 下每帧预算 **8.333 ms**。4 线程 CPU 占 **7.7 %**，DML 占 **12.8 %**。

### 3.2 建议的调度参数

| 参数 | 建议值 | 依据 |
|---|---|---|
| 推理步长 | **4 帧 @120 fps = 33.33 ms = 30 Hz** | 与旧节点「固定步长每 4 个输入采样帧推理一次」（`unreal-script/AIAnimation/README.md:33`）**完全一致**，可直接复用旧的对齐/融合代码 |
| 播放延迟 | **24 帧 = 200 ms** | 必须 ≥ 未来窗口长度，否则窗口末端预测会被提前播放 |
| 窗口生成 | 滚动：每次产出未来 24 帧，**只提交前 4 帧**，其余丢弃 | 与旧 `Playback->MergeWindow(...)`（`AnimNode_AIAnimation.cpp:403`）+「已播放或插值端点的预测被冻结」同构 |
| 融合 | 4 帧交叉淡化（位置 lerp、四元数 slerp） | 旧节点已有中心加权融合；步长=窗口/6 时接缝最需淡化处理 |

**为什么不每帧推理**：步长 1 需产出 24 帧却只用 1 帧（浪费 96 % 算力），而延迟仍需 24 帧；步长 4 时每次「新信息」是 4 帧，与 30 Hz 常规动作更新对齐，且**与旧节点 4 帧步长完全可比**，旧节点的自动化测试可直接套用。

### 3.3 未来 24 帧 Root 由谁填

这是与旧节点最大的**架构性差异**：
- **旧节点**：模型不预测 Root，`pose_history` 里的 Root 是「已发生的源动画 Root」，`ToLocal` 后被剔除（`AnimNode_AIAnimation.cpp:354`）。
- **新模型**：`root_plan[24,7]` 是**必需条件输入**，训练时是 oracle（`contract["root_condition_source"] = "ground_truth_oracle_for_offline_baseline_only"`，见 `conditioned_motion.py:188`）。

三个候选：

| 方案 | 实现 | 优点 | 风险 |
|---|---|---|---|
| **A. CMC/Mover 直接给** | 从 `UCharacterMovementComponent` 取速度/朝向，按时速度外推 24 帧 | 真实反映玩家意图，零额外模型 | 匀速外推在加减速/转向时误差大，正是训练分布的**外** |
| **B. 常量速度外推 + 末端钳制** | 取最近 4 帧 Root 平均速度与 yaw rate 线性外推 | 简单、确定性、可测 | 同上，但可预测 |
| **C. 单独训 Root 预测头** | 新模型 | 最接近真实 | 超出本次范围，需新训练 |

**建议先做 B**：`conditioned_motion.py:75-79` 已实现 `root_velocity(track, fps)`（含 `yaw_rate`），B 就是它的正向积分，代码现成，且能立刻跑通端到端门禁。**A 作为 B 的输入源**——初速度从 CMC 取即可。

口径提醒：训练数据的 `root_plan` 是**相对最后一帧历史 Root** 的（`root_local(root[middle:end], origin)`，`origin = root[middle-1]`，见 `train_conditioned_pose.py:169-173`），所以 B 的外推必须从当前播放位置起算并做同样的相对化，否则模型见到的是分布外输入。

### 3.4 参考 mask 运行时怎么填

`mask` 是**硬覆盖**：mask=1 的帧输出 = reference（实测 0.0 误差），所以 mask 是「告诉模型这几帧是硬约束」的开关。

| 场景 | 建议 mask | 说明 |
|---|---|---|
| 纯流动（无参考） | 全 0 | 训练档位 `no_reference`，实测 5.28 cm；最干净的纯生成 |
| 有明确终点（Target Match、蒙太奇末尾） | 仅 offset 23 = 1 | 训练档位 `end_reference`，实测 3.22 cm，末帧严格对齐 |
| 中间锚点 + 终点 | offset 12 与 23 = 1 | 训练档位 `middle_and_end`，实测 2.17 cm（最好） |
| **不要用** | 任意稀疏随机组合 | 默认 `--reference-sampling random` 只采样 `((), (12,), (23,), (12,23))`（`train_conditioned_pose.py:180`）；只有这 4 种是「训练见过的」 |

填充时机：**在窗口装配时按绝对采样索引算 offset**（与旧节点「窗口按绝对采样编号对齐」一致）。参考帧内容是 `target * mask`，即目标帧 9 维特征（位置已归一化）；未选帧必须填 **0**（`MotionWindows.__getitem__:190-192`），不是 NaN 也不是重复值。

### 3.5 与旧节点「4 帧步长 / 8 帧延迟」的可比性

| 项 | 旧 AIAnimation | 新 RefPose v3 | 可比性 |
|---|---|---|---|
| 推理步长 | 4 采样帧（`README.md:33`） | 建议 4 帧 | **完全可比**，同一套 `Advance()`（`AIAnimationSampling.h:11-24`）可直接复用 |
| 理论频率 | 7.5 Hz（30 fps 源） | 30 Hz（120 fps 源） | **不可直接比**：源帧率差 4 倍 |
| 播放延迟 | 8 帧 = 267 ms（`README.md:33`；`ActiveDelayFrames = 8`，`AnimNode_AIAnimation.h:111`） | 24 帧 @120 = 200 ms | 数值接近但**含义不同**：旧延迟是「补足窗口末帧」，新延迟是「未来窗口长度」 |
| 窗口 | 16 或 24 帧（+1） | 24 + 24，无 +1 | 帧数可比，结构不同 |

**结论**：新旧在「4 帧步长 + 固定采样网格 + 窗口融合」这套**调度骨架**上同构，旧节点的 `AIAnimation.Lab.FixedSampling / PlaybackBuffer / StationaryPlayback` 三个自动化测试（`README.md:107`）可原样沿用；但延迟的**语义与量级不可直接对标**，不要拿「旧 267 ms vs 新 200 ms」下结论。

**端到端延迟估算**：
`
总延迟 = 播放延迟(200 ms) + 推理(0.7~1.1 ms) + 装配(? ms) + 融合(4 帧 = 33 ms)
       ≈ 200 + 1 + 装配 + 33 ≈ 235 ~ 250 ms
`
装配成本（78 骨 × 24 帧的四元数/矩阵运算，纯 CPU 单线程）粗估 0.2–0.5 ms，需实测确认（U3）。在 30 Hz 决策、200 ms 感知延迟的常规动作游戏里可接受；要压到 100 ms 以内只能减窗口（模型重训），不是工程问题。

---

## 4. Q4：离线 → UE 三方一致性门禁

### 4.1 三级门限的定法

旧脚本用的是**单一 0.01 绝对差**（`export_unreal_animgraph.py:181`：`if error > .01`），UE 侧 `ValidateGpuModel` 也是 `MaxError < 0.01`（`AIAnimationPrepareCommandlet.cpp:233`）。新管线建议**分级**，因为三个边界的数值噪点量级差三个数量级：

| 级 | 对比 | 门限 | 理由 |
|---|---|---:|---|
| L1 | torch 训练前向 ↔ ONNX CPU | **1e-4** | 实测 3.33e-06，留 30 倍余量 |
| L2 | ONNX CPU ↔ DirectML | **1e-3** | 已实测 4.77e-06；GRU/Softmax 在 DML 上通常放大 1–2 个数量级，1e-3 保守但仍有意义 |
| L3 | DirectML ↔ UE 内 NNE 回读 | **1e-2** | 与旧脚本一致；UE 侧要走 float→FMatrix→FQuat 往返，DirectML 可能走 fp16 路径 |
| 附加 | 输出有限性 | 必须 `isfinite(all)` | 旧脚本 `:166, :181` 已强制 |

**为什么不用相对误差**：输出在归一化域里包含接近 0 的位置值（`std_min = 0.0238 cm` 说明某些轴几乎无变化），相对误差在这些槽位会爆炸。**绝对值是唯一稳定口径**。

**额外必须检查的语义项**（旧脚本未覆盖）：

| 检查 | 判据 | 理由 |
|---|---|---|
| mask 直通 | `max\|out[mask==1] - reference[mask==1]\| == 0`（精确） | `torch.where` 是精确选择，任何非零都说明导出改写了图 |
| 反归一化量纲 | `out_pos*std+mean` 全帧全骨落在 `[-0.5, 2.5] m`（实测数据域 `[-0.131, 2.135]`） | 抓「std 读错 / mean 没减」这类整链错误 |
| 骨长刚性 | 相邻骨距离逐帧标准差 `< 1e-4 m` | 数据本身 max 1.17e-07 m（实测），输出应近似刚性；偏差大说明 6D 解包错 |
| Root 一致性 | 24 帧根相对位移范数 `< 0.1 m`（除非 root_plan 本身跨大距离） | 抓「忘了用 root_plan 回填」 |

### 4.2 门禁脚本的接口设计（不实现，只定接口）

`Python
# 建议落位：inference/export/refpose_ue_export.py  与  inference/profiling/gate_refpose_consistency.py

def export_refpose(
    checkpoint: Path,          # best.pt
    release: Path,            # D:\MotionWeaverData\full_release
    output: Path,             # 导出目录（必须不存在）
    *,
    stride: int = 4,
    reference_offsets: tuple[int, ...] = (12, 23),
) -> dict: ...
# 产出：
#   output/model.onnx                     静态 batch=1、四输入
#   output/manifest.json                  见 §1.3
#   output/stats/pose_m.npy, pose_s.npy
#   output/stats.bin                      78*3*2*4 字节小端 f32，[mean..., std...]
#   output/validation_inputs.bin          N 组 × (24*709 + 24*7 + 24*702 + 24*1) f32
#   output/validation_torch.bin           N 组 × 24*702
#   output/validation_onnx.bin
#   output/gate_report.json               L1 + 语义四项结果


def gate_consistency(
    package: Path,
    *,
    dml_session: Callable | None = None,   # 传入则在本地跑 L2
    ue_report: Path | None = None,          # UE 侧写出的 gpu_validation.json（复用旧格式）
    thresholds: dict = {"L1": 1e-4, "L2": 1e-3, "L3": 1e-2},
) -> dict: ...
# 返回 {"L1": {...}, "L2": {...}, "L3": {...}, "semantic": {...}, "passed": bool}
# 任一违反即 passed=False，且 gate_report.json 必须列出首个失败元素的索引与数值
`

**夹具选取（必须覆盖分布边界）**：
1. 每条动作类别取 1 条 test clip（复用 `export_unreal_animgraph.py:153-155` 的 6 类：Walk / Run / Crouch / Jump / Traversal / Idle）
2. 每种 mask 档位各一组：`()` / `(12,)` / `(23,)` / `(12,23)`（对应 §3.4 的 4 个训练档位）
3. 至少 1 条零参考 + 1 条两端全参考极端组
4. 共 **≥ 30 组输入**，`validation_inputs.bin` 按固定顺序拼接，UE 侧按 `Count = size / (输入元素数*4)` 自解（与旧 `ValidateGpuModel:203` 同构）

**UE 侧复用点**：`AIAnimationPrepareCommandlet.cpp:179-238` 的 `ValidateGpuModel` 结构可照搬，只需把 `Dimensions = {1, W+1, J, 12}`（`:195`）换成四次 `SetInputTensorShapes`，把单 input/output binding 换成 4 in / 1 out。它已经会写 `gpu_validation.json`（`:235`），L3 报告格式无需新设计。

---

## 5. Q5：可视化 / 离线预览

### 5.1 现有资产盘点（`inference/visualization/`）

| 文件 | 行数 | 可复用度 | 说明 |
|---|---:|---|---|
| `motionweaver_preview.py` | 575 | **渲染层可复用，模型层不可用** | 模型层绑死 `motion_rep/global_rep/local_rep`（`:118-120`），且是 79 骨 VQ-VAE 接口 |
| `cmc_controller_preview.py` | — | 同上 | CMC 轨迹探针 |
| `compare_motionweaver.py` | — | 可复用 | 三面板 Source/Old/New 视频合成逻辑 |
| `summarize_acceptance.py` | — | 可复用 | 验收报告汇总 |
| `README.md` | 90 | 参考 | 明确「不测皮肤、不测 CMC、不测网络」的边界，新脚本应沿用同样的诚实声明 |

**渲染层具体可复用**：
- `SIZE = (1280, 720)`、`KEEP_BONES`（24 骨火柴人集合）、`RIGHT` / `DEPTH` 投影基（`motionweaver_preview.py:26-34`）
- PIL 逐帧绘制 + 字体标注骨架
- `timed_predict` / `timing_summary`（`:84-113`）—— 已把「4 帧重规划预算 @30 Hz = 133 ms」写成判据（`:111-112`），新管线可直接用同一口径

**不可复用**：`build_conditions`（`:116` 起）全部依赖 VQ-VAE 的 motion rep。refpose 的输入装配只有 §2.4 那 20 行，反而更简单。

### 5.2 建议方案

新写 `inference/visualization/refpose_preview.py`（估 200–250 行）：

`
输入：--checkpoint best.pt  --release full_release  --mmap-root full_mmap
      --clip-index <int>  --reference-mode {none,middle,end,both}
      --stride 4  --device {cpu,cuda}
输出：output/refpose_preview_<clip>/
        frames/*.png        三面板逐帧火柴人（源 / Lerp 基线 / 模型）
        preview.mp4         可选
        report.json         含 pose_axis_rmse_cm、脚滑倍数、推理 p50/p95
`

关键实现点：
1. 直接复用 `MotionWindows` 的 `_ensure_loaded` 逻辑读 mmap（`p/r/k.npy`），不重写数据层
2. 位置直接来自 `p_norm*std+mean`，**不经过 FK**（这样绕开 U1）
3. 旋转只用于画朝向指示，不用于重建位置
4. 三面板对齐：源 / Lerp 基线（`no_reference` 时的线性插值）/ 模型

### 5.3 成本与复杂度

| 项 | 评估 |
|---|---|
| 代码量 | 200–250 行（约 80 行渲染可直接抄） |
| 依赖 | `numpy / torch / PIL`（已在 `.venv` 里）+ 可选 `onnxruntime` |
| 运行成本 | 纯 CPU；单 clip 2575 帧、stride 4 → 644 次推理 × 0.7 ms ≈ **0.5 s**；渲染 644 张 1280×720 PNG ≈ 20 s |
| 人工 | 首次跑通 0.5–1 人日；后续零边际成本 |
| 价值 | **极高**：把「训练完就能看到效果」从「要开 UE 装插件」变成「一条命令 30 秒出视频」，是本项目**唯一**能让人不碰 UE 就判断姿态质量的通道 |

**强烈建议先做这个**。它比 UE 导出便宜一个数量级，且能提前暴露 U1 之外的所有数值问题。

---

## 6. 未证实事项清单

| ID | 事项 | 现状 | 影响 | 建议验证方式 |
|---|---|---|---|---|
| **U1** | `p.npy`（位置）与 `r.npy`（6D 旋转）之间的 FK 闭合关系 | **未验证**。已排除 12 种候选约定（全局/局部、`R`/`R^T`、与参考姿态左右乘、`UE_TO_MOTION` 共轭、父/自身索引、skeleton 相对/绝对偏移、Wahba 求解），**最佳候选（chain-then-use + 骨架父相对偏移）仍有平均 253 mm、中位 284 mm、max 424 mm 重建误差** | 高。模型本身不受影响（同表示进同表示出），但**任何**「由旋转推位置」或「由位置推旋转」的 UE 侧反解都要先确认语义 | ① 找产生 `p/r/k.npy` 的原始导入脚本（`full_mmap` 目录只有 `p/r/k.npy` 与 `skeleton.json`，**没有 `dataset.json`，生成脚本也没入库**）；② 或用一条有原始 UE NPZ 的 clip 跑 `unreal_dataset.convert_clip`（`:305-322`）后与 `p/r` 逐元素比对；③ 或用静止 T-pose clip 做零运动判据 |
| **U2** | 6D 的**数值布局** | **已验证**：`m00,m01,m10,m11,m20,m21`（前两列按行交织）。证据：交织分半后两半各自为单位向量（min 0.99999982）、点积 max 9.05e-08、整体范数恒为 √2；而「前 3 + 后 3」分半在骨骼 12/14/40/42/70/75 上后半段长度为 0 → 除零 NaN | — | 已完成 |
| **U3** | UE 侧数据装配耗时 | 未测。只测了模型推理（0.638–1.443 ms） | 中。> 2 ms 会吃掉动画工作线程预算 | 在 UE 里加 `AssembleMs` 的 `CSV_CUSTOM_STAT`（旧节点已有 `GpuSyncMs` 先例，`AnimNode_AIAnimation.cpp:374`） |
| **U4** | DirectML 在 **UE NNE** 里的数值 | 只测了 `onnxruntime-directml` 1.24.4 的 Python 侧（1.066 ms / 4.77e-06）。UE NNE 走 `NNERuntimeORTDml`，是另一条路径 | 中。L3 门限 1e-2 可能偏松也可能偏紧 | 用 §4.2 的 `gate_consistency(ue_report=...)` 实测 |
| **U5** | `root_plan` 在真实游戏轨迹上的分布 | 训练时是 oracle；实测抽样 clip 的 root_plan 位置范围仅 `[-0.0027, 0.0] m`（慢速 Idle，不代表全局） | 高。**最大的泛化风险**：模型没见过「外推 Root」 | 用 CMC 录制轨迹（`docs/experiments/assets/2026-09-28-motionweaver-a800-actor-root/cmc_root_capture_20260925_v2.csv`，`inference/visualization/README.md:15-16` 已引用）替换 root_plan 重跑评测，看 RMSE 退化多少 |
| **U6** | 统计量可复现性 | 已验证**不可**从 3 条 clip 复现（最大差 146.8 cm） | — | 已完成（结论：必须原样打包） |
| **U7** | 模型对 `mask` 稀疏组合的鲁棒性 | 只确认了 `(12,23)` 的实际行为（mask 帧精确直通）。`diverse` 采样模式（`count ∈ {0..3}`）下的组合未逐一验证 | 中。若 UE 想用「任意 1–3 个锚点」需先验证 | 用 `--reference-sampling diverse` 跑一遍 test 分区 |
| **U8** | 旧 `AIAnimation` 节点的可复用比例 | 只做了代码阅读：单 buffer 输入 → 需四 buffer；`ToLocal` 剔 Root 对新模型是**错的**（新模型输出本身就是根相对） | 中 | 需一次改造评估 |

---

## 7. 分阶段落地路径

### 阶段 A：纯 Python 自证（0.5–1 人日，零风险）

**做什么**
1. `inference/export/refpose_ue_export.py` —— 导出 ONNX + manifest + stats.npy + stats.bin + 夹具 bin（§4.2 的 `export_refpose`）
2. `inference/profiling/gate_refpose_consistency.py` —— 跑 L1 + 语义四项（§4.1 表 2）
3. `inference/visualization/refpose_preview.py` —— 三面板火柴人视频

**客观验收判据（全部必须通过，缺一不可）**
- [ ] 导出目录含：`model.onnx` / `manifest.json` / `stats/pose_m.npy` / `stats/pose_s.npy` / `stats.bin`（1872 字节）/ `validation_inputs.bin` / `validation_torch.bin` / `gate_report.json`
- [ ] `onnx.checker.check_model` 通过；opset == 17；`onnx_sha256` 与文件一致
- [ ] L1 `max_abs_error < 1e-4`（预期 ~3e-06）
- [ ] 语义·mask 直通：`max\|out[mask==1] - reference[mask==1]\| == 0`
- [ ] 语义·骨长刚性：相邻骨距离逐帧标准差 `< 1e-4 m`
- [ ] 语义·反归一化范围：`out_pos*std+mean` 全落在 `[-0.5, 2.5] m`
- [ ] 夹具 ≥ 30 组，覆盖 6 类别 × 4 mask 档位
- [ ] `refpose_preview` 对 test 分区 3 条 clip 各产出 ≥ 1 个 mp4，`report.json` 里 `pose_axis_rmse_cm` 与 `docs/refpose-v3-training-evaluation.md` §6 同档位数字差 `< 0.3 cm`

### 阶段 B：DirectML 与 UE 门禁（1–2 人日）

**做什么**
1. 本地 `gate_consistency(dml_session=...)` 打开 L2
2. 新建 `UAIAnimationRefPoseModel`（或改 `AIAnimationPrepareCommandlet`）支持四输入 + `gpu_validation.json`
3. `ValidateGpuModel` 把 `Dimensions` 从 `{1, W+1, J, 12}` 换成四次 `SetInputTensorShapes`

**判据**
- [ ] L2 `max_abs_error < 1e-3`（预期 ~5e-06）
- [ ] L3（UE 内）`max_abs_error < 1e-2`，`gpu_validation.json` 里 `passed: true`
- [ ] UE 内 `LastInferenceMs` p95 `< 2.0 ms`（DML 实测 1.07 ms，留一倍余量）
- [ ] 30 组夹具全部单次通过，无 fallback、无 NaN

### 阶段 C：UE 内闭环（3–5 人日）

**做什么**
1. 实现 `FRefPoseAnimNode`：24 帧历史环形 buffer、root_plan 外推（§3.3 方案 B）、mask 填充（§3.4）、反解（§2.4 的 E–I）
2. 复用 `AIAnimationSampling::Advance` + `MergeWindow` + 4 帧 slerp 融合
3. 预计算 78 组 `Q_pre` / `Q_post` 常量（§2.4 末尾）

**判据**
- [ ] `AIAnimation.Lab.FixedSampling` 等价测试通过（20/45/60/144 fps 等间隔采样）
- [ ] `AIAnimationSampling::Passed`（`AIAnimationSampling.h:27-30`）：每动作 ≥ 20 质量样本、失败 0、回退 0、游戏线程跳过 0
- [ ] 端到端延迟实测 `< 260 ms`（§3.5 估算 235–250 ms，留 10 ms 余量）
- [ ] 与阶段 A 的 `refpose_preview` 逐帧对比：同段动画、同 mask 档位，关节位置最大差 `< 1e-2 m`（同时是 U4 的验证）

### 阶段 D：泛化补课（依赖 U5）

**做什么**：用真实 CMC 轨迹替换 oracle root_plan 重跑评测；若退化超阈值，训 Root 预测头（方案 C）。

**判据**
- [ ] CMC 轨迹下 `no_reference` RMSE `< 8.0 cm`（oracle 基线 5.28 cm，允许 50 % 退化）
- [ ] CMC 轨迹下脚滑倍数 `< 12×`（oracle 基线 6.9×）

---

## 附：本次实测脚本与产物

| 路径 | 内容 |
|---|---|
| `D:\tmp\refpose_probe\model.onnx` | 前一轮产出的 ONNX，本次用于取 IO 契约与延迟 |
| `D:\tmp\refpose_probe2\probe5.py` | 窗口装配 + 形状/范围 + ONNX 契约 + 延迟 |
| `D:\tmp\refpose_probe2\lat.py` | p50/p95/max 延迟、mask 直通、归一化反解 |
| `D:\tmp\refpose_probe2\fin.py` | 6D 布局决定性判据（交织 vs 分半） |
| `D:\tmp\refpose_probe2\stats.py` | 统计量可复现性检验 |
| `D:\tmp\refpose_probe2\fk*.py` | FK 语义排查（12 种候选全部未闭合 → U1） |

**本次未做**：DirectML 复测（沿用上一轮结果）、UE 内实机、任何源码修改。
