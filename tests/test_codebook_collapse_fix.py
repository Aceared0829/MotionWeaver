"""码本塌缩修复的测试。

## 为什么必须测这个

`perplexity_pose` 从 20k 步起锁死 1.000 不是「训练不充分」，而是码本死锁：

1. `kmeans_init=False` 时码字范数约 0.25，而编码器输出范数约 35（差 141 倍），
   `argmin` 的选择基本由浮点噪声决定，命中随机。
2. `update_ema()` 的 Laplace 平滑会给「从未命中」的码字一个非零分母配零分子，
   于是 `embed` 每步被抹成**精确的零向量**。
3. 零向量离原点最近，反而成为所有输入的最近邻 —— 正反馈死锁。

实测 `fix_base_100k`：256 个码字里 248 个范数为精确 0，每 head 只用 1 个。

## 这些测试守什么

- 源码级：`threshold_ema_dead_code` 必须是**可配置**的，不能被重新写死。
- 数值级：kmeans 初始化后，码字与输入必须同尺度，距离必须可区分。
- 回归级：默认值仍是 0（不改变既有行为），避免悄悄改变所有历史 run 的语义。

详见 docs/soma-tokenizer-codebook-collapse-diagnosis.md。
"""

import sys
import unittest

import torch

sys.path.insert(0, r"D:\AILocomotonSystem\MotionWeaver")

QUANTIZER = (r"D:\AILocomotonSystem\MotionWeaver"
             r"\model\MotionWeaver\tokenizer\neural_modules"
             r"\quantize_cnn_multihead.py")
CLI = (r"D:\AILocomotonSystem\MotionWeaver"
       r"\training\pretrain\train_unreal.py")
YAML = (r"D:\AILocomotonSystem\MotionWeaver"
        r"\training\configs\pretrain\motionweaver_vqvae.yaml")

# 实测值（fix_base_100k 的编码器输出）
OBSERVED_ENCODER_NORM = 35.1
# 判别能力的下限：随机初始化实测 0.00109，kmeans 实测 0.5474
# 取几何中点 0.02，既能放过真实通过，又能在退化时报警
MIN_RELATIVE_SPREAD = 0.02


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class DeadCodeThresholdIsConfigurableTests(unittest.TestCase):
    def test_threshold_reads_from_args(self):
        """阈值必须从 args 读，不能写死；写死就失去了修复的手段。

        只在**代码行**里找硬编码，跳过注释——注释里出现该字样是正常的
        （文档会解释历史为什么是 0）。
        """
        text = read(QUANTIZER)
        self.assertIn('getattr(args, "threshold_ema_dead_code"', text,
                      "threshold_ema_dead_code 被写死了；修复码本死锁需要能调到 >=1")
        # 逐行扫描：只看非注释行，且排除多行表达式的起始行（以 int( 或 bool( 结尾）
        code_hardcoded = []
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if line.strip().startswith("#"):
                continue
            if "threshold_ema_dead_code=" not in line:
                continue
            rhs = line.split("threshold_ema_dead_code=", 1)[1].strip()
            # 允许的三种形态：getattr 直接出现，或 multiline 的 int(/bool( 开头
            if 'getattr(args, "threshold_ema_dead_code"' in rhs:
                continue
            if rhs.endswith(("int(", "bool(", "float(")):
                # 检查后续两行是否含 getattr
                tail = "".join(lines[i + 1:i + 3])
                if 'getattr(args, "threshold_ema_dead_code"' in tail:
                    continue
            code_hardcoded.append(line.strip())
        self.assertEqual(code_hardcoded, [],
                         f"仍存在硬编码的行内构造参数: {code_hardcoded}")

    def test_threshold_actually_forwarded(self):
        """构造器必须把 args 的值传给 VectorQuantize，而不是传一个常量。"""
        text = read(QUANTIZER)
        # 找 threshold_ema_dead_code= 之后到右括号之间是否为 getattr 表达式
        idx = text.index("threshold_ema_dead_code=")
        segment = text[idx:idx + 200]
        self.assertIn('getattr(args, "threshold_ema_dead_code"', segment,
                      "构造器没有把 args.threshold_ema_dead_code 传给量化器")

    def test_cli_exposes_the_flag(self):
        text = read(CLI)
        self.assertIn('"--threshold_ema_dead_code"', text,
                      "CLI 没有暴露 --threshold_ema_dead_code")
        self.assertIn("conf.model.pose_vqvae_network.threshold_ema_dead_code", text,
                      "CLI 参数没有传递到模型配置")

    def test_default_stays_zero(self):
        """默认必须仍是 0，否则会悄悄改变所有历史 run 的语义。"""
        text = read(YAML)
        self.assertIn("threshold_ema_dead_code: 0", text,
                      "YAML 默认值不是 0，会改变既有训练行为")
        q = read(QUANTIZER)
        self.assertIn('getattr(args, "threshold_ema_dead_code", 0)', q,
                      "构造器的默认值不是 0")


class CodebookInitScaleTests(unittest.TestCase):
    """数值级：kmeans 初始化必须让码字与输入同尺度，使 argmin 可区分。"""

    def _relative_spread(self, embed, x):
        """同一 token 到所有码字距离的相对极差。

        这是判别能力的直接度量：如果码字都挤在原点附近而输入远在天边，
        所有距离几乎相等，argmin 由噪声决定 —— 塌缩的起点。
        """
        d = torch.cdist(x[None, :], embed)
        return float((d.max() - d.min()) / d.mean())

    def test_random_init_is_indistinguishable(self):
        """钉住前提：随机初始化的判别能力确实极差，这解释了塌缩的起点。"""
        from vector_quantize_pytorch import VectorQuantize

        torch.manual_seed(0)
        vq = VectorQuantize(
            dim=256, codebook_dim=32, heads=8, separate_codebook_per_head=True,
            codebook_size=32, accept_image_fmap=False,
            threshold_ema_dead_code=0, decay=0.9, kmeans_init=False)
        embed = vq._codebook.embed  # [heads, K, D_head]

        torch.manual_seed(1)
        x = torch.randn(32) * OBSERVED_ENCODER_NORM

        spread = self._relative_spread(embed[0], x)
        self.assertLess(spread, MIN_RELATIVE_SPREAD,
                        f"随机初始化的相对极差 {spread:.6f} 比预期大；"
                        "若确实变好了，应同步下调本阈值而不是放宽断言")

    def test_kmeans_init_matches_input_scale(self):
        """修复的核心：kmeans 初始化后判别能力必须显著提升。"""
        from vector_quantize_pytorch import VectorQuantize

        torch.manual_seed(0)
        vq = VectorQuantize(
            dim=256, codebook_dim=32, heads=8, separate_codebook_per_head=True,
            codebook_size=32, accept_image_fmap=False,
            threshold_ema_dead_code=0, decay=0.9, kmeans_init=True)

        # kmeans_init=True 时初始 embed 全零，首次 forward 才用数据初始化
        torch.manual_seed(2)
        data = torch.randn(8, 9, 256) * OBSERVED_ENCODER_NORM
        with torch.no_grad():
            vq(data)

        embed = vq._codebook.embed
        torch.manual_seed(1)
        x = torch.randn(32) * OBSERVED_ENCODER_NORM

        spread = self._relative_spread(embed[0], x)
        self.assertGreater(spread, MIN_RELATIVE_SPREAD,
                           f"kmeans 初始化后相对极差只有 {spread:.6f}，"
                           "码字仍未与数据同尺度，修复无效")

    def test_kmeans_init_is_not_zero(self):
        """kmeans 初始化后码本不能留零向量（零向量会变成吸铁石）。"""
        from vector_quantize_pytorch import VectorQuantize

        torch.manual_seed(0)
        vq = VectorQuantize(
            dim=256, codebook_dim=32, heads=8, separate_codebook_per_head=True,
            codebook_size=32, accept_image_fmap=False,
            threshold_ema_dead_code=0, decay=0.9, kmeans_init=True)
        torch.manual_seed(2)
        data = torch.randn(8, 9, 256) * OBSERVED_ENCODER_NORM
        with torch.no_grad():
            vq(data)

        norms = vq._codebook.embed.norm(dim=-1).flatten()
        zeros = int((norms < 1e-6).sum())
        self.assertLess(zeros, 16,
                        f"kmeans 初始化后仍有 {zeros}/256 个零向量码字；"
                        "零向量会成为所有输入的最近邻，导致死锁")


class DeadCodeReplacementTests(unittest.TestCase):
    """死码重置必须真的能把归零的码字拉回来。"""

    def test_threshold_zero_disables_replacement(self):
        from vector_quantize_pytorch import VectorQuantize

        vq = VectorQuantize(
            dim=64, codebook_dim=64, heads=1, separate_codebook_per_head=True,
            codebook_size=32, accept_image_fmap=False,
            threshold_ema_dead_code=0, decay=0.9, kmeans_init=False)
        self.assertFalse(vq._codebook.has_dead_code_replacement,
                         "threshold_ema_dead_code=0 时不应启用死码重置")

    def test_threshold_positive_enables_replacement(self):
        from vector_quantize_pytorch import VectorQuantize

        for t in (1, 2):
            vq = VectorQuantize(
                dim=64, codebook_dim=64, heads=1, separate_codebook_per_head=True,
                codebook_size=32, accept_image_fmap=False,
                threshold_ema_dead_code=t, decay=0.9, kmeans_init=False)
            self.assertTrue(vq._codebook.has_dead_code_replacement,
                            f"threshold_ema_dead_code={t} 应启用死码重置")


if __name__ == "__main__":
    unittest.main(verbosity=2)
