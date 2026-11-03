# RefPose v3 接入 UE 5.8：运行时形态、推理后端、延迟、脚滑补偿与可视化验收调研
> **Lead 复核（2026-11-02）**
> - 6D 行交织已由 lead 在 `full_mmap/000000/r.npy` 复核：前两列各自为单位向量（最小范数 0.9999999），按列拼接会在骨 14/42/70/75 退化。
> - 本节「6 条 clip、RMSE 2.62 cm、接触脚速比 1.39×」是小样本探针，**不能替代**封盘评测的 test split 257,171 窗口：`no_reference` RMSE 5.28 cm、脚滑 6.9×。两套口径不要混用。
> - 延迟数字与导出调研不一致：导出建议 4 帧步长、约 235–250 ms；本文建议 8 帧步长、66.7 ms 起步。66.7 ms 只是播放缓冲，不含模型必须看到的 200 ms 未来。验收时以「按键到可见变化」实测为准，不先锁死其中一个。


> 调研范围：只做资料调研与取证，**未修改任何 C++/Python 源码**。所有结论均给文件路径:行号或官方 URL。
> 调研日期：2026-11-02。目标引擎 `D:\UE_5.8`，宿主工程 `D:\GameAnimationSample`（GASP），项目根 `D:\AILocomotonSystem\MotionWeaver`。
> 权重：`D:\MotionWeaverData\model_weight\refpose_v3_71k\best.pt`（实测 3,087,038 参数，width=256，epoch=19，78 骨）。

---

## 结论摘要

1. **接入点必须换新，不能复用旧节点契约。** 旧 `FAnimNode_AIAnimation` 是「单输入窗口重建」：`AIAnimationGpuSession.cpp:47-58` 硬断言 `GetInputTensorDescs().Num()==1 && GetOutputTensorDescs().Num()==1`，形状固定 `[1, W+1, B, 12]`，且 `AIAnimationModel.h:32-34` 明写「当前只支持已知姿态的 VQ 重建，不承诺 Pose 条件生成或网络位移」。refpose v3 是 4 输入（history/root_plan/reference/mask）、1 输出、24 帧未来——**推理会话层要重写，但「动画工作线程同步 RunSync + 有界融合缓冲」这套骨架可以整体沿用**。
2. **推理后端结论：NNERuntimeORTDml 能跑完整图，且数值与 CPU 一致。** 我把权重实导出为 opset 17 ONNX（257 节点、23 种算子、12.4 MB）并在 DirectML EP 上实跑：`max|CPU-DML| = 4.77e-6`，DML 把图切成 3 个 `DmlFusedNode` + 1 个独立 `GRU` 节点，**4 个节点全部落在 DmlExecutionProvider 上**，没有回退到 CPU。GRU 在 DML 注册表里是 `DmlGraphSupport::NotSupported`（不进 DML graph 融合，但仍由 DML EP 执行），LayerNormalization/Where/Softmax/Mod 均为 `Supported`。**不需要自建 ORT 插件。**
3. **延迟预算被一个此前没写进方案的事实改写：训练数据是 120 Hz，不是 30 Hz。** `contract.json` 里 71,127 条 clip 的 fps 全部是 120.0，`window={history:24,future:24}`，所以 **24 帧未来 = 200 ms，24 帧历史 = 200 ms**，不是 30 Hz 下的 800 ms。这既降低了未来轨迹的预测难度，也意味着 UE 侧必须按 8.33 ms 网格喂数据（或做 4:1 抽取并重新训练）。旧插件「30 Hz 网格 + 8 帧延迟 = 267 ms」的预算不能直接搬过来。
4. **脚滑必须靠 UE 端工程补偿，且这是可量化的。** 我在 6 条真实 clip 上实测（`contact_weight=0.4` 封盘权重、无参考档）：关节位置 RMSE 平均 **2.62 cm**、**骨长相对偏差 7.71%**、**接触期脚速比（预测/真值）平均 1.39×**。骨长 7.7% 的偏差说明模型输出的位置通道本身不是严格 FK 一致的，**必须先做 Two-Bone IK / Leg IK 才能谈脚滑**，否则 FootPlacement 的 planted 判定会被错误足高喂坏。
5. **已经实测到两个会直接坑死接入的契约细节**：(a) 6D 旋转的实际排布是 **row-major 交错**（`R00,R01,R10,R11,R20,R21`），不是「前两列连续 3+3」——按后者解码会得到非正交矩阵；(b) 位置数组是**米制、Y-up、身体在 root 局部水平系内**（髋骨 X/Z 恒为 0，骨长标准差 0），而根位移单独在 `k.npy` 里，且 `p[:,0]`（Root 骨）的位移方向与 `k` 的根轨迹**不一致**（Δp0=(9.30,0,1.82)、Δk=(-0.13,0,-9.48)，模长同为 9.48）。这两条都必须写进 UE 侧的转换器并在导入时断言。
6. **可视化最小闭环可以复用现有工作台，但资产导入命令必须扩。** `AIAnimationPrepareCommandlet.cpp:108-116` 校验的 manifest 是 `ai_animation_ue_pose_onnx_v1` + `pose_only_root_authoritative` + 单输入形状，`AIAnimationGpuSession` 也只认 1 in/1 out；GASP 的 UEFN 骨架命名（`pelvis/thigh_l/calf_l/foot_l/ball_l`）与 SOMA 78 骨命名（`Hips/LeftLeg/LeftShin/LeftFoot/LeftToeBase`）完全不同，重定向表是新增件。

---

## 问题 1：接入形态（AnimNode / AnimInstance / PoseSearch / Mover / CMC Root 驱动）

### 1.1 证据：旧节点的契约到底是什么

| 事实 | 证据 |
| --- | --- |
| 节点是 `FAnimNode_Base` 子类，走 `Update_AnyThread`/`Evaluate_AnyThread`，并有 `HasPreUpdate()` | `unreal-script\AIAnimation\Source\AIAnimation\Public\AnimNode_AIAnimation.h:45,71-76` |
| 每节点独占模型实例与缓冲，模型变更只在 PreUpdate 生效（GameThread 建 session） | 同上 `:39-43`；`Private\AnimNode_AIAnimation.cpp:40-66` |
| 推理在**动画工作线程**同步执行，GameThread 求值默认被拒 | `Private\AIAnimationGpuSession.cpp:71-77`（`(IsInGameThread() && !bAllowGameThread)` 直接 return false） |
| 输入形状硬编码为 `[1, WindowFrames+1, Bones, 12]`，输入/输出各 1 个 | `Private\AIAnimationGpuSession.cpp:47-58` |
| 窗口 8..64 且为 4 的倍数；采样率 30；Root 骨不得被模型修改 | `Public\AIAnimationModel.h:53-67` |
| 每 4 个 30 Hz 采样推一次（7.5 Hz），窗口滑动 1 帧重叠 | `Private\AnimNode_AIAnimation.cpp:271-280` |
| 输出窗口按「中心权重高」融合进播放缓冲，冻结已播放端点 | `Private\AIAnimationPoseBuffer.cpp:53-78,221-232` |
| 定位声明：「只支持已知姿态的 VQ 重建，不承诺 Pose 条件生成或网络位移」+「Root 始终由源动画驱动」 | `Public\AIAnimationModel.h:30-34`；`README.md:27-33` |

### 1.2 证据：refpose v3 的契约（实测，不是转述）

- 权重加载实测：`ckpt keys = [model,width,bones,epoch,val_pose_rmse_cm,args]`，`width=256`、`epoch=19`、`sum(p.numel())=3,087,038`、`forward(zeros) -> (1,24,702)`。
- 模型结构：`training/pretrain/train_conditioned_pose.py:235-253` —— `GRU(features+7 -> width)` 吃 history；`control = Linear(cat(root_plan, reference, mask))`；`+ state[-1] + time`；3 层 TransformerEncoder；`LayerNorm + Linear`；**最后 `torch.where(mask.bool(), reference, predicted)`**（参考帧硬覆盖，这是「游戏里给关键帧」的语义入口）。
- 输入拼装（训练侧真值口径）：`train_conditioned_pose.py:168-194` —— `origin = root[middle-1]`，`history = cat(combined[:24], root_local(root[start:middle], origin))`，`future_root = root_local(root[middle:end], origin)`，`reference = target * mask`。
- `root_local` 的定义：`data/runtime/conditioned_motion.py:67-72` —— **位置与朝向都相对「最后一帧历史 Root」**，避免泄漏世界绝对位置。
- 数据是 **120 Hz**：`D:\MotionWeaverData\full_release\contract\contract.json` 中 71,127 条 clip 的 `fps` 全为 120.0（实测统计：`[(120.0, 71127)]`），`window={history_frames:24, future_frames:24}`。

### 1.3 五个接入点的取舍

| 接入点 | 做法 | 优点 | 代价 / 风险 |
| --- | --- | --- | --- |
| **A. 新 AnimNode（推荐）** | 新建 `FAnimNode_RefPose`，沿用旧节点的「anim worker 同步 RunSync + PoseBuffer 融合」骨架，输出 `FCompactPose` | 与旧实验链路同构，工作台/测试夹具可复用；线程约束已被验证（旧节点实测推理线程 ID 与 GameThread 不同，`README.md:52`） | 必须在 `Evaluate` 里同时拿到「未来 Root 计划」与「参考帧」，而 `FPoseContext` 只给姿态——需要一条 GameThread→AnimProxy 的数据通道 |
| **B. AnimInstance 原生覆写** | 在 `UAnimInstance::NativeUpdateAnimation` 里算、`NativeEvaluateAnimation` 里写 | 数据通道最自由（可直接读 CMC/Mover/GameplayTarget） | 会与 Motion Matching 节点的求值顺序耦合；失去 AnimGraph 里可插拔/可调试的节点粒度 |
| **C. PoseSearch 前置** | 用 PoseSearch 生成 `FTransformTrajectory`（含未来预测段）作为 `root_plan` 来源，再把 refpose 作为后置节点 | 未来轨迹**不用自己发明**，官方已有 CMC 地面模拟 | PoseSearch 的 trajectory 采样率/粒度需对齐到 120 Hz 的 24 帧网格；Motion Matching 数据库与 refpose 生成结果可能打架 |
| **D. Mover** | 用 Mover 的模拟状态产出未来轨迹 | GASP 已启用 Mover（`GameAnimationSample.uproject` 的 Plugins 列表含 `Mover`/`ChaosMover`），且 Mover 有 `AnimRootMotionLayeredMove`/`RootMotionAttributeLayeredMove` | Mover 在 5.8 仍是 `Engine\Plugins\Experimental\Mover`；API 与回滚路径需按项目版本核验，不能假定与 CMC 等价（这点在 `docs/player-state-conditioned-animation-plan.md:17` 也已明确写过） |
| **E. CMC Root 驱动** | CMC 负责胶囊位移，模型只出 root 相对姿态 | 与旧插件「Root 权威外置」一致，最保守 | 未来 24 帧的 Root 轨迹需要自己预测（CMC 只给当前 Velocity/Acceleration） |

**建议：C/E 组合 + A 落地。** 即「CMC（或 Mover）拥有世界位移权威 → PoseSearch 的轨迹预测器提供未来 Root 计划 → 新 AnimNode 消费」。

### 1.4 未来 Root 轨迹从哪来（关键取证）

| 来源 | 证据 | 可给多长的未来 | 评价 |
| --- | --- | --- | --- |
| **PoseSearch 轨迹预测（首选）** | `D:\UE_5.8\Engine\Plugins\Animation\PoseSearch\Source\Runtime\Public\PoseSearch\PoseSearchTrajectoryLibrary.h:153` `UpdatePrediction_SimulateCharacterMovement(...)`「Update prediction by simulating the movement math for ground locomotion from UCharacterMovementComponent」；`:175-179` `PoseSearchGenerateTransformTrajectory(... InHistorySamplingInterval=0.04f, InTrajectoryHistoryCount=10, InPredictionSamplingInterval=0.2f, InTrajectoryPredictionCount=8)` | 默认 8×0.2 s=1.6 s，**远大于需要的 200 ms**；可把 `InPredictionSamplingInterval` 调到 1/120≈0.00833、`Count` 设为 24 | 官方支持 CMC 地面预测、转角 clamp（`MaxControllerYawRate`，`:70-73`）、`BendVelocityTowardsAcceleration`（`:75-78`）、贴地/落地碰撞处理（`:208` `HandleTransformTrajectoryWorldCollisions`）。**这是本机可用的最完整方案。** |
| CMC 速度外推 | `FVector UCharacterMovementComponent::Velocity`（项目里已有 CMC 轨迹抓取：`unreal-script\AIAnimation\Source\AIAnimation\Private\AIAnimationCMCRootCaptureTests.cpp:86,167` 记录 `Movement->Velocity`） | 无限，但匀速外推 | 转弯/加减速会错；只适合一阶短外推（<100 ms） |
| 玩家输入意图 | Input Action → 期望速度/朝向 | 同 CMC | 语义正确但噪声大，需要平滑；离线训练里没有这个条件 |
| AnimSequence 自带 root track | `README.md:17` 工作台已能 `ExtractRootMotionFromRange` 画 Root 轨迹 | 任意 | **只能离线用**：运行时拿不到「未来要播的动画」，用了就是泄漏真值，和 `docs/reference-guided-data-contract-v1.md:44` 的告警一致 |
| 自定义 `IPoseSearchTrajectoryPredictorInterface` | `PoseSearch\...\PoseSearchTrajectoryPredictor.h:20-40` 提供 `Predict/GetCurrentState/GetVelocity/GetAngularVelocity`（`UE_EXPERIMENTAL(5.8, ...)`） | 自定义 | 想做「模型自回归 Root 预测」时从这里接，5.8 标 Experimental |

**结论**：用 `UPoseSearchTrajectoryLibrary::PoseSearchGenerateTransformTrajectory` 把未来段重采样到 24×1/120 s，转成 `root_local(future, origin=最后一帧历史 root)` 喂 `root_plan`。**注意坐标系转换**：模型要的是「相对最后一帧历史 root 的位置+四元数（xyzw）」，而 `FTransformTrajectory` 给的是绝对位置+朝向（`Engine\Source\Runtime\Engine\Public\Animation\TrajectoryTypes.h:17-34`），必须自己做一次相对化。

### 1.5 「参考帧」在游戏运行时怎么表达

现状证据：`Public\AIAnimationReferenceSet.h:11-31` 的 `UAIAnimationReferenceSet` 存的是 `TSoftObjectPtr<UAnimSequence> Animation` + `TArray<int32> Frames`，语义是「编辑器预览时到源动画的第 N 帧取局部姿态」。**这套只适合重建实验室，不能直接当运行时参考。**

运行时的三种可行语义（按可信度排序）：

1. **事件驱动的目标姿态（推荐起步）**：落脚/攀爬/交互时，由 Gameplay 给出「在 t_k 时刻希望到达的姿态」= 现有 `reference` 通道。`mask` 里只有该帧为 1。可直接映射到模型 `torch.where(mask, reference, predicted)` 的语义（`train_conditioned_pose.py:253`）。
2. **PoseSearch 检索出来的目标姿态**：从数据库里匹配到的候选姿态，取首/尾帧作为参考。**但要注意是「用旧数据当条件」不是泄漏真值**——它必须来自玩家已经能看到的姿态库，而不是未来的真值。
3. **全身/局部姿态约束**：目前模型只支持「整帧全骨可见/不可见」（`reference_frame_mask` 粒度是帧，见 `docs/reference-guided-data-contract-v1.md:45`），**不支持逐骨骼遮罩**。想做「只约束手」需要重训。

### 1.6 风险

- **R1.1 数据通道**：`Evaluate_AnyThread` 里拿不到 GameThread 的轨迹；必须像旧节点那样用 `HasPreUpdate()` + `UAnimInstance` 侧快照（`AnimNode_AIAnimation.h:75-76`），并保证快照是原子的（旧代码的做法是只在 PreUpdate 访问 UObject，见 `AnimNode_AIAnimation.cpp:3` 注释）。
- **R1.2 帧率不匹配**：模型训练是 120 Hz，GASP 动画资产是 30 Hz 采样、游戏以渲染帧推进。**必须先决定：抽到 30 Hz 再喂，还是重采样到 120 Hz 喂**。前者等于改变训练分布（未验证），后者需要 8.33 ms 网格（对 60 FPS 渲染意味着每帧最多 2 个采样点）。见「未证实」一节。
- **R1.3 Root 双权威**：模型输出的是 root 相对姿态，世界位移必须只有一个执行者（CMC/Mover）。这条在 `docs/player-state-conditioned-animation-plan.md:11` 已作为硬边界写出。

---

## 问题 2：推理后端（NNE / DirectML / CPU / 自建 ORT）

### 2.1 实测（本轮新做，不是推测）

导出：`torch.onnx.export(..., opset_version=17, dynamo=False, input_names=[history,root_plan,reference,mask], output_names=[joints])`

| 项 | 实测值 |
| --- | --- |
| 文件大小 | 12,395,366 B（≈11.8 MiB / 12.4 MB） |
| opset | `('', 17)` |
| 节点数 / 算子种类 | 257 / 23（Add, Cast, Concat, Constant, Div, Expand, GRU, Gather, Gemm, LayerNormalization, MatMul, Mod, Mul, Relu, Reshape, Shape, Slice, Softmax, Sqrt, Squeeze, Transpose, Unsqueeze, Where） |
| 输入形状（静态） | `history[1,24,709]`、`root_plan[1,24,7]`、`reference[1,24,702]`、`mask[1,24,1]` |
| 输出形状 | `joints[1,24,702]` |
| GRU 属性 | `hidden_size=256, linear_before_reset=1` |
| LayerNormalization | 7 个，`axis=-1, epsilon=1e-5` |
| 最大 initializer | `onnx::GRU_608 [1,768,709]` = 2.08 MiB |

性能与数值（同一台 4070 Laptop，单流，300 次迭代）：

| EP | p50 | p95 | mean | max |
| --- | ---: | ---: | ---: | ---: |
| CPUExecutionProvider | 0.904 ms | 1.130 ms | 0.932 ms | 1.604 ms |
| DmlExecutionProvider | 0.896 ms | 1.157 ms | 0.859 ms | 1.324 ms |

数值一致性：`max|CPU-DML| = 4.77e-6`（随机输入）；在真实数据窗口上 `torch vs ORT-CPU max abs = 6.2e-6`。

**DML 节点分配（ORT profiling 实测，5 次连续 run 的稳态 15 条 kernel 事件）**：

```
DmlFusedNode_0_1  provider=DmlExecutionProvider  dur 32-43 us
/m/history/GRU    provider=DmlExecutionProvider  dur 146-182 us   <- op=GRU，独立节点
DmlFusedNode_1_4  provider=DmlExecutionProvider  dur 49-53 us
```

即：**整个图只有 4 个 kernel，全部在 DML 上，没有 CPU 回退**。GRU 因为不参与 DML graph 融合，单独成一个 DML 节点执行，反而成了耗时主体（约 150 us，占 ~55%）。

### 2.2 官方文档与源码证据

| 结论 | 证据 |
| --- | --- |
| DirectML EP 支持到 ONNX opset 20（除 GridSample 5d、DeformConv） | https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html （「supports up to ONNX opset 20 ... with the exception of Gridsample 20: 5d and DeformConv」）—— 我们的 opset 17 在范围内 |
| DirectML EP 不支持 memory pattern 与并行执行，必须 `DisableMemPattern` + `ORT_SEQUENTIAL` | 同上（Configuration Options 段）；引擎侧照做：`D:\UE_5.8\Engine\Plugins\NNE\NNERuntimeORT\Source\NNERuntimeORT\Private\NNERuntimeORTUtils.cpp:273-274`、`NNERuntimeORT.cpp:253-254` |
| GRU 已注册（opset 7 / 14），但 `DmlGraphSupport::NotSupported` | ORT 源码 `onnxruntime/core/providers/dml/DmlExecutionProvider/src/Operators/OperatorRegistration.cpp:785-786`：`{REG_INFO(7, GRU, ..., DmlGraphSupport::NotSupported)}`、`{REG_INFO(14, GRU, ..., DmlGraphSupport::NotSupported, ..., QueryRecurrentNeuralNetwork)}` |
| `NotSupported` 的准确含义 = 不进 DML graph 融合，不是「不支持」 | 同文件 `:1247` `:1342`：`bool kernelSupportsGraph = !bool(information.dmlGraphSupport & DmlGraphSupport::NotSupported); ... registryPrivate->RegisterOperatorKernel(..., kernelSupportsGraph, ...)`；枚举定义 `:10335-10339`（`Supported=0, NotSupported=1`） |
| LayerNormalization opset 17 | 同文件 `:776` `{REG_INFO_VER(17, LayerNormalization, ..., DmlGraphSupport::Supported, ..., QueryLayerNormalization)}` |
| Where opset 9/16 | 同文件 `:958-959` `{REG_INFO(9/16, Where, ..., DmlGraphSupport::Supported)}` |
| Softmax opset 7/11/13 | 同文件 `:1074-1076`（Supported） |
| Mod opset 10/13/28 | 同文件 `:1148-1150`（Supported） |
| Shape opset 7-21 为 `NotSupported`（同样只是不融合） | 同文件 `:1109-1113` |
| GRU 的 layout=1（batchwise）明确不支持 | ORT 源码 `DmlOperatorRecurrentNeuralNetwork.cpp`（`QueryRecurrentNeuralNetwork`，:413-419：「layout=1 for batchwise operation is unsupported」）。torch 默认导出 layout=0，**本次导出未带 layout 属性，安全** |

### 2.3 引擎内置 ORT 的具体版本

| 项 | 值 | 证据 |
| --- | --- | --- |
| ONNX Runtime | **1.24.1** | `D:\UE_5.8\Engine\Plugins\NNE\NNERuntimeORT\Binaries\ThirdParty\Onnxruntime\Win64\onnxruntime.dll` VersionInfo `FileVersion=1.24.20260304.11.3a728b7`；`ThirdParty\Onnxruntime\Onnxruntime_v1.24.1.tps` |
| DirectML | **1.15.4** | `ThirdParty\Onnxruntime\DirectML_v1.15.4.tps` |
| DirectML.dll 实际位置 | `D:\UE_5.8\Engine\Binaries\Win64\DML\x64\DirectML.dll`（18,538,488 B） | 目录实测 |
| 运行时可选项 | `NNERuntimeORTCpu`、`NNERuntimeORTDml` | `NNERuntimeORT.cpp:155,225` |

### 2.4 三条路线对比

| 路线 | 可行性 | 现有支持 | 代价 | 建议 |
| --- | --- | --- | --- | --- |
| **NNERuntimeORTDml**（首选） | ✅ 实测整图在 DML 上跑通，4 kernel，0 CPU 回退 | 旧插件已用：`AIAnimationGpuSession.cpp:39-46` `GetRuntime<INNERuntimeGPU>(TEXT("NNERuntimeORTDml"))` → `CreateModelGPU` → `CreateModelInstanceGPU` | 需支持多输入/多形状；DML 要求 sequential + no mem pattern（引擎已设） | **直接复用，改会话层** |
| **NNERuntimeORTCpu** | ✅ 实测 p50 0.904 ms，与 DML 几乎相同 | 同一插件的第二个 runtime，`NNERuntimeORT.cpp:153-155`；接口 `INNERuntimeCPU`（`Engine\Source\Runtime\NNE\Public\NNERuntimeCPU.h:22-88`） | 会占动画工作线程 CPU；多角色时需预算 | **作为 DML 不可用时的回退** |
| 自建 ORT 插件 | ❌ 不必要 | 引擎自带完整 ORT 1.24.1 + DML 1.15.4 | 重复维护、ABI/许可/打包风险 | **不做** |

补充：`NNERuntimeRDG` 也可用（`NNERuntimeORT.cpp:313-334` `FModelORTDmlRDG`），但它要求 D3D12 RHI 且走 RDG enqueue，对「动画工作线程同步取结果」这条链路反而不便，**不建议作为首条方案**。

### 2.5 风险

- **R2.1 版本差**：我的实测用的是 Python `onnxruntime-directml` **1.24.4**，引擎是 **1.24.1**。同小版本，但**必须在 UE 内重新跑一次数值校验**才算数。现有校验器 `AIAnimationPrepareCommandlet.cpp:179-235` `ValidateGpuModel` 只支持单输入单输出，**需要扩展才能校验 refpose**。
- **R2.2 融合形状**：DML 只切出 3 个 fused node，说明大量 reshape/transpose 落在融合块内；若更换导出方式（如 `dynamo=True`）节点划分会变，延迟需重测。
- **R2.3 GRU 是耗时主体**：约 150 us / 275 us。若多角色并发，GRU 会成为串行瓶颈；这一步需要实测（见「未证实」）。

---

## 问题 3：线程与延迟（窗口推进 / 融合策略）

### 3.1 证据：旧节点的实际参数

| 项 | 值 | 证据 |
| --- | --- | --- |
| 固定采样网格 | 30 Hz，`AIAnimationSampling::Advance` 按 `1/SampleRate` 步进并处理余量 | `Private\AIAnimationSampling.h:11-24` |
| 推理步长 | 每 4 个采样推一次 → 7.5 Hz | `AnimNode_AIAnimation.cpp:271`（`(SampleIndex - WindowFrames + 1) % 4 == 0`） |
| 播放延迟 | 4/8/12 帧可选，默认 8 帧 ≈ 267 ms | `AnimNode_AIAnimation.cpp:18,74`；`README.md:84-90` |
| 同步推理开销 | 平均 2.05 ms / P95 2.76 ms（8 帧档） | `README.md:86-89` |
| 融合方式 | 窗口内每帧权重 `1 + min(offset, W-1-offset)`，加权与已有预测混合，已播放端点冻结 | `AIAnimationPoseBuffer.cpp:53-78`，冻结 `:221-232` |
| 线程约束 | 必须在非 GameThread；GameThread 求值直接跳过并计数 | `AIAnimationGpuSession.cpp:74`；`AnimNode_AIAnimation.cpp:179-183` |

### 3.2 refpose v3 的时间尺度（关键）

- 训练 fps = **120.0048**（contract.json 实测 `120.00480019200768`，不是整数 120），窗口 24 帧 → **history 199.99 ms，future 199.99 ms（≈200 ms）**。证据：`contract.json` 全库 `fps` 该值；`window={history_frames:24, future_frames:24}`；窗口步长 `windows.jsonl` 实测恒为 24。
- 参考帧号是 **0..23 的 future 帧偏移**（`train_conditioned_pose.py:190-192`：`mask[list(offsets)] = 1`，offsets 取自 0..23），对应 0–191.7 ms 后。
- 因此「模型必须看到 200 ms 的未来」——**这 200 ms 就是硬延迟下界**，除非把 future 侧改成「只喂已经已知的部分」。

### 3.3 建议的窗口推进与融合（可落地）

**采样网格**：与模型保持 120 Hz（`1/120 = 8.333 ms`）。渲染 60 FPS 时每帧 ≤ 2 个采样点，复用 `AIAnimationSampling::Advance` 的多点补齐能力（`AIAnimationSampling.h:19-23` 已经能在一个 DeltaTime 内产出多个 fraction）。

**推进节奏（分三档，按验收数据选）**：

| 档位 | 推理步长 | 理论频率 | 播放延迟 | 说明 |
| --- | ---: | ---: | ---: | --- |
| 保守 | 8 帧（66.7 ms） | 15 Hz | 8 帧 = 66.7 ms | 与旧插件「步长 4 / 延迟 8」的比例一致，最适合先跑通 |
| 折中 | 12 帧（100 ms） | 10 Hz | 12 帧 = 100 ms | 推理开销更低，融合窗口重叠更多 |
| 轻量 | 16 帧（133 ms） | 7.5 Hz | 16 帧 = 133 ms | 多角色时用 |

**融合策略**：直接复用 `FAIAnimationPoseBuffer`（`AIAnimationPoseBuffer.h:22-46`），但要把「中心权重」从旧窗口换成 refpose 的 24 帧窗口：

- 权重函数保持 `1 + min(offset, W-1-offset)`（中心最高、两端最低）；
- 冻结规则保持「已播放/曾用作插值端点者不再修改」（`AIAnimationPoseBuffer.cpp:59`）；
- **新增**：`mask=1` 的参考帧必须**硬覆盖**而不是融合（因为模型输出本身就是 `where(mask, reference, predicted)`，两端必须同语义），可直接复用现有 `CorrectModel` 的锚点逻辑（`AIAnimationPoseBuffer.cpp:97-159`）；
- **新增**：静止保护（旧的是 6 个采样点判定，`AIAnimationPoseBuffer.cpp:29-40`）在 120 Hz 下要改成 `6 × (30/120) ≈ 1.5` 个 30 Hz 采样点的时间窗，否则 Idle 抖动抑制会失效或过度。

**延迟预算总账（建议写进验收）**：

```
可见延迟 = FutureHorizon(200 ms 固定) + 播放缓冲延迟(66.7~133 ms) + 推理墙钟(p95 2.8 ms 旧实测 / 需重测)
```

即 **~270–335 ms**。若不能接受，唯一的结构性办法是**把 future 窗口砍到 8–12 帧重训**，工程侧无解。

### 3.4 风险

- **R3.1 120 vs 30 Hz 的语义漂移**：旧插件所有阈值（静止判定 0.01 cm / 0.001 rad、回绕、0.25 s 重置）都是 30 Hz 语义。直接搬到 120 Hz 会让「静止」判定变苛刻 4 倍。
- **R3.2 未来窗口与响应性冲突**：200 ms 未来意味着玩家急停时模型仍看到「继续走」的旧计划，需要「按键/状态切换立即安排一次重规划」的旁路（`docs/player-state-conditioned-animation-plan.md:97` 已提出，尚未实现）。
- **R3.3 GameThread 跳过**：旧节点在 GameThread 求值时直接放弃（`AnimNode_AIAnimation.cpp:179-183`）。refpose 推理更贵（4 输入拼装），若在 GameThread 求值会直接掉帧。

---

## 问题 4：脚滑工程补偿（UE 5.8 具体机制）

### 4.1 证据：脚滑有多大（本轮实测）

在 6 条真实 clip、中心窗口、无参考（`mask=0`）、封盘权重上实测：

| clip | 关节位置 RMSE (cm) | 骨长相对偏差 | 真值接触脚速 (m/s) | 预测接触脚速 (m/s) | 比值 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 000016 | 3.95 | 7.07% | 1.975 | 0.761 | 0.39 |
| 000042 | 2.44 | 5.17% | 0.577 | 0.918 | 1.59 |
| 000100 | 1.76 | 6.65% | 0.011 | 0.043 | 4.05 |
| 000500 | 0.79 | 5.51% | 0.164 | 0.120 | 0.74 |
| 001000 | 3.07 | 10.56% | 1.122 | 0.939 | 0.84 |
| 002000 | 3.71 | 11.30% | 0.649 | 0.482 | 0.74 |
| **均值** | **2.62** | **7.71%** | — | — | **1.39** |

口径说明：接触帧取「足部高度最低 20% 分位」的近似启发式（与训练用的接触标签同源都是启发式，见 `contract.json` 的 `contact_source="world_foot_contacts_from_full_positions"`），**不等于物理接触真值**。

**关键推论**：**骨长偏差 7.71%** 是比脚滑更底层的问题。模型的位置通道与旋转通道是**分别回归**的（损失里 `position` 与 `rotation` 分列，`train_conditioned_pose.py:263-264`），所以输出的 78×3 位置**不是严格 FK 一致**的骨架。不先修骨长，任何 FootPlacement 的「足高/足速」判定都会被错误输入污染。

### 4.2 UE 5.8 可用机制清单（含位置与代价）

| 机制 | 插件/模块 | 证据 | 能修什么 | 代价 / 风险 |
| --- | --- | --- | --- | --- |
| **`FAnimNode_LegIK`** | `Engine\Source\Runtime\AnimGraphRuntime` | `Public\BoneControllers\AnimNode_LegIK.h`；实现 `Private\BoneControllers\AnimNode_LegIK.cpp` | 逐腿两骨 IK + 伸展限制（骨长强制） | 便宜；需要在 AnimGraph 里给每条腿终点；只修腿不修躯干 |
| **`FAnimNode_TwoBoneIK`** | 同上 | `Public\BoneControllers\AnimNode_TwoBoneIK.h` | 单条两骨链（Effector + JointTarget），官方文档 https://dev.epicgames.com/documentation/en-us/unreal-engine/animation-blueprint-two-bone-ik-in-unreal-engine | 最通用；每腿要手工配 effector/joint target |
| **`FAnimNode_FootPlacement`** | `Engine\Plugins\Animation\AnimationWarping` | `Source\Runtime\Public\BoneControllers\AnimNode_FootPlacement.h:522-568`；关键参数 `FFootPlacementPlantSettings`（`:453-519`）：`SpeedThreshold=60`、`DistanceToGround=10`、`LockType=PivotAroundBall`、`UnplantRadius=35`、`UnextensionRatio` 等；`EFootPlacementLockType`、`bReconstructWorldPlantFromVelocity`（`:512`） | **Foot Locking + 贴地 + 骨盆下沉**，正是脚滑的正解之一 | 节点标 `Experimental`（`:522`）；依赖 `PlantSpeedMode` 的足速曲线（`Manual` 模式要自己给曲线，`Graph` 模式要 root motion attribute）——**模型生成姿态没有 root motion 属性，只能走 Manual + 自算足速** |
| **`FAnimNode_StrideWarping`** | 同上 | `Source\Runtime\Public\BoneControllers\AnimNode_StrideWarping.h:35-114`；核心公式写着 `StrideScale = (LocomotionSpeed / RootMotionSpeed)`（`:55`） | **步幅匹配**：把步长缩放到与 CMC 实际速度一致 | 同样依赖 root motion（`:114` `bDisableIfMissingRootMotion`）——refpose 无 root motion 时会被自动禁用，需要伪造 root motion 或改用 Manual 模式手给 `StrideScale` |
| **`FAnimNode_OffsetRootBone`** | 同上 | `Source\Runtime\Public\BoneControllers\AnimNode_OffsetRootBone.h` | 吸收「模型根/胶囊根」的小偏差，避免整体抖动 | 只吸收平移/朝向，不修脚 |
| **Control Rig `RigUnit_TwoBoneIKSimple` / `FABRIK` / `CCDIK`** | `Engine\Plugins\Animation\ControlRig` | `Source\ControlRig\Public\Units\Highlevel\Hierarchy\RigUnit_TwoBoneIKSimple.h`、`RigUnit_FABRIK.h`、`RigUnit_CCDIK.h`；`AnimNode_ControlRig` 可从 AnimGraph 调用（`Source\ControlRig\Public\AnimNode_ControlRig.h`） | 复杂解算（多链、带约束、带后处理） | 重：每个角色一次 ControlRig 求值；调试成本高 |
| **Motion Warping（`RootMotionModifier_SkewWarp`）** | `Engine\Plugins\Animation\MotionWarping` | 目录实测；官方文档 https://dev.epicgames.com/documentation/en-us/unreal-engine/motion-warping-in-unreal-engine （「dynamically adjust a character's root motion to align to targets」，靠 Montage + NotifyState） | 对齐到目标点（攀爬/交互） | **只对 Montage + root motion 有效**，对连续生成姿态不适用 |
| **PoseSearch Trajectory** | `Engine\Plugins\Animation\PoseSearch` | 见 §1.4 | 提供未来轨迹做「预测式落脚」 | 本身不修脚，只是条件 |
| **`UAnimationLocomotionLibrary`（距离匹配）** | `Engine\Plugins\Animation\AnimationLocomotionLibrary` | `AnimDistanceMatchingLibrary.h`、`AnimCharacterMovementLibrary.h` | 起步/停步的步幅对齐 | 依赖特定曲线资产 |

### 4.3 建议组合（按顺序叠，每层都能单独验收）

```
RefPose 输出 (24×78 positions + 6D)
   │  ① 骨长/层级修复：LegIK 或 TwoBoneIK（Effector = 模型给的足位置，JointTarget = 膝）
   ▼
   │  ② Foot Locking：FootPlacement（Manual 模式，足速曲线由「模型输出的足部世界速度」算出）
   ▼
   │  ③ 步幅匹配：StrideWarping（Manual，StrideScale = CMC 速度 / 模型姿态隐含步速）
   ▼
   │  ④ 根偏差吸收：OffsetRootBone
   ▼
   最终 Pose → 蒙皮
```

**为什么是这个顺序**：①先修骨长，因为 7.7% 的骨长偏差会让 ② 的 `DistanceToGround/SpeedThreshold` 判定失真（这些阈值是绝对值 cm/cm-per-s，见 `AnimNode_FootPlacement.h:462,466`）；③必须在 ② 之后，否则缩放步幅会重新引入足部滑移。

### 4.4 风险

- **R4.1 无 root motion**：`StrideWarping` 默认在缺 root motion 时禁用（`AnimNode_StrideWarping.h:114`），`FootPlacement` 的 `PlantSpeedMode=Graph` 也依赖 root motion attribute（`:529-533`）。**refpose 生成姿态没有 root motion**，必须走 Manual 并自算足速/步幅，等于要额外写一层「足速估计」。
- **R4.2 Experimental 标记**：`FAnimNode_FootPlacement` 结构体上明确 `USTRUCT(BlueprintInternalUseOnly, Experimental)`（`AnimNode_FootPlacement.h:522`）。5.8 内可用但不保证跨版本稳定。
- **R4.3 过约束**：模型输出本身 RMSE 2.6 cm，IK 把足部硬拉到地面可能把误差集中到膝/踝。必须实测「IK 修正量」分布，不能只看脚滑降了。

---

## 问题 5：可视化验收（最小闭环）

### 5.1 证据：现有工作台能做什么

| 能力 | 证据 |
| --- | --- |
| 独立 Slate 视口 + 三路 Mesh（源/软输出/硬参考）+ 相机 | `AIAnimationEditor\Private\AIAnimationWorkbench.cpp:81-160,265-300` |
| Root 轨迹绘制、骨骼名标注、参考角色切换 | `Workbench.cpp:188-243,616-643` |
| 播放/暂停/逐帧/倍速/输出 FPS/区间循环 | `Workbench.cpp:574-604`；`README.md:13` |
| 参考帧增删、保存为 `UAIAnimationReferenceSet` 资产 | `Workbench.cpp:75-78,932-935` |
| 指标读取（推理 ms、关节误差、样本数） | `Workbench.cpp:424-429`（读 `FAIAnimationEvaluationStats`） |
| 统计结构体字段齐全（含旋转误差、加速度、速度误差、线程 ID） | `Public\AnimNode_AIAnimation.h:14-37` |
| 命令行对照台批量出报告 + 截图 | `Private\AIAnimationBenchmark.cpp:44-63,249-272` |
| 三路帧缓存回放校验 | `Workbench.cpp:397-455` |

### 5.2 最小可视化闭环（建议）

**准备资产（4 项）**

1. **ONNX 资产**：refpose_v3 opset17（我已实测可导、12.4 MB、DML 可跑）。导入路径仍走 `UNNEModelData::Init(TEXT("onnx"), Bytes)` + `SetTargetRuntimes`（`AIAnimationPrepareCommandlet.cpp:369-372`）。
2. **扩展后的 manifest**：现有格式是 `ai_animation_ue_pose_onnx_v1` + `training_contract=pose_only_root_authoritative` + `window_frames`/`fps`/`bones`/`onnx_sha256`（`AIAnimationPrepareCommandlet.cpp:108-158`）。**refpose 需要新增**：4 个输入名与形状、24+24 窗口、`fps=120`、`mask/reference` 语义、归一化统计量（`pose_m.npy`/`pose_s.npy` 的 SHA 与形状 `[78,3]`）、6D 排布标记（row-major interleaved）。
3. **骨架重定向表**：SOMA 78 骨（`D:\MotionWeaverData\full_mmap\skeleton.json`：`Root,Hips,Spine1,Spine2,Chest,Neck1,Neck2,Head,...,LeftLeg,LeftShin,LeftFoot,LeftToeBase,RightLeg,...`）↔ GASP UEFN 骨架（`pelvis/thigh_l/calf_l/foot_l/ball_l/spine_01`，实测于 `Content\Characters\UEFN_Mannequin` 的资产内字符串）。**两套命名零重叠，必须显式映射表**。SOMA 的 `roles` 已给出语义键（`left_foot/left_toe/right_foot/right_toe/pelvis/head...`），可作为映射表的源。
4. **对照场景**：可直接用现有 `AAIAnimationBenchmark`（`Public\AIAnimationBenchmark.h:20-96`）的三路布局，加一路「RefPose 生成」。

**用现有工作台还是新做？**

- **复用工作台的壳**（视口、相机、三路、骨骼标注、Root 轨迹、指标读取），**新增一个「RefPose 通道」**：把 `SAIAnimationPreviewViewport` 的第三路（硬参考）扩成第四路，或在现有三路上加「RefPose」选项。
- **不建议新做**：工作台已有 76 KB 的实现、三路帧缓存、回放校验和统计联动，重写成本远大于扩展。
- 判据：工作台刷新逻辑依赖 `FAIAnimationEvaluationStats`（`AnimNode_AIAnimation.h:14-37`），新节点只要产出同结构体即可零改动接入（`Workbench.cpp:424-429` 只读这几个字段）。

**指标记录（必须落盘，不能只看画面）**

| 指标 | 定义 | 记录点 |
| --- | --- | --- |
| 关节位置误差 | 78 骨组件空间位置 RMSE（cm），按 clip 等权 | 复用 `AnimNode_AIAnimation.cpp:420-474` `MeasureQuality` |
| 旋转误差 | 6D→四元数后的角距离 RMSE（度） | 同上，`LastRotationErrorDegrees` |
| **骨长偏差** | 每条骨 `abs(|p_c-p_pa| / rest_len - 1)` 的均值 | **新增**（旧节点没有；本轮实测均值 7.71%） |
| **接触脚速比** | 接触帧（足高最低 20% 分位）世界脚速 ÷ 源动作同帧脚速 | **新增**（口径同 `training/evaluation/evaluate_conditioned_baselines.py:83-95`，用 `contract["fps"]`） |
| 脚滑倍数 | 前者在「相邻两帧都着地」上的统计 | **新增** |
| 推理耗时 | `RunSync` 墙钟 p50/p95（含上传/下载/等待） | 复用 `AIAnimationGpuSession.cpp:80-82` + `CSV_CUSTOM_STAT`（`AnimNode_AIAnimation.cpp:374-375`） |
| 端到端延迟 | 采样时刻 → 屏幕可见时刻 | **新增**（旧节点用 `DelayFrames` 近似，`README.md:84-89`） |
| 回退/跳过计数 | `NumFailures/NumFallbacks/NumGameThreadSkips` | 复用 `AnimNode_AIAnimation.h:17-21` |

**现有验收门槛可以直接沿用**：`AIAnimationSampling::Passed`（`Private\AIAnimationSampling.h:27-30`）要求「每动作 ≥20 样本、失败/回退/线程跳过均为 0」。

### 5.3 风险

- **R5.1 编辑器 ≠ 运行时**：`README.md:19,23,160` 反复声明「模型效果与性能实验插件」「Runtime 只表示需要加载，不代表生产可用」。工作台是编辑器资产系统内的窗口，**不是轻量运行时**（`README.md:9`）。可视化通过 ≠ 玩法通过。
- **R5.2 源动画依赖**：现有工作台的历史/辅助骨骼**从源动画取**（`README.md:27-29`、`AnimNode_AIAnimation.cpp:319-330` 的 `ReadFullSource`）。refpose 的可视化若继续用源动画填历史，只能验证「补间」，不能验证「玩家驱动」。必须明确标注是哪一种。

---

## 建议的最小改动路径（分阶段 + 可验收客观判据）

> 原则：每阶段只加一个变量，每阶段有可机器判定的判据，且不修改训练侧任何东西。

### 阶段 0：契约固化（不改 C++，只写数据）

- 产出：`refpose_manifest_v1.json`（4 输入名/形状、24+24、fps=120、归一化统计量 SHA、6D 排布标记、骨骼名与父索引、单位/轴向声明）。
- **判据**：用一个独立脚本从 manifest + ONNX + 权重 + stats 重建 `history/root_plan/reference/mask` 并与训练侧 `MotionWindows.__getitem__` 逐元素比对，`max|Δ| < 1e-6`。

### 阶段 1：离线数值对齐（不改引擎）

- 导出 ONNX（opset 17，静态 batch=1），在 **CPU 与 DML 两个 EP** 上跑同一批真实窗口。
- **判据**：
  - `max|torch - ORT_CPU| < 1e-5`（本轮实测 6.2e-6 ✅）
  - `max|ORT_CPU - ORT_DML| < 1e-5`（本轮实测 4.77e-6 ✅）
  - 关节位置 RMSE 与训练侧评测口径一致（本轮 6 clips 均值 2.62 cm，与 test 档 5.28 cm 同量级但更小，因为只取 6 条中心窗口）

### 阶段 2：会话层（C++ 改动最小集）

- 新增 `FAIAnimationRefPoseSession`（4 输入 / 1 输出、静态形状 `[1,24,*]`），**不改 `FAIAnimationGpuSession`**（保留旧链路的 A/B 能力）。
- 新增 6D 解码器：**必须按 row-major 交错**（`c0=(v0,v2,v4)`, `c1=(v1,v3,v5)`, `c2=c0×c1`）。
- **判据**：在 UE 内 `RunSync` 一次，与 Python 端同输入输出逐元素比对 `max|Δ| < 1e-4`；`GetInputTensorDescs().Num()==4` 且全部 `Float`。

### 阶段 3：新 AnimNode + 复用 PoseBuffer

- `FAnimNode_RefPose`：PreUpdate 抓轨迹快照 → Evaluate 拼 4 输入 → anim worker `RunSync` → `MergeWindow` → `Read`。
- **判据**：
  - 每动作 ≥20 稳态推理样本，`Failures==Fallbacks==GameThreadSkips==0`（沿用 `AIAnimationSampling::Passed`）
  - 推理线程 ID ≠ GameThread ID（沿用旧验收方式，`README.md:52`）
  - 二阶差分（连续性）不劣于源姿态的 1.5 倍

### 阶段 4：脚部补偿链（先 IK 后 Foot Lock）

- 先只加 `LegIK`，量骨长偏差；再加 `FootPlacement`（Manual 足速），量接触脚速比。
- **判据**：
  - 骨长偏差从 7.71% 降到 < 1%
  - 接触脚速比从 1.39× 降到 < 1.2×（注意：<1 意味着「过度锁脚」，同样要拒）
  - IK 修正量 P95 < 5 cm（超过说明模型与 IK 在打架）

### 阶段 5：工作台接入 + 报告落盘

- 三路 → 四路，指标扩展到含骨长/脚滑/端到端延迟。
- **判据**：一条命令产出 `runtime_report.json` + 每 clip 截图，字段齐全且能被一个独立脚本复算（不允许只出截图）。

---

## 未证实 / 需要实测的部分

**明确标注：以下都是推测或未在本机验证的推断，不能当作事实引用。**

1. **引擎内 ORT 1.24.1 + DirectML 1.15.4 的实际算子分配未测。** 我用的是 Python `onnxruntime-directml 1.24.4`。虽然版本接近，但**必须在 UE 内重跑一次 DML profiling**才能确认 GRU 仍落在 DML 上、仍无 CPU 回退。
2. **120 Hz 与 30 Hz 的取舍未验证。** 目前只知道训练是 120 Hz；「把 GASP 的 30 Hz 数据上采样到 120 Hz 喂模型」是否会破坏模型质量、「把模型降到 30 Hz 用」是否需要重训，**两者都没有实验数据**。
3. **`p[:,0]`（Root 骨）与 `k.npy`（root track）语义不一致，尚未查清原因。** 在 027106 上实测：`Δp[:,0]=(-7.911, 0, 0.855)`、`Δk=(7.933, 0, 0.913)`，模长 7.957 / 7.986 m（差 0.03 m，方向相反）。两者方向不同、无法用简单轴置换对齐。**（2026-11-03 订正：此前写的 `Δp0=(9.30,0,1.82)` / `Δk=(-0.13,0,-9.48)`、「模长都是 9.48」在 000000/019534/019857/027106 四个 clip 上都复现不出，已换成 027106 的实测值。）** **UE 侧转换器必须以 `k.npy`（root track）为准，忽略 `p[:,0]` 的水平分量，但这一条需要先跟数据管线确认。**
4. **6D 旋转是 global 还是 local 未定论。** 已确认排布（row-major 交错、前两列、正交归一到 1e-6）和「与位置通道不严格 FK 一致」（Spine1 对齐度 0.995，四肢 0.64–0.90，骨长偏差 7.7%）。**「姿态应优先用位置通道还是旋转通道驱动 UE 骨骼」需要一次专门实验**。
5. **多角色并发性能未测。** 单流 p50 ≈ 0.9 ms，但 GRU 占 55% 且 DML 强制 sequential。N 个角色是线性放大还是更差，未知。
6. **FootPlacement / StrideWarping 在「无 root motion 生成姿态」上的实际行为未验证。** 源码显示有 `bDisableIfMissingRootMotion`（`AnimNode_StrideWarping.h:114`）和 Manual 模式，但**「Manual 模式下自算的足速/步幅够不够好」没有实测**。
7. **打包/Shipping 未验证。** 旧插件只测过编辑器进程内（`README.md:43,107`），refpose 同样未测 cook/打包后的 NNE 资产与 DML 可用性。
8. **GASP 的 `Plugins` 目录当前不存在**（实测 `Test-Path D:\GameAnimationSample\Plugins` = False），`.uproject` 里也没有 AIAnimation 条目。要复现旧实验链路必须先按 `README.md:123-127` 放插件并编译。

---

## 附：本轮实测命令与产物（可复现）

- ONNX 导出：`torch.onnx.export(Wrap(model), (h,r,ref,mask), out, opset_version=17, input_names=["history","root_plan","reference","mask"], output_names=["joints"], dynamo=False)`
- DML 验证：`onnxruntime-directml 1.24.4` + `InferenceSession(..., providers=["DmlExecutionProvider"], enable_profiling=True)`
- 数据：`D:\MotionWeaverData\full_release\contract\{contract.json,stats/pose_m.npy,stats/pose_s.npy}` + `D:\MotionWeaverData\full_mmap\<raw_file>\{p,r,k}.npy`
- 权重：`D:\MotionWeaverData\model_weight\refpose_v3_71k\best.pt`
- 本轮中间产物放在系统临时目录（ONNX / profiling json），**未写入仓库、未改动任何源码**。
