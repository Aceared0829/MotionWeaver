# 本地动作数据管线

代码属于 AIAnimationSystem 项目；数据库默认放在 `D:/MotionDataLibrary`，不纳入 Git。

- `catalog.sqlite3`：动作身份、原始标签、各源表示路径、处理状态。
- `motions/`：预留给通过验收的动作数组，当前不包含正式训练动作。
- `contracts/`：骨骼与坐标契约。
- `work/`：有界 UE 批处理临时数据。
- `reports/`：运行和验收报告。

使用项目的 Python 环境：

```powershell
.venv/Scripts/python.exe -m data.tools.ops.motion_pipeline scan-seed --source D:/BONES-SEED
.venv/Scripts/python.exe -m data.tools.ops.motion_pipeline status
```

重复扫描按数据集与动作名称去重，每 1000 条提交一次；SOMA Uniform、Proportional、G1 是同一动作的三种表示，不算三个训练样本。

## 当前实现边界

已实现清单登记、可恢复索引、状态查询和小批量 UE 动画轨道回读试验。
`discovered` 仅代表清单已登记，不表示文件存在、骨骼配对正确或动作质量合格。
`scripts/unreal/validate_animation_roundtrip.py` 是试验工具，不是 UE IK 重定向器。它使用 `MOTION_PIPELINE_WORK_ROOT` 指定的试验请求，创建未保存的临时资产并检查采样结果。

目前已完成 8 条 SOMA 小批量的源轨道导入、UE 原生 IK 重定向、资产保存后由独立 UE 进程导出。它们仍在隔离工作区，不是已发布训练数据。
待完成：全量源骨骼兼容性分类、根朝向策略、脚部接触与视觉质量验收、最终动作入库、训练读取，以及逐条删除事务。
当前删除入口明确拒绝执行。不能用数值回读成功代替语义/视觉质量验收，不能把旧外部 FK 试验 NPZ 标记为 UE 重定向完成。
原始示例项目与已有动画不属于清理范围。Proportional 的 C 盘 junction 必须单独验证物理删除边界。

## 导入既有训练集

`python -m data.tools.ingest.import_prepared_motions --source <训练目录> --library D:/MotionDataLibrary` 将完整快照复制到 `motions/prepared_<清单哈希>/`。
源与目标均经过现有训练加载器检查，每个复制文件校验 SHA256，保存原有统计量、Root 数组、标签和数据划分。`prepared_imported` 表示既有训练数据的完整性导入，不是新增视觉验收。源目录不会删除，重复导入相同清单不重复建立数据库条目。

`python -m data.tools.ops.run_ue_pipeline_check --engine <UnrealEditor-Cmd.exe> --project <uproject> --work <请求目录>` 为每次 UE 检查建立唯一运行目录，要求退出码、日志和完整逐条报告共同通过。仍只检查轨道往返，不冒充 IK 重定向或发布门禁。

## SOMA 实际 UE 试验

`import_soma_reference.py` 导入数据自带 USD 的真实网格和骨架；`setup_soma_retarget.py` 创建共享 SOMA/UEFN Rig 与链映射。所有生成资产位于 `/Game/MotionPipeline`，不修改原示例骨骼。
`data/tools/ingest/prepare_soma_ue_batch.py` 只将源 BVH 转换成源骨骼局部轨道；`retarget_soma_batch.py` 使用 UE 的 `IKRetargetBatchOperation` 生成目标动画。
启动处理这些 USD 资产的 UE 进程需传入 `-EnablePlugins=USDImporter`。

`fix_soma_root.py` 修复静止源 Root：从目标 Pelvis 提取水平位置，Root 高度归零，保持静态根旋转，未把骨盆倾斜写入 Root。根朝向尚待独立实现和验收。修改 Op 设置后必须强制保存，否则只改内存的设置可能丢失。
`data/tools/audit/audit_ue_batch.py` 保存一份共享骨骼和 N 份动画 NPZ，并检查协议、有限值、骨长、Root 范围和脚部高度；结果始终标记 `training_ready=false`，不能据此删除源动作。

`data/tools/audit/check_seed_sources.py` 全量逐文件读取骨架头、声明帧数与帧率，按头签名分组，写入 `source_header_checked`。它没有检查整个动作正文，也没有证明骨骼与 UEFN 兼容；不同签名仍需进一步分类。数据库按 `(dataset,state,id)` 建索引避免批处理反复全表扫描。

## 数据库批次工作进程

2026-10-04 完整长片段支持：`run_seed_queue.py --max-frames-per-clip N` 默认 6000，接受 2..12000。队列显式设置 `AILocomotionMaxBoneSamples=1000000` 与对应的 `MaxFramesPerClip`；原生导出器保留默认 250000，并允许命令行调整至最多 4000000 个骨骼采样。容量取决于实际骨骼数量、JSON 内存及磁盘门禁，不自动分段、截断或重采样。历史记录中的 3000 帧隔离原因属于旧预算。

`data/tools/ops/retry_seed_review_batch.py --cohort <固定拒绝名单>` 只重跑本次授权的 10 条、保留源哈希与旧二审结论，新输出存为 `needs_secondary_review`。结构一致且身体参考偏移差不超过 0.25 cm 的源可显式使用 `prepare_soma_ue_batch.py --normalize-reference-offsets`；绝对 Hips/Root 运动和旋转保留。归一化不会把源 FK 转换冒充为目标重定向；目标输出仍须真实 UE IK、导出审计和新二审。

`python -m data.tools.ops.run_seed_queue --max-batches <数量>` 从数据库选择待处理动作，每批最多32条、累计约20000源帧。UE 导出器按配置处理完整长动作，不自动切片或截断；超过单条帧数预算的动作进入 `needs_segmentation`，非已验证骨架头进入 `needs_skeleton_review`，不会丢弃。存在未收尾的 `retarget_running` 时拒绝重复处理，异常记录为 `retarget_failed` 后停止，需检查运行目录再恢复。

工作进程有单实例文件锁、单阶段30分钟超时、D盘40GiB和系统盘10GiB可用空间下限。建立 `D:/MotionDataLibrary/work/STOP` 可在当前批次结束后停止。当前仅推进到 `retargeted_pending_quality`；输出、源哈希、UE资产身份及完整审计回执进入数据库，原始动作和临时资产仍保留。没有质量发布或删除功能，不能把这些状态计为全量完成。

根据用户要求，新增提前停止余量：领取批次前，D盘不足44GiB或系统盘不足14GiB即写入持久 STOP 并停止，给原硬安全线预留4GiB。STOP 不自动移除；完成质量验收、可靠发布、授权范围内的清理并恢复安全余量后，才能人工或由跟进任务复核恢复。不能把仅几何初筛通过的数据当作已完整验收，也不能提前删除原始动作。

## 本地交互验收

人工验收单次上限为 50 条（用户最新要求），与每 500 条处理停批的数量不同。异常项优先，占用后剩余名额用于固定随机抽样（至多其余动作的 5%，向上取整）。异常超过 50 条则拒绝生成超额名单，先修复/隔离，不能偷偷省略异常或把未审动作算通过。已保存且不超限的名单不重抽，旧结论保留。

当用户明确授权“自动技术验收后直接入库并清理”时，`data/tools/ops/run_overnight_seed_pipeline.py` 监督队列：每 500 条运行完整性初筛与训练格式回读，以 `accepted_imported` 正式发布最终无损轨道、共享骨骼和训练快照，再调用只允许精确哈希清单的清理器删除对应三种源表示、UE 批次资产和可重建工作文件。任何哈希、训练回读、状态、路径边界或磁盘门禁不通过都会使该批停止，不会删除文件或启动下一批；删除由持久清理日志逐文件记录。人工验收仍可用于事后抽检，但不再是自动推进的前置条件。

## 已验收批次发布与清理

2026-09-10 首批收尾：582 条已写入 `accepted_imported`，位于 `D:/MotionDataLibrary/motions/seed_e3ba194764e38d6b`，同目录 `training` 保留已回读训练快照。`reports/close_e3ba194764e38d6b.json` 绑定人工名单与逐条来源；4671 个源/中间文件已执行清单删除（约16.59 GiB），`cleanup_journal` 可核对。原1722条保留。收尾后系统盘仅12.91 GiB，低于14 GiB提前停止线，下一批未启动。此为当时快照，恢复前必须读取实时状态。

用户确认整批采用“全部异常项 + 固定 5% 抽样”验收后，`data/tools/publish/close_seed_batch.py --validation <训练回读报告> --cohort <人工名单>` 先生成清理预案，加入 `--commit` 才复制无损 UE 轨道及共享骨架到 `motions/seed_<回执哈希>`，验证哈希后事务提交为 `accepted_imported`。此状态指已接受的数据资产入库，不代表跨批次模型训练统计量、训练/验证划分或最终模型质量已完成。保持导出的 Root 朝向，不暗中重解释身体朝向；人工接受范围与原技术检查记录均保留。

`data/tools/publish/extend_seed_cleanup.py --plan <清单>` 仅补充资产身份与 UE 导出哈希都一致的 JSON 副本。`data/tools/publish/cleanup_seed_batch.py --plan <清单>` 默认预检，`--execute` 才逐文件删除；它重新验证最终数据库、训练读取、共享骨架及全部候选哈希，将意向及删除结果持久化到 `cleanup_journal`。失败原地停止、可按同一清单续作，不递归删除目录。该操作不可从回收站恢复源文件；保留最终动作及其源身份记录。完成后必须复核磁盘余量，不能因清理完成就忽略安全停止线。

尚未完成的边界：发布进程若在数据库提交后、清理计划写入前崩溃，需要核对最终 manifest 与数据库来恢复计划，不能盲目重跑发布。混合成功/失败的 UE 工作目录不整目录清理；无法唯一归属的副本保留。跨批次统一统计量与数据划分应从最终无损动作派生，不能直接混用不同批次归一化张量。

`data/tools/audit/validate_seed_training.py [--limit N]` 直接读取隔离输出的共享骨架 + NPZ，经现有 `prepare_unreal_dataset.prepare` 转换，再用 `UnrealMotionDataset` 逐条取训练张量，并逐数组比较转换后姿态、旋转和 Root 轨道。生成 `work/training_check_*/training_validation.json`，不改变生产状态。失败时保留不完整快照供检查，不能算通过；运行过程 D 盘低于 44 GiB 停止。此验证快照使用本批统计量且未划分留出集，不能直接替代最终生产训练集或删除许可。

每轮最多累计 500 条 `retargeted_pending_quality` 动作，UE 微批最多 32 条且受 20000 源帧上限约束；接近 500 时缩小微批，达到上限写入 STOP 并初筛。磁盘提前停止也执行初筛。既有 582 条积压属于旧批次，不重新切分已标注的固定名单；未结案前不能追加新动作。人工通过本身不会减少待发布计数。

项目级规则位于 `.agents/skills/process-motion-batches/SKILL.md`。`data/tools/review/record_motion_review.py --cohort <完整名单路径>` 验证名单绑定的初筛报告、输出哈希和唯一审计记录，保存独立、内容寻址的人工回执；不会修改生产状态。最终训练回读、发布和可恢复删除仍须实现并验证，不能仅凭回执清理。

在仓库根目录运行 `.venv/Scripts/python.exe -m data.tools.review.motion_review_server --port 8765`，打开 `http://127.0.0.1:8765`。需要保持服务运行，不能直接双击 HTML 代替服务。

页面展示真实源 BVH 与 UE 导出动作的三维骨骼线框（不是蒙皮渲染），共用相机和时间轴。视口按 [Epic 的 Viewport Controls](https://dev.epicgames.com/documentation/unreal-engine/viewport-controls-in-unreal-engine) 实现主要导航：透视视图右键原地转向、右键配合 WASD/QE 飞行、右键配合滚轮调飞行速度，左键拖动前后移动并左右转向；Alt+左键绕焦点、Alt+右键推拉、中键或 Alt+中键平移、滚轮缩放；正交视图使用正交投影，右键平移、滚轮缩放。F 聚焦当前帧角色，“全段入镜”重置到整段动作范围。动画另有暂停、拖帧、逐帧和速度调整；这里的空格用于播放，不是 UE 的变换工具切换。坐标统一为厘米，不缩放身体；验收页固定显示未经对齐的导出 Root 坐标、保留高度的三维轨迹，以及逐帧 XYZ 和相对首帧位移。该坐标属于动画导出轨道，不等于关卡 Actor 的运行时世界 Location。

所有初筛异常均进入人工队列；其余按固定随机种子抽取 5%（向上取整）。名单保存在 `D:/MotionDataLibrary/reviews/cohort_*.json`，结论与备注保存在独立 `decisions.sqlite3`，可导出 JSON。刷新不会重抽名单或丢失已保存结论。人工记录不会自动改变生产状态、发布训练数据或删除源文件；抽样通过也不等于每条动作都已通过完整质量验收。
