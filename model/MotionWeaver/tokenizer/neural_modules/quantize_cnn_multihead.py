# NOTE: taken from MotionGPT code base
import numpy as np
import torch
import torch.nn.functional as F
from torch import nn
from vector_quantize_pytorch import VectorQuantize


class QuantizeEMAResetMultiHead(nn.Module):
    def __init__(self, nb_code: int, code_dim: int, args):
        super().__init__()
        self.nb_code = nb_code
        self.code_dim = code_dim
        self.mu = args.mu                       # the decay for ema reset
        self.num_heads = args.num_heads
        assert self.code_dim % self.num_heads == 0, "code dim cannot be divided by the number of heads."
        self.nb_code_per_head = round(2 ** (np.log2(self.nb_code) / self.num_heads))
        assert self.nb_code_per_head ** self.num_heads == self.nb_code, \
            "the specified number of code is not compatible with the number of heads."
        self.vq = VectorQuantize(
            dim=self.code_dim,
            codebook_dim=self.code_dim // self.num_heads,       # smaller codebook dimension is acceptable
            heads=self.num_heads,             # number of heads to vector quantize, codebook shared across all heads
            separate_codebook_per_head=True,  # whether to have a separate codebook per head.
            codebook_size=self.nb_code_per_head,
            accept_image_fmap=False,
            # 0 = 关闭死码重置（官方默认值）。
            # 原为 1，配合 decay=0.99 会形成死循环：码字被选用一次后，
            # 计数器在 EMA 衰减下迅速回落到 1 以下而触发重置，新中心又因
            # decay 接近 1 几乎不动，于是码字始终挤在中心附近、留不住。
            # 实测后果：编码器输出方差 2.285、量化后只剩 0.029（1.3%），
            # perplexity 5.64/10，commit 损失 0.0050，关节 RMSE 达 105 cm。
            # 详见 docs/soma-tokenizer-codebook-collapse.md。
            #
            # 注意：上面这组数字是**修复前**的测量值，只用来定位死码重置的副作用，
            # 不代表当前水平。归一化口径对齐后的全库训练是 84-86 cm
            # （docs/soma-tokenizer-joint-error-options.md 第零节）。
            # 而 perplexity 仍在 20k 步后塌缩到 1.000，是当前的主要瓶颈。
            threshold_ema_dead_code=0,
            decay=self.mu,
            kmeans_init=bool(args.kmeans_init)
        )  # commitment loss coeff = 1.0 since we do that weighting outside  #
        self.init = True
        self._calculate_per_head_perplexity = getattr(args, "calculate_per_head_perplexity", False)

    @torch.no_grad()
    def compute_perplexity(self, code_idx, nb_code=None):
        # Calculate new centres
        code_onehot = torch.zeros(self.nb_code if nb_code is None else nb_code,
                                  code_idx.shape[0], device=code_idx.device)  # nb_code, N * L
        code_onehot.scatter_(0, code_idx.view(1, code_idx.shape[0]), 1)

        code_count = code_onehot.sum(dim=-1)  # nb_code
        prob = code_count / torch.sum(code_count)
        perplexity = torch.exp(-torch.sum(prob * torch.log(prob + 1e-7)))
        return perplexity

    @torch.no_grad()
    def compute_perplexity_per_head(self, code_idx):
        # Calculate new centres
        perplexity = 0.0
        for i in range(self.num_heads):
            perplexity += self.compute_perplexity(code_idx[:, :, i].view(-1), nb_code=self.nb_code_per_head)
        return perplexity / self.num_heads

    def from_mh_indices_to_overall_indices(self, mh_indices):
        exponential_mul = \
            self.nb_code_per_head ** torch.arange(0, self.num_heads)[None, None, :].to(mh_indices.device)
        overall_indices = (exponential_mul * mh_indices).sum(dim=-1)
        return overall_indices

    def from_overall_indices_to_mh_indices(self, overall_indices):
        exponential_mul = \
            self.nb_code_per_head ** torch.arange(0, self.num_heads)[None, None, :].to(overall_indices.device)

        mh_indices = (overall_indices[:, :, None] % (exponential_mul * self.nb_code_per_head)) // exponential_mul
        return mh_indices

    def dequantize(self, code_idx, use_overall_indices = True):
        if use_overall_indices:
            mh_indices = self.from_overall_indices_to_mh_indices(code_idx)
        else:
            mh_indices = code_idx  # [batch, numTokens, numHeads]
        batch_size, numToken = code_idx.shape[0], code_idx.shape[1]
        x = self.vq.get_codes_from_indices(mh_indices).view([batch_size, numToken, -1])
        return x

    def forward(self, x):
        N, _width, T = x.shape
        # expected the shape of the input to vq: (1, 1024, 256) --> batch, Timesteps, feat_dim
        # The input to x is batch, width(feat_dim), T
        x = x.permute(0, 2, 1).contiguous()

        # quantize and dequantize through bottleneck
        x_d, mh_indices, commit_loss = self.vq(x)

        # Update embeddings
        if self._calculate_per_head_perplexity:
            if self.num_heads == 1:
                mh_indices = mh_indices[:, :, None]
            perplexity = self.compute_perplexity_per_head(mh_indices)
        else:
            overall_indices = self.from_mh_indices_to_overall_indices(mh_indices)
            perplexity = self.compute_perplexity(overall_indices.view([-1]))

        # Loss
        commit_loss = F.mse_loss(x, x_d.detach())

        # Passthrough
        x_d = x + (x_d - x).detach()

        # Postprocess
        x_d = x_d.view(N, T, -1).permute(0, 2, 1).contiguous()   #(N, DIM, T)

        # 软使用熵：基于「编码器到各码字的距离」的软分配，**可导**。
        # 硬索引版 usage_entropy 只做监控；这一项才是用来反塌缩的正则。
        # 注意：这里传入的是 quantizer 的输入 x（未经过 x + (x_d-x).detach 的
        # 直通修正），因此梯度能真正回到编码器。
        #
        # 形状对齐：
        #   x 此时是 [B, T, D_total]（quantize 前 permute 过），
        #   soft_usage_entropy 期望 [B, D, T]，因此再 permute 回来；
        #   codebook 是 [heads, K, D]，每个 head 的 D = D_total/heads。
        x_bt = x.permute(0, 2, 1).contiguous()       # [B, D_total, T]
        head_dim = self.code_dim // self.num_heads
        soft_entropy = 0.0
        for head in range(self.num_heads):
            part = x_bt[:, head * head_dim:(head + 1) * head_dim, :]
            soft_entropy = soft_entropy + self.soft_usage_entropy(
                part, self.vq.codebook[head])
        soft_entropy = soft_entropy / self.num_heads

        return x_d, commit_loss, perplexity, mh_indices, soft_entropy

    @staticmethod
    def usage_entropy(mh_indices, nb_code_per_head):
        """码字使用分布的归一化熵（**不可导**，仅用于监控）。

        ## 为什么这个版本只做监控

        它基于 `torch.bincount` 统计离散索引，对输入没有梯度，
        因此把它乘以系数加进损失**不会推动编码器学出多样性**。

        早期版本误把它当成正则项接进损失，验证时发现
        `entropy.requires_grad == False` 才暴露。真正的正则是下面的
        `soft_usage_entropy()`，它用连续距离构造概率分布，梯度可回传。

        ## 定义

        对每个 head 统计码字频率 p，算 H(p) = -Σ p log p，
        除以 log(nb_code_per_head) 归一化到 [0,1]，再对 head 取平均。
        1 = 完全均匀，0 = 全用同一码字。
        """
        # mh_indices: [B, T, num_heads]（或 [B, T]，单 head 已被扩维）
        if mh_indices.dim() == 2:
            mh_indices = mh_indices[:, :, None]
        heads = mh_indices.shape[-1]
        total = 0.0
        for head in range(heads):
            index = mh_indices[..., head].reshape(-1).long()
            counts = torch.bincount(index, minlength=nb_code_per_head).float()
            prob = counts / counts.sum().clamp(min=1.0)
            # 0 * log 0 记为 0，用 where 避免 NaN
            log_prob = torch.where(prob > 0, torch.log(prob), torch.zeros_like(prob))
            entropy = -(prob * log_prob).sum()
            normalizer = torch.log(
                torch.tensor(float(nb_code_per_head),
                             device=entropy.device, dtype=entropy.dtype))
            total = total + (entropy / normalizer if float(normalizer) > 0 else entropy)
        return total / max(heads, 1)

    @staticmethod
    def soft_usage_entropy(encoded, codebook, temperature=0.1):
        """可导的码字使用熵：用编码器到各码字的软分配概率算熵。

        ## 与 usage_entropy 的区别

        `usage_entropy` 统计硬索引（bincount），不可导，只能监控。
        本函数用 `-distance/temperature` 的 softmax 作为「该 token 会选哪个
        码字」的软概率，对 `encoded` 完全可导，因此把
        `-coeff * soft_entropy` 加进损失**能真正把编码器推向多样性**。

        ## 参数

            encoded : [B, D, T] 编码器输出（量化器输入）
            codebook: [K, D] 某个 head 的码字表
            temperature : softmax 温度；越小越接近硬分配。
                0.1 是相对 codebook 尺度（归一化后量级约 1）的经验值。

        ## 返回

        [0,1] 的标量：1 = 所有码字被等概率使用，0 = 全部塌缩到一个码字。

        ## 温度为什么重要

        temperature 过大 -> 软分配接近均匀 -> 熵恒为 1，梯度消失（无信号）；
        temperature 过小 -> 软分配接近硬 one-hot -> 熵恒为 0，梯度也消失。
        0.1 让分布在「少数码字占优但未完全独占」的区域，梯度最有效。
        """
        # encoded: [B, D, T] -> [B*T, D]
        flat = encoded.permute(0, 2, 1).reshape(-1, encoded.shape[1])
        # 距离 [B*T, K]
        distance = torch.cdist(flat, codebook)
        # 软分配概率 [B*T, K]
        prob = torch.softmax(-distance / max(temperature, 1e-6), dim=-1)
        # 该 head 的平均使用分布 [K]
        mean_prob = prob.mean(dim=0)
        log_prob = torch.log(mean_prob.clamp(min=1e-8))
        entropy = -(mean_prob * log_prob).sum()
        normalizer = torch.log(
            torch.tensor(float(codebook.shape[0]),
                         device=entropy.device, dtype=entropy.dtype))
        if float(normalizer) <= 0:
            return entropy
        return entropy / normalizer


    def forward_into_idx(self, x, fetch_overall_indices: bool = True):
        _N, _width, _T = x.shape

        # expected the shape of the input: (1, 1024, 256) --> batch, T, width
        # The input to x is batch, width, T
        x = x.permute(0, 2, 1).contiguous()

        # quantize and dequantize through bottleneck
        _, mh_indices, _ = self.vq(x)
        overall_indices = self.from_mh_indices_to_overall_indices(mh_indices)

        if fetch_overall_indices:
            return overall_indices
        else:
            return mh_indices

class QuantizeEMAResetLFQ(nn.Module):
    pass
