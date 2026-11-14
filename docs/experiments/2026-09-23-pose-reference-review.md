# 参考姿态实验审查与验证（2026-09-23）

范围：Candidate24 隔离候选、参考选择消融、阶段审计、手选 HTML 实验室、软/硬约束对照及关联文档。个人 profile 只同步已验证的实验能力与限制。

## 审查修复

第一轮修复：

- 部分固定输出路径缺少父目录创建，首次运行会在推理结束后保存失败。
- 阶段候选对超短片段使用空区间求极值；现在保留唯一首尾，空动画明确报错；稳态阶段评估明确记录跳过的短片。
- 修改参考后，旧曲线错误地使用草稿参考线；现在实际参考与草稿分开绘制，指标保留实际窗口、边界选项和参考列表。
- 浏览器存储禁用会中断页面初始化；现在退化为会话内选帧。
- 约束求解未检查重复/跳跃帧号、非法权重与无效四元数；现在拒绝不满足等间隔局部姿态契约的输入。
- 本地 HTTP 服务增加 Host/Origin 检查，保留会话令牌、请求大小与并发锁，并补参数和失败释放回归。

第二轮修复：短姿态四参考评估明确跳过不足10个真实帧的输入；视频渲染对无 Traversal 的报告提前报错；删除“用户认可手选”的不准确措辞，更新导出包装器的历史/代理时间边界注释。复查修复后的源代码、数据单位、报告口径及文档链接，未发现剩余阻塞本次实验工具合并的问题。

## 本轮实际验证

- 22 项 Python 回归通过，覆盖选择器、阶段候选、硬约束、HTTP 请求/并发失败与流式冻结行为。
- `evaluate_hard_constraints` 使用本机 V2 权重实际重跑：用户参考32/34/44/52/56/59，平均关节误差软11.321751、直接覆盖9.733179、平滑硬6.902577 cm；对应加速度误差4.008097、9.428189、4.185783 cm/帧²。
- Chrome 页面保留六个用户参考后实际运行；第56帧软18.49、硬0.0000 cm。切换边界选项后旧结果标识正确，随后恢复用户选项并重跑。
- UE Editor Development 增量构建成功，目标已是最新；没有把0个编译动作描述为全量重编译。
- 无渲染 UE 自动化实际执行 FixedSampling、PlaybackBuffer、StationaryPlayback，3项成功。

```powershell
.venv/Scripts/python.exe -m unittest inference.profiling.test_hard_pose_constraints inference.profiling.test_pose_reference_lab inference.profiling.test_phase_references inference.profiling.test_pose_references inference.profiling.test_streaming_comparison
.venv/Scripts/python.exe -m inference.profiling.evaluate_hard_constraints
```

本地日志：`.build/pose_reference_cr_build.log`、`.build/pose_reference_cr_automation.log`。本轮未重新跑全部历史消融；各历史报告保留当轮实测范围。

## 仍存在的实验限制

精确关键帧由最终等式约束构造，不能算作模型预测成功；参考邻域加速度误差仍比软输出大。当前没有速度边界等式、接触/碰撞、关节限位或骨长保持保证；主值旋转向量接近180度仍有分支连续性限制。约束只作用于计分区间28～94内的手选参考，区间外明确标为未约束。

代理48读取已知未来；硬约束离线处理整段融合结果，并影响关键帧之前的邻域。两者都不能作为未知未来下实时因果过渡生成的证明。没有替换训练权重、默认UE模型或实现游戏跑酷代理。检查点、原始资产及实验输出留在本地，不随源码发布。
