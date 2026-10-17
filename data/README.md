# 数据

71,132 条非镜像 SOMA uniform 的全量处理入口、续跑与目录见 [SOMA 全库处理](../docs/soma-full-processing.md)，当前批次为 `20261018-soma-full-v1`；是否完成以 E 盘报告中的 `progress.json` 为准。

UE 来源转统一 SOMA 的实现与目录见 [UE → SOMA 离线重定向](../docs/uefn-soma-retarget.md)：原始不同骨架仍在 `raw/gameanimation`，新 78 节点结果和 BVH 在 `processed/gameanimation_soma`，来源/目标和排除记录在 `reports/gameanimation_soma`。

原生数据入口见 [独立原生运动导入与标准化](../docs/native-motion-import.md)：`NativeMotion`/`SampledMotion` 保存来源骨架，JSON/二进制按骨架签名共享，原生审核复用现有页面。2026-10-17 的新训练方向见 [SOMA 训练骨架方案](../docs/soma-training-data-direction.md)，来源导入与后续固定 SOMA 骨架转换分开执行。

最新计划明确普通 Locomotion 直接执行，使用当前姿态、站蹲状态和基础姿态参考；复杂跑酷由代理提供轨迹、语义和硬参考。数据侧共用 SOMA 标准化动作与分区，分别建立条件窗口索引，先用小样本核对 Root/Hips、时间轴及条件可见性，详见上述开发计划。

已实现 [SOMA 观察试点](../docs/soma-observation-pilot.md)：`tools/soma_observation.py` 选取 30 条非镜像完整源动作，保留原始时间，生成五关节轨迹与几何候选；原生审核页可播放及逐帧查看。输出位于 `E:/MotionWeaverData/{raw,processed,reports,reviews}/soma_observation/`；Root、训练 FPS 与跨骨架映射仍待后续确定。

当前按 [SOMA 轻量数据处理](../docs/soma-motion-processing.md) 执行：复用来源粗语义，生成可逆 Root 代理与自动三态接触候选，建立参考/任务候选索引。详细 LLM 请求与模型验收已移除；原始数据、实验输出和历史备注保留。


`runtime/`：可安装的数据加载器、骨架与交换契约，保留 `motionbricks.data` 导入名称。

`runtime/stance_motion.py` 和 `tools/build_stance_motion_index.py` 在既有 v1 条件契约上建立站蹲 v2 试验索引。只有历史允许首帧补齐，未来始终取真实帧；旧动画方向标注的目标状态是推定代理条件，不是玩家输入或 CMC 批准。当前 P0 标注见 `annotations/stance_transition_p0_provisional.json`，仅四条明确过渡动作，边界尚为 `provisional`。结果与实际命令见 `docs/experiments/2026-09-28-stance-transition-v2.md`。

`tools/`：数据准备、格式校验、批次调度、人工审核服务和入库工具。`review_web/` 随审核服务归档。`validate_seed_training.py` 校验训练数据格式，属于数据验收，不执行梯度训练。

`raw/` 与 `prepared/`：本地数据，忽略提交。当前大数据物理目录为 `E:/AIAnimationSystemData/`，包括 `BONES-SEED/`、`MotionDataLibrary/`、`UE/AILocomotionDataset/` 和 `prepared/`；旧 D 盘入口可能是兼容 Junction，不能视为第二份数据。

`tools/migrate_motion_storage_to_e.py` 是 Windows 本机的一次性存储迁移工具。默认只生成清单，`--execute` 才执行复制及切换旧路径；使用前停止修改来源数据的程序。它检查目录与链接边界、共享目标中的同名冲突，并以 SHA-256 验证复制结果，再删除已验证且未改变的来源文件、建立兼容 Junction。已有不同内容的目标文件、链接或再次迁移已切换的路径均会拒绝；校验读取全部数据，耗时和磁盘 I/O 随数据量增长。每次复制失败会保留来源，应根据清单与 Robocopy 日志恢复，不能将目录大小当作内容完整性证明。

`migration_progress_server.py` 按清单观察复制进度；`migration_progress_lite_server.py` 和 `migration_progress_lite.js` 只扫描目标目录大小，使用当前机器的估算总量，不验证内容，也不能证明迁移完成。三个观察器只绑定 `127.0.0.1`，默认端口都是 `8767`，一次只运行其中一个；轻量 Python 版支持 `--root`、`--port`。回归验证使用 `python -m unittest discover -s tests -p test_motion_storage_migration.py -v`，只操作临时夹具，不运行实际数据迁移。

已有 UE 导出数据的 Root/姿态条件索引与非学习基线全部使用 Python 离线处理，无需启动完整 UE 编辑器。契约、实测数量、局限和复现命令见 [Root 驱动与硬参考姿态数据契约](../docs/reference-guided-data-contract-v1.md)。新资产的采样或跨骨架重定向仍依赖 UE 资产系统，可走已有无界面批处理；编辑器用于最终视觉验收。

根目录安装后运行：`python -m data.tools.prepare_unreal_dataset --help` 或 `python -m data.tools.motion_review_server --help`。
