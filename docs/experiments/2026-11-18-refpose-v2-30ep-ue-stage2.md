# RefPose v2 30ep：UE 阶段一导出与阶段二 NNE 数值门禁

| 字段 | 内容 |
| --- | --- |
| 实验 ID / 类型 | `2026-11-18-refpose-v2-30ep-ue-stage2`；ONNX 导出 / UE 本地推理 |
| 开始、结束时间与时区 | 2026-11-18 16:18–16:42（Asia/Shanghai） |
| 状态 | 完成（阶段一导出与阶段二 NNE CPU 数值门禁） |
| 执行者与来源 | 本项目执行者；只导出并验证既有权重，不训练、不修改模型 |
| 关联任务或前序记录 | [30 epoch 训练](2026-11-14-refpose-v2-30ep.md)；[旧 UE 蒙皮推理](2026-09-25-ue-skinned-inference.md)；用户要求阶段三蒙皮由其亲自执行 |

## 问题与方案

- 研究问题：从本机现有检查点中选择同契约精度最好的权重，完成 ONNX 导出、真实窗口夹具与 UE NNE CPU 数值闭环。
- 选择：`refpose_v2_30ep/best.pt`，验证 RMSE 3.9145 cm；test 四档 5.40/3.39/2.94/2.38 cm。与 20ep、v3d 使用同一 release_v2 与评测口径，且四档均更优。它不是文档中缺失的 refpose_v3_71k 封盘权重。
- 范围：Python 导出与 CPU ORT 对齐、UE NNE CPU 对齐；不做骨架转换、蒙皮、AnimGraph、CMC/Mover、DML 或打包。
- 验收：四输入形状严格匹配，输出有限，PyTorch/ORT 与 Python ORT/UE NNE 的数值差异满足门禁，mask 硬覆盖保持。

## 代码、环境与数据

- 仓库提交 SHA：`8ef1b61cd136a486e603ae494c466168e7fa689a`；实验期间 dirty。本次实现包括 `inference/export/refpose/`、`inference/export/export_refpose_ue.py`、宿主及镜像插件的 `RefPose/`、`MotionWeaverTests/Private/RefPose/`、`tests/test_refpose_ue_export.py` 和兼容入口。未提交，未建分支。
- 实际导出命令（`--clips-per-split 2`）：

```bash
.venv/bin/python -m inference.export.export_refpose_ue \
  --checkpoint /home/acear/MotionWeaverData/runs/refpose_v2_30ep/best.pt \
  --release /home/acear/MotionWeaverData/full_release_v2 \
  --mmap /home/acear/MotionWeaverData/full_mmap_v2 \
  --output /home/acear/MotionWeaverData/model_weight/refpose_v2_30ep_ue_stage2 \
  --clips-per-split 2
bash scripts/ue.sh BuildEditor
MW_REFPOSE_UE_PACKAGE=/home/acear/MotionWeaverData/model_weight/refpose_v2_30ep_ue_stage2 \
MW_REFPOSE_UE_REPORT=/home/acear/项目/MotionWeaver/docs/experiments/assets/2026-11-18-refpose-v2-30ep-ue-stage2/ue_nne_report.json \
/mnt/Data/UnrealEngine/UE_5.8.4/Engine/Binaries/Linux/UnrealEditor \
  /home/acear/项目/MotionWeaver/unreal-sample/AIMotionSample/AIMotionSample.uproject \
  -unattended -nop4 -nosplash -NullRHI -log -NoLogTimes \
  -ExecCmds='Automation RunTests MotionWeaver.RefPose.NNE;Quit'
```

- 验证：`.venv/bin/python tools/dev.py lint` 通过；`.venv/bin/python -m pytest -q` 为 **303 passed、38 skipped、48 subtests passed**（57.25 s）。跳过为旧封盘数据、旧权重、平台专项和缺失的上游检查点，不是本次包或 UE 测试跳过。
- 独立用真实 test 窗口对照 `MotionWindows(scenario="two_refs")` 与独立重建器：history/root_plan/reference/mask/target 最大绝对差全为 **0**。
- 阶段三骨架映射、坐标反解与蒙皮未实现，按用户要求留给用户执行。
- 权重：`/home/acear/MotionWeaverData/runs/refpose_v2_30ep/best.pt`，sha256 `12908bcb0df88e721d7f4fa84f61aedd7e346dd56e770103e61139248550c849`，epoch 29，78 骨，width 256。
- 数据：`full_release_v2`，contract sha256 `fd94c9f78c16701f834ae38613ccced0b1262002ccac91e8ddd3f1fad2b42a15`；`full_mmap_v2`；骨架 sha256 `159050ea134c282c7d60ebc92d3b3a80f839e5ab30693e54f1b98c0b24cd1345`。
- 环境：Linux、UE 5.8.4、NNERuntimeORTCpu；Python 3.14、PyTorch 2.15.0、ONNX/ORT 版本以导出清单为准。
- 条件边界：历史与理想未来 Root 来自同一真实窗口；参考档位 ()/(12)/(23)/(12,23)。这不是在线 CMC 预测 Root 或玩家控制验证。

## 结果与证据

| 指标或观察 | 对照 | 本次 | 口径与样本 |
| --- | ---: | ---: | --- |
| ONNX | PyTorch CPU | opset 17 / IR 8 / 257 节点 / 12,393,645 字节 | 静态 batch=1，四输入一输出 |
| torch 对 ORT CPU 最大绝对差 | 既有 allclose 与相对门禁 | **1.3113e-05** | 216 个真实窗口；最大相对差 6.0420e-06，门禁通过 |
| UE NNE 对 Python ORT 最大绝对差 | <= 1e-4 | **4.529953e-06** | 72 组真实窗口 × 24 × 702 = **1,213,056** 个输出值 |
| mask 硬覆盖最大绝对差 | 精确 0 | **0** | ()/(12)/(23)/(12,23) 四档 |
| UE NNE 单实例延迟 | 只作本机参考 | **p50 0.728 / p95 0.775 ms** | Linux、NullRHI、单角色、72 次，含 RunSync；不是帧预算或多角色结论 |
| UE 自动化 | 两项均须成功 | **Numeric / RejectsBadInput 均 Success，进程退出 0** | UE 5.8.4、NNERuntimeORTCpu、NullRHI |

- 导出包：本机 `/home/acear/MotionWeaverData/model_weight/refpose_v2_30ep_ue_stage2/`（72 个目录、每目录四输入与 expected.f32）。ONNX sha256 `34d4d27f970b018ddefa383ffca4c74dcd7aab32d5b1a9925e65439fa88034f7`；skeleton sha256 `159050ea134c282c7d60ebc92d3b3a80f839e5ab30693e54f1b98c0b24cd1345`；stats.bin sha256 `a2ce1570117cd857da1b0aeaebef3589a31c461595bd8447337f8abedb10f779`。
- 仓库内证据：[导出摘要](assets/2026-11-18-refpose-v2-30ep-ue-stage2/python_export_summary.json)、[UE 报告](assets/2026-11-18-refpose-v2-30ep-ue-stage2/ue_nne_report.json)、[UE 日志摘录](assets/2026-11-18-refpose-v2-30ep-ue-stage2/ue_nne_final_trimmed.txt)。完整编辑器日志在本机 `unreal-sample/AIMotionSample/Saved/Logs/AIMotionSample.log`（约 344 KB），不入库。
- 包覆盖 train/validation/test 各 2 个均匀抽样 clip、首/中/尾三个位置和四种 mask，共 72 组；不是全 test 集重新评测，不更新模型 RMSE。
- 异常与处理：导出初次清单在 ONNX 移出暂存子目录前计算，改为先移出再哈希；新增 Python 3.14 导入/包边界问题通过共享实现与薄兼容入口解决；UE 的 Linux `GetSHA256Signature` 是断言占位，改用引擎 OpenSSL 后正常；刷新 UBT clang 数据库并补显式 JSON policy include 后，新增 C++ clangd 源码诊断为 0。一次全量 pytest 的无关实验室锁测试失败，单独重跑通过，最终全套通过。

## 结论与论文使用边界

- **直接测得：** 选定的 30ep 权重已导出；真实离线窗口上 Python ORT 与 UE NNE CPU 数值一致，mask 精确硬覆盖，ONNX 字节哈希在 UE 内复算匹配。
- **工程结论：** 阶段二已具备可复用四输入 CPU 会话和可重复门禁，可作为阶段三蒙皮适配的数值基线。
- **尚未验证、不能据此宣称：** 未做骨架转换、蒙皮、FK/IK、脚滑、AnimGraph、CMC/Mover、在线预测 Root、多角色、DML、Cook 或 Shipping；单次延迟不代表帧预算。


## 修订历史

| 日期 | 变更 | 依据 |
| --- | --- | --- |
| 2026-11-18 | 完成阶段一、阶段二与数值/构建证据 | 导出摘要、UE 报告与自动化日志 |
