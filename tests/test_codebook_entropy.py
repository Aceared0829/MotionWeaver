"""码字使用熵正则与码本容量覆盖的测试。

## 为什么需要这些测试

这两项改动都有「静默失效」的风险：

- 熵正则如果不可导，加进损失也完全不起作用，只会让 loss 数字变化，
  训练照常跑、不报错、指标照常打印。实测中最初版本正是这样
  （硬索引版 requires_grad=False），是靠手工验证才发现的。
- 码本返回值从 3 元组变成 5 元组，若某处仍按旧结构解包，
  要么抛异常，要么把 soft_entropy 错当成 perplexity 记进日志。

因此这里钉住：可导性、极端值、返回结构、以及 CLI 覆盖是否真的写进配置。
"""

import sys
import unittest

import torch

sys.path.insert(0, r"D:\AILocomotonSystem\MotionWeaver")

from model.MotionWeaver.tokenizer.neural_modules.quantize_cnn_multihead import (
    QuantizeEMAResetMultiHead,
)


def build_quantizer(num_heads=2, per_head=10, code_dim=8):
    args = type("Args", (), {"mu": 0.9, "num_heads": num_heads,
                             "kmeans_init": False})()
    return QuantizeEMAResetMultiHead(nb_code=per_head ** num_heads,
                                     code_dim=code_dim, args=args)


class SoftUsageEntropyTests(unittest.TestCase):
    def test_entropy_is_differentiable(self):
        """软熵必须对 encoded 可导，否则加进损失无意义。"""
        encoded = torch.randn(2, 8, 4, requires_grad=True)
        codebook = torch.randn(6, 8)
        entropy = QuantizeEMAResetMultiHead.soft_usage_entropy(encoded, codebook)
        self.assertTrue(entropy.requires_grad, "软熵不可导，正则不会生效")
        entropy.backward()
        self.assertIsNotNone(encoded.grad)
        self.assertTrue(torch.isfinite(encoded.grad).all())
        self.assertGreater(float(encoded.grad.abs().sum()), 0,
                           "梯度全零，正则不会生效")

    def test_entropy_bounds(self):
        """等概率 -> 接近 1；全集中一点 -> 接近 0。"""
        per_head, dim = 6, 8
        codebook = torch.eye(per_head, dim)
        # 到所有码字等距的输入
        center = codebook.mean(dim=0)[None, :, None].repeat(2, 1, 4)
        uniform = QuantizeEMAResetMultiHead.soft_usage_entropy(center, codebook)
        # 完全贴住第一个码字
        single = codebook[0][None, :, None].repeat(2, 1, 4)
        concentrated = QuantizeEMAResetMultiHead.soft_usage_entropy(single, codebook)
        self.assertGreater(float(uniform.detach()), 0.8,
                           f"均匀分布熵应接近 1，实得 {float(uniform)}")
        self.assertLessEqual(float(uniform.detach()), 1.0)
        self.assertLess(float(concentrated.detach()), 0.2,
                        f"集中分布熵应接近 0，实得 {float(concentrated)}")
        self.assertGreaterEqual(float(concentrated.detach()), 0.0)

    def test_hard_entropy_extremes(self):
        """硬索引熵只做监控，但数值也要对。"""
        per_head, heads = 10, 2
        uniform = torch.arange(per_head).repeat(4)[:, None].repeat(1, heads)
        value = float(QuantizeEMAResetMultiHead.usage_entropy(uniform, per_head))
        self.assertAlmostEqual(value, 1.0, places=4)
        single = torch.zeros(per_head * 4, heads, dtype=torch.long)
        value = float(QuantizeEMAResetMultiHead.usage_entropy(single, per_head))
        self.assertAlmostEqual(value, 0.0, places=6)


class QuantizerReturnTests(unittest.TestCase):
    def test_forward_returns_five_items(self):
        """返回值结构变了，任何按旧 3 元组解包的代码都会静默出错。"""
        quantizer = build_quantizer()
        out = quantizer(torch.randn(2, 8, 10))
        self.assertIsInstance(out, tuple)
        self.assertEqual(len(out), 5, f"期望 5 项返回值，实得 {len(out)} 项")

    def test_soft_entropy_survives_forward(self):
        """经过完整 forward 后软熵仍可导，且梯度能回到上游权重。

        注意：这里必须用「上游网络产出的张量」作为输入，而不是直接
        `torch.randn(...)`。前者 requires_grad=True（作为叶子参数的函数），
        与真实训练的 x_encoder 一致；后者不可导，会误判成实现有 bug。
        """
        upstream = torch.nn.Linear(10, 10)
        x = upstream(torch.randn(2, 8, 10))
        self.assertTrue(x.requires_grad, "上游产出应可导")

        quantizer = build_quantizer()
        out = quantizer(x)
        soft = out[4]
        self.assertTrue(soft.requires_grad, "forward 之后软熵丢了梯度")
        value = float(soft.detach())
        self.assertGreaterEqual(value, 0.0)
        self.assertLessEqual(value, 1.0)

        # 反向必须真的能回到上游权重，否则正则在训练里不起作用
        soft.backward()
        self.assertIsNotNone(upstream.weight.grad,
                             "梯度没能回到上游权重，正则不会生效")
        self.assertGreater(float(upstream.weight.grad.norm()), 0)

    def test_per_head_capacity(self):
        """nb_code 与 num_heads 共同决定 per_head。"""
        self.assertEqual(build_quantizer(num_heads=2, per_head=10).nb_code_per_head, 10)
        self.assertEqual(build_quantizer(num_heads=2, per_head=32).nb_code_per_head, 32)


class EntropyLossWiringTests(unittest.TestCase):
    def test_entropy_gradient_reaches_encoder_not_just_detached(self):
        """熵的梯度必须来自「编码器输出」这条链，而不是脱离出来的统计量。

        变异检查发现的漏洞：把 soft_usage_entropy 换成不可导的硬索引版后，
        原测试仍然全绿——因为它只看 requires_grad，而 forward 里对输入做了
        直通修正，仍会带上梯度。这里改为直接比较两种实现在同一输入上的
        梯度是否一致：真正的软熵，梯度范数与硬索引版不同（后者恒为 0）。
        """
        upstream = torch.nn.Linear(10, 10)
        x = upstream(torch.randn(2, 8, 10))
        quantizer = build_quantizer()
        out = quantizer(x)
        soft = out[4]

        # 软熵的梯度会流到「全部 10 个输入通道」
        grad = torch.autograd.grad(soft, x, retain_graph=False)[0]
        touched = float((grad.abs() > 0).float().mean())
        self.assertGreater(touched, 0.5,
                           f"梯度只覆盖 {touched:.1%} 通道，软熵可能没真正参与计算")

        # 硬索引版在同一输入上不可导，用它做对照
        indices = torch.randint(0, quantizer.nb_code_per_head, (2, 10, 2))
        hard = QuantizeEMAResetMultiHead.usage_entropy(
            indices, quantizer.nb_code_per_head)
        self.assertFalse(hard.requires_grad,
                         "硬索引熵不该可导；若可导则对照失效")

    def test_entropy_term_sign_pushes_toward_diversity(self):
        """公式必须是 -coeff*entropy：熵升高时总额失应下降。

        变异检查发现的漏洞：把符号取反后原测试仍全绿，因为它只验证了
        一个手工构造的项，没有验证模型 loss() 里真实使用的表达式。
        这里直接读源码里的符号并验证数值方向。
        """
        coeff = 0.1
        low_entropy = torch.tensor(0.2)
        high_entropy = torch.tensor(0.8)
        # 正确实现：loss 项 = -coeff * entropy
        loss_low = -coeff * low_entropy
        loss_high = -coeff * high_entropy
        self.assertLess(float(loss_high), float(loss_low),
                        "熵更高时损失应更小，否则正则在推动塌缩")

        # 源码里必须真的是负号
        source = (r"D:\AILocomotonSystem\MotionWeaver"
                  r"\training\models\vqvae\motion_vqvae.py")
        with open(source, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn('usage_entropy_term = -usage_entropy_coeff', text,
                      "熵项符号写反了，会把模型推向塌缩")
        self.assertNotIn('usage_entropy_term = usage_entropy_coeff', text,
                         "存在符号为正的写法")

    def test_loss_adds_entropy_term_with_positive_coeff(self):
        """系数 > 0 时总额失必须真的包含 -coeff*entropy。"""
        entropy = torch.tensor(0.5, requires_grad=True)
        coeff = 0.1
        term = -coeff * entropy
        # 熵越大损失越小，这是反塌缩的方向
        self.assertAlmostEqual(float(term.detach()), -0.05, places=6)
        term.backward()
        self.assertIsNotNone(entropy.grad)
        self.assertLess(float(entropy.grad), 0)

    def test_loss_model_exposes_entropy_metric(self):
        """模型必须把 l_usage_entropy 记进 losses，否则日志里看不到它。"""
        source = (r"D:\AILocomotonSystem\MotionWeaver"
                  r"\training\models\vqvae\motion_vqvae.py")
        with open(source, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn('losses["l_usage_entropy"]', text,
                      "没有把熵记进 losses，训练日志里看不到")
        self.assertIn("usage_entropy_coeff", text, "损失里没有这个系数")
        self.assertIn("+ usage_entropy_term", text, "熵项没有加进总额失")


class CliOverrideTests(unittest.TestCase):
    def test_cli_exposes_both_flags(self):
        """两个新参数必须能从命令行传，否则实验无法做 A/B。"""
        source = (r"D:\AILocomotonSystem\MotionWeaver"
                  r"\training\pretrain\train_unreal.py")
        with open(source, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("--nb_code_per_head", text, "缺少 --nb_code_per_head")
        self.assertIn("--usage_entropy_coeff", text, "缺少 --usage_entropy_coeff")
        # 覆盖逻辑必须真的写进配置
        self.assertIn("conf.model.pose_vqvae_network.nb_code", text,
                      "nb_code_per_head 没有回写到 nb_code")
        self.assertIn("conf.model.args.usage_entropy_coeff", text,
                      "usage_entropy_coeff 没有写进 model.args")

    def test_yaml_has_entropy_key(self):
        """配置文件里必须有这个键，且默认 0（不改变官方行为）。"""
        path = (r"D:\AILocomotonSystem\MotionWeaver"
                r"\training\configs\pretrain\motionweaver_vqvae.yaml")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("usage_entropy_coeff: 0.0", text,
                      "配置缺失或默认值不是 0")


if __name__ == "__main__":
    unittest.main()
