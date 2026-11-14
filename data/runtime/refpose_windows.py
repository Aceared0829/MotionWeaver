"""Window indexing, loading and sampling for reference-guided pose data."""
import base64
import json
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import Dataset

from data.runtime.conditioned_motion import load_raw, root_local
from data.runtime.unreal_dataset import contained_file


class MotionWindows(Dataset):
    def __init__(
        self,
        release,
        contract,
        split,
        *,
        seed=42,
        scenario="random",
        include_contacts=False,
        cache_capacity=256,
        source_verified=False,
        tiny_root=None,
        mmap_root=None,
    ):
        self.contract = contract
        self.split = split
        self.seed = seed
        self.scenario = scenario
        self.include_contacts = include_contacts
        self.epoch = 0
        self.rows = []
        with (Path(release) / "contract" / "windows.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                if contract["clips"][row["clip_index"]]["split"] == split:
                    self.rows.append(row)
        if len(self.rows) != contract["window_split_counts"][split]:
            raise ValueError(f"{split} 窗口数与封版不一致")
        # 懒加载 + LRU：原实现把该 split 所有 clip 一次性读进内存
        # （20k 数据峰值 ~9.67 GB，超出可用内存后换页到机械盘，训练被拖到 6.7 小时）。
        # 改为按需读取，只保留最近 cache_capacity 条；显存/内存占用与数据规模解耦。
        self.source = Path(contract["source_dataset"])
        self.raw = OrderedDict()
        self.contacts = OrderedDict()
        self.cache_capacity = int(cache_capacity) if cache_capacity else 256
        # 注意：不再预读全部 clip，因此这里不做逐条哈希校验。
        # 完整性由契约里的 source_manifest_sha256 / skeleton_sha256 在 build 阶段保证，
        # 单条 raw_sha256 仍在 source_verified=True 时按需校验。
        self.source_verified = bool(source_verified)
        # tiny_root：预先切好的 48 帧窗口目录（每 clip 一个 npz，~135 KB）。
        # 实测：整条 clip 平均 868 帧 5.3 MB，训练只用 48 帧，浪费 94.5%；
        # 用 tiny 后每次读取 135 KB，随机读 0.18 ms（原 11.4 ms），
        # 全量 20k 条仅 2.76 GB，可全部驻留内存，磁盘 IO 归零。
        self.tiny_root = Path(tiny_root) if tiny_root else None
        if self.tiny_root is not None and not self.tiny_root.is_dir():
            raise FileNotFoundError(f"tiny_root 不存在：{self.tiny_root}")
        # mmap_root：每 clip 一个目录，内含 p/r/k/c.npy（整条 clip 全部帧）。
        # 与 tiny_root 的区别：tiny 只存前 48 帧（97.1% 窗口会取错），
        # mmap 存全部帧但用内存映射按需读取，随机窗口读取 1.33 ms（npz 解压 3.75 ms）。
        # 不占常驻内存（靠 OS page cache），也不受 LRU 容量限制。
        self.mmap_root = Path(mmap_root) if mmap_root else None
        if self.mmap_root is not None and not self.mmap_root.is_dir():
            raise FileNotFoundError(f"mmap_root 不存在：{self.mmap_root}")
        stats = contract["stats"]["pose_m"]
        self.mean = np.load(Path(release) / "contract" / "stats" / stats["mean"]).astype(np.float32)
        self.std = np.load(Path(release) / "contract" / "stats" / stats["std"]).astype(np.float32)

    def __len__(self):
        return len(self.rows)

    def _ensure_loaded(self, index):
        """按需加载一条 clip；LRU 淘汰最久未用的。mmap_root 存在时优先读内存映射。"""
        if index in self.raw:
            # 只有 OrderedDict 支持 move_to_end；测试会用 object.__new__ 绕过 __init__
            # 直接塞普通 dict，这里不能假定容器类型。
            if hasattr(self.raw, "move_to_end"):
                self.raw.move_to_end(index)
            return
        clip = self.contract["clips"][index]

        if self.mmap_root is not None:
            # 目录名必须来自契约里的路径字段，不能用契约数组下标 index。
            # 20k 那次两者恰好相同（契约按原始顺序建、目录也按原始顺序命名），
            # 但全量 71k 的 clips 顺序与 mmap 目录命名顺序不一致，用 index 会读到别的 clip 甚至 KeyError。
            # 新版契约为 raw_file（NPZ）与 mmap_file（mmap 目录）两个字段；
            # 旧版契约只有 raw_file 且就写成目录名，所以这里要回落到它。
            md = self.mmap_root / str(clip.get("mmap_file", clip["raw_file"]))
            if md.is_dir():
                pos = np.load(md / "p.npy", mmap_mode="r")
                rot = np.load(md / "r.npy", mmap_mode="r")
                root = np.load(md / "k.npy", mmap_mode="r")
                cf = md / "c.npy"
                con = np.load(cf, mmap_mode="r") if cf.is_file() else None
                self.raw[index] = (pos, rot, root)
                # 接触：mmap 目录里通常没有 c.npy（全量转换未落盘接触），
                # 此时必须回落到契约里的 contact_bits，否则 self.contacts 为空，
                # __getitem__ 会抛 KeyError。
                if self.include_contacts:
                    if con is None:
                        packed = np.frombuffer(base64.b64decode(clip["contact_bits"]), dtype=np.uint8)
                        con = np.unpackbits(packed)[: clip["source_frames"] * 4].reshape(
                            clip["source_frames"], 4
                        ).astype(np.float32)
                    self.contacts[index] = con
                while len(self.raw) > self.cache_capacity:
                    old_key, _ = self.raw.popitem(last=False)
                    self.contacts.pop(old_key, None)
                return

        if self.tiny_root is not None:
            tf = self.tiny_root / f"{index:06d}.npz"
            if tf.is_file():
                with np.load(tf, allow_pickle=False) as a:
                    pos = np.asarray(a["p"], dtype=np.float32)
                    rot = np.asarray(a["r"], dtype=np.float32)
                    root = np.asarray(a["k"], dtype=np.float32)
                    con = np.asarray(a["c"], dtype=np.float32) if "c" in a.files else None
                self.raw[index] = (pos, rot, root)
                if self.include_contacts and con is not None:
                    self.contacts[index] = con
                while len(self.raw) > self.cache_capacity:
                    old_key, _ = self.raw.popitem(last=False)
                    self.contacts.pop(old_key, None)
                return

        # 没有 mmap_root 时回落到逐条 NPZ。raw_file 就是 NPZ 路径；
        # 新版构建器另写了 raw_npz 作为同义冗余，优先用它（更明确），老契约没有就回落到 raw_file。
        npz_rel = clip.get("raw_npz", clip["raw_file"])
        raw, _ = load_raw(
            contained_file(self.source, npz_rel),
            clip["source_frames"],
            self.contract["bone_count"],
            self.contract["fps"],
            expected_sha256=clip["raw_sha256"] if self.source_verified else None,
        )
        self.raw[index] = (
            raw["positions"],
            raw["rotations"][..., :2].reshape(
                clip["source_frames"], self.contract["bone_count"], 6
            ).copy(),
            raw["root_track"],
        )
        if self.include_contacts:
            packed = np.frombuffer(base64.b64decode(clip["contact_bits"]), dtype=np.uint8)
            self.contacts[index] = np.unpackbits(packed)[: clip["source_frames"] * 4].reshape(
                clip["source_frames"], 4
            ).astype(np.float32)
        while len(self.raw) > self.cache_capacity:
            old_key, _ = self.raw.popitem(last=False)
            self.contacts.pop(old_key, None)

    def __getitem__(self, item):
        row = self.rows[item]
        self._ensure_loaded(row["clip_index"])
        positions, rotations, root = self.raw[row["clip_index"]]
        start = row["start"]
        middle, end = start + 24, start + 48
        origin = root[middle - 1]
        pose = (positions[start:end] - self.mean) / self.std
        combined = np.concatenate((pose, rotations[start:end]), axis=-1).reshape(48, -1).astype(np.float32)
        history_root = root_local(root[start:middle], origin)
        future_root = root_local(root[middle:end], origin)
        history = np.concatenate((combined[:24], history_root), axis=-1)
        target = combined[24:]
        if self.scenario in ("random", "diverse", "hybrid"):
            rng = np.random.default_rng(self.seed + self.epoch * 1000003 + item)
            legacy = self.scenario == "random" or (
                self.scenario == "hybrid" and rng.random() < 0.5
            )
            if legacy:
                offsets = ((), (12,), (23,), (12, 23))[int(rng.integers(4))]
            else:
                count = int(rng.choice(4, p=(0.25, 0.35, 0.35, 0.05)))
                offsets = tuple(sorted(rng.choice(24, count, replace=False).tolist()))
        elif self.scenario == "one_ref":
            offsets = (12,)
        elif self.scenario == "two_refs":
            offsets = (12, 23)
        elif self.scenario == "end_ref":
            # 只有未来窗口末帧（第 23 帧）是硬参考，对应封盘四档里的 end_reference。
            offsets = (23,)
        else:
            offsets = ()
        mask = np.zeros((24, 1), dtype=np.float32)
        mask[list(offsets)] = 1
        reference = target * mask
        result = (
            torch.from_numpy(history),
            torch.from_numpy(future_root),
            torch.from_numpy(reference),
            torch.from_numpy(mask),
            torch.from_numpy(target),
        )
        if self.include_contacts:
            result += (torch.from_numpy(self.contacts[row["clip_index"]][middle:end]),)
        return result


def balanced_window_weights(data, category_power=0.5, clip_power=0.5):
    """Weight existing windows without moving clips across the frozen split."""
    if not 0 <= category_power <= 1 or not 0 <= clip_power <= 1:
        raise ValueError("sampling powers must be in [0, 1]")
    categories = Counter(
        data.contract["clips"][row["clip_index"]]["category"] for row in data.rows
    )
    clips = Counter(row["clip_index"] for row in data.rows)
    weights = [
        categories[data.contract["clips"][row["clip_index"]]["category"]] ** -category_power
        * clips[row["clip_index"]] ** -clip_power
        for row in data.rows
    ]
    return torch.tensor(weights, dtype=torch.double)


class ClipShuffleSampler(torch.utils.data.Sampler):
    """按 clip 分块打乱窗口，让相邻样本落在同一条动作上。

    为什么需要它：``MotionWindows._ensure_loaded`` 每次换 clip 都要 open 三个
    .npy（实测 1.5–4 ms，其中 99% 是 np.load 的冷元数据开销，真正读 48 行只要
    0.18 ms）。全量 71,128 条 clip 摊到约 35 个窗口/条，默认 shuffle 下每个样本
    都换 clip，LRU 命中率≈0，数据管线 700–1,000 samples/s，GPU 只有 3–21% 利用率
    （实测 batch 256、8 worker：388 ms/步，其中 196 ms 在等数据）。

    本采样器把 clip 分成 ``chunk`` 个一组：组间顺序随机、组内所有窗口再整体打乱。
    一个 chunk 覆盖 chunk×35 个窗口，而 LRU 容量 256 条，组合内命中率接近 1。
    实测同参数下 33–39 ms/步（约 8,000 samples/s），快 10 倍以上。

    语义与 ``shuffle=True`` 一致：一个 epoch 仍恰好遍历每个窗口一次（无放回、
    无重复、无遗漏），变的只是遍历顺序。参考帧采样与数据增强都由
    ``seed + epoch*1000003 + item`` 决定，与遍历顺序无关，因此换采样器不改变
    单样本内容，只改变 mini-batch 的组合。
    """

    def __init__(self, rows, chunk=16, seed=0):
        if chunk < 1:
            raise ValueError(f"chunk 必须为正整数：{chunk}")
        groups = {}
        for index, row in enumerate(rows):
            groups.setdefault(row["clip_index"], []).append(index)
        # 按 clip_index 排序，保证同一份契约在任意机器上分组顺序一致。
        self.groups = [np.asarray(groups[key], dtype=np.int64) for key in sorted(groups)]
        self.chunk = int(chunk)
        self.seed = int(seed)
        self.epoch = 0
        self._length = sum(len(group) for group in self.groups)

    def set_epoch(self, epoch):
        self.epoch = int(epoch)

    def __iter__(self):
        rng = np.random.default_rng(self.seed + self.epoch * 100003)
        order = rng.permutation(len(self.groups))
        for start in range(0, len(order), self.chunk):
            block = order[start:start + self.chunk]
            windows = np.concatenate([self.groups[int(i)] for i in block])
            yield from windows[rng.permutation(len(windows))].tolist()

    def __len__(self):
        return self._length


def select_windows(index_file, contract, split, per_clip):
    grouped = defaultdict(list)
    with Path(index_file).open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if contract["clips"][row["clip_index"]]["split"] == split:
                grouped[row["clip_index"]].append(row)
    if not grouped:
        raise ValueError(f"分区 {split} 没有合法未来窗口")
    return [
        row
        for index in sorted(grouped)
        for row in (
            grouped[index]
            if per_clip == 0
            else [grouped[index][len(grouped[index]) // 2]]
        )
    ]
