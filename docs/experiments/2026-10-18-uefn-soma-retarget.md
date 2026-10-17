# UE 来源运动到固定 SOMA 的离线重定向

| 字段 | 内容 |
| --- | --- |
| 实验 ID / 类型 | `2026-10-18-uefn-soma-retarget`；离线动画重定向、交换与审核 |
| 日期与时区 | 2026-10-18，Asia/Singapore；全批首条结果约 01:06 |
| 状态 | 全批完成；1 条失败已从 raw 重试，仍待复核 |
| 执行者与来源 | 项目 Python 脚本；既有 UE/GameAnimation 动作，不是本项目采集 |

## 问题与操作

将原生 UE 导出的不同人体骨架统一为已冻结的 SOMA uniform，同时保留原始骨架和轨道。本次不训练、不进行模型对照。骨段方向、固定骨长、位置表示、独立 BVH FK 和文件回读是可直接验证的指标；不以它们代替接触、蒙皮或游戏运行时验收。

源版本为 `reports/gameanimation/20261015-full-export/export-index.csv`，共 1,970 条已导出的 AnimSequence、六类来源骨架。运行前 E 卷为 exFAT、Healthy/OK，约 1.54 TB 可用且未标记 Dirty。新输出位于 `processed/gameanimation_soma`，没有写入既有 `soma_uniform`、`soma_proportional` 或来源 raw。

## 代码、环境与生效配置

- 初始 HEAD `63a25e8`；执行期间 dirty，实现随后保存为 `54285c0`，审核显示为 `d034998`，用户追加的 raw 重算和独立待复核队列为 `658db29`。涉及 `retarget_motion.py`、显式映射 JSON、批次入口、SOMA 解析试验、安装资源及原生审核接线。
- 固定目标 `data/skeletons/soma_uniform_v1.json`，签名 `215689644b435e6307ec67ce9b81fd579f781a5f0148c1c86ac23e7b4267c412`。实际映射及目标快照随每个批次报告保存。
- 61 个显式映射节点，47 个骨段方向约束，17 个参考补全节点；来源正缩放进入 FK，目标不迁移骨长拉伸。完整录制位移进入 Hips，SOMA Root 固定。来源原始 Root 继续保留。
- 保留既有 30 FPS 网格和源时长；按参考髋部对齐朝向，按两侧腿长比例均值统一位移尺寸；原生与 BVH 输出均记录文件哈希。
- 软件为 Windows、仓库 Python 3.12 虚拟环境、NumPy/SciPy、Node、Playwright Chromium。无 GPU、优化器或随机训练种子；没有启动 UE/Blender。
- 未创建 train/validation/test 分区或训练窗口；所有结果 `training_ready=false`。`auxiliary_pose` 标记校正姿态，来源审核通过不继承为目标通过。

实际执行：

```powershell
./.venv/Scripts/python.exe -m data.tools.retarget_uefn_to_soma --index E:/MotionWeaverData/reports/gameanimation/20261015-full-export/export-index.csv --run-id 20261018-soma-retarget-pilot-v1 --limit 30
./.venv/Scripts/python.exe -m data.tools.retarget_uefn_to_soma --index E:/MotionWeaverData/reports/gameanimation/20261015-full-export/export-index.csv --run-id 20261018-soma-retarget-pilot-v2 --limit 30
./.venv/Scripts/python.exe -m data.tools.retarget_uefn_to_soma --index E:/MotionWeaverData/reports/gameanimation/20261015-full-export/export-index.csv --run-id 20261018-soma-retarget-v1
```

## 试点、异常与验证

| 检查 | 结果 | 口径 |
| --- | --- | --- |
| 第一轮试点 | 23/30 转换，7 条排除 | 初版拒绝所有非单位动画缩放；记录保留 |
| 第二轮试点 | 29/30 转换，1 条排除 | 正缩放参与仿射 FK，目标固定骨长；缺少四肢的 875 节点来源排除 |
| 解析运动回归 | 四组通过 | 局部轴重定基、全局 Root 运动、BVH 与参考掩码、正缩放/固定骨长 |
| 原生审核回归 | 既有四组通过 | 保留原审核身份和改动拒绝机制 |
| 页面回归 | 两个既有 Node 文件通过 | 视口、相机、预加载及版本绑定 |
| 试点 HTTP 回读 | 29/29，2,688 帧 | 来源与 78 节点目标数组长度、身份哈希、同步时间轴 |
| 浏览器 | 双视图、动作切换、逐帧与末帧可用 | 行走和攀爬画面；未替用户标记任何样本通过；控制台 0 错误/警告 |
| 安装资源 | 仓库外命令帮助、78 节点参考与映射可读 | 用 uv 刷新 strict-editable 安装，无新依赖 |

初版试点中的非单位缩放还包含两条正常跑步动作的微小缩放变化。修订后来源完整仿射几何进入方向/扭转计算，统一目标保留固定骨长。旧试点及其异常原因保留，不覆盖历史结果。

每条原生输出逐数组回读相等；BVH 单独解析并计算所有帧、所有节点的世界位置，最大绝对分量误差门槛 `1e-7 m`。方向验证同时检查归一化叉积及点积，避免反向骨段被误认为一致。

## 全批完成与失败重试

本机批次 `20261018-soma-retarget-v1` 已完成 1,970 条来源处理，得到 **1,969 条、226,455 帧**固定 SOMA 动画和同数 BVH；输出目录只有一个共享目标骨架。来源分布为 91/161/201/354/363 节点对应 1843/28/8/89/1 条，139 条有辅助姿态标记，不作为普通动作训练样本。以下数值对全部成功输出聚合，无抽样：

| 指标 | 结果 |
| --- | --- |
| 原生数组落盘回读 | 全部逐项相等 |
| BVH 世界位置最大绝对分量误差 | `6.035711521690246e-9 m` |
| 骨段方向归一化叉积最大范数 | `2.192541660052538e-13` |
| 骨段方向归一化点积最小值 | `0.9999999999999994` |
| 完整审核目录 | 1,969 条，40 页，首末页与动画端点回读通过 |
| 来源审核数据库 | `quick_check=ok`，原 1,970 条 approved 记录保留 |

用户要求再处理有问题的条目后，执行了：

```powershell
./.venv/Scripts/python.exe -m data.tools.retarget_uefn_to_soma --index E:/MotionWeaverData/reports/gameanimation/20261015-full-export/export-index.csv --run-id 20261018-soma-retarget-retry-v1 --retry-report E:/MotionWeaverData/reports/gameanimation_soma/20261018-soma-retarget-v1/run.json
```

唯一失败的 `neckCorr_m_med_nrw_RBFSolver_anim` 从 native raw 重新标准化后，仍确认 875 节点来源缺少完整四肢，未生成 SOMA 目标。重试报告保留 `recomputed_from_native_raw=true`。独立待复核队列为 `reports/gameanimation_soma/20261018-soma-retarget-retry-v1-issues/export-index.csv`；其 HTTP 已验证只有该 1 条、47 帧来源动画，target 为空，没有自动人工通过。

成功结果入口 `http://127.0.0.1:8784/`，待复核入口 `http://127.0.0.1:8785/`。两个地址只在本机服务运行时有效；待复核报告目录保存 `start-review.ps1` 和说明，方便下次重开。全批结束后 E 卷仍未标记 Dirty。

可提交证据：[全批汇总](assets/2026-10-18-uefn-soma-retarget/full-summary.json)、[两轮试点汇总](assets/2026-10-18-uefn-soma-retarget/pilot-summary.json)、[行走对照](assets/2026-10-18-uefn-soma-retarget/walk-source-target.png)、[攀爬对照](assets/2026-10-18-uefn-soma-retarget/traversal-source-target.png)、[未解决来源](assets/2026-10-18-uefn-soma-retarget/unresolved-source.png)。完整 per-clip 日志、来源/输出轨道及哈希位于 E 盘批次目录，仅本机有效。

## 结论边界

本方法将固定 SOMA 局部骨长与来源运动方向结合，是离线几何重定向；没有调用 UE IK Retargeter，也不声称保持手脚场景接触。脊柱与比例差异、参考补全节点、静态 Root 和辅助姿态身份均有显式规则。正式训练划分、滑窗、接触 IK、蒙皮、碰撞及在线控制不在此次验证范围。实现规则与 E 盘布局见 [UE → SOMA](../uefn-soma-retarget.md)。

## 修订历史

| 日期 | 变更 | 依据 |
| --- | --- | --- |
| 2026-10-18 | 记录两轮试点及启动全批 | 本机批次日志、解析回归与浏览器检查 |
| 2026-10-18 | 全批完成并按用户要求重试，保留 1 条待复核 | 全批汇总、raw 重算回执、独立待复核页面 |
