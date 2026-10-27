import logging
from collections.abc import Callable, Iterable

import numpy as np
import torch
import torch as t
from pytorch_lightning import LightningModule

from motionweaver.representation.core.motion_reps import MotionRepBase
from motionweaver.representation.core.motion_reps.dual_root_global_joints import (
    GlobalRootGlobalJoints,
    LocalRootGlobalJoints,
)
from motionweaver.representation.helper.data_training_util import (
    extract_feature_from_motion_rep,
    sample_keyframes,
    sample_motion_segments_from_motion_clips,
)
from motionweaver.tokenizer.neural_modules import vqvae as vqvae_module

log = logging.getLogger(__name__)


class MotionVQVAEModel(LightningModule):
    """VQVAE model for motion generation, wrapped in PyTorch Lightning.

    The pose VQVAE encodes local/global motion representations into discrete
    codes and reconstructs them with optional keyframe conditioning and
    external root-motion conditioning.
    """

    def __init__(
        self,
        pose_vqvae_network: vqvae_module.VQVAE,
        root_vqvae_network: vqvae_module.VQVAE | None,
        motion_rep: MotionRepBase,
        optimizer: Callable[[Iterable[torch.nn.Parameter]], torch.optim.Optimizer],
        scheduler: Callable[[torch.optim.Optimizer], torch.optim.lr_scheduler.LRScheduler] | None,
        device: str | torch.device | None = None,
        args: dict | None = None,
        **kwargs,
    ):
        super().__init__()
        assert isinstance(motion_rep, GlobalRootGlobalJoints)
        assert motion_rep.dual_rep is not None
        dual_rep = motion_rep.dual_rep
        assert isinstance(dual_rep.global_motion_rep, GlobalRootGlobalJoints)
        assert isinstance(dual_rep.local_motion_rep, LocalRootGlobalJoints)

        self.optimizer = optimizer
        self.scheduler = scheduler

        assert (
            dual_rep.local_motion_rep.num_joints
            == dual_rep.global_motion_rep.num_joints
        ), "The number of joints should be the same."
        self.NUM_JOINTS = motion_rep.num_joints

        self.pose_net = pose_vqvae_network
        self.root_net = root_vqvae_network
        self.motion_rep: GlobalRootGlobalJoints = motion_rep
        self.global_motion_rep: GlobalRootGlobalJoints = (
            dual_rep.global_motion_rep
        )
        self.local_motion_rep: LocalRootGlobalJoints = (
            dual_rep.local_motion_rep
        )
        if args is None:
            raise ValueError("Model configuration is required")
        self._args = args

        if device is not None:
            self.pose_net = self.pose_net.to(device)
            self.root_net = (
                self.root_net.to(device) if self.root_net is not None else None
            )

    @property
    def dual_rep(self):
        dual_rep = self.motion_rep.dual_rep
        assert dual_rep is not None
        return dual_rep

    def configure_optimizers(self):
        if self.optimizer is None:
            raise ValueError("An optimizer is required for training")
        optimizer = self.optimizer(self.parameters())
        if not self.scheduler:
            return optimizer

        lt_kwargs = dict(self.scheduler.keywords.pop("lt_kwargs", {}))
        lt_kwargs["scheduler"] = self.scheduler(optimizer)
        return {"optimizer": optimizer, "lr_scheduler": lt_kwargs}

    def training_step(self, batch, batch_idx):
        batch_size, device = batch["batch_size"], batch["motion"].device
        motions, motion_lengths, _ = (
            batch.pop("motion"),
            batch.pop("motion_len"),
            batch.pop("motion_pad_mask"),
        )

        num_codes = np.random.choice(
            np.arange(self._args["min_tokens"], self._args["max_tokens"] + 1)
        )
        num_frames = num_codes * self.get_num_frames_per_code()

        # filter out short samples and truncate
        valid_samples_id = motion_lengths >= num_frames + 1
        num_invalid_samples = batch_size - valid_samples_id.sum()
        if num_invalid_samples > batch_size // 2:
            return None

        motions = sample_motion_segments_from_motion_clips(
            motions,
            motion_lengths,
            num_frames,
            self.args["batchsize_mul_factor"],
            motion_rep=self.global_motion_rep,
        )
        actual_batch_size = int(batch_size * self.args["batchsize_mul_factor"])

        # prepare global & local motion representations
        first_frame_heading_angle = (
            t.rand(actual_batch_size).to(device) * np.pi * 2.0
            if not self.motion_rep.compute_kwargs["removing_heading"]
            else 0.0
        )
        global_motions = self.global_motion_rep.change_first_heading(
            motions,
            first_frame_heading_angle,
            is_normalized=True,
            to_normalize=True,
        )
        local_motions = self.dual_rep.global_to_local(
            global_motions,
            is_normalized=True,
            to_normalize=True,
            lengths=t.full([actual_batch_size], motions.shape[1]).to(device),
        )
        local_motions, global_motions = (
            local_motions[:, :num_frames, :],
            global_motions[:, :num_frames, :],
        )

        # keyframe conditioning
        assert self.pose_net.motion_rep.name in [
            "local",
            "global",
        ], "should be using local or global rep."
        prob_pose_num_keyframes, _ = self._construct_keyframe_prob()
        pose_motions = (
            local_motions
            if self.pose_net.motion_rep.name == "local"
            else global_motions
        )
        pose_has_target_cond, pose_target_cond = sample_keyframes(
            pose_motions,
            self._args["pose_vqvae_max_num_keyframes"],
            prob_pose_num_keyframes,
        )
        pose_external_cond = extract_feature_from_motion_rep(
            pose_motions,
            self.pose_net.motion_rep,
            self.pose_net.decoder_external_cond_feature_mode,
        )

        batch["local_motions"], batch["global_motions"] = local_motions, global_motions
        batch["pose_has_target_cond"] = pose_has_target_cond
        pose_external_cond = self._construct_masked_pose_external_cond_if_needed(
            pose_external_cond, batch
        )

        # network forward
        if self.root_net is not None:
            raise NotImplementedError("Root VQVAE is not implemented.")
        else:
            root_net_output = None

        pose_net_output = self.pose_net(
            batch["local_motions"]
            if self.pose_net.motion_rep.name == "local"
            else batch["global_motions"],
            target_cond=pose_target_cond,
            has_target_cond=pose_has_target_cond,
            external_cond=pose_external_cond,
        )
        losses = self.loss(batch, pose_net_output, root_net_output)

        for key, val in losses.items():
            self.log(
                f"loss/train_{key}",
                val,
                on_step=True,
                on_epoch=True,
                sync_dist=True,
                batch_size=batch["batch_size"],
            )
        return losses["loss"]

    def loss(self, batch, pose_net_output, root_net_output):
        if self.root_net is not None:
            raise NotImplementedError("Root VQVAE is not implemented.")
        else:
            global_root_recons_loss = local_root_recons_loss = 0.0

        # pose reconstruction loss
        if self.pose_net.motion_rep.name == "local":
            local_pose_recons_loss = t.nn.SmoothL1Loss()(
                pose_net_output["recon_state"], batch["local_motions"][:, :, :]
            )
            global_pose_recons_loss = 0.0
            pose_recons_loss = local_pose_recons_loss
        else:
            global_pose_recons_loss = t.nn.SmoothL1Loss()(
                pose_net_output["recon_state"], batch["global_motions"][:, :, :]
            )
            pred_local_motions = self.dual_rep.global_to_local(
                pose_net_output["recon_state"],
                is_normalized=True,
                to_normalize=True,
                lengths=t.full(
                    [pose_net_output["recon_state"].shape[0]],
                    pose_net_output["recon_state"].shape[1],
                ).to(pose_net_output["recon_state"].device),
            )
            pred_local_motions = t.concat(
                [
                    pred_local_motions[:, :-1, :],
                    t.concat(
                        [
                            batch["local_motions"][
                                :, -1:, self.local_motion_rep.indices["root"]
                            ],
                            pred_local_motions[
                                :, -1:, self.local_motion_rep.indices["body"]
                            ],
                        ],
                        dim=-1,
                    ),
                ],
                dim=1,
            )
            local_pose_recons_loss = t.nn.SmoothL1Loss()(
                pred_local_motions, batch["local_motions"]
            )
            pose_recons_loss = (
                self.args["global_root_loss_coeff"] * global_pose_recons_loss
                + self.args["local_root_loss_coeff"] * local_pose_recons_loss
            )

            global_root_recons_loss = t.nn.SmoothL1Loss()(
                pose_net_output["recon_state"][
                    :, :, self.global_motion_rep.indices["root"]
                ],
                batch["global_motions"][:, :, self.global_motion_rep.indices["root"]],
            )
            local_root_recons_loss = t.nn.SmoothL1Loss()(
                pred_local_motions[:, :, self.local_motion_rep.indices["root"]],
                batch["local_motions"][:, :, self.local_motion_rep.indices["root"]],
            )

        # foot contact loss
        if self.pose_net.motion_rep.name == "local":
            pred_joints_output = self.local_motion_rep.inverse(
                pose_net_output["recon_state"],
                is_normalized=True,
                return_quat=True,
                return_all=True,
            )
        else:
            pred_joints_output = self.global_motion_rep.inverse(
                pose_net_output["recon_state"],
                is_normalized=True,
                return_quat=True,
                return_all=True,
            )

        assert "posed_joints" in pred_joints_output
        pred_joints_pos = pred_joints_output["posed_joints"]
        pred_foot_contacts = pred_joints_output.get("foot_contacts")

        fidx = self.motion_rep.skeleton.foot_joint_idx
        feet_pos = pred_joints_pos[:, :, fidx]
        dt = 1.0 / self.motion_rep.fps
        foot_vel = torch.norm(feet_pos[:, 1:] - feet_pos[:, :-1], dim=-1) / dt

        assert pred_foot_contacts is not None
        foot_contacts = pred_foot_contacts[:, :-1]
        vel_err = foot_vel * foot_contacts
        mean_vel = torch.sum(vel_err, (1, 2)) / (
            torch.sum(foot_contacts, (1, 2)) + 1e-6
        )
        mean_vel = mean_vel.mean()

        # joint velocity loss
        joint_vel_loss_coeff = self._args.get("joint_vel_loss_coeff", 0.0)

        # 关节位置损失（方案 A）的系数。0 = 关闭，保持原有行为。
        #
        # 为什么需要它：现有损失全部作用在「归一化后的 941 维特征」上，
        # 与「米制关节误差」不是同一个量。实测该 checkpoint 重建损失 0.36，
        # 但还原成关节后 8 条样本平均 171.71 cm、位置 RMSE 103.51 cm。
        # 其中根速度平均误差 4.42 m/s，积分 64 帧末帧漂移 193.39 cm；
        # ric_data 单独贡献约 116 cm。这两项在 941 维平均里被摊平，
        # 所以加训练步数并不会把它们压下去。
        #
        # 这里直接对「还原后的关节位置」算 SmoothL1（单位：米）。选 SmoothL1
        # 而非 MSE：MSE 在实测中最大单样本达 43.87，会被离群样本主导。
        #
        # 口径已验证：训练路径不传 init_heading_info，评测路径传。实测两者
        # 误差只差 2.1%（171.71 vs 175.34 cm），因为 gt 与 pred 同时少同一步
        # 旋转，是等距变换，不改变逐关节距离。因此这里可以沿用现有调用。
        joint_pos_loss_coeff = self._args.get("joint_pos_loss_coeff", 0.0)

        # 真值关节位置：关节速度损失和关节位置损失都要用。
        # 这里放在 if 外面算一次，避免两个损失各算一遍 inverse（每步省一次前向）。
        gt_joints_pos = None
        need_gt_joints = (joint_vel_loss_coeff > 0.0
                          or joint_pos_loss_coeff > 0.0)
        if need_gt_joints:
            gt_joints_output = self.global_motion_rep.inverse(
                batch["global_motions"],
                is_normalized=True,
                return_quat=False,
                return_all=False,
                joint_positions_from="ric_data",
            )
            assert "posed_joints" in gt_joints_output
            gt_joints_pos = gt_joints_output["posed_joints"]

        if joint_vel_loss_coeff > 0.0:
            batch_size, num_frames = (
                pred_joints_pos.shape[0],
                pred_joints_pos.shape[1],
            )
            pred_joints_vel = (
                (pred_joints_pos[:, 1:] - pred_joints_pos[:, :-1]) / dt
            ).view([batch_size, num_frames - 1, -1])
            gt_joints_vel = (
                (gt_joints_pos[:, 1:] - gt_joints_pos[:, :-1]) / dt
            ).view([batch_size, num_frames - 1, -1])
            joint_vel_indices = self.global_motion_rep.indices["local_vel"]
            assert self.global_motion_rep.stats is not None
            vel_mean = self.global_motion_rep.stats.mean[joint_vel_indices]
            vel_std = self.global_motion_rep.stats.std[joint_vel_indices]

            joint_vel_loss = t.nn.SmoothL1Loss()(
                (pred_joints_vel - vel_mean) / torch.sqrt(vel_std**2 + 1e-5),
                (gt_joints_vel - vel_mean) / torch.sqrt(vel_std**2 + 1e-5),
            )
            joint_vel_loss = (
                joint_vel_loss
                / len(self.local_motion_rep.indices["all"])
                * len(joint_vel_indices)
            )
        else:
            joint_vel_loss = 0.0

        # 关节位置损失：直接罚米制误差，与验收指标同口径。
        #
        # CR 抓到的问题：pose_vqvae_motion_rep=local 时
        #   pred_joints_pos 来自 self.local_motion_rep.inverse（无全局根轨迹）
        #   而上面 gt 来自 self.global_motion_rep.inverse（含全局根轨迹）
        # 两者实测相差 120.50 cm，直接相减是在罚「两个不同坐标系之差」，
        # 梯度方向没有意义。
        #
        # 修法：把 recon（local 归一化特征）先转成 global 归一化特征，再走
        # global 路径还原。实测这条转换可逆：
        #   特征最大绝对差 1.2e-7，关节位置差 1.0e-6 cm。
        if joint_pos_loss_coeff > 0.0:
            assert gt_joints_pos is not None
            if self.pose_net.motion_rep.name == "local":
                # local -> global（都保持归一化状态），再按 global 口径还原
                recon_global = self.dual_rep.local_to_global(
                    pose_net_output["recon_state"],
                    is_normalized=True,
                    to_normalize=True,
                    lengths=t.full(
                        [pose_net_output["recon_state"].shape[0]],
                        pose_net_output["recon_state"].shape[1],
                    ).to(pose_net_output["recon_state"].device),
                )
                pred_global_joints = self.global_motion_rep.inverse(
                    recon_global,
                    is_normalized=True,
                    return_quat=False,
                    return_all=False,
                    joint_positions_from="ric_data",
                )["posed_joints"]
            else:
                pred_global_joints = pred_joints_pos
            joint_pos_loss = t.nn.SmoothL1Loss()(
                pred_global_joints, gt_joints_pos)
        else:
            joint_pos_loss = 0.0

        losses = {}
        losses["perplexity_pose"] = pose_net_output["perplexity"]
        losses["l_commit_pose"] = pose_net_output["l_commit"]
        losses["l_recons_pose"] = pose_recons_loss
        losses["l_recons_root_global"] = global_root_recons_loss
        losses["l_recons_root_local"] = local_root_recons_loss
        losses["l_joint_vel"] = joint_vel_loss
        losses["l_joint_pos_m"] = joint_pos_loss
        losses["l_recons_pose_global"] = global_pose_recons_loss
        losses["l_recons_pose_local"] = local_pose_recons_loss
        losses["l_skate_contact"] = mean_vel
        skate_contact_loss_coeff = self._args.get("skate_contact_loss_coeff", 0.0)

        # 码字使用熵正则：鼓励码本被均匀使用，抑制塌缩。
        #
        # 动机（实测 full_400k，40,356 步）：per_head=32 时 perplexity 均值
        # 只有 1.29（4% 容量），最小值 1.00（整批 token 用同一个码字），
        # 而同期 recons 从 0.52 降到 0.24。也就是说模型发现「少用码字」
        # 能更好地压低重建误差，官方 loss 里没有任何项反对这个方向。
        #
        # 这里用 -coeff * entropy：entropy 越大（码字越均匀）损失越小，
        # 因此梯度推动模型提高多样性。coeff 由配置提供，默认 0（保持原行为）。
        usage_entropy_coeff = self._args.get("usage_entropy_coeff", 0.0)
        if usage_entropy_coeff > 0.0 and "usage_entropy" in pose_net_output:
            losses["l_usage_entropy"] = pose_net_output["usage_entropy"]
            usage_entropy_term = -usage_entropy_coeff * losses["l_usage_entropy"]
        else:
            losses["l_usage_entropy"] = 0.0
            usage_entropy_term = 0.0

        losses["loss"] = (
            pose_recons_loss
            + self._args["commit_loss_coeff"] * losses["l_commit_pose"]
            + skate_contact_loss_coeff * losses["l_skate_contact"]
            + joint_vel_loss_coeff * losses["l_joint_vel"]
            + joint_pos_loss_coeff * losses["l_joint_pos_m"]
            + usage_entropy_term
        )
        return losses

    @property
    def args(self):
        return self._args

    def get_num_frames_per_code(self):
        return 2 ** self._args["down_t"]

    def _construct_keyframe_prob(self):
        probs = {}
        for module in ["pose", "root"]:
            max_num_keyframes = self.args[f"{module}_vqvae_max_num_keyframes"] * (
                self.trainer.global_step / self.args["keyframe_num_warmup_steps"]
            )
            max_num_keyframes = int(
                max(
                    1,
                    min(
                        max_num_keyframes,
                        self.args[f"{module}_vqvae_max_num_keyframes"],
                    ),
                )
            )

            prob_num_keyframes = [
                1.0 if i > 0 and i <= max_num_keyframes else 0.0
                for i in range(self.args[f"{module}_vqvae_max_num_keyframes"] + 1)
            ]
            prob_no_keyframe = self.args[f"{module}_vqvae_no_keyframe_prob"]
            prob_num_keyframes[0] = (
                sum(prob_num_keyframes) / (1 - prob_no_keyframe) * prob_no_keyframe
            )
            prob_num_keyframes = np.array(prob_num_keyframes)
            prob_num_keyframes /= prob_num_keyframes.sum()
            probs[module] = prob_num_keyframes

        return probs["pose"], probs["root"]

    def _construct_masked_pose_external_cond_if_needed(
        self, pose_external_cond: t.Tensor, batch: dict
    ):
        if self.pose_net.motion_rep.name != "global":
            return pose_external_cond
        if (
            self.pose_net.decoder_external_cond_feature_mode
            != "root_without_hip_height_without_heading_with_mask"
        ):
            return pose_external_cond

        batch_size, motion_length = (
            batch["local_motions"].shape[0],
            batch["local_motions"].shape[1],
        )
        device = batch["local_motions"].device
        unnorm_gt_local_motion = self.local_motion_rep.unnormalize(
            batch["local_motions"]
        )

        max_perturb_angle = self.args.get("max_perturb_angle", 5.0)
        max_vel_norm_perturb_ratio = self.args.get("max_vel_norm_perturb_ratio", 0.2)
        norm_ratio = (
            torch.rand([batch_size, unnorm_gt_local_motion.shape[1], 1])
            * max_vel_norm_perturb_ratio
            * 2
            + (1 - max_vel_norm_perturb_ratio)
        )
        angle = (
            torch.rand([batch_size, unnorm_gt_local_motion.shape[1]])
            * max_perturb_angle
            * 2
            - max_perturb_angle
        ) * np.pi / 180.0
        norm_ratio, angle = norm_ratio.to(device), angle.to(device)

        accumulated_angle = torch.cumsum(angle, dim=1)
        unnorm_gt_local_motion[
            :, :, self.local_motion_rep.indices["local_root_vel"]
        ] *= norm_ratio[:, :, :]
        cos, sin = torch.cos(accumulated_angle), torch.sin(accumulated_angle)
        rotated_x = (
            unnorm_gt_local_motion[
                :, :, self.local_motion_rep.indices["local_root_vel"][0]
            ]
            * cos
            - unnorm_gt_local_motion[
                :, :, self.local_motion_rep.indices["local_root_vel"][1]
            ]
            * sin
        )
        rotated_z = (
            unnorm_gt_local_motion[
                :, :, self.local_motion_rep.indices["local_root_vel"][0]
            ]
            * sin
            + unnorm_gt_local_motion[
                :, :, self.local_motion_rep.indices["local_root_vel"][1]
            ]
            * cos
        )
        unnorm_gt_local_motion[
            :, :, self.local_motion_rep.indices["local_root_vel"][0]
        ] = rotated_x
        unnorm_gt_local_motion[
            :, :, self.local_motion_rep.indices["local_root_vel"][1]
        ] = rotated_z

        perturbed_pose_external_cond = extract_feature_from_motion_rep(
            self.dual_rep.local_to_global(
                unnorm_gt_local_motion,
                is_normalized=False,
                to_normalize=True,
                lengths=torch.full([batch_size], motion_length).to(device),
            ),
            self.pose_net.motion_rep,
            self.pose_net.decoder_external_cond_feature_mode,
        )

        perturbed = torch.rand([batch_size]) < self.args.get(
            "percentage_of_perturbed_samples", 0.2
        )
        perturbed_pose_external_cond = t.where(
            batch["pose_has_target_cond"][:, :, None],
            pose_external_cond,
            perturbed_pose_external_cond,
        )
        pose_external_cond = t.where(
            perturbed[:, None, None].to(device),
            perturbed_pose_external_cond,
            pose_external_cond,
        )
        pose_external_cond[:, :, -1] = batch["pose_has_target_cond"].float()

        batch["perturbed"] = perturbed
        return pose_external_cond
