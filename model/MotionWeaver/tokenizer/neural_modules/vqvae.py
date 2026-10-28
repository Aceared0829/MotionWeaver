from types import SimpleNamespace

import numpy as np
import torch as t
from torch import nn

from motionweaver.representation.helper.data_training_util import (
    convert_sparse_cond_to_dense_cond_if_needed,
    extract_feature_from_motion_rep,
)
from motionweaver.representation.core.motion_reps import MotionRepBase
from motionweaver.tokenizer.neural_modules.encdec import Encoder
from motionweaver.tokenizer.neural_modules.encdec_double_cond import DoubleCondDecoder
from motionweaver.tokenizer.neural_modules.quantize_cnn_multihead import (
    QuantizeEMAResetMultiHead,
)


class VQVAE(nn.Module):
    ALLOWED_FEATURE_MODE = [
        'invalid', "pose",
        "root", "root_without_hip_height", "root_without_hip_height_without_heading", "root_without_heading",
        "root_without_hip_height_without_heading_with_mask",
        "joint_positions_and_rotations",
        "joint_positions_and_rotations_and_foot_contact", "joint_positions_and_rotations_and_hip_height"
    ]
    def __init__(self,
                 pose_root_mode: str,
                 motion_rep: MotionRepBase,

                 # dim information
                 encoder_state_dim: int,
                 decoder_state_dim: int,

                 decoder_target_cond_dim: int,
                 decoder_external_cond_dim: int,
                 feature_mode: list,

                 # network config
                 quantizer_strategy: str = 'multihead_ema_reset',
                 quantizer_mu: float = 0.99,
                 nb_code: int = 512,
                 code_dim: int = 512,
                 output_emb_width: int = 512,
                 down_t: int = 2,
                 stride_t: int = 2,
                 width: int = 512,
                 depth: int = 3,
                 dilation_growth_rate: int = 3,
                 activation: str = 'relu',
                 num_heads: int = 4,
                 kmeans_init: bool = True,
                 norm: str | None = None,
                 calculate_per_head_perplexity: bool = True,
                 **kwargs):
        """ @brief:
            Both the root vqvae and pose vqvae use a similar structure where
            1) the encoder is a unconditional x -> z
            2) the decoder takes z, c-> x
                The c is the condition vector. For root vqvae, it's the target condition (boundary root values).
                And for pose vqvae, it's target condition (boundary pose values) as well as external
                condition (root values).
        """

        super().__init__()
        self.code_dim = code_dim
        self.num_code = nb_code
        self._num_heads = num_heads
        self._motion_rep = motion_rep
        self._pose_root_mode = pose_root_mode
        self._down_t = down_t
        self._calculate_per_head_perplexity = calculate_per_head_perplexity

        assert self._pose_root_mode in ['pose', 'root'], "Only support either provide local pose rep or root rep."
        assert len(feature_mode) == 4 and np.all([f in self.ALLOWED_FEATURE_MODE for f in feature_mode])
        self.encoder_input_feature_mode, self.decoder_input_feature_mode, \
            self.decoder_target_cond_feature_mode, self.decoder_external_cond_feature_mode = feature_mode

        dummy_input = t.zeros([1, 1, len(motion_rep.indices['all'])])
        encoder_state_dim = extract_feature_from_motion_rep(dummy_input, motion_rep,
                                                           self.encoder_input_feature_mode).shape[-1]
        decoder_state_dim = extract_feature_from_motion_rep(dummy_input, motion_rep,
                                                           self.decoder_input_feature_mode).shape[-1]
        decoder_target_cond_dim = extract_feature_from_motion_rep(dummy_input, motion_rep,
                                                                 self.decoder_target_cond_feature_mode).shape[-1]
        decoder_external_cond_dim = extract_feature_from_motion_rep(dummy_input, motion_rep,
                                                                   self.decoder_external_cond_feature_mode).shape[-1]

        self.encoder = Encoder(encoder_state_dim, output_emb_width,
                               down_t, stride_t, width, depth,
                               dilation_growth_rate, activation=activation, norm=norm)
        self.decoder = DoubleCondDecoder(decoder_state_dim, output_emb_width,
                                         down_t, width, depth, dilation_growth_rate,
                                         activation=activation, norm=norm,
                                         target_cond_dim=decoder_target_cond_dim,
                                         external_cond_dim=decoder_external_cond_dim,
                                         cond_fusion_last_layer=kwargs.get('cond_fusion_last_layer', False))

        self.quant_strategy = quantizer_strategy
        quant_args = SimpleNamespace(mu=quantizer_mu, num_heads=num_heads, kmeans_init=kmeans_init,
                                     calculate_per_head_perplexity=calculate_per_head_perplexity)
        if quantizer_strategy == "multihead_ema_reset":
            self.quantizer = QuantizeEMAResetMultiHead(nb_code, code_dim, quant_args)
        else:
            assert False, "Invalid quantizer strategy for training."

    def extract_feature(self, x: t.Tensor, feature: str = ""):
        """ @brief: extract the root / localPose / boundary features from the original full 353 feature
        """
        feature = self._pose_root_mode if feature == "" else feature
        return extract_feature_from_motion_rep(x, self._motion_rep, feature)

    def forward(self, x, target_cond: t.Tensor, has_target_cond: t.Tensor | None = None, external_cond: t.Tensor | None = None):
        """ @brief: full encoder decoder path that goes from x to z to x
        @params x: [batch_size, numFrames, feat_dim]
        @params target_cond: [batch_size, numFrames, feat_dim]
        @params has_target_cond: [batch_size, numFrames]
        @params external_cond: [batch_size, numFrames, feat_dim]

        @returns x_out: [batch_size, numFrames, feat_dim]
        """
        num_expected_frames = x.shape[1]
        # encoder
        x_in = self.extract_feature(x, self.encoder_input_feature_mode).permute(0, 2, 1)  # from [B,T,F] to [B,F,T]
        x_encoder = self.encoder(x_in)

        # quantization
        # mh_indices 是量化索引；软熵已在 quantizer 内部用同一个 x 算好，
        # 这里不参与损失，因此用下划线前缀表明有意不使用。
        x_quantized, loss, perplexity, _mh_indices, soft_entropy = self.quantizer(x_encoder)

        # 码字使用熵（**可导**）：作为正则在模型层加入损失，避免码本塌缩。
        # 详细动机见 QuantizeEMAResetMultiHead.soft_usage_entropy 的文档字符串。
        # 与硬索引熵不同，这里梯度能传回编码器，因此 -coeff*entropy 真正有效。
        usage_entropy = soft_entropy

        # decoder
        if target_cond is not None:
            assert has_target_cond is not None
            target_cond, has_target_cond = \
                convert_sparse_cond_to_dense_cond_if_needed(target_cond, has_target_cond, num_expected_frames)
            target_cond = self.extract_feature(target_cond, self.decoder_target_cond_feature_mode)
        x_decoder = self.decoder(x_quantized, external_cond, target_cond, has_target_cond)

        x_out = x_decoder.permute(0, 2, 1)
        return {'recon_state': x_out, 'l_commit': loss,
                'perplexity': perplexity, 'usage_entropy': usage_entropy}

    def encode_into_idx(self, x, fetch_overall_indices: bool = True):
        """ @brief: encoder part of the @forward function
        @param x: the frame features.
        """
        assert self.quant_strategy == "multihead_ema_reset"
        x_in = self.extract_feature(x, self.encoder_input_feature_mode).permute(0, 2, 1)  # from [B,T,F] to [B,F,T]

        x_encoder = self.encoder(x_in)
        code_idx = self.quantizer.forward_into_idx(x_encoder, fetch_overall_indices=fetch_overall_indices)
        return code_idx

    def forward_decoder(self, x,
                        target_cond: t.Tensor, has_target_cond: t.Tensor | None = None, external_cond: t.Tensor | None = None,
                        use_overall_indices: bool = True, token_mask: t.Tensor | None = None):
        """ @brief: decoder part of the @forward function
        @param x: the code indices.
        """
        assert self.quant_strategy == "multihead_ema_reset"
        num_expected_frames = x.shape[1] * (2 ** self._down_t)
        x_d = self.quantizer.dequantize(x, use_overall_indices=use_overall_indices)
        x_quantized = x_d.permute(0, 2, 1).contiguous()  # [batch, feat_dim, T]

        # decoder
        if target_cond is not None:
            assert has_target_cond is not None
            target_cond, has_target_cond = \
                convert_sparse_cond_to_dense_cond_if_needed(target_cond, has_target_cond, num_expected_frames)
            target_cond = self.extract_feature(target_cond, self.decoder_target_cond_feature_mode)
        x_decoder = self.decoder(x_quantized, external_cond, target_cond, has_target_cond, token_mask=token_mask)

        x_out = x_decoder.permute(0, 2, 1)
        return {'recon_state': x_out}

    def get_codebook(self):
        if self.quant_strategy == "ema_reset":
            raise NotImplementedError("ema_reset quantization is not supported by this VQVAE")
        elif self.quant_strategy == "multihead_ema_reset":
            return self.quantizer.vq.codebook.clone()
        else:
            raise NotImplementedError

    @property
    def pose_root_mode(self):
        return self._pose_root_mode

    @property
    def motion_rep(self):
        return self._motion_rep
