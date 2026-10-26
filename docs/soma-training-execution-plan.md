# MotionWeaver 数据—模型—推理统一执行方案 v1

> 状态：方案定稿，待实施。本文取代此前口头讨论中关于帧率、窗口与场景划分的临时结论。
> 日期：2026-10-21。基线：`codex/uefn-soma-retarget` @ `de5fecc`。
> 相关文档：[SOMA 训练数据方向](soma-training-data-direction.md)、[玩家状态条件动画计划](player-state-conditioned-animation-plan.md)、[混合运动开发方案](../unreal-script/AILocomotionSystem/LocomotionPlan.md)、[SOMA 全库处理](soma-full-processing.md)。

---

## 0. 一句话结论

**数据已经齐了（73,101 条：主库 71,132 @120fps + 重定向库 1,969 @30fps），模型能力也够（主干骨数自由），真正要做的不是"再采数据"，而是把 SOMA 数据接进训练、把三个训练场景定死、把模型与程序化的分工划清。**

---

## 1. 已核实的现状

### 1.1 数据侧

| 项 | 实测值 | 来源 |
| --- | --- | --- |
| SOMA uniform 全库 | **71,132 / 71,132，全部 normalized** | `E:/MotionWeaverData/reports/soma_uniform/20261018-soma-full-v1/progress.json` |
| 未解决条目 | 0 | `unresolved.json` |
| 回读错误 | 0 | `acceptance.json` |
| 骨架 | 78 节点，含完整手指与面部 | `data/skeletons/soma_uniform_v1.json` |
| 主库帧率 | **120.0048 FPS**（全量 200/200 抽样一致） | `export-index.csv` |
| 磁盘占用 | 2.48 TB | `E:` 卷 |
| UE→SOMA 重定向 | 1,969 条成功，1 条待复核 | `processed/gameanimation_soma/20261018-soma-retarget-v1` |
| 重定向帧率 | **30.0 FPS**（全量 200/200 抽样一致，`source_fps: 30`） | 同批次 `export-index.csv` |
| 重定向骨架签名 | `2156896...412`，**与主库同一套** | 两侧 `skeletons/` 目录 |

**关键结论：两库骨架签名相同（同一个 78 骨），但帧率不同（120 vs 30）。可以逻辑合并为一个数据集，训练时必须按帧率分组——见 §5.1b。**

### 1.2 模型侧

| 项 | 实测值 | 含义 |
| --- | --- | --- |
| 主干骨数 | `num_joints` 从数据推导 | **不写死骨数，78 骨零改造可训** |
| 硬编码 `bones=79` | 仅 `train_conditioned_pose.py:157`、`train_stance_pilot.py:23` | 旧对照线，非主线 |
| `max_tokens` | 16 | 单窗口 token 上限 |
| `frames_per_token` | `2 ** down_t` | token 与帧数的唯一关系 |
| VQ-VAE `down_t/stride_t` | 2 / 2 | 当前配置 |
| fps 校验 | `1 <= fps <= 120` | **120.0048 超限，必须放宽** |
| 文本条件通道 | 存在但被 `MotionWeaverPoseModel:44-45` 显式拒绝 | 保持拒绝 |

### 1.3 推理侧

| 项 | 现状 |
| --- | --- |
| UE 插件运行内容 | 79 骨 VQ-VAE **重建**，依赖源动画 |
| 推理步长 / 播放延迟 | 4 帧 / 8 帧 = 400 ms |
| 已有 Warping/IK 规划 | `LocomotionPlan.md` 已定义，未实现 |
| 已有手动参考工具 | `UAIAnimationReferenceSet` + `pose_reference_lab` |

---

## 2. 三处必须修正的先前判断

| 编号 | 先前判断 | 实际 | 影响 |
| --- | --- | --- | --- |
| 1 | 78 与 79 骨不兼容，必须二选一 | 主干骨数自由，78 骨可直接训 | 从"重大决策"降级为"无需决策" |
| 2 | 建议降到 30 FPS | 120 原生更好，用 down_t 吸收 | 不重采样，改 fps 上限到 125 |
| 3 | 分场景各用不同 down_t 可共享 tokenizer | token 时间粒度不同，码本语义不同 | **必须两套 tokenizer** |

---

## 3. 定稿参数

### 3.1 帧率与 token 预算

数据统一保留 **120 FPS 原生**，不做重采样。`fps` 校验上限由 120 放宽到 **125**（仅放宽上限，不改任何已有逻辑）。

`frames_per_token = 2 ** down_t`，`tokens = round(窗口秒数 × 120) / frames_per_token`，上限 16。

### 3.2 三场景

**数据量按帧率拆分**（30fps 的分配见 §5.1b，短片段已扣除）：

| 场景 | 窗口 | tokens | 120fps 数据 | 30fps 数据 | 合计 |
| --- | ---: | ---: | ---: | ---: | ---: |
| **A 常规移动** | 0.4 s | 3 | 18,340 | 1,050 | 19,390 |
| **B 大姿态转换** | 0.6 s | 9 | 4,242 | 585 | 4,827 |
| **C 跑酷/攀爬** | 0.6 s | 9 | ~3,500 | 122 | ~3,622 |

30fps 列已扣除 <12 帧（A 场景）与 <18 帧（B/C 场景）的不可用片段：A 场景 14 条、B 场景 21 条、C 场景 1 条被排除，另有 110 条短片段集中在 `other`（pose 资产，不参与训练）。

**⚠️ 注意 120fps 列的构成**：`18,340` / `4,242` / `~3,500` 是按元数据 `content_type_of_movement` 粗分类的估计值，**尚未扣除窗口长度不足的片段**，也未经 S4 的实际窗口索引验证。真实可用条数在 S4 产出 `windows/` 索引后才能确定。这是本方案中唯一未实测的数据量数字。

### 3.2b token 完整预算（含历史与参考帧）

`max_tokens = 16`。完整预算必须同时容纳窗口、历史与参考帧：

| 场景 | 窗口 token | 历史 token（0.2 s） | 参考帧 token（`max_num_start_keyframe: 4`） | 合计 | ≤16 |
| --- | ---: | ---: | ---: | ---: | :---: |
| A（0.4 s @120fps） | 48/16 = 3 | 24/16 = 1.5 → 2 | 4 | **9** | ✅ 余 7 |
| B/C（0.6 s @120fps） | 72/8 = 9 | 24/8 = 3 | 4 | **16** | ⚠️ 余 0 |
| B/C（0.8 s @120fps） | 96/8 = 12 | 24/8 = 3 | 4 | **19** | ❌ 超 3 |

**这修正了先前的错误论证**：原文说「0.8 秒 = 12 token，只剩 4 余量，装不下参考帧」，但 12+4=16 恰好等于上限。真正装不下的是把历史（3 token）也算进去后：12 + 3 + 4 = 19 > 16。

**0.6 秒的决策依据**：9 + 3 + 4 = 16，刚好用满，零 buffer。若未来需要更多参考帧或更长历史，0.6 秒也会不够，届时需重新评估。

**注意**：0.6 秒对"站→蹲→趴"三段式可能不足，需按 §4.3 的场景 B 拆分策略处理。

### 3.3 推理参数

| 参数 | 值 | 延迟 |
| --- | --- | --- |
| 出姿态频率 | 30 Hz | — |
| 推理步长 | 4 帧 | 133 ms |
| 播放延迟 | 4 帧 | 133 ms |
| **总延迟** | — | **267 ms**（原 400 ms） |

窗口长度不影响延迟；延迟只由步长与播放延迟决定。**先用 4+4，实测后再评估 2+4（200 ms）。**

---

## 4. 模型与程序化的分工

这是本方案的核心设计决策。**不是"模型全做"也不是"程序化兜底"，而是按可控性划界。**

### 4.1 分工表

| 层 | 谁负责 | 理由 |
| --- | --- | --- |
| **位移（Root）** | CMC / Mover（默认）；Model Root 作为可选模式 | 碰撞与网络权威必须在引擎侧 |
| **姿态生成** | 模型 | 这是模型的唯一核心职责 |
| **步幅/朝向匹配** | Stride / Orientation Warping（程序化） | 确定性几何，模型做不准 |
| **脚部接触固定** | Foot Lock + Foot IK（程序化） | 需要世界空间约束，模型输出不保证 |
| **大姿态转换** | 模型生成 + 程序化相位门控 | 见 §4.2 |
| **手部环境接触** | **不做判定**，由 Gameplay 提供 IK 目标 | 无场景几何 |
| **手部自接触** | 几何判定（离线标签） | 可由骨架算出 |
| **补间渲染** | 引擎插值 | 标准做法 |

### 4.2 混合策略：模型 + 程序化的三个接触点

```text
1. 相位门控（程序化 → 模型条件）
   程序化计算 gait phase / stance progress，作为模型输入条件。
   模型不需要自己"发现"正在蹲下，直接告诉它"处于蹲下阶段 40%"。

2. 输出后处理（模型 → 程序化）
   模型输出姿态后，Foot Lock 固定支撑脚，Foot IK 修正地面残差。
   模型不需要输出"完美贴地"的姿态，只需要姿态合理。

3. 失败回退（程序化监控）
   Warping/IK 修正量超阈值 → 判定上游姿态不匹配 → 记录指标并回退传统动画。

**初始阈值（实测后调整）**：

| 指标 | 初始阈值 | 说明 |
| --- | ---: | --- |
| Foot IK 修正量 | 5 cm | 超过则说明模型脚位与地面偏差过大 |
| Stride Warping 缩放 | [0.8, 1.25] | 超出则步幅与速度严重不匹配 |
| Orientation Warping 角度 | 30° | 超出则朝向偏差过大 |
| 回退延迟 | 1 帧（33 ms @30Hz） | 回退到传统动画的切换成本，需实测确认 |

这些阈值直接决定 §9.1 阶段 C 的验收，以及 §9.2 的延迟预算（回退本身也计入延迟）。
```

**这个分工让模型专注它擅长的（生成合理姿态），把确定性约束交给程序化。** 这比让模型学"脚必须贴地"更可靠，也更容易调。

### 4.3 大姿态转换的场景拆分

"站立→蹲下→趴下"是**三段**，不是一段。0.6 秒窗口装不下完整三段。

| 策略 | 做法 | 适用 |
| --- | --- | --- |
| **分段生成（推荐）** | 每次转换由控制器触发一次，模型只负责当前段 | 站→蹲、蹲→趴 各一段 |
| 一次生成整段 | 需 1.2 秒以上窗口，token 超限 | 不可行 |

**结论：B 场景按"单段转换"训练，不追求一次生成三段。** 控制器用状态机串起两段。

---

## 5. 数据侧要做什么

### 5.1 三层数据组织

```text
E:/MotionWeaverData/
  soma_uniform/                   原始 BVH（不改写，120fps）
  raw/soma_uniform/<run>/         原生轨道（不改写）
  processed/soma_uniform/<run>/   固定骨架局部 TRS（不改写，120fps）
  processed/soma_train_v1/        ← 新增：训练视图
    <scene>/                       A / B / C 三个场景目录
      clips/<id>/                 30Hz 推理视图（按需）
      skeletons/                  共享骨架
    dataset.json                  manifest + training_signature
    stats/                        归一化统计量
    windows/                      窗口索引（按秒定义）
```

**原始数据永不改写。训练视图是派生物，可随时重建。**

### 5.1b 两个数据源：逻辑合并 + 物理隔离

现有两个 SOMA processed 库，**骨架签名完全相同**（`215689644b435e63...412`），但帧率不同：

| 来源 | 条数 | 帧率 | 路径 |
| --- | ---: | ---: | --- |
| 主库（SOMA 原生） | 71,132 | **120.0048** | `processed/soma_uniform/20261018-soma-full-v1` |
| 重定向库（UE 来源） | 1,969 | **30.0** | `processed/gameanimation_soma/20261018-soma-retarget-v1` |
| **合计** | **73,101** | 混合 | — |

**决定：不物理合并，采用「逻辑合并 + 物理隔离」。**

```text
processed/soma_train_v1/                 统一训练视图
  dataset.json                           登记 73,101 条，每条带 fps 与 fps_group
  clips/<id>/                            四键 NPZ（positions/rotations/timestamps/root_track）
  skeletons/                             共享骨架（一份）
  stats/<fps_group>/                     按组独立统计量
  windows/<fps_group>/                   按组独立窗口索引
  groups/
    fps120/                              71,132 条
    fps30/                               1,969 条
```

规则：

1. 每条 clip 必带 `fps` 与 `fps_group` 字段。
2. **训练时按 `fps_group` 分组，不同帧率不进同一个 batch**（DataLoader 按组划分）。
3. 统计量按组独立计算，不跨组混算。
4. 需要混合时只能**显式降采样**（120→30），不能隐式混入，且必须在配置中写明。
5. **评测主表只用 120fps 主库**；C 场景的 30fps 子集单独出一份指标，标注「内部参考，不与主表混算」（见 §8.5）。

**为什么不物理合并、也不上采样：**

| 方案 | 结论 | 原因 |
| --- | --- | --- |
| 上采样 30→120 | ❌ 否决 | 重定向库源头 UE 动画本身只有 30fps（`source_fps: 30`, `rate_scale: 1`），插值到 120 是伪造信息 |
| 降采样 120→30 | ❌ 否决 | 丢掉 71,132 条的真实细节，违背保留 120 原生的决定 |
| 重跑重定向为 120fps | ❌ 不可行 | 源头无 120fps 信息 |
| **逻辑合并 + 物理隔离** | ✅ 采用 | 零伪造，两类数据都能用 |

**重定向库的实际构成（实测，按动作名语义分类）：**

| 类别 | 条数 | 主要服务场景 |
| --- | ---: | --- |
| walk | 548 | A |
| run | 456 | A |
| crouch | 182 | A / B |
| jump / land | 143 | B |
| **跑酷（traversal 78 + slide/roll 123）** | **221** | **C** |
| idle | 54 | A |
| poses / poseassets | ~138 | 参考姿态，不直接训 |
| 其他 | ~227 | 待分类 |

**结论：1,969 条主要补充 A 场景（约 1,058 条），B 约 325 条，C 221 条（占该场景的重要补充）。**

**⚠️ 早先的判断错误**：曾按目录名只数出 `traversal` 78 条，漏掉了 `Slide_*`（123 条滑铲/翻滚）等名字不含 traversal 的跑酷动作。实际跑酷类为 **221 条**。

**可用性（按窗口长度筛选）：**

| 场景 | 所需帧数 @30fps | 可用条数 |
| --- | ---: | ---: |
| A（0.4 s） | ≥ 12 | 1,823 / 1,969 |
| B/C（0.6 s） | ≥ 18 | 1,806 / 1,969 |

**146 条 < 12 帧的短片段不可用于训练**，需进 `rejected_clips.json` 并记录原因。

### 5.1c 关键设计：按「时间粒度」而非「帧数」对齐

30fps 数据进 A 场景时，若沿用 `down_t=4`（1 token = 16 帧 @30fps = 533 ms），与 120fps 的 133 ms 语义完全不同，码本无法共用。

**解法：让两种帧率的时间粒度一致。**

| 来源 | fps | down_t | 1 token 帧数 | **1 token 时间** |
| --- | ---: | ---: | ---: | ---: |
| 主库 A | 120 | 4 | 16 | **133 ms** |
| 重定向 A | 30 | **2** | 4 | **133 ms** ✅ 等价 |

```text
120fps + down_t=4 → 16 帧 = 133 ms
 30fps + down_t=2 →  4 帧 = 133 ms   ← 时间粒度相同，码本语义可对齐
```

**结论：A 场景两种帧率的时间粒度相同（都是 133 ms/token），码本语义一致，因此只需一个 `tokenizer_A`。**

同理适用于 B/C：

| 来源 | fps | down_t | 1 token 帧数 | 1 token 时间 |
| --- | ---: | ---: | ---: | ---: |
| 主库 B/C | 120 | 3 | 8 | **66 ms** |
| 重定向 B/C | 30 | **1** | 2 | **66 ms** ✅ 等价 |

**结论：两种帧率可以完全对齐，不存在精度损失。**

规律：**30fps 的 `down_t` 比 120fps 少 2 即可**（因为 120/30 = 4 = 2²）。

| 场景 | 120fps down_t | 120fps 1 token | 30fps down_t | 30fps 1 token | 时间误差 |
| --- | ---: | ---: | ---: | ---: | ---: |
| A 常规 | 4 | 133.33 ms | **2** | 133.33 ms | 0.004% |
| B/C 跑酷 | 3 | 66.66 ms | **1** | 66.67 ms | 0.004% |

0.004% 的偏差仅来自 `120.0048` 与 `30` 的舍入差，可忽略。

**token 数也完全一致（两种帧率同窗口同 token 数）：**

| 场景 | 窗口 | 120fps tokens | 30fps tokens | 上限 |
| --- | ---: | ---: | ---: | ---: |
| A | 0.4 s | 3（48 ÷ 16） | 3（12 ÷ 4） | 16 ✅ |
| B/C | 0.6 s | 9（72 ÷ 8） | 9（18 ÷ 2） | 16 ✅ |

**因此模型结构完全不需要为 30fps 改，也不需要为 30fps 单独训 tokenizer。**

**实现要求**：每条 clip 记录 `fps` 与 `down_t`；`down_t` 由 fps 推导，公式为

```text
down_t(fps) = down_t(基准帧率) - round(log2(基准帧率 / fps))
```

对 `基准=120, fps=30` 即 `down_t - 2`。训练时按 `(fps, down_t)` 组合加载对应的 tokenizer。

**这修正了本方案早先的错误判断**：曾认为「30fps 无法与 66ms 对齐，只能降级处理」，实际 `30fps + down_t=1` 正好是 66.67ms。

### 5.2 新增模块

| 模块 | 职责 |
| --- | --- |
| `data/runtime/soma_train_dataset.py` | 读 SOMA processed + NPZ，输出 Schema v3 clip（78 骨、Root 相对、按秒窗口） |
| `data/tools/build_soma_training_set.py` | 批次入口：场景划分 → 统计量 → dataset.json |
| `data/tools/build_semantic_index.py` | 149 种自由值 → 受控词表 → 结构化标签 |
| `data/runtime/posture_transition.py` | 大姿态转换检测：从 body_position 多值 + 几何验证 |
| `data/runtime/self_contact.py` | 自接触几何候选（指尖球 + 躯干胶囊 + 头球） |

### 5.3 Schema v3 契约

```json
{
  "schema_version": 3,
  "skeleton": {"id": "soma_uniform_v1", "joints": 78, "signature": "..."},
  "fps": 120.00480019200768,
  "windows": {"unit": "seconds", "history": 0.2, "future": 0.4},
  "training_contract": "pose_only_root_authoritative",
  "scene": "A | B | C",
  "conditions": ["requested_stance", "accepted_stance", "can_uncrouch",
                 "locomotion_class", "speed_norm", "heading_error", "grounded", "root_mode"],
  "contact": {"states": ["not_applicable", "unknown_requires_scene", "noncontact_candidate", "contact_candidate"], "root_mode": "model|cmc|mover"}
}
```

**窗口一律用秒定义**，训练时按 `round(seconds × fps)` 推导帧数。这样换帧率语义不变。

### 5.4 分组划分（硬约束）

按 [soma-training-data-direction.md:136](soma-training-data-direction.md) 执行：

> 先按来源动作/录制会话确定分区。左右镜像、重采样、裁剪、uniform/proportional/G1 同源版本、跨骨架转换结果继承同一分区。

**必须编码进构建器，不能事后补。** 否则同一演员的同类动作会跨训练集与测试集。

---

## 6. 语义条件

### 6.1 词表收敛

`content_type_of_movement` 有 **149 种**不同值。收敛为两层：

| 层 | 用途 | 是否进训练 |
| --- | --- | --- |
| `locomotion_class`（8-10 种） | 粗粒度，进模型条件 | ✅ |
| `action_label`（原文 149 种） | 检索、审核、可视化 | ❌ |

**冲突值标 `unresolved`，不猜。** 已知冲突见 [soma-source-semantics.md:16-23](soma-source-semantics.md)。

### 6.2 条件表（对齐 player-state-conditioned-animation-plan.md 第三节）

| 条件 | 取值 | 运行时来源 |
| --- | --- | --- |
| `requested_stance` | stand / crouch | 玩家输入 |
| `accepted_stance` | stand / crouch | 碰撞批准结果 |
| `can_uncrouch` | bool | 头顶净空检查 |
| `locomotion_class` | idle/walk/run/jump/climb/slide/other | 控制意图 + 状态机 |
| `speed_norm` | 0-1 | CMC/Mover 实际速度 |
| `heading_error` | 弧度 | 意图方向与实际方向差 |
| `grounded` | bool | 碰撞查询 |
| `root_mode` | model / cmc / mover | 配置 |

**`requested_stance` 与 `accepted_stance` 必须分开。** 起身被拒时不能给模型"必定站起"的目标。

**`root_mode` 的双重位置**：它同时出现在 `conditions`（作为模型输入）与 `contact.root_mode`（标注该帧接触标签的语义归属）两处。**以 `contact.root_mode` 为准**记录每条接触标签的产生模式；`conditions.root_mode` 是该帧推理时的模型输入。二者在训练数据中通常相同，但推理时可能不同（例如改配置后用不同模式推理），因此分两处存，不做合并。

**训练侧注意**：`requested_stance` / `accepted_stance` / `can_uncrouch` 在离线 SOMA 数据中**不存在**（无玩家输入、无碰撞批准）。离线训练时这三项标 `unknown` 并 mask，不能伪造。这是 §5.3 Schema v3 的条件列表与本节条件表的已知差异：**结构定义完整，但离线数据只能填充其中一部分**。

### 6.3 文本通道

保持 `MotionWeaverPoseModel:44-45` 的显式拒绝。理由：资产路径解析出的文本，运行时拿不到。

将来若要在运行时产生稳定文本（如任务系统显式写入），再启用 `pose_backbone.py` 的文本通道。

---

## 7. 接触

### 7.1 四态语义

| 状态 | 含义 | 训练处理 |
| --- | --- | --- |
| `not_applicable` | 该动作不需要判手（普通移动） | mask |
| `unknown_requires_scene` | 需要判但无场景（跑酷/攀爬） | mask |
| `noncontact_candidate` | 有证据未接触 | 可用 |
| `contact_candidate` | 有证据接触 | 可用 |

**`not_applicable` 与 `unknown_requires_scene` 必须分开**，不能都用 -1。

### 7.2 分工

| 部位 | 方法 | 可信度 |
| --- | --- | --- |
| 脚触地 | 已有 `world_foot_contacts` + 迟滞 | 启发式，可用 |
| 自接触 | 几何：指尖球(1cm) + 手掌/躯干胶囊 + 头球 | 高，可验证 |
| 环境接触 | **不判定**，标 `unknown_requires_scene` | 无 |

### 7.3 非解剖挂点必须排除

UE 骨架含 `weapon_l/r`、`ik_hand_*`、`props_root`、`poi` 等，位置常与手重合。**自接触计算前必须白名单过滤**，否则大量假阳性。

### 7.4 接触必须绑定 root_mode

同一帧的脚接触，在 Model Root 与 CMC 下语义不同：

- Model Root：模型自身一致性约束
- CMC/Mover：模型姿态与位移是否匹配的检验

**`contact` 字段必须带 `root_mode` 一起存。**

---

## 8. 训练计划

### 8.1 tokenizer 配置（按帧率独立训练）

**先澄清一个此前的错误结论。** `down_t` 不是抽象参数，而是**编码器层数**：

```python
# model/MotionWeaver/tokenizer/neural_modules/encdec.py:26-32
for i in range(down_t):
    blocks.append(nn.Sequential(
        nn.Conv1d(width, width, filter_t, stride_t, pad_t),   # stride=2 下采样
        Resnet1D(width, depth, dilation_growth_rate, ...),
    ))
```

`down_t=4` 是 4 个 stride-2 卷积层 + 4 个 Resnet 块；`down_t=2` 只有 2 层。
**层数不同 ⇒ 网络深度不同 ⇒ 权重不能共享。** 因此「同一码本、双帧率共用一个 tokenizer」在本架构下不成立。

**正确配置：4 个独立 tokenizer。**

| tokenizer | 服务场景 | fps | down_t | 层数 | 1 token | 数据条数 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `tok_A120` | A 常规移动 | 120 | 4 | 4 | 133 ms | 18,340 |
| `tok_A30` | A 常规移动 | 30 | **2** | 2 | 133 ms | 1,050 |
| `tok_BC120` | B 大姿态 + C 跑酷 | 120 | 3 | 3 | 66 ms | ~4,242 + ~3,500 = **~7,742** |
| `tok_BC30` | B 大姿态 + C 跑酷 | 30 | **1** | 1 | 66 ms | 707 |

**时间语义完全一致**（133 ms / 66 ms，误差 0.004%），但**权重各自独立**，因为编码器深度不同。

`down_t` 推导：`down_t(fps) = down_t(基准) - round(log2(基准fps / fps))`。对 120→30 即减 2。
该公式仅在基准帧率为目标帧率的 **2 的整数次幂倍** 时精确；其他帧率（如 24）会引入误差，需单独配表。

**代价与缓解：**

| 问题 | 说明 |
| --- | --- |
| 训练量翻倍 | 从 2 个变 4 个 tokenizer |
| `tok_A30` 仅 1,050 条 | 数据量偏小，过拟合风险；用下节 §8.1b 的策略缓解 |
| `tok_BC30` 仅 707 条 | 同上 |

### 8.1b 小数据 tokenizer 的缓解策略

`tok_A30`（1,050 条）与 `tok_BC30`（707 条）数据量远小于 120fps 对应版本，直接训练风险高。三种可选策略，按优先级：

| 策略 | 做法 | 风险 |
| --- | --- | --- |
| **① 复用 120fps 码本**（推荐先试） | 冻结 `tok_A120`/`tok_BC120` 的 codebook，只训新 encoder 映射到同一码本空间 | encoder 深度不同，映射可能学不好 |
| ② 从 120fps 版本热启动 | 用 `tok_A120` 权重初始化，但层数不同只能部分迁移（浅层 Conv 可迁移，深层新增） | 实现复杂 |
| ③ 独立从零训练 | 最简单 | 数据少，欠拟合 |

**必须先做实验再定**，见 §8.1c。

### 8.1c 验证实验（S2 必做，否则 §8.1 的策略无依据）

| 项 | 内容 |
| --- | --- |
| 样本 | 同一动作源若存在 120fps 与 30fps 两版，取配对样本；否则各取 20 条代表性动作 |
| 分组 | 至少 30 条 30fps（A/B/C 各 10 条） |
| 对比项 | ① 独立训练 vs ② 冻结码本只训 encoder vs ③ 热启动 |
| 指标 | 重建误差（关节 RMSE）、码本使用率（perplexity）、码本冲突率 |
| 判定 | 若策略①的重建误差接近②/③且明显优于随机初始化，则采用① |

### 8.2 训练顺序

1. `tok_A120` 训练（数据最多，先验证链路）
2. `tok_BC120` 训练
3. **§8.1c 的小数据策略实验**（决定 `tok_A30` / `tok_BC30` 怎么训）
4. `tok_A30` / `tok_BC30` 训练
5. Pose Token 主干（结构共享，权重按场景分开）
6. 离线验收：无参考 / 固定参考 分别报告
7. 与 `baseline_79_v1` 同条件对比

### 8.3 验收判据

| 项 | 判据 |
| --- | --- |
| 重建质量 | 关节位置 RMSE；**79 骨基线的对比口径见 §8.3b** |
| 无参考能力 | 必须单独报告，不能被硬覆盖后的零误差掩盖 |
| 脚滑 | 接触帧世界脚速，源动作 vs 生成 |
| 稳定性 | 至少 2 个 seed 结论一致 |
| 显存 | 单卡可训，记录峰值；训练资源估算见 §8.5 |

### 8.3b 78 骨与 79 骨基线的对比口径

`baseline_79_v1` 是 79 骨，SOMA 是 78 骨，**关节数不同，RMSE 不能直接相减**。三种可行口径：

| 口径 | 做法 | 评价 |
| --- | --- | --- |
| **共同关节子集** | 建立 78↔79 的关节对应表，只在共有骨上算 RMSE | 推荐；需人工核对对应关系 |
| 末端效应器 + 关键关节 | 只比手/脚/头/骨盆的位置误差与关键关节角 | 推荐；对骨数不敏感，但丢失全身信息 |
| 各自全骨 RMSE | 直接比较两个数 | **禁止**；分母不同，无意义 |

**要求**：S8 之前必须产出 78↔79 关节对应表（人工确认），否则「与基线同口径」是空话。

### 8.4 训练资源与时间线

| 项 | 当前状态 |
| --- | --- |
| GPU | 本机 RTX 4070 Laptop 8GB（历史实验用）；S5 起需评估是否租用云 GPU |
| 120fps + 78 骨序列长度 | `tokenizer` 输入为 48 帧（A）/ 72 帧（B/C）× 78 骨 × 特征维，需 S2 实测显存 |
| 单 tokenizer 训练时长 | **未测**，S2 后补 |
| S2→S10 里程碑日期 | **未定**，需在 S2 完成后排期 |

**这是方案的已知缺口**：在没有实测显存与单步耗时之前，无法给出可信的训练时长与 GPU 需求。

### 8.5 评测的帧率覆盖

**先前规则「评测只用 120fps 主库」需要修正。**

C 场景的 122 条 30fps 跑酷数据是该场景的重要补充，若完全不做评测覆盖，这批数据训练出的能力无法验证。30fps 采样对跑酷这类快速动作最敏感，风险最高。

| 场景 | 评测数据 | 报告方式 |
| --- | --- | --- |
| A / B | 120fps 主库 | 主表 |
| **C** | 120fps + **30fps 子集单独报告** | 30fps 结果标注「内部参考，不与主表混算」 |

**因此 §5.1b 规则 5 修正为**：主表只用 120fps；30fps 子集单独出一份指标，可比较但不并入主表。

---

## 8A. 评测：已有的与要补的

本节回答一个此前混淆过的问题：**MotionWeaver 到底有没有 in-betweening（给定两端补中间）评测？**

### 8A.1 已有设施（不需要新建）

[`training/evaluation/evaluate_conditioned_baselines.py`](../training/evaluation/evaluate_conditioned_baselines.py) 已经是一个完整的 in-betweening 评测框架：

| 能力 | 代码位置 | 说明 |
| --- | --- | --- |
| 参考帧注入 | `with_references(base, offsets)` | 只暴露指定帧，其余为零 |
| **Lerp 基线** | `_interpolate_pose` / `_interpolate_rotations` | 位置线性插值 + 四元数 SLERP |
| Hold 基线 | `predict(method="hold")` | 保持最后一帧，最差基线 |
| 四种参考场景 | `evaluate()` 内 `scenarios` | 见下表 |
| 评分 | `score()` | 关节 RMSE、旋转角误差、速度误差、脚滑 |
| 分组聚合 | `aggregate()` + `by_category` | 逐 clip 等权，不按帧池化 |

四种场景即标准 in-betweening 协议：

| 场景 | 给定内容 | 难度 |
| --- | --- | --- |
| `no_reference` | 仅历史 | 真在线难度 |
| `middle_reference` | 历史 + 中间帧 | 中 |
| `end_reference` | 历史 + 终点帧 | in-betweening |
| `middle_and_end` | 历史 + 中间 + 终点 | 最容易 |

### 8A.2 缺什么

| 缺口 | 原因 | 补法 |
| --- | --- | --- |
| **SOMA 数据不可直接读取** | `load_raw` 硬编码要求 `positions/rotations/timestamps/root_track` 四键 NPZ，SOMA 存的是 `body_positions` + 局部 TRS | 由 `soma_train_dataset.py` 产出四键 NPZ（与训练共用，见 §5.2） |
| 端点连续性指标 | `score()` 无此项 | 在现有文件内加约 20 行 |
| in-betweening 抽样协议 | 现按 `middle_window_per_clip` 选窗 | 加一个 A/B 端点协议，约 40 行 |

**结论：不新建 `evaluate_inbetweening.py`。** 在现有 `evaluate_conditioned_baselines.py` 内补两项即可，避免两套评测口径。

### 8A.2b 一个实施时会撞上的细节：接触标签是派生的

`build_index` 里的 `contact_bits` **不是从数据里读的，是现场派生的**：

```python
# conditioned_motion.py:140-143
contacts = world_foot_contacts(raw["positions"], raw["root_track"], skeleton, fps)
contact_bits = base64.b64encode(np.packbits((contacts.numpy() > 0.5).astype(np.uint8).ravel()).tobytes())
```

| 含义 | 影响 |
| --- | --- |
| 适配器**不需要**预先计算接触标签 | ✅ 工作量减少 |
| 只要给对 `positions` + `root_track`，接触自动派生 | ✅ |
| 派生用的是 v1 的 `world_foot_contacts`（79 骨启发式） | ⚠️ 用在 SOMA 78 骨上**未经验证** |
| SOMA 自己已算过 `contact_states`（三态） | ⚠️ 两套接触口径可能不一致 |

**要求**：SOMA 适配器产出四键 NPZ 后，必须比对「`world_foot_contacts` 派生结果」与「SOMA 已算的 `contact_states`」，不能默认两者等价。

**比对方法（此前未定义，现补）**：

| 项 | 定义 |
| --- | --- |
| 比对单位 | **逐脚逐帧的二值接触**（左脚、右脚各一列） |
| SOMA 三态映射 | `contact_candidate`(1) → 1；`noncontact_candidate`(0) → 0；`-1 unknown` → **排除，不参与比对** |
| `world_foot_contacts` 映射 | 值 > 0.5 → 1，否则 0 |
| 不一致率 | `不一致帧数 / 双方均有定义的帧数` |
| 抽样规模 | 先抽 **30-50 个 clip**（覆盖 walk/run/crouch/jump/traversal）做人工可视化核对，建立基线 |
| 阈值 | **> 5% 停止并排查**；5% 以内的差异需在报告中列出分布，不能静默接受 |

**为什么是 5%**：SOMA 用「稳定区间 + 多高度聚类」，v1 用「脚速阈值 + 迟滞」，两套启发式在动作边界处必然有差异。5% 是「可接受的方法差异」与「口径错配」的分界，由 30-50 条人工核对后微调。

### 8A.3 关键依赖

```text
soma_train_dataset.py（S2）
        ↓ 产出四键 NPZ + contract.json
evaluate_conditioned_baselines.py  ← 直接可读
        ↓
第一份 in-betweening 报告
```

**评测不是独立工作项，它是 S2 适配器的副产品。** 适配器一通，训练与评测同时解锁。

### 8A.4 口径要求

| 要求 | 原因 |
| --- | --- |
| **必须同时报 `no_reference` 与 `middle_and_end`** | 只报后者会把「给终点补中间」的成绩当在线能力，严重高估 |
| 报告标注 `oracle_root_condition: true` | 现有报告已含此字段，Root 用真值，是理想上界 |
| 不与外文论文数字直接比 | 数据集不同（SOMA/BONES-SEED vs HumanML3D/AMASS），跨库比数无意义 |
| Lerp 基线是必报项 | 模型赢不过 Lerp 说明没学到东西，这是最不可辩驳的对照 |

### 8A.5 不引入外部开源 benchmark 的理由

| 选项 | 代价 | 收益 |
| --- | --- | --- |
| 引入 HumanML3D/CondMDI 等外部 benchmark | 下载权重 + 骨架格式转换 + 跑对方推理脚本，数周且易错 | 与文献数字可比 |
| 本地评测（推荐） | 0（设施已有） | 内部可比、可复现、口径统一 |

跨数据集比较在方法论上无效：相同误差数值在两个不同骨架/坐标系/单位下不具可比性。

---

## 9. 推理计划

### 9.1 分阶段（对齐 LocomotionPlan.md 阶段 A-E）

| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| A | 78 骨常量参数化，契约校验 | 错误契约无法加载 |
| B | 无模型回放：录制 78 骨姿态跑 CMC→AnimGraph | 无 Root/Pelvis 双重位移 |
| C | Foot Lock / Foot IK / Warping | 分别输出 FootSlip 与修正量 |
| D | 三种 Root 模式接入 | 同一帧无双重位移 |
| E | 新模型异步推理 + 超时回退 | 回退计数与日志 |

### 9.2 延迟目标

| 阶段 | 目标 |
| --- | --- |
| 初版 | 267 ms（4+4） |
| 优化 | 200 ms（2+4），需推理频率翻倍 |

**产品约束（267 ms 的适用边界）**：

| 场景类型 | 267 ms 是否可接受 | 说明 |
| --- | --- | --- |
| 单人叙事 / 探索 / 过场 | ✅ 可接受 | 姿态时滞不影响判定 |
| 非竞技多人 | ⚠️ 需实测 | 取决于对手动作可预测性 |
| 竞技 / 快速反应 | ❌ 不可接受 | 格斗 <100 ms、平台跳跃 100-150 ms 是常见阈值 |

**本方案的目标场景是前两类**。若将来要用于竞技场景，必须做到 2+4（200 ms）甚至更低，届时推理频率需翻倍，GPU 成本另算。

**旧模型 267 ms 的测量不能沿用**，新模型必须重测。

---

## 10. 实施顺序

| 阶段 | 内容 | 依赖 | 可回退 |
| --- | --- | --- | --- |
| **S0** | 冻结 `baseline_79_v1`（封版权重 + AIAnimation 插件原地不动） | — | — |
| **S1** | fps 校验上限 120→125 | — | ✅ |
| **S2** | 100 条分层样本打通 Schema v3 链路（产出四键 NPZ，训练与评测共用；按 `fps_group` 分目录） | S1 | ✅ |
| **S2b** | 补评测两项：端点连续性 + A/B 端点抽样协议（§8A.2） | S2 | ✅ |
| **S3** | 语义词表 + 冲突清单 | — | ✅ |
| **S4** | 三场景窗口索引（按秒） | S2 | ✅ |
| **S5** | `tokenizer_A` 训练 | S4 | ✅ |
| **S6** | 自接触几何 + 四态接触 | S3 | ✅ |
| **S7** | `tokenizer_BC` 训练 | S4 | ✅ |
| **S8** | Pose Token 三场景训练 | S5+S7 | ✅ |
| **S9** | ONNX 导出 + UE 无模型回放 | S8 | ✅ |
| **S10** | Warping/IK + 模型接入 | S9 | ✅ |

**100 条分层样本（必须覆盖两种帧率）**：

| 维度 | 分配 | 目的 |
| --- | --- | --- |
| 120fps | 70 条 | 链路验证 |
| **30fps** | **30 条** | **验证 `down_t - 2` 规则与 §8.1c 的小数据策略** |
| 30fps 内部分布 | A 10 / B 10 / C 10 | 每个场景都覆盖到 |

120fps 的 70 条按动作类型分：25 walking/jogging、15 jumping、15 climbing box、10 crouching、5 gesture/dancing。

**30fps 的 30 条不可省略**：若这 100 条全取自 120fps 主库，§5.1c 的 `down_t - 2` 对齐规则与 §8.1c 的「复用码本」策略都得不到验证，S5/S7 的小数据 tokenizer 将在没有依据的情况下开工。

**验证方法**（对应 §8.1c）：同一动作源若存在 120/30 两版则取配对；否则各取代表性动作。对比「独立训练 / 冻结码本 / 热启动」三种策略的重建误差与码本使用率。

---

## 11. 明确不做的事

| 项 | 原因 |
| --- | --- |
| 环境接触自动判定 | 无场景几何，只能猜 |
| 自动生成姿态硬约束 | 已改为手动放置（`UAIAnimationReferenceSet`） |
| 文本条件接入训练 | 运行时无法构造同一文本 |
| 重采样到 30 FPS | 丢细节，用 down_t 吸收更好 |
| 一次生成三段大姿态转换 | token 装不下，改分段 |
| 只推理关键帧 + 补帧生成 | 插值锁定质量上限 |
| **新建 `evaluate_inbetweening.py`** | 现有 `evaluate_conditioned_baselines.py` 已含 Lerp/Hold 与四场景，另起一套会分裂口径（见 §8A.2） |
| **引入外部开源 benchmark** | 跨数据集（SOMA vs HumanML3D/AMASS）比数无意义，见 §8A.5 |
| 改代码同时跑批次 | `verify_resume_manifest` 会拒绝续写 |

---

## 12. 待确认项

| 编号 | 问题 | 选项 |
| --- | --- | --- |
| Q1 | 三场景是共享主干还是各训一个 | 建议共享结构、权重分开 |
| Q2 | B 场景 0.6 秒是否足够单段转换 | 需 100 条样本验证 |
| Q3 | `locomotion_class` 具体分几类 | 需看 149 种值的分布 |
| Q4 | Model Root 模式是否在本期实现 | 建议放到 D 阶段 |
| Q5 | in-betweening 抽样：端点间隔取多少 | 建议 0.4-0.6 秒，与 B 场景窗口一致 |
| Q6 | `no_reference` 与 `middle_and_end` 是否都要报 | **必须都报**，见 §8A.4 |
| Q7 | 重定向库 1,969 条（fps30）如何参与训练 | 已定：独立 `fps_group=fps30`，`down_t` 比 120fps 少 2，与主库时间粒度完全对齐（§5.1c） |
| Q8 | 是否允许 120→30 显式降采样以扩充 fps30 组 | 已无必要：30fps 可原生对齐，不需要跨帧率转换 |
| Q9 | 221 条 30fps 跑酷数据是否进 C 场景 | **进**：`down_t=1` 与 120fps 的 `down_t=3` 同为 66ms，无精度损失 |

---

## 13. 风险登记

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| `down_t=3` 时序仍可能太粗 | B 场景质量不足 | 100 条样本先验证 |
| B/C 数据重叠 | 条件混淆 | 按 `package` 细分（`package` 是 `seed_metadata_v004.csv` 的字段，8 类：Locomotion 37,260 / Communication 10,749 / Interactions 7,322 / Dances 5,503 / Gaming 4,351 / Everyday 2,908 / Sport 1,998 / Other 1,041） |
| 大姿态转换 4,242 条偏少 | 欠拟合 | 共享主干 + 增广 |
| 78 骨权重全部重训 | 时间成本 | 分阶段，先跑通 100 条 |
| 120fps 序列长度 | 训练慢 | down_t 吸收；必要时按场景调 |
| **只报 in-betweening 成绩** | 把「给终点补中间」误当在线能力 | 强制同时报 `no_reference`（§8A.4） |
| **SOMA 四键 NPZ 与 v1 口径不一致** | 新旧评测数不可比 | 报告内写明契约版本，不跨版本算提升率 || **两套接触口径不一致** | `world_foot_contacts`（v1 派生）vs SOMA `contact_states`（三态）可能给出不同标签 | 适配器产出后强制比对两者不一致率，超阈值停止（§8A.2b） |
| **帧率混批** | 120fps 与 30fps 进同一 batch，时间步长不一致，模型学乱 | DataLoader 按 `fps_group` 划分；统计量按组独立（§5.1b） |
| **误把 30fps 样本上采样** | 制造「看起来 120 实际是插值」的假数据 | §5.1b 明确否决上采样；源头 UE 动画只有 30fps 信息 |

---

## 修订历史

| 日期 | 变更 | 依据 |
| --- | --- | --- |
| 2026-10-21 | 首版：数据/模型/推理统一方案 | SOMA 全库完成实测、模型配置核对、candidate24 实验数据 |
| 2026-10-21 | 新增 §8A 评测章节；S2 增加四键 NPZ 产出、新增 S2b 评测补齐；§11 补两条「不做」；新增 Q5/Q6 与两条评测风险 | 核查 `evaluate_conditioned_baselines.py` 已有 Lerp/Hold/四场景；核查 `load_window/load_raw` 硬性要求四键 NPZ |
| 2026-10-21 | 新增 §5.1b「逻辑合并 + 物理隔离」；§1.1 改正「可直接合并」的错误结论；§3.2 场景表标注 fps_group；新增 Q7/Q8 与两条帧率风险 | 实测两库帧率不同：主库 71,132 条全为 120.0048fps，重定向库 1,969 条全为 30.0fps（各 200 条抽样 200/200 一致）；骨架签名相同 `2156896...412`；重定向 `source_fps: 30`、`rate_scale: 1` |
| 2026-10-21 | ~~**改正「30fps 无法对齐 66ms」的错误结论**：§5.1c 给出 `down_t - 2` 对齐规则（误差 0.004%）；§8.1 tokenizer 从 4 个降为 2 个（双帧率共享码本）~~ **← 本条后半句已被下一行推翻，保留作为纠错记录** | 实测：`30fps + down_t=1` = 66.67ms，与 `120fps + down_t=3` = 66.66ms 等价；两种帧率 token 数完全一致（A 3/3，B/C 9/9）；按动作名语义分类得跑酷类 221 条（traversal 78 + slide/roll 123），此前只按目录名数出 78 条。**教训：当时未核实 `down_t` 的实现语义就下「可共享码本」结论** |
| 2026-10-21 | 新增 §8A.2b（接触标签是派生的，不是读的）；§13 补接触口径风险 | 逐条核对文档 14 项断言（清单见附录 A），发现 `contact_bits` 由 `world_foot_contacts` 现场派生（`conditioned_motion.py:140-143`），与 SOMA 三态接触存在双口径 |
| 2026-10-21 | **按外部评审修正 P0/P1/P2 共 14 项**：§3.2 拆分 120/30 数据并新增 §3.2b 完整 token 预算；§8.1 **推翻「共享码本」改为 4 个独立 tokenizer**（down_t=层数），新增 §8.1b/§8.1c 小数据策略与验证实验；§8.3b 补 78↔79 对比口径；§8.4 补训练资源缺口；§8.5 修正评测帧率覆盖并改 §5.1b 规则 5；§5.3 conditions 补全；§6.2 补 root_mode 双位置说明与离线缺失说明；§8A.2b 补接触比对方法；§4.2 补回退阈值；§9.2 补产品约束；S2 样本补 30fps 30 条；P2 补 package 字段解释、tokenizer 数据量算式；新增附录 A | 评审指出：`encdec.py:26` 证明 `down_t` 是编码器层数（`for i in range(down_t)`），层数不同则权重不可共享；§3.2 与 §5.1b 数据矛盾；Schema 与条件表不一致；多处阈值/口径未定义 |

---

## 附录 A：文档断言核对清单

2026-10-21 对本文档逐条核对的事实断言（全部通过，发现 1 项表述需补）：

| # | 断言 | 核对方式 | 结果 |
| ---: | --- | --- | --- |
| 1 | 16 个引用路径全部存在 | `Test-Path` 逐条检查 | ✅ |
| 2 | `motionweaver_pose_model.py:44-45` 拒绝离线文本 | 读取该行 | ✅ |
| 3 | `unreal_dataset.py:231` fps ≤ 120 | 读取该行 | ✅ |
| 4 | `conditioned_motion.py:20` `CONTRACT_ID = reference_guided_root_pose_v1` | 读取该行 | ✅ |
| 5 | `max_tokens: 16` | `motionweaver_pose.yaml:70` | ✅ |
| 6 | `frames_per_token = 2 ** down_t` | `pose_backbone.py:227` | ✅ |
| 7 | `bones=79` 仅两处 | `train_conditioned_pose.py:157`、`train_stance_pilot.py:23` | ✅ |
| 8 | 主干 `num_joints` 从数据推导 | `pose_model.py:71`、`motion_vqvae.py:57` | ✅ |
| 9 | SOMA 全库 71,132 条 | `export-index.csv` 行数 | ✅ |
| 10 | 重定向库 1,969 条 | 目录计数 | ✅ |
| 11 | 两库骨架签名相同 | 两侧 `skeletons/` 目录名 | ✅ |
| 12 | 四场景 + Lerp/Hold 基线存在 | `evaluate_conditioned_baselines.py:144-146, 71/78` | ✅ |
| 13 | `contact_bits` 是 4 路布尔 | `conditioned_motion.py:142` | ✅ |
| 14 | SOMA `contact_states` 是三态含 -1 | `motion_processing.py:17` | ✅ |

**补充核对（第二轮，针对评审意见）**：

| # | 断言 | 核对方式 | 结果 |
| ---: | --- | --- | --- |
| 15 | `down_t` 是编码器层数，非抽象参数 | `encdec.py:26` `for i in range(down_t)` | ✅ 确认，推翻「共享码本」 |
| 16 | 重定向库 30fps 按场景拆分 | 按动作名语义分类统计 | ✅ A 1,064 / B 606 / C 123 / other 176 |
| 17 | 146 条 <12 帧短片段分布 | 同上 | ✅ other 110 / B 21 / A 14 / C 1 |
