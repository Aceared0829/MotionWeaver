"""使用 Unreal 动画训练 VQ-VAE、Pose 或 Root；不加载 G1 预训练权重。"""

import argparse
import hashlib
from collections import Counter
from pathlib import Path

import pytorch_lightning as pl
import torch
from hydra.utils import instantiate
from omegaconf import DictConfig, OmegaConf, open_dict
from pytorch_lightning.callbacks import ModelCheckpoint
from pytorch_lightning.loggers import CSVLogger
from torch.utils.data import DataLoader, Subset, WeightedRandomSampler

from data.runtime.motionweaver_dataset import (
    MotionWeaverUnrealDataset,
    collate_motionweaver,
)
from data.runtime.synthetic_dataset import collate_batch
from data.runtime.unreal_dataset import UnrealMotionDataset, file_sha256
from motionweaver.legacy import canonical_config
from motionweaver.representation.helper.pl_util import load_motion_rep
from training.common.optim.scheduler import WarmupCosineScheduler
from training.common.unreal_quality import PoseAwareBatchSampler, collate_native


class DatasetContract(pl.Callback):
    """检查点携带骨架和配置契约，阻止把 G1 或其他数据集权重误用于此训练。"""

    def __init__(self, signature, kind, config, training_contract="legacy_root_in_pose"):
        self.signature, self.kind, self.config, self.training_contract = signature, kind, config, training_contract

    def on_save_checkpoint(self, trainer, pl_module, checkpoint):
        checkpoint["unreal_contract"] = {"signature": self.signature, "kind": self.kind, "training_contract": self.training_contract,
                                         "motionweaver_profile": self.config.get("motionweaver_profile"), "config": self.config}

    def on_train_start(self, trainer, pl_module):
        # Lightning 恢复检查点后才执行此回调；此时旧调度器状态已覆盖构建时的新周期。
        schedule = self.config["model"]["scheduler"]
        for entry in trainer.lr_scheduler_configs:
            scheduler = entry.scheduler
            if not isinstance(scheduler, WarmupCosineScheduler):
                raise TypeError("Unreal training requires WarmupCosineScheduler")
            scheduler.num_training_steps = schedule["num_training_steps"]
            scheduler.num_warmup_steps = schedule["num_warmup_steps"]
            rates = scheduler.get_lr()
            for group, rate in zip(scheduler.optimizer.param_groups, rates):
                group["lr"] = rate
            scheduler._last_lr = rates

    def on_before_backward(self, trainer, pl_module, loss):
        if not torch.isfinite(loss).all():
            raise ValueError("训练损失非有限值，请检查动作特征和训练配置")


def check_checkpoint(path, signature, kind):
    checkpoint = torch.load(path, map_location="cpu", weights_only=False)
    contract = checkpoint.get("unreal_contract", {})
    if contract.get("signature") != signature or contract.get("kind") != kind:
        raise ValueError(f"{path} 与当前 UE 数据集或模型类型不匹配；不能使用 G1 权重")
    return {**contract, "config": OmegaConf.to_container(
        canonical_config(OmegaConf.create(contract["config"])), resolve=False)}


class _FeatureStepLoader:
    """包一层 DataLoader：取到「原始窗口批」后立刻算成特征批。

    为什么要包这一层：特征计算需要 GPU，而 GPU 模块不能放进 DataLoader 的
    worker（每 worker 会占一份显存，且 Windows spawn 下不可序列化）。
    因此让 worker 只做 CPU 重活（读盘 + 算接触），主进程在这一层批量算特征，
    两者的时间就能重叠：实测 num_workers=8 时每条约 11.3ms，
    而全部塞在主进程时约 69.9ms。
    """

    def __init__(self, loader, step):
        self._loader = loader
        self._step = step
        self.dataset = getattr(loader, "dataset", None)
        self.batch_size = getattr(loader, "batch_size", None)

    def __iter__(self):
        for payload in self._loader:
            # 已经是特征批就直接放行（单进程 collate 路径）
            if "motion" in payload:
                yield payload
            else:
                yield self._step(payload)

    def __len__(self):
        return len(self._loader)


def training_indices(manifest):
    """所有模型都遵守显式划分，只有未划分的数据集才使用全部动作。"""
    clips = manifest["clips"]
    if manifest.get("split_policy", "none") == "none":
        return list(range(len(clips)))
    if any(item.get("split") not in {"train", "validation", "test"} for item in clips):
        raise ValueError("数据集存在缺失或非法划分")
    indices = [i for i, item in enumerate(clips) if item["split"] == "train"]
    if not indices:
        raise ValueError("数据集没有训练分区，禁止使用留出动作训练")
    return indices


def build_config(args, dataset):
    base = Path(__file__).resolve().parents[1] / "configs/pretrain" / f"motionweaver_{args.model}.yaml"
    conf = OmegaConf.load(base)
    if not isinstance(conf, DictConfig):
        raise TypeError("Configuration must be a mapping")
    with open_dict(conf):
        # --soma_views 模式下数据根是四键 NPZ 训练视图；stats 由 compute_stats 写到同一目录，
        # 骨架则用实时 Dataset 派生出的可读副本，因此这里统一取实际数据目录。
        data_root = Path(getattr(args, "soma_views", None) or args.dataset).resolve()
        # UE Schema 数据集里骨架就在数据根目录；实时 Dataset 可能派生到缓存目录，
        # 因此优先用数据集自己报告的 skeleton_root。
        skeleton_dir = str(getattr(dataset, "skeleton_root", data_root))
        conf.data = {"folder": str(data_root), "text_embeddings": None}
        conf.data["manifest_sha256"] = hashlib.sha256((data_root / "dataset.json").read_bytes()).hexdigest()
        conf.skeleton = {"_target_": "data.runtime.unreal_dataset.UnrealSkeleton",
                         "folder": skeleton_dir}
        conf.motion_rep.name = "unreal_dual_root_global_joints"
        conf.motion_rep.stats.folder = str(data_root / "stats")
        if getattr(args, "kmeans_init", None) is not None:
            # 码本初始化方式：0 用随机初始化，1 用首批数据做 kmeans 初始化。
            # 默认值由 YAML 决定（当前 false）；只在显式传参时覆盖，
            # 这样能做 A/B 对照而不必改动共享配置文件。
            conf.model.pose_vqvae_network.kmeans_init = bool(args.kmeans_init)
        if getattr(args, "down_t", None) is not None:
            # 下采样层数决定 frames_per_token = 2**down_t，进而决定一段窗口
            # 切成多少个 token。必须同时满足 min_tokens <= 窗口/2**down_t <= max_tokens，
            # 否则要么 token 过多超出主干上限、要么少到信息不足。
            # YAML 默认 2，对 48 帧是 12 token、对 72 帧是 18 token（超过 max=16），
            # 因此按场景选窗口时必须同时覆盖这个值。
            conf.model.pose_vqvae_network.down_t = int(args.down_t)
            # model.args 下还有一份 down_t，它参与 token 预算与关键帧调度计算。
            # 两份不一致会让编码器按一个层数下采样、而调度按另一个算 token 数，
            # 因此必须同步覆盖。
            if "down_t" in conf.model.args:
                conf.model.args.down_t = int(args.down_t)
        if getattr(args, "nb_code_per_head", None) is not None:
            # 码本容量：nb_code 与 num_heads 共同决定 nb_code_per_head
            # （代码里 assert nb_code_per_head ** num_heads == nb_code）。
            # 直接给 nb_code 容易算错，因此允许按「每 head 多少码字」指定，
            # 由这里反算 nb_code，并把校验交给模型构造时的 assert。
            heads = int(conf.model.pose_vqvae_network.num_heads)
            per_head = int(args.nb_code_per_head)
            conf.model.pose_vqvae_network.nb_code = per_head ** heads
        if getattr(args, "usage_entropy_coeff", None) is not None:
            # 码字使用熵正则系数：>0 时损失里加 -coeff * entropy，
            # 推动码本被均匀使用，抑制「所有 token 用同一个码字」的塌缩。
            conf.model.args.usage_entropy_coeff = float(args.usage_entropy_coeff)
        conf.fps = dataset.manifest["fps"]
        conf.id, conf.run_dir, conf.out_dir = "unreal", str(Path(args.output).resolve()), str(Path(args.output).resolve())
        conf.trainer.max_steps = args.max_steps
        conf.model.scheduler.num_training_steps = args.max_steps
        conf.model.scheduler.num_warmup_steps = min(1000, max(0, args.max_steps // 10))
        conf.model.args.keyframe_num_warmup_steps = min(1000, max(1, args.max_steps // 10))
        if getattr(args, "native_quality", False):
            if args.model != "vqvae" or dataset.manifest.get("split_policy", "none") == "none":
                raise ValueError("原生质量训练要求 VQ-VAE 和预先划分的训练/留出集")
            conf.model._target_ = "training.common.unreal_quality.UnrealQualityVQVAE"
            conf.model.args.ue_geometry_coeff = args.geometry_coeff
            conf.model.args.ue_hand_endpoint_coeff = args.hand_endpoint_coeff
            conf.model.args.ue_traversal_endpoint_coeff = args.traversal_endpoint_coeff
            conf.model.args.pose_aware_sampling = getattr(args, "pose_aware_sampling", False)
            conf.model.args.pose_vqvae_no_keyframe_prob = 0.75
        conf.trainer.devices = 1
        conf.trainer.num_nodes = 1
        if args.model == "pose":
            if not args.vqvae:
                raise ValueError("训练 Pose 必须通过 --vqvae 指定本数据集训练的 VQ-VAE")
            contract = check_checkpoint(args.vqvae, dataset.manifest["training_signature"], "vqvae")
            conf.model.pose_vqvae_network = contract["config"]["model"]["pose_vqvae_network"]
            conf.model.args.vqvae_model_ckpt_path = str(Path(args.vqvae).resolve())
            conf.model.args.vqvae_sha256 = file_sha256(args.vqvae)
            if getattr(args, "motionweaver_online", False):
                conf.model._target_ = "training.models.backbone.motionweaver_pose_model.MotionWeaverPoseModel"
                net = conf.model.backbone_network.args
                net.n_embd, net.n_head, net.n_layers = 256, 8, 6
                net.pose_feat_width, net.root_feat_width, net.token_length_feat_width = 256, 128, 64
                conf.model.args.batchsize_mul_factor = 1
                conf.motionweaver_profile = "actor_root_history4_compact_v1"
                conf.motionweaver_actor_root_manifest_sha256 = file_sha256(
                    Path(args.actor_root_sidecar) / "actor_roots_manifest.json"
                )
        if args.tiny:
            # 仅用于 CPU 冒烟验证；模型配置会随检查点保存，不能与正式模型互换。
            if args.model == "vqvae":
                net = conf.model.pose_vqvae_network
                net.width, net.depth, net.code_dim, net.output_emb_width = 32, 1, 32, 32
                net.num_heads, net.nb_code = 2, 16
            else:
                net = conf.model.backbone_network.args
                net.n_embd, net.n_head = 32, 4
                if args.model == "pose":
                    net.n_layers = 1
                else:
                    net.n_layers_shared, net.n_layers_root_token, net.width, net.depth = 1, 1, 32, 1
    return conf


def train(args):
    if getattr(args, "motionweaver_online", False) and args.model != "pose":
        raise ValueError("--motionweaver_online 仅适用于 Pose Token 训练")
    if getattr(args, "motionweaver_online", False) and not args.actor_root_sidecar:
        raise ValueError("MotionWeaver Actor Root 条件训练需要 --actor_root_sidecar")
    if getattr(args, "motionweaver_online", False) and args.native_quality:
        raise ValueError("MotionWeaver Actor Root 条件训练暂不支持会丢失独立 Root 的 native_quality collate")
    if getattr(args, "pose_aware_sampling", False) and not getattr(args, "native_quality", False):
        raise ValueError("--pose_aware_sampling 必须与 --native_quality 一起使用")
    if args.max_steps < 1 or args.batch_size < 1:
        raise ValueError("训练步数和批次大小必须为正数")
    if args.geometry_coeff < 0 or args.hand_endpoint_coeff < 0 or args.traversal_endpoint_coeff < 0:
        raise ValueError("几何损失权重不能为负数")
    pl.seed_everything(args.seed, workers=True)
    soma_views = getattr(args, "soma_views", None)
    if soma_views:
        # 直接吃四键 NPZ 训练视图：不预展开 942 维稠密数组（全库需 918 GB），
        # 改为在 collate 里对整批一次算特征。详见 soma_realtime_dataset 的实测说明。
        # 延迟导入：本模块只在 SOMA 四键视图路径下需要。放在文件顶层会让
        # 「以文件路径直接运行本脚本」的既有测试在 import 阶段就失败（data 包不在 sys.path）。
        from data.runtime.soma_realtime_dataset import SomaRealtimeDataset
        dataset = SomaRealtimeDataset(
            soma_views,
            window_frames=getattr(args, "soma_window_frames", 48),
            history_frames=getattr(args, "soma_history_frames", 0),
            fps_group=getattr(args, "soma_fps_group", None),
            scenes=getattr(args, "soma_scenes", None),
        )
    else:
        dataset = (MotionWeaverUnrealDataset(args.dataset, args.actor_root_sidecar)
                   if getattr(args, "motionweaver_online", False) else UnrealMotionDataset(args.dataset))
    if dataset.manifest.get("training_contract") == "pose_only_root_authoritative" and args.model == "root":
        raise ValueError("Pose-only 数据集的世界移动由 CMC/Mover 权威提供，禁止训练 Root 模型")
    indices = training_indices(dataset.manifest)
    signature = dataset.manifest["training_signature"]
    conf = build_config(args, dataset)
    if args.resume:
        contract = check_checkpoint(args.resume, signature, args.model)
        previous = contract["config"]
        if previous.get("motionweaver_profile") != conf.get("motionweaver_profile"):
            raise ValueError("续训必须保持相同的 MotionWeaver 在线条件配置")
        if previous.get("motionweaver_actor_root_manifest_sha256") != conf.get("motionweaver_actor_root_manifest_sha256"):
            raise ValueError("续训必须使用相同的独立 Actor Root sidecar")
        previous_native = previous["model"].get("_target_", "").endswith("UnrealQualityVQVAE")
        if previous_native != getattr(args, "native_quality", False):
            raise ValueError("续训必须保持原检查点的 --native_quality 模式，避免静默改变帧掩码和训练目标")
        if args.model == "pose" and previous["model"]["args"].get("vqvae_sha256") != conf.model.args.vqvae_sha256:
            raise ValueError("Pose 续训必须使用训练时的同一份 VQ-VAE 权重；相同骨架不代表码本相同")
        # 续训复用原网络结构；仅保留本次的训练步数、路径和调度器配置。
        for key in ("pose_vqvae_network", "backbone_network"):
            if key in previous["model"]:
                conf.model[key] = previous["model"][key]
    output = Path(args.output).resolve()
    if output.exists() and not args.resume:
        raise ValueError("输出目录已存在，请指定新目录或使用 --resume")
    motion_rep = load_motion_rep(conf)
    model_conf = conf.model
    pose_net = instantiate(model_conf.pose_vqvae_network, motion_rep=motion_rep.dual_rep.local_motion_rep) if model_conf.pose_vqvae_network else None
    injections = {"pose_vqvae_network": pose_net, "root_vqvae_network": None, "motion_rep": motion_rep}
    if "backbone_network" in model_conf:
        injections["backbone_network"] = instantiate(model_conf.backbone_network, motion_rep=motion_rep, _recursive_=False)
    model = instantiate(model_conf, **injections, optimizer=instantiate(model_conf.optimizer), scheduler=instantiate(model_conf.scheduler), _recursive_=False)
    if args.model == "pose":
        assert pose_net is not None
        for parameter in pose_net.parameters():
            parameter.requires_grad_(False)
    output.mkdir(parents=True, exist_ok=True)
    # 保留 ??? 注入点；其余插值由 Hydra 在构建对应组件时解析。
    config = OmegaConf.to_container(conf, resolve=False)
    OmegaConf.save(conf, output / "config.yaml")
    checkpoint = ModelCheckpoint(dirpath=str(output / "checkpoints"), every_n_train_steps=max(1, min(getattr(args, "checkpoint_every", 1000), args.max_steps)), save_last=True, save_top_k=-1)
    trainer = pl.Trainer(max_steps=args.max_steps, accelerator=args.accelerator, devices=1,
                         precision=getattr(args, "precision", "32"),
                         logger=CSVLogger(str(output), name="metrics"),
                         callbacks=[checkpoint, DatasetContract(signature, args.model, config, dataset.manifest.get("training_contract", "legacy_root_in_pose"))], gradient_clip_val=1.0, num_sanity_val_steps=0, log_every_n_steps=1)
    if getattr(args, "native_quality", False):
        categories = [dataset.manifest["clips"][i]["labels"]["category"] for i in indices]
        counts = Counter(categories)
        weights = [1 / counts[category] ** 0.5 for category in categories]
        sampler = WeightedRandomSampler(weights, len(indices), replacement=True)
        if getattr(args, "pose_aware_sampling", False):
            sampler = PoseAwareBatchSampler([dataset.manifest["clips"][i] for i in indices], args.batch_size)
            loader = DataLoader(Subset(dataset, indices), batch_sampler=sampler, num_workers=0, collate_fn=collate_native)
        else:
            loader = DataLoader(Subset(dataset, indices), batch_size=args.batch_size, sampler=sampler, num_workers=0, collate_fn=collate_native)
    else:
        if soma_views:
            # 特征在 worker 进程内算（CPU）。这是关键：若把特征留在主线程算
            # （19.7ms/批），worker 的预取会随之停摆——实测 workers=8 比单进程
            # 还慢 3-5 倍。放 worker 里则 8 进程真正并行，主线程拿到特征就能训练。
            indices_arr = dataset.representation.indices["global_rep"]
            from data.runtime.soma_realtime_dataset import SomaRealtimeCollate
            collate_fn = SomaRealtimeCollate(dataset.representation, indices_arr)
        else:
            collate_fn = collate_motionweaver if getattr(args, "motionweaver_online", False) else collate_batch
        workers = 0 if soma_views and args.accelerator == "cpu" else int(
            getattr(args, "num_workers", 0) or 0)
        # persistent_workers 必须开启：数据量小时一个 epoch 只有几十步，
        # 若每个 epoch 都销毁重建 worker 进程池，每次要付 1-2 秒的 torch
        # JIT 初始化（日志里每个 worker 一条 "triton not found"）。
        # 实测 200 条数据跑 5 个 epoch：关闭时 57 秒，开启时 24.9 秒（2.3 倍），
        # 且首 epoch 之后每轮只要 3.7 秒。关闭时累积的重建开销会让长训练
        # 看起来像卡死（曾观察到 119 步后长时间无进展）。
        loader = DataLoader(Subset(dataset, indices), batch_size=args.batch_size,
                            shuffle=True, num_workers=workers,
                            collate_fn=collate_fn,
                            persistent_workers=workers > 0,
                            prefetch_factor=(2 if workers > 0 else None))
    trainer.fit(model, train_dataloaders=loader, ckpt_path=args.resume)
    trainer.save_checkpoint(output / "checkpoints" / "final.ckpt")
    print(f"训练完成：{output / 'checkpoints' / 'final.ckpt'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, choices=["vqvae", "pose", "root"])
    parser.add_argument("--dataset", help="UE Schema 数据集目录；与 --soma_views 二选一")
    parser.add_argument("--output", required=True)
    parser.add_argument("--vqvae", help="可信的本地 VQ-VAE 检查点")
    parser.add_argument("--resume", help="可信的同类模型检查点，用于恢复优化器及步数")
    parser.add_argument("--max_steps", type=int, default=10000)
    parser.add_argument("--batch_size", type=int, default=8)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--accelerator", choices=["auto", "cpu", "gpu"], default="auto")
    parser.add_argument("--precision", choices=["32", "bf16-mixed", "16-mixed"], default="32")
    parser.add_argument("--motionweaver_online", action="store_true", help="Pose Token 使用 4 帧历史条件和紧凑主干；不输入未来真实姿态")
    parser.add_argument("--actor_root_sidecar", help="与 UE prepared 动作逐帧对齐的独立 Actor Root sidecar")
    parser.add_argument("--tiny", action="store_true", help="使用小网络，仅用于接口和训练冒烟验证")
    parser.add_argument("--native_quality", action="store_true", help="真实帧掩码、家族留出集及几何监督")
    parser.add_argument("--pose_aware_sampling", action="store_true", help="原生质量训练采用长度分桶和困难类别采样，不增加模型参数")
    parser.add_argument("--geometry_coeff", type=float, default=1.0, help="0 用于几何损失消融对照")
    parser.add_argument("--hand_endpoint_coeff", type=float, default=0.2, help="全类别手部端点几何损失权重，不增加模型参数")
    parser.add_argument("--traversal_endpoint_coeff", type=float, default=0.2, help="Traversal 手脚端点额外几何损失权重，不增加模型参数")
    parser.add_argument("--checkpoint_every", type=int, default=1000, help="周期检查点间隔；增大间隔可减少训练产物磁盘占用")
    parser.add_argument("--soma_views", help="四键 NPZ 训练视图目录；直接读取并实时算特征，跳过预转换")
    parser.add_argument("--soma_window_frames", type=int, default=48, help="每段训练窗口帧数（A 场景 48，B/C 场景 72）")
    parser.add_argument("--soma_history_frames", type=int, default=0, help="窗口前的历史帧数，通常为 0")
    parser.add_argument("--soma_fps_group", choices=("fps120", "fps30"),
                        help="只训该帧率组；120fps 与 30fps 不可混批")
    parser.add_argument("--soma_scenes", nargs="+", choices=("A", "B", "C", "D"),
                        help="只训指定场景；B/C 共用 72 帧窗口与 down_t=3，A/D 用 48 帧与 down_t=4")
    parser.add_argument("--num_workers", type=int, default=0,
                        help="DataLoader 并行读盘进程数；实测 8 约 6 倍加速（仅影响速度，不影响数值）")
    parser.add_argument("--kmeans_init", type=int, choices=(0, 1), default=None,
                        help="覆盖码本初始化：1=kmeans 初始化，0=随机初始化；不传则用 YAML 默认值")
    parser.add_argument("--down_t", type=int, choices=(2, 3, 4), default=None,
                        help="覆盖下采样层数；frames_per_token=2**down_t。48帧配2=12token，配3=6token；72帧配2=18token(超上限)，配3=9token")
    parser.add_argument("--nb_code_per_head", type=int, default=None,
                        help="按每 head 的码字数指定码本容量；内部反算 nb_code=per_head**num_heads。"
                             "10=当前默认(10^8)，32=32^8≈1.1e12")
    parser.add_argument("--usage_entropy_coeff", type=float, default=None,
                        help="码字使用熵正则系数；>0 时加 -coeff*entropy 到损失，推动码字均匀使用。"
                             "0=关闭（官方原行为）。建议从 0.1 起试")
    train(parser.parse_args())
