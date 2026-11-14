# 固定目标骨架

`soma_uniform_v1.json` 是 2026-10-17 按用户确认的 SOMA uniform 固定人体骨架生成的参考快照，包含 78 个节点：`Root` 和 `Hips` 子树中的 77 个身体节点。具名末端节点、手指、Jaw 和双眼均保留；这里的身体节点集合是参考拓扑分组，不决定未来的模型输入选择或损失掩码。

## 参考来源与空间

权威参考为 `E:/MotionWeaverData/soma_shapes/soma_base_rig/soma_base_skel_minimal.usd` 的 `restTransforms`。依照 [OpenUSD Skeleton 定义](https://openusd.org/release/api/class_usd_skel_skeleton.html)，它们是逐关节局部参考变换；`bindTransforms` 是另一种世界空间绑定变换，不能混用。

配套专用参考 BVH 的两帧均与 USD 的参考 FK 一致。冻结的完整局部平移、四元数旋转和缩放来自 USD，不采用 BVH 零通道姿态，也不从普通动作的第一帧猜测参考姿态。BVH 原生导入器继续保留原始表示，后续适配不能再给已经包含参考骨轴的动画旋转重复叠加参考旋转。

目标存储使用米制右手 Motion 坐标：Y 向上、Z 向前、X 指向角色左侧、xyzw 四元数、列向量局部 TRS 组合。USD 声明厘米和 Y-up；参考手部左右位置及足部到脚掌的方向确认其 +X 左、+Z 前，因此本次只将平移从厘米转为米，轴基为单位矩阵。这个参考几何转换不决定动作轨迹或采样规则。之前 SOMA 单样本探针沿用旧 SOMA→UE 路径的旋转轴基只用于读写探测，不作为本目标的空间契约。

Root 和 Hips 的节点身份已固定；Root/Hips 运动分工和时间轴规则仍待用户验收后讨论。快照不包含 FPS、重采样规则或根轨迹派生算法。

## 版本与验收

- 目标 ID：`soma_uniform_v1`。
- 骨架几何签名：`215689644b435e6307ec67ce9b81fd579f781a5f0148c1c86ac23e7b4267c412`。
- 完整文件 SHA-256：`2124bddce418e5c005224b30c25c4ff46be2d8d4775f559b99ac237957f2ea67`。
- 输入文件哈希保存在快照中。下游发布和审核应同时绑定完整快照文件哈希，不能仅绑定几何签名而遗漏语义角色和空间定义。
- 相同来源重复生成字节一致；同名目标内容改变时工具拒绝覆盖，后续改动必须升级目标版本。

技术验收记录见 [2026-10-17 骨架验收](../../docs/experiments/2026-10-17-soma-skeleton-acceptance.json)，参考姿态预览见 [正面与侧面](../../docs/experiments/assets/2026-10-17-soma-skeleton/reference-skeleton.png)。技术检查和人工验收均已通过；用户确认“没什么问题”，[人工回执](../../docs/experiments/2026-10-17-soma-skeleton-manual-acceptance.json)绑定当前快照与预览哈希。此结论仅覆盖固定参考骨架与所展示参考姿态。

从仓库根目录复核：

```powershell
./.venv/Scripts/python.exe -m data.tools.ingest.freeze_soma_skeleton
./.venv/Scripts/python.exe -m unittest discover -s tests -p test_soma_skeleton.py -v
```

冻结工具仅提取此已知 ASCII USD 参考资产的静态骨架数组，不是通用 USD 动画导入器。快照读取使用 `data.runtime.skeleton_contract.load_skeleton_contract`，无需 E 盘或 Unreal Engine。
