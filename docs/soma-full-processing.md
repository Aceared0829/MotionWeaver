# SOMA uniform 非镜像全库处理

执行入口为 `python -m data.tools.process_soma_dataset`，保留原始 BVH，处理 `seed_metadata_v004.csv` 中 `is_mirror=False` 的 71,132 条、62,301,201 帧。镜像数据及 `soma_proportional` 不在本轮处理范围。

## 输出与来源

```text
E:/MotionWeaverData/
  soma_uniform/bvh/                           既有原始 BVH，不改写或删除
  raw/soma_uniform/<run>/clips/<id>/           原始局部轨道、时间键与来源骨架
  processed/soma_uniform/<run>/
    skeletons/<固定SOMA签名>/                 一份固定目标骨架
    clips/<id>/                              固定骨架局部 TRS、源时间戳
  processed/soma_processing/<run>/<前缀>/     Root/身体、接触候选 NPZ
  reports/soma_uniform/<run>/
    manifest.json                            元数据、代码、目标和规则身份
    selection.csv                            本轮全部原始元数据
    clips.jsonl                              逐次处理回执；失败重试保留旧回执
    progress.json                            运行阶段、数量与最近更新时间
    observations/<前缀>/<id>.json             来源标签、候选与处理说明
    export-index.csv                         完成后生成的成功索引
    reference-candidates.jsonl               完成后的参考帧候选
    task-index.jsonl                         完成后的任务候选，非正式训练集
    unresolved.json                          重试后仍需人工复核的条目
    run.json / acceptance.json               完成后的整批汇总
```

现有 SOMA 已有相同名称、层级与局部轴，先验证 78 节点和旋转通道，随后统一静态局部偏移至 `soma_uniform_v1`。保留所有录制局部旋转；来源 Root 完整变换合入 Hips，输出 Root 固定。Hips 世界位置必须保持，身体关节动态平移不能当成静态骨长丢弃。原始骨架和轨道另存 raw，参考偏移变化及引起的世界位置变化有记录。这是同局部轴的固定参考适配，不是 UE→SOMA 那套跨轴人体映射。

时间保留来源声明秒数和 FPS，不降到 30 FPS、不裁片段或补帧。处理复用 `motion_processing.py`：可逆 Root 位置表示、三态脚部接触候选、手部保持未知。候选不是接触真值、在线玩家输入或已选硬参考；任务索引不做正式训练划分，`training_ready=false`。

原生轨道、固定运动和 NPZ 均实际落盘回读。原始 BVH 不额外复制一份；当前格式保守预估及 64 GiB 余量在启动前检查，并每批复查剩余空间。磁盘 I/O 错误中止处理，不把设备故障伪装成可忽略的数据问题。

## 运行与续跑

```powershell
./.venv/Scripts/python.exe -m data.tools.process_soma_dataset --run-id 20261018-soma-full-v1 --workers 8
```

独立后台进程将 stdout/stderr 保存到 `reports/soma_uniform/20261018-soma-full-v1-launch/`。本轮启动记录与续跑脚本也在该目录。原始文件不清理；关闭查看页不停止任务，电脑睡眠会暂停计算。

需要续跑时使用该目录 `resume.ps1`，它拒绝在原任务或其工作进程仍运行时再启动另一份写入者。续跑绑定元数据、目标、核心代码和处理规则，核对已有输出哈希及共享骨架；本版本未写完的片段保留到 `partial/` 后重新处理。不得同时手工启动同一版本的两个 `--resume` 进程。

代码优化后的续跑必须先停止旧写入者及其子进程，在同一来源样本上逐项对照输出。`--upgrade-receipt <JSON>` 仅接受原 manifest SHA、当前全部代码 SHA 和等价验证通过的显式回执；元数据、目标、规则或范围变化仍拒绝续写。原 manifest、对照回执及本次修改前代码保存在 `manifest-history/`。2026-10-18 的优化减少重复文件打开、目录创建和 FK，增大二进制写缓冲，仍保留实际落盘回读、全部 float64 数组及源时间键；见[实测记录](experiments/2026-10-18-soma-processing-speed.md)。

先完成首轮，再对技术未通过条目重试一次，残余问题保存在 `unresolved.json`。朝向未知与参考偏移统一属于显式候选/适配标记，不等同文件损坏，不能仅为消除标记而编造方向或接触。

`progress.json` 的 complete 才表示本轮首轮和重试结束。最终索引可交给既有 `motion_review_server --native-index <export-index.csv>` 查看。当前本地进度网页的启动被自动审批拒绝，未启用该服务；直接检查进度文件和运行日志。
