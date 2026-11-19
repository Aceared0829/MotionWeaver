# 姿态代理组「生成并播放」实现记录

| 字段 | 内容 |
| --- | --- |
| 状态 | 多窗口整段生成与播放已跑通真实 NNE，关键帧可按秒编辑，骨架映射 62/78，界面已汉化并加入逐帧诊断；稀疏/跨度大的关键帧上仍有可见畸形，归因与证据见下文 |
| 日期 | 2026-11-18（Asia/Shanghai） |
| 关联 | [阶段二 NNE 数值门禁](2026-11-18-refpose-v2-30ep-ue-stage2.md)；[UE 导出调研](../refpose-ue-export-research.md) |

## 目标与边界

在姿态代理组编辑器里加「生成并播放」：读取关键帧参考姿态与求解器产出的 Root 轨迹，
装配模型四输入，在 UE 内执行真实 NNE 推理，把输出解码成可蒙皮的逐骨局部变换并播放。

明确不做：

- 不训练新模型。
- **不预测 Root**：Root 来自姿态组求解轨迹，模型只生成身体姿态。
- 不做 AnimGraph、玩家控制、碰撞、IK 或打包。
- 不用关键帧插值冒充模型输出。

## 数据语义（本次确证）

这些结论由 `tools/verify_refpose_ue_contract.py` 用独立判据复算，
不是「用同一公式回算一遍」：

| 事实 | 判据 | 实测 |
| --- | --- | --- |
| `p.npy` 是 Root 局部、米制、Y-up 的逐帧骨骼位置 | 相邻骨「子-父距离」标准差 | ≤ 4.68e-8 m（76 根骨） |
| Root 是独立辅助轨道，不是 Hips 的骨长父级 | Hips→Root 距离标准差 | 2.22–2.93 m（不恒定） |
| `r.npy` 解码后正交 | `max\|RRᵀ−I\|` | 1.67e-7 |
| `p_root == −R(k)ᵀ·k_pos` | 复算 manifest 的 axis0 声明 | ≤ 9.24e-7 m |
| `skeleton.json` 的 `position` 是父级相对平移 | 与逐帧骨长逐骨比对 | 除 Hips 外全部相等 |

**一处已知契约瑕疵**：`manifest.json` 的 `inputs.history.layout` 写成
「offset 0 开始是全部位置、234 开始是全部旋转」，但训练侧
`np.concatenate((pose, rotations), axis=-1).reshape(F, -1)` 实际生成的是
**逐骨 9 维**（每骨 3 位置 + 6 旋转）。UE 侧 `FRefPoseWindowBuilder`
以训练夹具为准按逐骨 9 维写入，`NNE.Numeric` 与 `expected.f32` 逐元素比对通过。
manifest 的 `layout` 段只能当语义说明，不能当内存块排布；修正导出器会改变已封盘
manifest 的哈希，留待后续版本处理。

**U1 仍未闭合**：旋转与位置的骨轴是否同源。因此实现里**不用旋转做 FK 重建位置，
也不用位置反推旋转**；两者分开解码。位置负责骨长与方向，旋转只作诊断。

2026-11-19 进一步实测：模型 6D 旋转的第一列与「位置差分方向」平均夹角
**100.2°**，只有 41% 的骨落在 90° 内。这不是小偏差，而是两套骨轴约定
不同源，因此旋转通道**不能**用来做 FK，也不能用来求 roll。

## 实现结构

| 位置 | 职责 |
| --- | --- |
| `RefPose/RefPoseModel` | ONNX 与骨架契约：`skeleton.json`、`stats.bin`、`manifest.json` 的 timing |
| `RefPose/RefPosePoseContract` | 6D 解码/编码、米制与 UE 坐标互转、窗口解码 |
| `RefPose/RefPoseWindowBuilder` | 四输入装配；只接受已验证的参考档位 `()/(12)/(23)/(12,23)` |
| `RefPose/RefPoseSkeletonSolver` | 由位置差分求方向、中间骨长取模型契约，解成局部变换 |
| `RefPose/RefPoseSkeletonMap` | 模型骨名 → UE 骨名的显式映射与重定向 |
| `RefPose/RefPoseGenerator` | 单窗口推理：装配 → 推理 → 解码 → 解算 |
| `RefPose/RefPoseSession` | 复用同一个 ONNX 推理会话，避免逐窗口重复解析模型 |
| `RefPose/RefPoseGroupGenerator` | 姿态组 → 模型 → UE 局部变换的完整多窗口管线；编辑器与自动化测试共用 |
| `RefPose/RefPoseGenerationSettings` | 生成设置：参考档位、起点关键帧、模型包路径、播放帧率口径 |
| `PoseGroupProxyAssetEditor_Playback` | 生成、播放时钟、状态文字、过期标记、诊断文本与 JSON 导出 |
| `SPoseKeyTimeline` | 生成设置行、诊断区、「生成并播放 / 暂停继续 / 停止」入口 |

模型空间的 6D/轴变换/位置差分一律用引擎自带的双精度类型
（`FVector3d`、`FQuat4d`），不自造 3x3 矩阵；写入 UE 骨骼局部变换时
再转成引擎骨骼使用的 `FTransform`（float），转换点集中在契约层。

## 编辑器交互

- 时间轴按**秒**编辑关键帧，旁边显示换算后的帧号；Enter 或失去焦点时提交。
  `FPoseKey::Seconds` 是 Transient 派生字段，序列化真值仍是 `Frame`。提交时
  `PostEditChangeProperty` 先把秒数换算回 `Frame` 再 `Solve()`，
  避免「按秒改了却被求解结果覆盖」。
- 每次「生成并播放」前都会强制 `GroupAsset->Solve()`，并在日志打印关键帧、
  帧率、速度上限与轨迹样本范围，便于确认改过的参数确实参与了求解。
- 时间轴新增生成设置行：参考档位（四档白名单）、起点关键帧（显示对应秒数、
  可「从选中帧开始」）、模型包路径（可浏览目录）、是否按姿态组帧率播放。
  所有改动走 `FScopedTransaction + Modify + MarkPackageDirty`，并立即标记生成结果过期。
- 模型包优先级：资产里显式填写的路径 > 环境变量 `MW_REFPOSE_UE_PACKAGE` >
  工程 `Saved/MotionWeaver/RefPosePackage.txt`；资产路径一旦填写且不存在就报错，
  不静默回退。后两者是本地覆盖入口，不提交。
- 「生成诊断」可展开，显示逐窗口推理耗时、Root 接缝最大跳变、时长膨胀、
  已映射骨上的锚点对齐误差与告警；「导出诊断」写到
  `Saved/MotionWeaver/RefPoseReports/<资产名>-<时间戳>.json`。
- 状态文字常驻并带颜色：失败红、过期黄、播放蓝；显示「当前帧/总帧 + 秒数」。
- 播放中切换或编辑关键帧会停止播放并标记过期，旧结果不会被当成当前资产继续播放。

## 关键设计决定

1. **最终骨长取 UE 骨架，不取模型**：模型是 78 骨，UE 是 161 骨；直接写模型长度会把角色拉变形。
   求解器先用模型契约骨长构造中间局部变换（保证位置差分链在模型尺度上闭合），
   `Retarget` 阶段再统一换成 UE 参考骨长，并用 `RebuildSkeletonPositions` 自检。
2. **参考档位白名单**：训练只见过四种组合。其它组合直接拒绝，而不是当作可用档位。
3. **硬参考是精确覆盖**：`reference = target × mask`，未选帧整行为 0。
4. **过期必须可见且停止播放**：增删关键帧、改帧号、视口拖拽、细节面板改动与
   撤销/重做都会标记过期并暂停播放；状态文字明确说「结果已过期」，
   避免拿旧缓存当当前结果。「停止」或重新选中关键帧会退出播放预览，
   回到可编辑的关键帧视图；「继续」会重新进入播放预览。
5. **多窗口连续生成，历史取作者参考姿态**：每窗口输入 24 帧历史 + 24 帧未来，
   输出未来 24 帧，直到覆盖整段求解轨迹。所有窗口共用
   `PlaybackOrigin = Samples[HistoryFrames - 1]`，接缝处不会把各窗口的
   播放原点重复叠加成 Root 跳变。历史不复用生成结果，避免误差逐窗口累积、
   也让输入保持训练时见过的分布；末窗口不足 24 帧时用末帧补齐输入，
   输出按实际帧数截断。
6. **关键帧快照按骨名解析**：快照的保存顺序不一定等于 UE 参考骨架的槽位顺序，
   按数组下标硬对会静默贴错骨。`SnapshotToSkeletonLocal` 建立名字索引，
   查不到的骨直接报错；参考骨架有、快照没有的骨保持参考局部变换。
7. **UE 骨架 → 模型输入是显式转换**：先在组件空间重建全局变换，去掉 UE 根变换
   （root motion 只由姿态组轨迹提供），转成米制 Y-up，再表达为该帧姿态组 Root 的
   局部坐标。Root 位置遵循 `world = R(k)·p_root + k_pos`，因此输入模型的是
   `p_root = −R(k)ᵀ·(world − k_pos)`；Root 朝向由 `HeadingDegrees` 构造。
8. **求解器参考旋转取 UE 参考姿态**：平移长度仍取模型契约（保证求解器内部尺度一致），
   旋转取 UE 参考骨架相对最近映射祖先的旋转。这样静止姿态能复现 UE 的肩/腕/踝扭转，
   而不是每根骨都从 `+X` 做最短弧旋转。
9. **映射缺口分两类处理**：
   - UE 独有、模型没有的中间骨（如 `spine_04/spine_05`）用参考旋转链
     `ChainInverse` 补偿，把模型方向转回直接父级空间；
   - 模型有、UE 没有的骨（面部、手指末端、脚趾末端）不参与重定向，
     但会按 `skeleton.json` 的参考局部偏移挂到最近已映射祖先上，
     避免它们在模型输入里变成零位移。
10. **模型包懒加载且必须持有 GC 根**：编辑器不是 UObject，普通成员指针不会被 GC
    扫描。`ModelAsset` 用 `TStrongObjectPtr` 持有，首次点击时从
    `MW_REFPOSE_UE_PACKAGE` 读取并复用，避免重复解析 ONNX 与统计量。
11. **播放帧率取 manifest 的 `timing.fps`**：训练数据的帧率是 120.0048…，
    不是整数 120。`LoadContractFromPackage` 同时校验窗口是当前 C++ 装配器
    支持的 24/24；包不匹配时直接拒绝，而不是按默认值播放。
12. **根运动只写一次**：生成帧的 `Frame[0]` 已经是根骨相对组件的局部变换，
     `UPoseableMeshComponent` 的 `BoneSpaceTransforms` 会再乘组件变换。
     播放时只写骨骼，不再把根位移写到组件上，否则最终矩阵会把根运动叠加两次。
13. **起点关键帧的语义分两种**：索引 0 表示动作起点，模型仍需要 24 帧前置历史，
    所以输出从第 25 个求解样本开始（与首版整段滑窗完全一致）；索引 > 0 表示
    作者指定的起点，以该关键帧对应的求解样本作为第一个输出帧，要求它之前
    已有至少 24 帧求解样本，不够就明确报错，不静默退回第 0 帧。
14. **参考档位只能选训练验证过的四档**：UI 不提供任意帧手选。任意稀疏组合
    属于 U7 未验证档位，要开放必须先用 `diverse` 采样在 test 分区验证。
15. **诊断不是精度指标**：`MaxRootSeamJumpCm` 只说明输出连续；锚点对齐误差
    把求解器时长膨胀、硬参考覆盖、位置差分与关键帧旋转的骨轴约定差异都算进去，
    只能用于发现「姿态跑偏」，不能当作模型质量门禁。

## 2026-11-19 视觉复核与畸形归因

这一轮的目标不是「把数字调小」，而是回答「画面为什么畸形」。以下判断都用
独立对照或反事实实验，不把同一套公式正反算当成证明。

### 界面与调试动线

生成面板已按实际排查顺序重排，且全部汉化：

1. 顶部是「生成并播放」，右侧常驻异常徽标（无异常帧 / 异常帧 N 个）；
2. 第二行是逐帧、逐关键帧、逐异常帧导航与「跳到最差帧」；
3. 第三行是进度条、当前帧号与秒数；
4. 再往下是当前帧的生成帧 / 求解帧 / 作者帧 / 窗口 / 是否硬参考；
5. 逐帧调试行同时显示 **模型误差、骨架空间旋转跳变、骨架空间位置跳变**
   以及对应骨名；
6. 生成设置、模型包、四个开关（姿态组帧率 / 关键帧幽灵 / 参考幽灵 /
   迁移扭转）和诊断 JSON 导出放在最后。

「参考幽灵」是同一帧作者参考姿态的线框显示，和实体生成姿态同帧推进。
它显示的是模型实际看到的 history/reference 分布，不是另一份模型输出；
实体与线框的偏离量就是模型误差，用来区分「模型跑偏」和「解算/播放接错」。

异常阈值是 **位置 > 5 cm/帧或旋转 > 45°/帧**，只用于快速定位肉眼可见的
抽搐，不是质量门禁。点击异常导航没有命中时，按钮旁会明确显示「前面/后面
没有异常帧」，不会静默不动。

### 畸形归因（实测）

| 观察 | 对照/判据 | 结果 |
| --- | --- | --- |
| 历史旋转通道不能用位置方向编码 | 同一窗口只替换 history 的 6D 旋转，模型与参考输入不变 | 训练先验 8.1 cm → 方向编码 28.8 cm，明显变差，因此保留先验 |
| 旋转通道不能做 FK / 求 roll | 模型 6D 第一列与位置差分方向的夹角 | 平均 100.2°，仅 41% 骨 < 90°，不是同一套骨轴 |
| 尖峰不是「上一帧 roll 没搬运」造成 | 把上一帧全局旋转并行搬到下一帧 | `carriedRoll=17232` 但 `maxrotstep` 仍在 168–175° 波动，锚点误差反而变差，已完全回滚 |
| 尖峰也不是扭转迁移的阈值跳变 | 关闭 `bTransferAuthoredTwist` 对照 | 锚点旋转均值只从 39.67/48.64° 降到 37.88/46.02°，最大跳变仍存在 |
| 硬参考帧会放大单帧弹跳 | 四档参考模式对照（关闭扭转） | 无参考：`maxrotstep=172.93` / `posestep=51.84` / `predMean=23.19`；中间+末端：`maxrotstep=175.23` / `posestep=74.07` / `predMean=17.67` |
| 辅助骨会把指标放大一个数量级 | 只统计蒙皮骨，排除 `VB/ik/prop/poi/attach/weapon` | 同一段结果含辅助骨 159 cm，只算蒙皮骨 74 cm |
| 该测试资产本身在训练分布外 | 逐窗口预测 vs 作者位置 | 四个窗口平均 8.1 / 10.8 / 31.0 / 13.4 cm；训练同档位 test RMSE 约 2.38 cm |

该测试资产只有 3 个关键帧（0 / 180 / 360 帧，120 fps），姿态跨度极大；
中间帧的 history/reference 是由关键帧插值合成，模型在该分布上泛化明显变差。
因此当前画面的主要畸形来源是：

1. **模型位置预测本身在摆动**（尤其手指、脚掌），不是播放端把骨骼空间写错；
2. **硬参考帧是精确覆盖**：相邻帧模型误差可达 40 cm，参考帧被作者姿态
   拉回后，下一帧必然出现一次单帧弹跳；
3. 辅助骨与高误差帧会放大诊断数字，但排除辅助骨后仍有 74 cm 的真实
   蒙皮骨跳变，不能归因于「指标口径」。

扭转迁移的平滑权重**保留**：它没有让 `maxrotstep` 变小，因为尖峰来自
位置预测而不是 roll 奇异；但它消除了「轴夹角跨过硬阈值时在迁移/不迁移
两条路径之间跳变」这一层不连续。这个结论必须和「平滑后仍有尖峰」一起
读，不能把平滑当成畸形已修复。

### 尚需用户决策的选项

以下改动会改变当前已写入文档的语义，**本轮没有擅自实现**：

- **加密关键帧（推荐先做）**：当前 3 帧跨度 3 秒，history 是插值合成；
  先加真实关键帧，再判断模型误差，不能用调低阈值或关闭扭转掩盖数字。
- **硬参考平滑混合**：把硬参考位置在前后若干帧做平滑过渡，会降低单帧
  弹跳，但会改变「硬参考是精确覆盖」的训练/实现契约，需要单独批准。
- **生成结果回灌历史**：离线对照曾把误差从 26.67 cm 降到 16.65 cm，但它
  违反当前「历史取作者参考、误差不累积」的决策，且需要重新定义多窗口
  接缝与过期语义，本轮不实现。

## 骨架映射现状

本机实测：UE5 Mannequin 是 161 骨、`root/pelvis/spine_01/...` 命名；
模型是 78 骨、`Root/Hips/Spine1/...` 命名。

| 类别 | 数量 | 处理 |
| --- | --- | --- |
| 躯干、四肢主链 | 23 | 按映射表重定向 |
| 颈部 `Neck2` | 1 | 映射到 `neck_02` |
| 双手手指 | 38 | 映射到 UE 的 `thumb_* / index_* / middle_* / ring_* / pinky_*` 链 |
| **已映射合计** | **62** | 由模型输出驱动 |
| 面部 `Jaw/LeftEye/RightEye/HeadEnd` | 4 | 保持 UE 参考姿态 |
| 每手 5 根手指末端 `*End` | 10 | 保持 UE 参考姿态 |
| 左右脚趾末端 `LeftToeEnd/RightToeEnd` | 2 | 保持 UE 参考姿态 |
| **未映射合计** | **16** | 在状态里显示数量 |

映射表在 `FRefPoseSkeletonMap::GetDefaultUe5Mapping()`，来源是
`data/skeletons/mappings/ue_humanoid_to_soma_v1.json`（target_signature
`215689…`，对 161 骨 UE5 Mannequin 的 `missing_mapped_bones` 为空）。
该文件本身有 61 条关节映射但不含 `Root`，映射表里额外显式补了
`Root → root`，合计 62 条。

**没有为了「看起来全动」把面部骨或末端骨硬凑到不存在的 UE 骨上**：
这 16 根会明确落进未映射集合，保持参考姿态。`Map.DefaultCoverageOnUe5Mannequin`
会同时断言覆盖数 62、未映射数 16 和缺口骨名集合；`Map.RetargetThroughUnmappedIntermediate`
覆盖 `spine_04/spine_05` 这类 UE 独有中间骨的旋转链补偿。

因此当前播放结果可以验证「躯干、四肢主链与双手手指」，但**不是完整蒙皮效果**：
面部与末端骨保持静止。这是已知限制，不是可以忽略的细节。

这里必须区分两套 UE 骨架：`SKM_Manny` 是 161 骨，`SKM_UEFN_Mannequin`
在本机是 93 骨且包含 `VB/IK/prop/poi` 等辅助骨。62/78 与 16 个未映射
是按 161 骨 UE5 Mannequin 的映射契约核对的；93 骨目标的映射覆盖数会因
目标骨缺失而不同，不能用 93 骨探针的数字反过来推翻 161 骨门禁。

## 验证

UE 自动化（`-NullRHI`，逐项执行；并发运行同一工程会互相干扰，必须串行）：

```bash
UE=/path/to/UnrealEditor
PROJ=unreal-sample/AIMotionSample/AIMotionSample.uproject
export MW_REFPOSE_UE_PACKAGE=/path/to/refpose_ue_package
"$UE" "$PROJ" -unattended -nop4 -nosplash -NullRHI -NoLogTimes \
  -abslog=/tmp/refpose.log \
  -ExecCmds="Automation RunTests MotionWeaver.RefPose;Quit"
```

| 测试 | 结果 |
| --- | --- |
| `Contract.AxisRoundTrip` | 通过 |
| `Contract.Decode6DRoundTrip` | 通过 |
| `Contract.DecodeWindow` | 通过 |
| `Contract.SolverFollowsPositions` | 通过 |
| `Contract.SolverPreservesBoneLength` | 通过 |
| `Contract.SolverComposesParentRotation` | 通过（父级旋转累计） |
| `Contract.SolverRejectsBadHierarchy` | 通过 |
| `Contract.SolverRebuildsRealOutput` | 通过（真实夹具输出重建骨长） |
| `Window.Packing` | 通过 |
| `Map.Build` | 通过 |
| `Map.RetargetPreservesSkeletonBoneLength` | 通过 |
| `Map.RetargetThroughUnmappedIntermediate` | 通过（`spine_04/spine_05` 补偿） |
| `Map.DefaultCoverageOnUe5Mannequin` | 通过（62 映射 / 16 未映射，缺口骨名精确匹配） |
| `Input.SkeletonPoseConvertsToRootLocal` | 通过（UE 骨架 → 米制 Root 局部） |
| `Input.SnapshotResolvesByName` | 通过（快照按骨名解析） |
| `Input.BlendSkeletonLocal` | 通过（history/reference 插值） |
| `Input.SolverReferenceUsesUeRest` | 通过（求解器保留 UE 参考扭转） |
| `Input.HistoryUsesRotationPrior` | 通过（历史旋转使用训练先验，方向编码对照更差） |
| `Input.RealSkeletonConversion` | 通过（真实 161 骨骨架转换） |
| `Input.RealPoseGroupGeneration` | 通过（姿态组 → NNE → 解码 → 解算 → 重定向完整链路） |
| `Input.RealPoseGroupSoftTrajectory` | 通过（超速轨迹仍可生成；多窗口覆盖整段；接缝 Root 连续） |
| `Input.PoseGroupStartKeyIndex` | 通过（起点样本、播放原点、首帧 Root 与默认整段一致） |
| `Input.PoseGroupReferencePatterns` | 通过（四档硬参考帧数；参考档位确实进入模型输入） |
| `Input.PoseGroupInsufficientHistory` | 通过（历史不足与下标越界都明确报错） |
| `Contract.TwistTransferPreservesJoints` | 通过（迁移扭转只改 roll，关节位置不变） |
| `Contract.SolverIgnoresUnusableChild` | 通过（无映射子骨不参与父骨朝向） |
| `NNE.Numeric` | 通过（真实窗口夹具，输出与 `expected.f32` 一致） |
| `NNE.RejectsBadInput` | 通过 |
| `Probe.SkeletonBones` | 通过（非断言取证，仅打印骨骼信息） |
| `Probe.UserGroup` | 通过（非断言取证，打印工程内姿态组与生成诊断；资产缺失时跳过） |

本轮结果为 **30 项 Success**：28 项正式门禁 + 2 项非断言 Probe。

`Input.RealPoseGroupGeneration` 用 `SKM_Manny` 与真实模型包构造最小姿态组
（0/48 两帧、上臂相差 70°、Root 平移 100 cm、偏航 45°），断言：
生成 24 帧、UE 骨数 161、模型骨数 78、未映射 16 骨、窗口数 1、
NNE 推理耗时大于 0、输出不是静态参考姿态、Root 朝向随轨迹变化；
并核对 `FrameRate` 来自 manifest（120.0048…，不是默认 120）与 24/24 窗口。

`Input.RealPoseGroupSoftTrajectory` 构造「原始关键帧跨度 37 帧、短于 48 帧窗口」
的姿态组，让求解器的超速门禁触发时长膨胀。断言：超速只作为非阻断告警带出、
生成帧数等于求解样本数减 24、窗口数按未来 24 帧切分，并检查相邻帧 Root 位移
没有接缝跳变（本轮实测最大约 11.95 cm）；同时核对诊断里的
`MaxRootSeamJumpCm` 与逐帧实测值一致（防止诊断另算一条轨迹）。

诊断一致性：`metrics.mean_prediction_error_cm` 只对实际进入播放的生成帧求平均，
不把末窗口用末帧补齐的输入帧重复计权；`max_prediction_error_cm` 仍是逐骨最大值。
因此 JSON 里的均值与逐帧数组 `frame_prediction_error_cm` 是同一批帧。

`-NullRHI` 下 `SK_UEFN_Mannequin` 可能加载不出 `USkeletalMesh`，
因此测试骨架统一用 `/Game/Characters/UE5_Mannequins/Meshes/SKM_Manny`。

Python 侧（本轮复跑）：`tools/dev.py lint` 通过；`pytest` 为
**303 passed、38 skipped**，跳过项是既有的「本机缺少权重/旧封盘数据」，
与本次改动无关。

`tools/verify_refpose_ue_contract.py` 的三条 clip 四项判据全部为 true；
判据实现（而不是自证公式）放在 `data/runtime/refpose/__init__.py`。

```bash
MW_DATA_ROOT=/path/to/MotionWeaverData \
  .venv/bin/python tools/verify_refpose_ue_contract.py
```

脚本不写死本机路径；数据根从 `MW_DATA_ROOT` 读，也可以用
`--release/--source/--mmap` 分别指定。

## 尚未验证、不能据此宣称

- 完整蒙皮：手指已映射并随模型输出运动；面部 4 骨与 12 根末端骨仍未映射，
  播放画面不是完整蒙皮结果。
- 多窗口接缝：只断言了相邻帧 Root 连续，没有做逐帧人工视觉复核。历史来自
  作者参考姿态而不是生成回灌，因此长时间生成不会累积模型误差，但也无法表达
  关键帧之间「模型自洽演化」的历史；相邻窗口的骨骼姿态在接缝处可能不完全连续，
  因为下一窗口看到的历史是参考插值，不是上一窗口的输出。
- 末窗口不足 24 帧时用末帧补齐条件输入，只输出实际剩余帧；补齐段不进入播放，
  但会参与该窗口的推理。
- 实时滚动推理、帧预算、多角色。
- 脚部接触、IK、碰撞、地面落点。
- 编辑器内的正式人工播放复核：当前只能在带 RHI 的编辑器里做；已有的视觉
  反馈是「稀疏关键帧测试资产上明显畸形」，还没有逐帧对照记录。
- 锚点对齐误差目前只做数值报告，没有与人工视觉复核对照；它不能反过来证明
  模型姿态正确，也不能作为质量门槛。

## 已知实现限制

- 起点仍以**关键帧**为单位，不是任意求解样本；非首个关键帧之前必须有 24 帧
  求解样本，否则拒绝生成。求解轨迹样本数必须不短于一个完整窗口 48 帧
  （超速膨胀后的样本数也算）。
- Root 朝向只有偏航：姿态组求解轨迹给位置与 `HeadingDegrees`，
  没有逐帧 Root 四元数，因此 `root_plan` 的朝向分量只含偏航，没有俯仰/滚转。
- 关键帧之间用线性插值提供 history/reference；这只用于给模型「已有姿态」，
  最终播放帧仍由模型输出覆盖。
- 历史帧的旋转通道取训练分布先验，不取逐帧真实旋转；这是因为旋转与位置
  骨轴不同源（U1），且离线 A/B 中方向编码明显更差。它不表达真实的逐帧
  roll 历史，是当前模型输入的一个已知近似。
- 硬参考帧是**精确覆盖**，没有做前后帧平滑混合。相邻帧模型误差大时，
  参考帧会出现单帧弹跳；是否改变这条语义需要单独批准。
- 当前可见畸形主要出现在「关键帧少、姿态跨度大」的测试资产上；这属于
  训练分布外样本，不能据此宣称模型在所有输入上都不可用，也不能把畸形
  说成已修复。
- 模型包通过资产设置、环境变量 `MW_REFPOSE_UE_PACKAGE` 或
  `Saved/MotionWeaver/RefPosePackage.txt` 指定；仍然只是目录路径，
  没有把 ONNX 导入成 UE 资产。
- `manifest.json` 的 `layout` 段与实际逐骨 9 维打包不一致（见「数据语义」），
  当前实现以训练夹具为准；修正导出器留待后续版本。

## 修订历史

| 日期 | 变更 |
| --- | --- |
| 2026-11-18 | 首版：契约、装配、解算、骨架映射、单窗口生成与播放界面 |
| 2026-11-18 | 后继：关键帧按骨名解析、UE 骨架显式转换、Root 坐标/偏航修复、求解器参考扭转、62/78 映射、完整管线测试、manifest timing 契约、文档更新 |
| 2026-11-18 | 后继：多窗口整段生成、复用 NNE 会话、超速轨迹非阻断、求解帧号映射回关键帧、关键帧按秒编辑、模型包本地覆盖、播放状态与过期停止、新增 SoftTrajectory 测试 |
| 2026-11-19 | 后继：生成设置（参考档位 / 起点关键帧 / 资产内模型包路径 / 播放帧率口径）、诊断指标与 JSON 导出、起点与参考档位测试；模型包优先级改为「资产 > 环境变量 > Saved 覆盖」 |
| 2026-11-19 | 后继：界面汉化与调试动线重排、参考幽灵、异常帧导航、骨架空间接缝口径、扭转迁移平滑、畸形归因实测（历史旋转先验、骨轴不同源、硬参考弹跳、辅助骨口径） |
