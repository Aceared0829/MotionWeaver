# SOMA 全库处理速度优化

| 字段 | 内容 |
| --- | --- |
| 实验 ID / 类型 | `2026-10-18-soma-processing-speed`；数据处理性能 / 等价验证 |
| 时间 | 2026-10-18 02:16 起，Asia/Singapore |
| 状态 | 对照验证完成；71,132 条全库仍在处理 |
| 执行者 / 来源 | 本项目本地 Python 管线；用户要求优化处理速度 |
| 前序记录 | [SOMA 全库处理](2026-10-18-soma-full-processing.md) |

## 问题与方案

运行中的 8 进程批次 CPU 利用率偏低。cProfile 对一条 3,769 帧动作测得 19.870 秒，其中文件打开累计 6.912 秒、目录创建 5.015 秒、stat 2.084 秒、替换 1.851 秒；压缩约 0.320 秒。该测量与原全量批次同时访问 E 盘，含首次创建目录的额外成本，只用于定位 I/O 等待，不能与后续空闲盘对照直接计算加速比。

保持原数学过程、原生文件和固定骨架格式不变，复用已计算的世界坐标供特征和观察使用；回读时直接计算实际文件字节 SHA，避免再次打开两个原生文件；复用 NPZ 哈希；每个进程缓存固定骨架与已创建的输出目录；二进制写缓冲从默认值增至 1 MiB。没有减少回读校验、降低 float64 精度、降采样或取消来源文件。

旧批次主进程 56240 及其 8 个工作进程在 02:19 停止。逐条日志完整解析得到 518 条回执，全部通过技术处理，原始数据未删除。优化版本通过显式 `--upgrade-receipt` 续跑，元数据/目标/策略/范围必须完全一致，只允许验证过的代码身份变化，原清单、修改前代码和回执归档到本机 E 盘 `manifest-history/`。

## 代码、环境与数据

- 对照基线 `ba67be2`，本次工作区 dirty 文件为 5 个数据模块及新测试，最终代码以本实验提交及回执 `code_sha256` 为准。
- Windows，Python 3.12.12、NumPy 2.5.3、SciPy 1.18.1，32 逻辑处理器、约 32 GB 内存；无新依赖。BLAS 每进程 1 线程。E 卷本次非 Dirty。
- 元数据 SHA `71d09422034946d17dbb0db027d569408c486545bdb12f7979d55e6ffd9bbc39`；固定目标与处理策略延续原批次 manifest。
- 在 71,132 条非镜像元数据按 `linspace(0, N-1, 8)` 确定性选择 8 条，总计 14,926 帧，帧数为 3769、316、1520、655、779、220、688、6979。7 轮重复使用同一组样本，不合计为独立数据量。未划分训练/验证集、未训练模型、未进行人工视觉验收。
- 源声明时间键与 FPS、78 节点固定目标、原始骨架、Root/接触候选定义不变。任务仍 `training_ready=false`，候选不代表真实接触或在线可见条件。

## 结果与证据

下表为停止全量写入者后，顺序运行的同一 8 条样本端到端计时，含工作进程启动和全部落盘回读。每轮输出到独立 run ID；OS 缓存未清空，不能据此推算全库固定完成时间。

| 版本 | 并发 | 端到端秒数 | 比较 |
| --- | ---: | ---: | --- |
| 原版 | 1 | 22.524 | 并发探测 |
| 原版 | 4 | 14.546 | 并发探测 |
| 原版 | 8 | 11.364 | 对照第 1 轮 |
| 优化 | 4 | 8.577 | 较同并发原版 1.70 倍吞吐 |
| 优化 | 8 | 6.718 | 第 1 轮 1.69 倍吞吐，耗时减少 40.9% |
| 原版 | 8 | 11.938 | 对照第 2 轮 |
| 优化 | 8 | 6.643 | 第 2 轮 1.80 倍吞吐，耗时减少 44.4% |

8 条基线分别与优化 4 进程、8 进程及复测版本比较，共 24 次配对：raw 和 processed 的 motion.json/arrays.bin SHA-256 完全相同，9 个 NPZ 数组逐元素完全相同，观察结果与逐条回执除输出路径/派生文件哈希外语义相同。最大 Root/身体重合成误差为回执中的对应值，门槛仍 `1e-9 m`。

仓库内证据：[等价与计时回执](assets/2026-10-18-soma-processing-speed/equivalence.json)。完整对照产物、本机 profiling 与修改前代码在 `E:/MotionWeaverData/reports/soma_uniform/20261018-speed-*/`、原批次 `manifest-history/` 及本机 `output/soma-speed-profile/`；路径仅本机有效，原始大文件未提交。对照脚本读取原批次 selection.csv；原版子进程显式加载备份模块，优化模块加载路径已核对与维护源码哈希相同。

## 实际命令与验证

```powershell
# 本机对照脚本，version 分别取 baseline-w1/w4/w8、optimized-w4/w8 和两次 repeat
$env:OPENBLAS_NUM_THREADS='1'
./.venv/Scripts/python.exe output/soma-speed-profile/benchmark.py baseline-w8 --workers 8
./.venv/Scripts/python.exe output/soma-speed-profile/benchmark.py optimized-w8 --workers 8
./.venv/Scripts/python.exe output/soma-speed-profile/verify.py

./.venv/Scripts/python.exe -m unittest tests.test_soma_processing_optimization tests.test_soma_full_processing tests.test_motion_import tests.test_motion_features
./.venv/Scripts/python.exe -m unittest discover -s tests -p test_soma_observation.py

./.venv/Scripts/python.exe -u -m data.tools.process_soma_dataset --data-root E:/MotionWeaverData --run-id 20261018-soma-full-v1 --workers 8 --resume --upgrade-receipt E:/MotionWeaverData/reports/soma_uniform/20261018-soma-full-v1/optimization-equivalence.json
```

新增 4 组参数化用例验证回读哈希、共享 FK、显式代码升级及拒绝策略改变；现有固定骨架、导入、运动特征和观察检查通过。最初按包名执行观察测试时，其既有顶层 `test_motion_import` 导入失败，改用该测试既有 discover 路径后通过；不是管线计算错误。修改文件通过 Ruff `E9,F63,F7,F82,I`。

优化后台启动于 02:25:01，wrapper PID 52116。stdout/stderr/启动命令在本机 `E:/MotionWeaverData/reports/soma_uniform/20261018-soma-full-v1-launch/optimized-*`。续跑先校验全部已有回执哈希，再处理剩余片段；旧回执不会因优化作废。实时状态以原批次 progress.json 为准，不能把对照完成当作全库完成。

## 结论与使用边界

直接测得该样本集约 1.69–1.80 倍吞吐，输出一致；主要瓶颈为 E 盘文件操作等待，8 进程在本对照中快于 4 或 1。全库随动作长度、目录规模、磁盘缓存及其他负载变化，尚未验证最终平均加速或完成时间。未据此宣称训练、UE 蒙皮或模型质量提升。

## 修订历史

| 日期 | 变更 | 依据 |
| --- | --- | --- |
| 2026-10-18 | 首次记录 | 两轮 8 进程对照、24 次文件和数组等价验证、原批次续跑 |
