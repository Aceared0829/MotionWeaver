# P0：站蹲过渡数据与审核基线审计

| 字段 | 内容 |
| --- | --- |
| 实验 ID / 类型 | `2026-09-25-p0-stance-data-audit`；数据审计 / 离线视觉复核 |
| 日期与时区 | 2026-09-25，Asia/Hong_Kong；具体开始/结束时刻未记录 |
| 状态 | **P0 完成**：数据身份与分析复跑一致；用户在 2026-09-25 确认 107 条全部通过，并要求按逐条标记的结论处理。未将其写成助手见证逐条播放；UE 场景验收仍属后续阶段。 |
| 来源 | 本项目现有 UE 导出数据、条件索引、质量审计报告及本次只读复核 |
| 关联计划 | 玩家状态驱动动画计划已归档，见 [archive.md](../archive.md)；本记录保留 P0 审计证据 |

## 问题与范围

本次核查现有数据是否真正包含可训练的 Stand↔Crouch 预测目标、旧测试集是否有训练来源近似变体、107 条候选已有何种人工验收证据。**没有重新训练、重划分、改写来源 NPZ 或覆盖旧评估结果。**新产物是本页和随附的小型审计表/脚本；原始数据与图片抽查输出仍在本机。

评价口径：帧数和窗口按契约的 30 FPS、24 帧历史 + 24 帧未来、步长 4；过渡阶段用骨盆世界高度相对首末帧变化的 10%–90% 区间；同 Root 组的近似度用对齐帧、全部 79 骨骼位置欧氏距离的均值，单位 cm。这个数值只是泄漏排查线索，不是语义或动画质量的自动判决。

## 代码、环境与数据身份

- 开始审计时工作分支 `codex/skeletal-overlay-preview`，HEAD `884f34c34c7b19409d305157cccd39a64db9258b`；本机 `origin/main` 为 `af9f9330e6c19ac082d4cbbc92631db573436761`，两侧分别有 2/19 个独有提交。当前工作树 **dirty**，已有 `data/tools/motion_review_server.py`、`data/tools/review_web/*` 等修改及多份未跟踪数据/脚本；本次未合并、重置、暂存或提交这些文件。后续训练代码以核对后的 `origin/main` 派生独立干净工作区，再明确移植需要的本地变动。本审计使用的分析脚本亦未提交。
- `origin/main` 的条件训练脚本 `training/pretrain/train_conditioned_pose.py` Git blob `94ce445e15ec6573343f37693f41978563d479fb`；条件评估脚本 `training/evaluation/evaluate_conditioned_pose.py` Git blob `81989a2744b91c08ccbc210f884c215d1dd73296`。本次没有运行这两个脚本；它们只作为旧权重/报告的代码身份参考。质量审计报告来自先前运行，报告 SHA256 为 `0b6869f6773434ad6e4d583a89ba032cc82ea6cb3101a015c61b21505a4ae21b`；当前核查对其做了独立只读分析。
- 条件契约 `E:\AIAnimationSystemData\prepared\reference_guided_root_pose_v1_20260924_audited\contract.json` SHA256 `9bbf2893d461290371c2cefdf54eeb201b09cf639f50ea0dd0254bcc2f9eae9d`；窗口索引 `windows.jsonl` SHA256 `48e6e86df75fa0d0056aaa8876f989dc92dfcb98e6979ae3e12718c76d0c97b2`。来源 `E:\AIAnimationSystemData\prepared\native30_traversal_semantic_v1\dataset.json` SHA256 `9a6c10fa2cd906c47f7f137cf5a83361e86fbbb40d2a7182d8e87c631145483a`；骨架 SHA256 `1a799ca14585ce18edcfce3bb755dc7adcffa84d5111ac3afa2aa71238b8b547`。以上 E 盘路径仅在当前机器有效。
- 旧权重归档：`E:\AIAnimationSystemData\training_runs\conditioned_pose_20260925_hybrid12\epoch_012.pt` SHA256 `fc5cb84b737d0bb0e9e87fd2bad423388cb52aa03822fe657627a9f9b4fcd436`；`...\conditioned_pose_20260925_uniform12\epoch_012.pt` SHA256 `28662ffd1855ab18fc8091f866252d1800bbd2a2595222b907009891f0eb3930`。本次没有加载权重或复算论文指标。
- 环境：Windows、Python 3.12.12、NumPy 2.5.3、SciPy 1.18.1、Matplotlib 3.11.1、SQLite 3.50.4；NVIDIA GeForce RTX 4070 Laptop GPU，驱动 616.92，显存 8188 MiB。本次审计主要为 CPU/文件读取，未测 GPU 性能。
- 旧 v1 留出测试片段和基线报告**原样保留**。审计版 1,722 条来源动作，活跃 train/validation/test 片段 1337/198/184，窗口 26861/4052/3820，共 34733；测试基线 `output/conditioned_baselines_20260924_audited/report.json` SHA256 `c95c2763537566d6f1a46784db534055e139b36374a830aafd35d4ca72f6e30b`。后续若重划分，必须建立 v2 身份，不可把 v2 测试分数与旧 v1 测试分数直接当作同一测试集比较。

本次可复现命令，在项目根目录执行：

```powershell
& .venv\Scripts\python.exe docs\experiments\assets\2026-09-25-p0-stance-data-audit\analyze_p0.py
& .venv\Scripts\python.exe docs\experiments\assets\2026-09-25-p0-stance-data-audit\render_stance_filmstrip.py
```

脚本默认从 E 盘正式封版的 `contract/` 与 `audit/` 读取输入；它们与上文原审计版契约、报告和队列的 SHA 逐字节一致，因此可在新的主线工作区复跑。脚本逐 NPZ 校验契约中记录的 SHA，读取同 Root 审计组和 Motion Review SQLite，不改原数据与审核结论。分析无随机抽样/随机种子。若迁移了 E/D 盘路径，应先更新脚本中仅本机有效的路径；脚本的 `summary.json` 同时记录输入契约、报告和队列 SHA。

## 结果一：显式站蹲动作

四条资源各 76 帧、各 8 个窗口，Root 位置全程只有一个值 `(0,0,0)`。侧视线框的**每一帧**已在 [226 起身](assets/2026-09-25-p0-stance-data-audit/stance_226_all_76_frames.png)、[227 蹲下](assets/2026-09-25-p0-stance-data-audit/stance_227_all_76_frames.png)、[429 起身](assets/2026-09-25-p0-stance-data-audit/stance_429_all_76_frames.png)、[430 蹲下](assets/2026-09-25-p0-stance-data-audit/stance_430_all_76_frames.png) 中核对；[骨盆高度曲线](assets/2026-09-25-p0-stance-data-audit/stand_crouch_heights.png) 和 [逐窗跨度](assets/2026-09-25-p0-stance-data-audit/stance_windows.csv) 用全部原始帧计算。线框为 2D 投影，不含 UE 蒙皮/碰撞/场景接触验收。

| 片段 | 分区 | 方向 | 首末骨盆高度变化 | 10%–90% 主要阶段 | 8 个窗口中最大的未来骨盆高度跨度 |
| --- | --- | --- | ---: | --- | ---: |
| 226 Neutral | train | Crouch→Stand | +47.38 cm | 第 3–13 帧 | 1.63 cm |
| 227 Neutral | train | Stand→Crouch | −47.38 cm | 第 7–17 帧 | 1.88 cm |
| 429 Relaxed | train | Crouch→Stand | +48.91 cm | 第 3–14 帧 | 1.79 cm |
| 430 Relaxed | test | Stand→Crouch | −48.91 cm | 第 6–17 帧 | 1.88 cm |

当前窗口最早从第 0 帧取 24 帧历史，未来从第 24 帧才开始。因此 **32/32 个窗口的未来骨盆高度跨度均不足 10 cm**；现有“3 条训练过渡、24 个训练窗口”的数量不能解读为模型已经见过蹲下/起身的主要生成阶段。实际更多是“历史里已完成转换，预测保持目标姿态”。资源名和线框方向一致，Root 为原地动作；尚无移动中切换或真实玩家请求时间。完整数值在 [stance_clips.json](assets/2026-09-25-p0-stance-data-audit/stance_clips.json)，原 NPZ SHA 已逐条记录。

## 结果二：跨分区来源家族

旧质量审计报告的 13 个相同 Root 轨道组已逐组核对，并形成 [来源家族草表](assets/2026-09-25-p0-stance-data-audit/source_family_draft.csv) 与 [逐对距离](assets/2026-09-25-p0-stance-data-audit/same_root_cross_split_pairs.csv)。相同 Root 是候选线索：AimOffset/静态 Pose 可能共享零 Root，不能仅凭它合并为一个语义家族。剔除已禁用训练片段后，有 258 对同长度、跨分区、活跃片段可按帧比较；平均关节距离 ≤1/2/3/5 cm 的分别为 3/5/8/14 对。

| 候选来源家族 | 跨分区片段 | 平均逐关节距离 | 判断 |
| --- | --- | ---: | --- |
| Stand→Crouch Neutral/Relaxed | 227 train / 430 test | 1.082 cm | 高度近似；当前 test 430 不能证明独立来源的蹲下泛化 |
| Jump backward Start 左脚风格变体 | 547 train / 619 test | 0.002 cm | 几乎相同的 79 骨骼轨道，应同家族隔离 |
| Jump backward Start/Off 右脚风格变体 | 548 train / 618 test | 0.002 cm | 几乎相同；旧精确重复筛选只禁用了 620 train |
| Walk Loop 与 LookAtPOI 测试资源 | 1590 train / 682 test | 0.001 cm | 几乎相同；跨类别复用素材需要来源隔离 |

另有第 9 组的 660 train/661 validation 为旧审计发现的精确重复，660 已从活跃条件训练屏蔽；第 1、11 组都是不足 48 帧而无生成窗口的静态姿态组。其余各组为**待语义核定**的共享 Root/左右脚/方向/风格候选，不能自动全部删除或强制合并。当前 `traversal_semantic_v1.json` 已按 Traversal 大类隔离语义家族，非 Traversal 仍需建立录制/资源血缘 ID。v2 应按来源家族整体划分，再另留独立站蹲过渡测试来源；v1 测试集与旧报告继续保存作历史回归。

## 结果三：Motion Review 队列的原始记录（用户后续确认前）

审计队列 `review_queue.json` SHA256 `a189cab6fd1d9343b1ff9a8f94043f066af67b538bfcc7c4bab4fc681852a9fb`，共 107 条。只读检查 `D:\MotionDataLibrary\reviews\decisions.sqlite3` 发现 107 条数据库状态均为 `approved`，其中 **58 条的备注明确写了“批量通过：用户一键标记，未声明逐条观看”**，另 49 条为单独标记但备注为空。两类数据库状态都不能推出本次已逐段看完整动画。按名单、原状态、异常标签和待办导出的 [review_queue_state.csv](assets/2026-09-25-p0-stance-data-audit/review_queue_state.csv) 未改写 SQLite。

本次对 58 条批量通过片段制作并查看了 8 页本机抽样帧拼图：每条展示开头、指标标记帧和结尾骨架，以及整段 Root 高度/位移曲线，原文件均校验 SHA。拼图保留在 `output/p0_20260925/bulk_montages/page_01.png` 至 `page_08.png`，名单在同目录 `manifest.json`。抽样帧中多数局部骨架结构可辨，但多条 Jump/Traversal 的 Root 大幅升降或位移在无场景条件下无法判断落点是否成立。**这不是全帧播放或逐条验收，不能据此把 58 条复核项关闭。**49 条单独批准也需要确认查看过程并补具体备注。Root 高速、高度跨度、零派生接触仅是优先复核信号，不是自动拒收理由。

复核入口自身还发现过显示偏差：旧版默认“原地跟随”逐帧扣掉骨盆水平坐标，“世界位移”再扣首帧水平坐标，轨迹线又强行画在固定 Z 高度。这会掩盖导出 Root 的水平位置与垂直运动，旧批准状态因此**不能证明检查过真实 Root 轨道**。页面现已固定显示未经重新对齐的导出 Root 坐标、完整三维轨迹及逐帧 XYZ/相对首帧位移；数据库旧结论没有改写。这里的 Root 坐标属于动画资产导出轨道，不能当作关卡里角色 Actor 的运行时世界 Location。

这 58 条中 Traversal 34、Jump 17、其他类别合计 7；41 条命中 Root 速度、39 条命中 Root 高度跨度、29 条命中骨盆速度，标签可重叠。优先播放 Root 高度跨度最大的 653（29.83 m）、1241–1246（25.85–27.18 m），再查 639、1290、1293（约 16 m）；具体资源名和标记见审核状态表。没有关卡几何时先记“待场景核定”，不能依据高度阈值直接拒绝。

现有正式入口：

```powershell
& .venv\Scripts\python.exe -m data.tools.motion_review_server `
  --library 'D:\MotionDataLibrary' `
  --conditioned-contract 'E:\AIAnimationSystemData\prepared\reference_guided_root_pose_v1_20260924_audited' `
  --conditioned-audit 'output\conditioned_data_audit_20260924_complete'
```

在 `http://127.0.0.1:8765/` 按 50 条分页播放、看异常帧和 Root 轨道。此处是原审计时的复核建议；后续用户确认与冻结清单见下节。审核数据原位保留。离线骨架验收仍不能代替 UE 场景里的蒙皮、IK、接触和碰撞。

本次已在本机启动上述入口，`GET /api/catalog` 返回 HTTP 200；本地浏览器可直接打开该地址。服务进程只提供查看和人工结论入口，不自动改动来源资产。

## 结果四：P0 复跑与 107 条用户确认

2026-09-25 用户在当前任务中明确确认：“这107段动作是全验收通过的，就当我是一个一个全部标记了”。本次据此把固定名单中的 **107/107 条按用户指令视为逐条批准结论**，不再把 58 条旧批量标记当作 P0 待人工复核阻塞项。这记录的是用户的验收决定，不宣称用户或助手完成了 107 次逐条播放；助手也没有替用户重新判定每条场景落点。

复跑使用原 `analyze_p0.py`，将输出写到 [restart_2026-09-25](assets/2026-09-25-p0-stance-data-audit/restart_2026-09-25/) 以保留首轮产物。六份重算结果与首轮对应文件逐字节一致，包括 [summary.json](assets/2026-09-25-p0-stance-data-audit/restart_2026-09-25/summary.json)、四条过渡逐窗表、13 组来源家族表和旧数据库状态表。契约 SHA256 仍为 `9bbf2893d461290371c2cefdf54eeb201b09cf639f50ea0dd0254bcc2f9eae9d`，审核名单 SHA256 仍为 `a189cab6fd1d9343b1ff9a8f94043f066af67b538bfcc7c4bab4fc681852a9fb`；旧 v1 基线报告和两份第 12 轮权重的 SHA 也与本页上文一致。重查 `origin/main`、训练/评估脚本 Git blob、源 `dataset.json` SHA、Python/NumPy/SciPy/Matplotlib/SQLite 版本及 RTX 4070 Laptop GPU/616.92 驱动，均与上文冻结身份一致。

只读查询 Motion Review SQLite，固定名单仍为 **107/107 `approved`**。原记录中 58 条保留“批量通过”备注、49 条为单独标记；没有覆盖历史备注或伪造 107 次点击。另用 [freeze_user_attested_review.py](assets/2026-09-25-p0-stance-data-audit/freeze_user_attested_review.py) 把本次用户确认按每条 clip ID 绑定到 [review_acceptance.csv](assets/2026-09-25-p0-stance-data-audit/restart_2026-09-25/review_acceptance.csv)，确认来源、旧标记分布和哈希见 [review_attestation.json](assets/2026-09-25-p0-stance-data-audit/restart_2026-09-25/review_attestation.json)。CSV SHA256 为 `f2c8ca85fa7a58e2d16f51c625ccb5a38e238ece5e933d1621b712e2d65bc1de`，原数据库记录的规范快照 SHA256 为 `52cfe631bc5ec8c7ec82dd962e1d37bbef2bf8dc588b4a94a512342b97b65453`。SQLite、来源 NPZ、训练分区、旧报告和权重均未改写。

实际复跑命令（项目根目录，`--attested-at-utc` 对应用户本次确认的记录时间）：

```powershell
& .venv\Scripts\python.exe -c 'from pathlib import Path; import sys; sys.path.insert(0,"docs/experiments/assets/2026-09-25-p0-stance-data-audit"); import analyze_p0 as p; p.OUT=Path("docs/experiments/assets/2026-09-25-p0-stance-data-audit/restart_2026-09-25"); p.OUT.mkdir(parents=True,exist_ok=True); p.main()'
& .venv\Scripts\python.exe docs\experiments\assets\2026-09-25-p0-stance-data-audit\freeze_user_attested_review.py --attested-at-utc 2026-09-25T07:32:48Z
```

## 结论、限制与下一步

- **直接测得：**四条显式站蹲动作均原地，主要过渡落在第 24 帧前；现有 32 个未来窗口没有 ≥10 cm 的骨盆高度变化；跨分区存在多对近乎相同的动作轨道；数据库的 107 条状态均为 `approved`，其中 58 条保留旧批量标记。复跑结果与首轮一致，旧 v1 契约、测试集与基线报告未变。
- **用户确认：**固定名单的 107 条全部通过，并按逐条标记的结论处理。此决定已按 clip ID 冻结；它不提供逐条播放过程的独立证据。
- **工程判断：**先修正过渡窗口取样/补采独立来源，再做状态条件训练；直接增加 `Stand/Crouch` 输入而继续沿用当前窗口会缺乏有效过渡目标。风格/左右脚/跨类别近似素材要按来源家族分区。需要保留旧基线并把 v2 结果单独报告。
- **尚未验证：**UE 关卡里的蒙皮、IK、接触、碰撞和实际落点，真实玩家状态响应、重训收益及在线推理性能。这些是后续阶段的独立验收项，不推翻用户对当前离线动作名单的通过结论。
- **下一步 P1：**在不覆盖 v1 的前提下建立逐帧状态与过渡阶段标签、补采覆盖矩阵和按来源家族隔离的 v2 分区。站蹲主过渡缺乏未来预测目标，是比重复复核已通过片段更紧迫的数据问题。

## 修订历史

| 日期 | 变更 | 依据 |
| --- | --- | --- |
| 2026-09-25 | 首次记录；状态进行中 | 契约/NPZ/旧审计/审核库只读核对及逐帧线框图 |
| 2026-09-25 | 补充视口坐标和轨迹显示偏差；要求 Root 项重新复核 | 页面源代码与 1241、234 两段的浏览器实测 |
| 2026-09-25 | 复跑 P0 并按用户决定关闭 107 条复核，状态改为 P0 完成；保留旧标记历史 | 用户在当前任务中的明确确认、只读 SQLite 快照、重算结果和哈希 |
