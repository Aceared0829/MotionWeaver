"""训练独立的未来 Root 头。不改姿态网，也不覆盖 refpose_v3_71k。

输入只有过去 24 帧的 history。输出是未来 24 帧的 root_plan（位置 3 + xyzw 四元数 4）。
未来真值 Root 只当标签，不喂进网络。
"""

import argparse
import json
import math
import queue
import random
import sys
import threading
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, IterableDataset, Sampler, get_worker_info

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "inference" / "visualization"))

import paths

from training.pretrain.train_conditioned_pose import MotionWindows


class ClipWindowSampler(Sampler[int]):
    """先打乱 clip，再打乱这条 clip 里的窗口。避免每个样本都随机换一条动作、把盘打成冷读。"""

    def __init__(self, rows, seed):
        groups = {}
        for index, row in enumerate(rows):
            groups.setdefault(row["clip_index"], []).append(index)
        self.groups = list(groups.values())
        self.seed = seed
        self.epoch = 0
        self._length = sum(len(group) for group in self.groups)

    def __iter__(self):
        rng = np.random.default_rng(self.seed + self.epoch * 100003)
        for clip_index in rng.permutation(len(self.groups)):
            windows = np.array(self.groups[int(clip_index)], dtype=np.int64)
            rng.shuffle(windows)
            yield from windows.tolist()

    def __len__(self):
        return self._length


class ClipPackIterable(IterableDataset):
    """每个 worker 一次读完整条 clip，只交回两块堆好的张量。"""

    def __init__(self, base, seed):
        super().__init__()
        self.base = base
        self.seed = seed
        self.epoch = 0
        groups = {}
        for index, row in enumerate(base.rows):
            groups.setdefault(row["clip_index"], []).append(index)
        self.groups = list(groups.values())

    def __iter__(self):
        info = get_worker_info()
        groups = self.groups
        worker_id = 0
        if info is not None:
            groups = groups[info.id::info.num_workers]
            worker_id = info.id
        rng = np.random.default_rng(self.seed + self.epoch * 100003 + worker_id)
        for group_index in rng.permutation(len(groups)):
            windows = np.array(groups[int(group_index)], dtype=np.int64)
            rng.shuffle(windows)
            histories = []
            targets = []
            for item in windows.tolist():
                sample = self.base[int(item)]
                histories.append(sample[0])
                targets.append(sample[1])
            yield torch.stack(histories), torch.stack(targets)


def clip_batches(dataset, batch_size, workers, pin_memory):
    loader = DataLoader(
        dataset, batch_size=None, num_workers=workers,
        pin_memory=pin_memory, persistent_workers=False)
    held_history = []
    held_target = []
    held_count = 0
    for history, target in loader:
        held_history.append(history)
        held_target.append(target)
        held_count += history.shape[0]
        while held_count >= batch_size:
            history_all = torch.cat(held_history, 0)
            target_all = torch.cat(held_target, 0)
            yield history_all[:batch_size], target_all[:batch_size]
            rest_history = history_all[batch_size:]
            rest_target = target_all[batch_size:]
            if rest_history.shape[0]:
                held_history = [rest_history]
                held_target = [rest_target]
                held_count = rest_history.shape[0]
            else:
                held_history = []
                held_target = []
                held_count = 0


class CpuPrefetch:
    def __init__(self, source):
        self.queue = queue.Queue(maxsize=2)
        self.thread = threading.Thread(target=self._fill, args=(source,), daemon=True)
        self.thread.start()

    def _fill(self, source):
        try:
            for item in source:
                self.queue.put(item)
        finally:
            self.queue.put(None)

    def __iter__(self):
        while True:
            item = self.queue.get()
            if item is None:
                break
            yield item


def load_contract(release):
    release = Path(release).resolve()
    contract_path = release / "contract" / "contract.json"
    if not contract_path.is_file():
        alt = release / "contract.json"
        if alt.is_file():
            contract_path = alt
            release = release.parent
        else:
            raise FileNotFoundError(f"找不到 contract.json：{contract_path}")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    window = contract["window"]
    if window["history_frames"] != 24 or window["future_frames"] != 24:
        raise ValueError(f"需要 24+24 帧契约，当前 {window}")
    return release, contract


class RootPlanHead(nn.Module):
    """从历史身体和历史 Root 预测未来 24 帧 Root。不输出骨骼。"""

    def __init__(self, width=256):
        super().__init__()
        self.width = width
        self.history = nn.GRU(709, width, batch_first=True)
        self.time = nn.Parameter(torch.zeros(1, 24, width))
        layer = nn.TransformerEncoderLayer(
            width, 4, width * 3, dropout=0.1, batch_first=True, norm_first=True)
        self.temporal = nn.TransformerEncoder(layer, 3)
        self.output = nn.Sequential(nn.LayerNorm(width), nn.Linear(width, 7))
        nn.init.zeros_(self.output[-1].weight)
        nn.init.zeros_(self.output[-1].bias)
        with torch.no_grad():
            self.output[-1].bias[6] = 1.0

    def forward(self, history):
        _, state = self.history(history)
        hidden = state[-1].unsqueeze(1) + self.time
        raw = self.output(self.temporal(hidden))
        position = raw[..., :3]
        quaternion = raw[..., 3:]
        quaternion = quaternion / quaternion.norm(dim=-1, keepdim=True).clamp_min(1e-6)
        return torch.cat((position, quaternion), dim=-1)


def root_loss(predicted, target):
    position = (predicted[..., :3] - target[..., :3]).square().mean()
    dot = (predicted[..., 3:] * target[..., 3:]).sum(dim=-1).abs().clamp(max=1.0)
    rotation = (1.0 - dot.square()).mean()
    velocity = (predicted[:, 1:, :3] - predicted[:, :-1, :3]
                - target[:, 1:, :3] + target[:, :-1, :3]).square().mean()
    total = 10.0 * position + rotation + 2.0 * velocity
    return total, position, rotation


@torch.no_grad()
def validate(model, loader, device, max_batches):
    model.eval()
    pos_sq = 0.0
    count = 0
    angle = 0.0
    angle_count = 0
    for index, batch in enumerate(loader):
        if max_batches and index >= max_batches:
            break
        history = batch[0].to(device, non_blocking=True)
        target = batch[1].to(device, non_blocking=True)
        with torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=device.type == "cuda"):
            predicted = model(history)
        predicted = predicted.float()
        target = target.float()
        delta = predicted[..., :3] - target[..., :3]
        pos_sq += float(delta.square().sum())
        count += delta[..., 0].numel()
        dot = (predicted[..., 3:] * target[..., 3:]).sum(dim=-1).abs().clamp(max=1.0)
        angle += float(torch.rad2deg(2.0 * torch.acos(dot)).sum())
        angle_count += dot.numel()
    rmse_cm = math.sqrt(pos_sq / max(count, 1)) * 100.0
    angle_deg = angle / max(angle_count, 1)
    return rmse_cm, angle_deg


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True)
    parser.add_argument("--mmap-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--width", type=int, default=256)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--log-every", type=int, default=500)
    parser.add_argument("--max-steps", type=int, default=0, help="大于 0 时只跑这么多步，用于短跑")
    parser.add_argument("--max-val-batches", type=int, default=0)
    args = parser.parse_args()

    output = Path(args.output).resolve()
    if output.exists():
        raise FileExistsError(f"输出目录已存在，不覆盖：{output}")
    output.mkdir(parents=True)
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type != "cuda" and args.max_steps == 0:
        raise RuntimeError("全量训练要求 CUDA")

    release, contract = load_contract(args.release)
    mmap_root = Path(args.mmap_root)
    train_set = MotionWindows(release, contract, "train", seed=args.seed, scenario="random",
                              include_contacts=False, mmap_root=mmap_root)
    val_set = MotionWindows(release, contract, "validation", seed=args.seed, scenario="random",
                            include_contacts=False, mmap_root=mmap_root)
    train_sampler = ClipWindowSampler(train_set.rows, args.seed)
    train_loader = DataLoader(
        train_set, batch_size=args.batch_size, sampler=train_sampler, num_workers=args.workers,
        drop_last=True, pin_memory=device.type == "cuda",
        persistent_workers=args.workers > 0, prefetch_factor=(4 if args.workers > 0 else None))
    steps_per_epoch = max(1, len(train_loader))
    val_loader = DataLoader(
        val_set, batch_size=args.batch_size, shuffle=False, num_workers=args.workers,
        pin_memory=device.type == "cuda", persistent_workers=args.workers > 0,
        prefetch_factor=(4 if args.workers > 0 else None))

    model = RootPlanHead(args.width).to(device)
    params = sum(p.numel() for p in model.parameters())
    config = {
        "role": "root_plan_head",
        "pose_weights": str(paths.POSE_WEIGHT),
        "does_not_replace_pose": True,
        "input": "history[24,709]",
        "output": "root_plan[24,7]",
        "epochs": args.epochs,
        "batch_size": args.batch_size,
        "width": args.width,
        "lr": args.lr,
        "seed": args.seed,
        "params": params,
        "train_windows": len(train_set),
        "val_windows": len(val_set),
        "loader": "clip_window_sampler",
    }
    (output / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    print(f"RootPlanHead {params/1e6:.2f}M 参数  device={device}  train={len(train_set)} val={len(val_set)}", flush=True)

    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs * steps_per_epoch)
    best = float("inf")
    history_log = []
    started = time.perf_counter()
    step = 0
    stop = False
    for epoch in range(args.epochs):
        train_set.epoch = epoch
        train_sampler.epoch = epoch
        model.train()
        running = 0.0
        seen = 0
        for batch in train_loader:
            history = batch[0].to(device, non_blocking=True)
            target = batch[1].to(device, non_blocking=True)
            with torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=device.type == "cuda"):
                predicted = model(history)
            total, position, rotation = root_loss(predicted.float(), target.float())
            if not torch.isfinite(total):
                raise ValueError(f"非有限损失 epoch={epoch} step={step}")
            optimizer.zero_grad(set_to_none=True)
            total.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            running += float(total.detach())
            seen += 1
            step += 1
            if step % args.log_every == 0:
                rmse_cm = math.sqrt(float(position)) * 100.0
                print(f"ep{epoch} step{step} loss={running/seen:.5f} pos_rmse_cm={rmse_cm:.2f} "
                      f"rot={float(rotation):.5f} lr={scheduler.get_last_lr()[0]:.2e} "
                      f"{time.perf_counter()-started:.0f}s", flush=True)
                running = 0.0
                seen = 0
            if args.max_steps and step >= args.max_steps:
                stop = True
                break
        rmse_cm, angle_deg = validate(model, val_loader, device, args.max_val_batches)
        print(f"== epoch {epoch} 验证 Root 位置 RMSE {rmse_cm:.2f} cm  朝向 {angle_deg:.2f} deg", flush=True)
        history_log.append({"epoch": epoch, "val_root_rmse_cm": rmse_cm, "val_heading_deg": angle_deg})
        checkpoint = {
            "model": model.state_dict(),
            "class": "RootPlanHead",
            "width": args.width,
            "epoch": epoch,
            "val_root_rmse_cm": rmse_cm,
            "val_heading_deg": angle_deg,
            "input": "history[24,709]",
            "output": "root_plan[24,7]",
            "args": vars(args),
        }
        torch.save(checkpoint, output / "last.pt")
        if rmse_cm < best:
            best = rmse_cm
            torch.save(checkpoint, output / "best.pt")
            print(f"   -> 新最优 {best:.2f} cm", flush=True)
        (output / "history.json").write_text(json.dumps(history_log, indent=2), encoding="utf-8")
        if stop:
            break
    print(f"完成 最优 {best:.2f} cm  用时 {time.perf_counter()-started:.0f}s  权重 {output / 'best.pt'}", flush=True)


if __name__ == "__main__":
    main()
