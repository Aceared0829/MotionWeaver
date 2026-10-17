# UE 来源运动 → SOMA uniform

2026-10-18：离线读取既有原生导出，保留来源骨架与轨道，在独立版本中生成固定 `soma_uniform_v1` 的 78 节点动画。处理只使用 Python、NumPy 和 SciPy。

## 数据位置

```text
E:/MotionWeaverData/
  soma_uniform/                            既有 SOMA/BONES-SEED 来源，不改写
  soma_proportional/                       既有不同人体比例来源，不改写
  raw/gameanimation/<原始批次>/             UE 来源骨架及原始局部轨道
  processed/gameanimation/<原始批次>/       UE 来源骨架的既有标准化运动
  processed/gameanimation_soma/<新批次>/
    skeletons/<固定SOMA签名>/               一份共享参考骨架
    clips/<id>/motion.json                  来源身份、转换规则、监督掩码
    clips/<id>/arrays.bin                   统一骨架局部 TRS 与时间戳
    bvh/<id>.bvh                           对应的通用交换动画
  reports/gameanimation_soma/<新批次>/
    target-skeleton.json                   固定 SOMA 参考快照
    mapping.json                           本轮实际使用的映射快照
    export-index.csv                       成功结果与原始 raw 路径
    clips.jsonl                            逐片段日志，完成一条即落盘
    run.json                               结果、来源/目标哈希、异常原因
    acceptance.json                        技术回读结果与排除名单
  reviews/gameanimation_soma/<新批次>/      独立审核；不继承来源人工通过
```

新结果通过原始路径和哈希引用既有 raw，不复制或改写来源轨道。索引中的名字与 UE 资产路径用于将 `<id>` 关联回原动画。所有完整目标数据共享与项目冻结快照完全相同的骨架内容签名。

## 映射与处理规则

显式映射位于 `data/skeletons/mappings/ue_humanoid_to_soma_v1.json`，只接受已核对的来源签名。六类来源中五类有完整身体；875 节点颈部校正来源缺少四肢，保留原数据并列入待骨架处理，不能用参考四肢伪造完整运动。

- 61 个目标节点有显式来源映射。五节来源脊柱通过 `Spine1←spine_01`、`Spine2←spine_02`、`Chest←spine_03` 与 `Chest→Neck1` 的跨上脊柱方向约束汇入 SOMA。它是明确的 FK 近似，不宣称逐节脊柱等价。
- 47 个骨段以实际来源 FK 的方向为第一轴，以随来源关节变换的参考锚点确定扭转；转换为目标局部旋转。参考锚点在源/目标参考骨段均不平行的固定轴中选择，不在动画期间切轴。
- 来源整体朝向由参考左右髋对齐到目标参考左右髋，转换同时作用于运动位置、方向和扭转。校正基于真实参考姿态，不取动作第一帧为参考。
- 来源 FK 包含正缩放和父级产生的仿射变换；目标保留固定参考局部偏移、单位缩放，不迁移骨段拉伸。无方向约束的映射节点使用世界旋转的极分解与参考旋转校正。
- 来源位移按左右腿长比例的均值统一尺寸，以目标参考骨盆为固定基线。SOMA `Root` 固定，完整录制的位移和高度进入 `Hips`；原始 UE Root 仍在 raw 和来源标准化版本中。该离线表示不是关卡 Actor 轨迹的恢复。
- Root、面部与具名末端共 17 个无直接映射节点保留参考局部变换。`rotation_supervision_mask` 和 `track_mask` 显示哪些关节具有来源运动依据；静态参考补全不能作为真实面部或末端旋转监督。
- 保留既有 UE 导出的 30 FPS 网格、时间戳和源时长，不拉伸时间或补帧。没有重新读取 UE 资产，不运行 UE。

BVH 使用厘米、Y-up、Z-forward、局部 ZXY 欧拉旋转；Root/Hips 的平移通道为相对 `OFFSET` 的增量。参考旋转写入每帧旋转通道，不能把 BVH 零通道姿态当成固定人体参考。原生 `motion.json`、`arrays.bin` 与固定骨架快照是完整契约，BVH 用于交换和检查。其导入配置使用单位 `0.01`、单位轴基、`absolute_position_joints=[]`，不同于既有 BONES-SEED 的 BVH 配置。

## 执行与审核

```powershell
./.venv/Scripts/python.exe -m data.tools.retarget_uefn_to_soma `
  --index E:/MotionWeaverData/reports/gameanimation/20261015-full-export/export-index.csv `
  --run-id 20261018-soma-retarget-v1

./.venv/Scripts/python.exe -m data.tools.motion_review_server `
  --library E:/MotionWeaverData `
  --native-index E:/MotionWeaverData/reports/gameanimation_soma/20261018-soma-retarget-v1/export-index.csv `
  --port 8784
```

`--limit 30` 选择覆盖来源骨架和运动类别的技术试点。已有输出版本拒绝覆盖；数据盘 I/O 失败中止批次，来源数据保持不变。报告中的 `needs_skeleton_review` 记录不支持或几何退化的片段。

失败条目可以单独从 raw 重新标准化后再转换一次：

```powershell
./.venv/Scripts/python.exe -m data.tools.retarget_uefn_to_soma `
  --index E:/MotionWeaverData/reports/gameanimation/20261015-full-export/export-index.csv `
  --run-id 20261018-soma-retarget-retry-v1 `
  --retry-report E:/MotionWeaverData/reports/gameanimation_soma/20261018-soma-retarget-v1/run.json
```

仍失败的条目自动形成 `<重试run-id>-issues/export-index.csv`，可用同一审核服务查看其真实来源。该列表的 `normalized` 仅表示来源标准化数据可读，`retarget_status=unresolved`、`retargeted_clips=0` 明确表示没有生成合格 SOMA 目标，不进入成功结果索引。

审核页同步显示来源和目标，左侧只在显示时校正朝向、比例和固定位置基线，右侧读取真实落盘目标。原生数组逐项回读相等；每个 BVH 经独立解析和 FK 后与目标世界位置比较，最大分量误差门槛为 `1e-7 m`。所有方向约束同时检查叉积误差和方向点积，防止反向骨段通过。

局部校正、PoseAsset 和服装烘焙姿态有独立 `auxiliary_pose` 标记，不因成功转换就成为普通动作训练样本。所有结果 `training_ready=false`：本工作交付统一动画与来源追踪，尚未建立正式划分/训练窗口，未执行接触 IK、蒙皮或场景碰撞验收。跨骨架骨长变化可能改变手脚落点；方向和回读检查不等于接触精度。Epic 对跨骨架与接触约束的区分见 [IK Rig Retargeting 文档](https://dev.epicgames.com/documentation/unreal-engine/ik-rig-animation-retargeting-in-unreal-engine?lang=en-US)。本实现为项目离线 FK 方法，没有调用 UE IK Retargeter。
