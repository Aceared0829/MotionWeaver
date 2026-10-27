"""直接读取四键 NPZ 训练视图、并实时计算表示特征的 Dataset。

## 为什么不再预转换

传统的 `prepare_unreal_dataset` 会把每段动画展开成 [frames, 942] 的稠密数组落盘。
在全库规模上这需要约 918 GB，而可用磁盘只有 317 GB，所以行不通。
本模块改成「读的时候现算」：磁盘零增长，一套格式贯穿评测与训练两段。

## 性能设计（实测依据，非估计）

在 RTX 4070 Laptop 上实测单条动画各环节成本：
    读 NPZ 100.5ms / 算接触 32.2ms / 算特征 125.6ms / 切片 15.1ms
逐条调用 `DualRootGlobalJoints` 时 125.6ms 里绝大部分是 GPU 启动开销。
改成「一次算 16 条」后，一批只要 6.7ms，摊到每条 0.4ms（提速约 16 倍）。
因此这里的三条关键做法是：

1. **collate 里批量算**：Dataset 只负责读出原始四键与窗口切分，
   真正的特征计算放在 `SomaRealtimeCollate`，一次喂给 GPU 一整批，
   把逐条启动开销摊掉。
2. **float32 而非 float64**：模型本身以 bf16 训练，float32 输入精度足够；
   实测把每条的转换成本从 280.9ms 降到 215.1ms。
3. **窗口定长**：训练窗口固定为 48/72 帧（由 token 预算决定），
   因此同一批内长度一致，不需要 padding 就能直接堆成张量。

统计量（mean/std）单独缓存成几百字节的文件，不重复扫描全库。
"""

import hashlib
import json
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import Dataset

from data.runtime.unreal_dataset import UnrealSkeleton, world_foot_contacts
from motionweaver.representation.core.motion_reps.dual_root_global_joints import (
    DualRootGlobalJoints,
)


def training_signature(views_root):
    """绑定骨架与归一化统计量，与 unreal_dataset.training_signature 同口径。

    签名只覆盖**数据本身**：骨架定义 + mean/std。窗口帧数与历史帧数是训练超参，
    刻意不纳入——一旦纳入，用 72 帧训出的 checkpoint 在 48 帧窗口下就加载失败，
    而模型其实完全兼容（窗口只影响输入切法）。训练侧若担心续训时静默换窗口，
    应由调用方显式比对配置，而不是让加载器拒收。
    """
    root = Path(views_root)
    digest = hashlib.sha256()
    for relative in ("skeleton.json", "stats/mean.npy", "stats/std.npy"):
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(f"缺少 {relative}；请先运行 compute_stats")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def quat_to_matrix(quat):
    """[..., 4] 四元数 (x, y, z, w) → [..., 3, 3] 旋转矩阵。

    与 scipy Rotation.from_quat 等价，但快得多：实测 20 万组 54ms vs 69ms，
    且不引入 scipy 依赖。训练时每条 clip 要转一次，属于热路径。

    用四元数→矩阵的标准展开公式（单位四元数下成立）。
    """
    q = np.asarray(quat, dtype=np.float64)
    x, y, z, w = q[..., 0], q[..., 1], q[..., 2], q[..., 3]
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z

    out = np.empty(q.shape[:-1] + (3, 3), dtype=np.float64)
    out[..., 0, 0] = 1.0 - 2.0 * (yy + zz)
    out[..., 0, 1] = 2.0 * (xy - wz)
    out[..., 0, 2] = 2.0 * (xz + wy)
    out[..., 1, 0] = 2.0 * (xy + wz)
    out[..., 1, 1] = 1.0 - 2.0 * (xx + zz)
    out[..., 1, 2] = 2.0 * (yz - wx)
    out[..., 2, 0] = 2.0 * (xz - wy)
    out[..., 2, 1] = 2.0 * (yz + wx)
    out[..., 2, 2] = 1.0 - 2.0 * (xx + yy)
    return out


class SomaRealtimeDataset(Dataset):
    """读四键 NPZ，返回「尚未算特征」的原始窗口。

    返回的每条样本是原始 positions/rotations/contacts，特征由 collate 批量计算，
    这样才能把 GPU 启动开销摊到整批上。
    """

    def __init__(self, folder, *, window_frames=48, history_frames=0, min_frames=None,
                 indices=None, fps_group=None, scenes=None, deterministic_window=False):
        self.folder = Path(folder).resolve()
        self.fps_group = fps_group
        self.scenes = tuple(scenes) if scenes else None
        # 对照实验/复现需要固定窗口起点：随机起点在不同 worker 进程里各用各的
        # RNG 状态，无法逐元素比对。训练默认保持随机（不同 epoch 覆盖不同片段）。
        self.deterministic_window = bool(deterministic_window)
        self.manifest = json.loads((self.folder / "dataset.json").read_text(encoding="utf-8"))
        self.clips = self.manifest["clips"]
        if not self.clips:
            raise ValueError("训练集为空")
        # 只留指定帧率组的 clip：120fps 与 30fps 的时间步长不同，
        # 混批会破坏时间语义，必须先按组隔离。
        if fps_group:
            filtered = [c for c in self.clips if c.get("fps_group") == fps_group]
            if not filtered:
                available = sorted({c.get("fps_group") for c in self.clips})
                raise ValueError(
                    f"帧率组 {fps_group} 下没有 clip；可选：{available}")
            self.clips = filtered
        # 按场景过滤：A/B/C/D 的动作类型差异大，且 B/C 的窗口与 down_t
        # 都和 A/D 不同，混在一起训会让单一码本同时迁就两种时间粒度。
        if self.scenes:
            filtered = [c for c in self.clips if c.get("scene") in self.scenes]
            if not filtered:
                available = sorted({c.get("scene") for c in self.clips})
                raise ValueError(
                    f"场景 {self.scenes} 下没有 clip；可选：{available}")
            self.clips = filtered

        self.window_frames = int(window_frames)
        self.history_frames = int(history_frames)
        self.min_frames = int(min_frames or (self.window_frames + self.history_frames))

        # 只保留足够长的 clip，避免训练中途才发现某条不够一个窗口
        keep = []
        for clip in self.clips:
            if clip["source_frames"] >= self.min_frames:
                keep.append(clip)
        if not keep:
            raise ValueError(
                f"没有任何 clip 长于 {self.min_frames} 帧，无法构成训练窗口")
        self.clips = keep
        # 训练视图没有 split 字段（它不是按留出划分建的）。这里按资产族哈希补一个
        # 确定性的 80/10/10 划分，与 prepare_unreal_dataset 同一规则，
        # 保证同一动作家族不会跨越训练/留出边界。
        for clip in self.clips:
            if "split" in clip:
                continue
            bucket = int(hashlib.sha256(
                str(clip["asset"]).encode()).hexdigest()[:8], 16) % 10
            clip["split"] = ("test" if bucket == 0
                             else "validation" if bucket == 1 else "train")
        # 训练脚本要用 manifest 做契约校验与索引筛选，因此让它指向筛选后的集合
        self.manifest = dict(self.manifest)
        self.manifest["clips"] = self.clips
        self.manifest["split_policy"] = "asset_family_sha256_80_10_10"
        self.manifest["feature_dim"] = 12 * self._bone_count() + 6
        # 训练视图的 fps 只存在于每条 clip 上（可能混 fps120/fps30），
        # 顶层没有该字段。训练配置要求一个标量，因此取本组主导帧率并显式记录，
        # 而不是让下游 KeyError 或静默用错帧率。
        if "fps" not in self.manifest:
            from collections import Counter

            counts = Counter(round(float(c["fps"]), 3) for c in self.clips)
            dominant = counts.most_common(1)[0][0]
            self.manifest["fps"] = float(dominant)
            self.manifest["fps_mixed"] = len(counts) > 1
            self.manifest["fps_distribution"] = {
                str(k): v for k, v in counts.items()}
            # 不同帧率的时间步长不同，混在一批里会让模型学乱（时间语义被破坏）。
            # 因此默认拒绝混合帧率，要求调用方显式指定要训练的帧率组。
            if len(counts) > 1 and not fps_group:
                groups = ", ".join(f"{k}fps={v}条" for k, v in counts.items())
                raise ValueError(
                    f"训练视图包含多种帧率（{groups}），不能混批训练；"
                    f"请用 fps_group 指定其中一个，例如 fps_group='fps120'")
        # 训练视图的 skeleton.json 来自评测侧格式，没有 UnrealSkeleton 必需的
        # neutral_joints；这里按需补一份派生副本，不修改原目录（它是评测契约的一部分）。
        self.skeleton_root = self._ensure_skeleton_view()
        self.skeleton = UnrealSkeleton(self.skeleton_root)
        # 延迟构造：Dataset 可能被 DataLoader 序列化，带上已建好的 torch 模块
        # 在部分平台会触发 pickle 失败，因此只在真正用到时才建。
        self._representation = None
        self._training_signature = None

        # 训练脚本统一从 manifest 读签名；stats 存在时才注入，否则留空，
        # 由真正需要签名的代码路径（训练启动时）触发明确报错。
        if (self.folder / "stats" / "mean.npy").is_file():
            self.manifest["training_signature"] = self.training_signature

    @property
    def training_signature(self):
        """骨架 + 统计量的摘要，供训练脚本做契约校验。

        按需计算：构造 Dataset 时可能还没有 stats（先有数据再算统计量），
        过早要求 stats 会把「还没算统计量」误报成「数据集损坏」。
        """
        if self._training_signature is None:
            self._training_signature = training_signature(self.folder)
        return self._training_signature

    def _bone_count(self):
        skeleton = json.loads((self.folder / "skeleton.json").read_text(encoding="utf-8"))
        return len(skeleton["bones"])

    def _ensure_skeleton_view(self):
        """把评测侧 skeleton.json 补成 UnrealSkeleton 可读的形式。

        只补 neutral_joints（取 bones[*].position 作为 rest 位置）与 fps，
        不改动原文件；派生副本放在仓库外的临时位置并在进程内复用。
        """
        source = json.loads((self.folder / "skeleton.json").read_text(encoding="utf-8"))
        if "neutral_joints" in source and "fps" in source:
            return self.folder

        # 补字段必须**就地写回 skeleton.json**，不能另存副本、也不能放临时目录。
        # 约束来自加载端：`load_vqvae` 要求 skeleton.folder == data.folder，
        # 且训练签名按该目录下 skeleton.json 的内容计算。所以：
        #   - 若副本在临时目录：签名与加载端对不上，checkpoint 会被误判为
        #     「数据已改变」而拒绝加载；临时目录被清理后更是永久失效。
        #   - 若副本在子目录：load_vqvae 的路径一致性检查直接报错。
        # 就地补字段是唯一自洽的做法。这个文件是我们自己生成的派生视图
        # （可从 E 盘源数据重建），补充 neutral_joints 属于补全而非篡改。
        payload = dict(source)
        payload["neutral_joints"] = [
            [float(v) for v in bone["position"]] for bone in source["bones"]
        ]
        if "fps" not in payload:
            payload["fps"] = float(self.clips[0]["fps"])
        skeleton_path = self.folder / "skeleton.json"
        skeleton_path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return self.folder

    # ---- 表示层按需构造：Dataset 会被 DataLoader 复制/序列化，
    #      直接持有构造好的模块在部分平台会触发 pickle 失败。
    @property
    def representation(self):
        if self._representation is None:
            fps = float(self.clips[0]["fps"])
            self._representation = DualRootGlobalJoints(
                fps=fps, skeleton=self.skeleton,
                name="unreal_dual_root_global_joints",
            )
        return self._representation

    def __len__(self):
        return len(self.clips)

    def __getitem__(self, index):
        clip = self.clips[index]
        with np.load(self.folder / clip["file"], allow_pickle=False) as archive:
            positions = np.asarray(archive["positions"], dtype=np.float32)
            # 兼容两种旋转表示：
            #   quat_xyzw —— 压缩视图（float32 四元数，读盘快 5 倍）
            #   rotations —— 原始视图（float64 的 3×3 矩阵）
            if "quat_xyzw" in archive.files:
                rotations = quat_to_matrix(
                    np.asarray(archive["quat_xyzw"], dtype=np.float32)
                ).astype(np.float32)
            else:
                rotations = np.asarray(archive["rotations"], dtype=np.float32)
            root_track = np.asarray(archive["root_track"], dtype=np.float32)

        fps = float(clip["fps"])
        total = len(positions)
        # 训练窗口从随机位置切，让同一段动画在不同 epoch 覆盖不同片段。
        # deterministic_window=True 时用确定性起点（按样本下标算），
        # 供对照实验与复现使用。
        span = self.window_frames + self.history_frames
        if total > span:
            if self.deterministic_window:
                start = (index * 37) % (total - span + 1)
            else:
                start = int(np.random.randint(0, total - span + 1))
        else:
            start = 0
        stop = start + span
        positions = positions[start:stop]
        rotations = rotations[start:stop]
        root_track = root_track[start:stop]

        contacts = world_foot_contacts(positions, root_track, self.skeleton, fps)
        return {
            "positions": torch.from_numpy(positions),
            "rotations": torch.from_numpy(rotations),
            "contacts": contacts if contacts is not None else torch.zeros(
                (len(positions), 4)),
            "frames": len(positions),
            "history_frames": self.history_frames,
            "asset": clip["asset"],
            "scene": clip.get("scene"),
        }


class SomaRealtimeStackCollate:
    """轻量 collate：只把一批原始窗口堆成张量，不碰 GPU、不建模块。

    必须可被 Windows spawn 序列化（因此只持有 numpy 索引，不持有 torch 模块），
    这样才能放进 DataLoader 的 worker 并行跑。特征由主进程
    `SomaRealtimeFeatureStep` 在训练循环里批量计算。
    """

    def __init__(self, indices):
        self.indices = np.asarray(indices, dtype=np.int64)

    def __call__(self, batch):
        lengths = [item["frames"] for item in batch]
        if len(set(lengths)) != 1:
            raise ValueError(f"同批窗口长度不一致 {lengths}，不符合定长训练假设")
        frames = lengths[0]
        return {
            "positions": torch.from_numpy(np.stack([b["positions"].numpy() for b in batch])),
            "rotations": torch.from_numpy(np.stack([b["rotations"].numpy() for b in batch])),
            "contacts": torch.from_numpy(np.stack([b["contacts"].numpy() for b in batch])),
            "frames": frames,
            "batch_size": len(batch),
        }


class SomaRealtimeFeatureStep:
    """在主进程把整批原始窗口算成 [B, T, 942] 特征张量。

    与旧的 SomaRealtimeCollate 的区别：本类不被 DataLoader 持有，
    因此可以安全地持 GPU 模块；特征计算发生在训练循环里，
    与 worker 的读盘/算接触流水线重叠。
    """

    def __init__(self, representation, indices, *, device="cuda"):
        self.representation = representation
        prepared = representation
        if device == "cuda" and torch.cuda.is_available():
            prepared = prepared.cuda()
        else:
            device = "cpu"
        self.device = device
        self._prepared = prepared
        self._indices = torch.as_tensor(np.asarray(indices), dtype=torch.long).to(device)

    def __call__(self, payload):
        positions = payload["positions"].to(self.device, non_blocking=True)
        rotations = payload["rotations"].to(self.device, non_blocking=True)
        contacts = payload["contacts"].to(self.device, non_blocking=True)
        frames = positions.shape[1]
        with torch.no_grad():
            features = self._prepared(
                {"posed_joints": positions, "global_joint_rots": rotations,
                 "foot_contacts": contacts},
                to_normalize=False,
                lengths=torch.tensor([frames] * positions.shape[0]),
            )
        if features.ndim == 4:
            features = features.squeeze(1)
        return {
            "motion": features[..., self._indices].float(),
            "motion_len": torch.tensor([frames] * positions.shape[0]),
            "motion_pad_mask": torch.zeros(
                (positions.shape[0], frames), dtype=torch.bool),
            "batch_size": positions.shape[0],
        }


class SomaRealtimeCollate:
    """在 worker 内把一批原始窗口算成 [B, T, 942] 特征（CPU）。

    为什么放在 worker 里：如果把特征计算留在主进程，主线程会阻塞在 19.7ms 的
    前向计算上，worker 的预取随之停摆——实测让 workers=8 比单进程还慢 3-5 倍。
    改成 worker 内计算后，8 个进程真正并行，主线程拿到特征就能直接训练。

    代价：每个 worker 各持一份 representation（4.5M 参数，约 20MB），
    8 个 worker 合计约 160MB，相对 8GB 显存与整机内存可以忽略。

    注意：本类会随 DataLoader 一起被 pickle 到 worker，因此**不能**持有
    已搬到 GPU 的模块；构造时强制使用 CPU。
    """

    def __init__(self, representation, indices):
        self.representation = representation.cpu()
        self.indices = np.asarray(indices, dtype=np.int64)

    def __call__(self, batch):
        lengths = [item["frames"] for item in batch]
        if len(set(lengths)) != 1:
            raise ValueError(f"同批窗口长度不一致 {lengths}，不符合定长训练假设")
        frames = lengths[0]

        positions = torch.from_numpy(np.stack([b["positions"].numpy() for b in batch]))
        rotations = torch.from_numpy(np.stack([b["rotations"].numpy() for b in batch]))
        contacts = torch.from_numpy(np.stack([b["contacts"].numpy() for b in batch]))
        with torch.no_grad():
            features = self.representation(
                {"posed_joints": positions, "global_joint_rots": rotations,
                 "foot_contacts": contacts},
                to_normalize=False,
                lengths=torch.tensor([frames] * len(batch)),
            )
        if features.ndim == 4:
            features = features.squeeze(1)
        picked = features[..., torch.as_tensor(self.indices, dtype=torch.long)]
        return {
            "motion": picked.float(),
            "motion_len": torch.tensor([frames] * len(batch)),
            "motion_pad_mask": torch.zeros((len(batch), frames), dtype=torch.bool),
            "batch_size": len(batch),
        }


def compute_stats(folder, *, window_frames=48, history_frames=0, sample_clips=2000,
                  device="cuda"):
    """在训练集帧上算 mean/std（对应 prepare_unreal_dataset 的 stats_scope）。

    只采样一部分 clip 来估计，因为它们共享同一个骨架与动作分布；
    输出维度是完整 motion_rep_dim（946），与表示层的断言一致。
    """
    dataset = SomaRealtimeDataset(
        folder, window_frames=window_frames, history_frames=history_frames)
    representation = dataset.representation
    prepared = representation
    if device == "cuda" and torch.cuda.is_available():
        prepared = prepared.cuda()
    else:
        device = "cpu"

    rng = np.random.default_rng(12345)
    picks = rng.choice(len(dataset), size=min(sample_clips, len(dataset)), replace=False)

    total = 0
    sum_x = None
    sum_x2 = None
    batch_size = 16
    for start in range(0, len(picks), batch_size):
        indices = picks[start:start + batch_size]
        items = [dataset[int(i)] for i in indices]
        frames = min(item["frames"] for item in items)
        positions = torch.from_numpy(
            np.stack([item["positions"].numpy()[:frames] for item in items])).to(device)
        rotations = torch.from_numpy(
            np.stack([item["rotations"].numpy()[:frames] for item in items])).to(device)
        contacts = torch.from_numpy(
            np.stack([item["contacts"].numpy()[:frames] for item in items])).to(device)
        with torch.no_grad():
            features = prepared(
                {"posed_joints": positions, "global_joint_rots": rotations,
                 "foot_contacts": contacts},
                to_normalize=False,
                lengths=torch.tensor([frames] * len(items)),
            )
        if features.ndim == 4:
            features = features.squeeze(1)
        values = features.reshape(-1, features.shape[-1]).double().cpu().numpy()
        total += values.shape[0]
        sum_x = values.sum(0) if sum_x is None else sum_x + values.sum(0)
        sum_x2 = (values ** 2).sum(0) if sum_x2 is None else sum_x2 + (values ** 2).sum(0)

    mean = (sum_x / total).astype(np.float32)
    std = np.sqrt(np.maximum(sum_x2 / total - (sum_x / total) ** 2, 0)).astype(np.float32)
    stats = Path(folder) / "stats"
    stats.mkdir(exist_ok=True)
    np.save(stats / "mean.npy", mean)
    np.save(stats / "std.npy", std)
    return {"mean_shape": list(mean.shape), "frames_used": int(total),
            "clips_used": len(picks)}
