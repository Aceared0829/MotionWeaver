# 推理与部署验证

`runtime/checkpoint.py` 提供受数据契约保护的 VQ-VAE 加载，供训练评估和推理导出共用；不导入训练 CLI 或绘图库。

`runtime/backbone/` 保留上游生成编排；`runtime/experiment/` 是 G1 参考演示兼容加载路径，仍需 Lightning 模型封装。它与轻量 UE VQ-VAE 导出路径的依赖要求不同。

`export/` 导出 FP32 权重、骨架、统计量和契约；`profiling/` 测量神经网络推理与实验播放质量；`cli/`、`demo/`、`assets/` 提供 G1 MuJoCo 参考演示。

```powershell
python -m inference.export.export_unreal_inference --help
python -m inference.export.export_unreal_animgraph --help
python -m inference.profiling.profile_unreal_inference --help
python -m inference.profiling.evaluate_unreal_streaming --help
python -m inference.profiling.compare_unreal_streaming --help
python -m inference.cli.interactive_demo_g1 --help
```

`export_unreal_inference` 保留 PyTorch 导出包。`export_unreal_animgraph` 导出固定窗口 ONNX、骨架契约和数值验证夹具，对应已移除的 `AIAnimation` Inference Lab 的单输入 79 骨契约；当前 refpose v3 主线使用四输入 78 骨 SOMA 契约，导出入口见 `benchmark/manifest/export_onnx.py`。`evaluate_unreal_streaming` 在 CPU 数值路径上对齐相同源时间区间，评估末帧与延迟融合播放的姿态和连续性；它不测 UE GPU 性能。G1 演示仍需安装 `.[demo]` 并准备对应权重。

2026-09-12 已在当前 `.venv` 补齐 MuJoCo 与 G1 权重，并通过实际生成、渲染和中文窗口验收，见 [G1 演示验收](../docs/g1-demo-acceptance.md)。

24 帧候选使用 `export_unreal_animgraph --window_frames 24 --pose-condition endpoints`，仅以历史窗口首尾已知姿态作软条件。导出仍默认 16 帧与 `none`。`compare_unreal_streaming` 对两个导出包使用相同源时间区间评估，并单独记录过短片段；残差加权融合仅用于离线消融。候选实验报告随旧 `unreal-script/AIAnimation/` 插件移除，需要时从 git 历史取回。

## 跑酷参考姿态实验

本地交互入口：`./inference/profiling/Start-PoseReferenceLab.ps1`。服务只监听本机，HTML 从服务调用实际模型；手选帧保存在浏览器。支持历史24 / 已知代理48、软参考 / 融合后平滑硬约束、逐参考位置与旋转误差、JSON 导出。硬约束属于离线局部变换投影，不是新训练权重或因果运行时能力。

- [操作、硬约束算法及实测](../docs/experiments/2026-09-23-pose-reference-lab.md)
- [阶段候选与条件遵守审计](../docs/experiments/2026-09-23-phase-reference-results.md)
- [已知代理动画与手选参考](../docs/experiments/2026-09-23-proxy-reference-results.md)
- 具体问题姿态与短片对照：原记录位于已移除的 `unreal-script/AIAnimation/docs/targeted-pose-reference-evaluation.md`，需要时从 git 历史取回（`git show e7330af:unreal-script/AIAnimation/docs/targeted-pose-reference-evaluation.md`）
- [本轮审查修复与验证](../docs/experiments/2026-09-23-pose-reference-review.md)

自动选择脚本用于历史消融及诊断，页面手选不会调用自动选择器。GPU 批量实验脚本需要 CUDA；交互实验室和硬约束对照支持 CUDA 或 CPU。检查点和准备数据均需本地提供。
