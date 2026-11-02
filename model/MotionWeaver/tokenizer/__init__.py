"""离散姿态码本（VQ-VAE）模块。

状态：**已证否，非主线**。

该路线试图把连续姿态压缩为离散码字，再用 Pose Token 主干做条件生成。
实测到 20,000 步时码本塌缩至 perplexity = 1.000——每批所有 token 量化到同一码字，
分词器退化为自编码器，码字里不再有可优化信息，关节误差卡在 96.5 cm 量级。

本项目因此改走参考引导的直接关节回归（见 tools/train_reference_pose.py），
不经码本直接回归关节，全库 20 epoch 后验证误差 3.654 cm。

本模块保留的原因：
- benchmark/training_metrics.py 与 inference 链路仍引用其中的表示与度量
- 作为并列对照路线，用于报告中的路线比较

新增功能不应再依赖本模块。完整结论见 docs/refpose-v3-training-evaluation.md。
"""
