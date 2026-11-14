"""训练吞吐基准：把「取数」和「GPU 计算」分开计时，给出可复现的 step/s 与 epoch 预估。

为什么单独写：
  train_reference_pose.py 只打印累计秒数，看不出瓶颈在哪一段；而本机曾经的坑正是
  「读盘 1.33 ms/窗口、GPU 只有 3–8% 利用率」，需要精确知道时间花在 DataLoader 还是前反向。

测三段：
  1) data-only   —— 只迭代 DataLoader，不碰模型，得到窗口/秒（纯 CPU + 磁盘）
  2) gpu-only    —— 固定一个 batch 反复前反向，得到单步 GPU 毫秒（不含取数）
  3) end-to-end  —— 真实训练循环，得到可用的 step/s

用法：
  .venv/bin/python tools/bench_training_throughput.py \
      --release /home/acear/MotionWeaverData/full_release_v2 \
      --mmap-root /home/acear/MotionWeaverData/full_mmap_v2 \
      --batch-sizes 64,128,256 --workers 0,4,8 --steps 60

  # 想看限流后的速度就套一层配额：
  tools/run_capped.sh --quota 800% -- .venv/bin/python tools/bench_training_throughput.py --help
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from training.pretrain.train_conditioned_pose import (   # noqa: E402
    ClipShuffleSampler, MotionWindows, ReferenceGuidedPose, losses,
)


def load_contract(release):
    release = Path(release).resolve()
    path = release / "contract" / "contract.json"
    if not path.is_file():
        raise FileNotFoundError(f"找不到 contract.json：{path}")
    return release, json.loads(path.read_text(encoding="utf-8"))


def make_loader(data, batch_size, workers, chunk=0, seed=42):
    """chunk>0 时用生产同款 ClipShuffleSampler，chunk=0 时退回全局 shuffle。

    两者必须能对比：全量 71k 下全局 shuffle 每个样本都换 clip，
    而生产训练走 chunk 采样，只测 shuffle 会低估真实速度 4–8 倍。
    """
    if chunk > 0:
        sampler = ClipShuffleSampler(data.rows, chunk=chunk, seed=seed)
        return DataLoader(data, batch_size=batch_size, sampler=sampler,
                          num_workers=workers, drop_last=True, pin_memory=True,
                          persistent_workers=workers > 0,
                          prefetch_factor=(4 if workers > 0 else None))
    return DataLoader(data, batch_size=batch_size, shuffle=True,
                      num_workers=workers, drop_last=True, pin_memory=True,
                      persistent_workers=workers > 0,
                      prefetch_factor=(4 if workers > 0 else None))


def move_batch(batch, device):
    return [x.to(device, non_blocking=True) for x in batch]


def bench_data(loader, steps, warmup=3):
    it = iter(loader)
    for _ in range(warmup):
        next(it)
    del it
    it = iter(loader)
    t0 = time.perf_counter()
    n = 0
    for _ in range(steps):
        batch = next(it)
        n += len(batch[0])
    elapsed = time.perf_counter() - t0
    return n / max(elapsed, 1e-9), elapsed, n


def bench_gpu(model, opt, batch, device, reps, warmup=5):
    history, root, reference, mask, target = move_batch(batch[:5], device)
    pose_std = torch.ones(model.bones, 3, device=device)
    def one_step():
        with torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=device.type == "cuda"):
            pred = model(history, root, reference, mask)
        total, _, _ = losses(pred.float(), target.float(), mask, pose_std)
        opt.zero_grad(set_to_none=True)
        total.backward()
        opt.step()
    for _ in range(warmup):
        one_step()
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    for _ in range(reps):
        one_step()
    torch.cuda.synchronize()
    return (time.perf_counter() - t0) / reps


def bench_end_to_end(model, opt, loader, device, steps, warmup=5, contacts=False, foot_indices=None,
                     pose_std=None, pose_mean=None, contact_weight=0.0):
    it = iter(loader)
    def one(batch):
        parts = move_batch(batch, device)
        history, root, reference, mask, target = parts[:5]
        con = parts[5] if len(parts) > 5 else None
        with torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=device.type == "cuda"):
            pred = model(history, root, reference, mask)
        total, _, _ = losses(pred.float(), target.float(), mask, pose_std, pose_mean=pose_mean,
                             root_plan=root, contacts=con, foot_indices=foot_indices,
                             contact_weight=contact_weight)
        opt.zero_grad(set_to_none=True)
        total.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
    for _ in range(warmup):
        one(next(it))
    torch.cuda.synchronize()
    per_step = []
    for _ in range(steps):
        t = time.perf_counter()
        one(next(it))
        torch.cuda.synchronize()
        per_step.append(time.perf_counter() - t)
    return per_step


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--release", required=True)
    ap.add_argument("--mmap-root", required=True)
    ap.add_argument("--batch-sizes", default="256")
    ap.add_argument("--workers", default="8")
    ap.add_argument("--chunk-size", type=int, default=16,
                    help="按 clip 分块打乱的粒度，与生产训练一致；0 表示全局 shuffle（每个样本换 clip）")
    ap.add_argument("--steps", type=int, default=60, help="每档采样的训练步数（不含 warmup）")
    ap.add_argument("--data-steps", type=int, default=60, help="纯取数采样的批数")
    ap.add_argument("--width", type=int, default=256)
    ap.add_argument("--contact-weight", type=float, default=0.4)
    ap.add_argument("--split", default="train")
    ap.add_argument("--skip-data", action="store_true", help="跳过纯取数测量")
    ap.add_argument("--data-only", action="store_true",
                    help="只测取数，不建模型、不占 GPU（可与正在跑的生产训练并行测）")
    ap.add_argument("--skip-gpu", action="store_true", help="跳过纯 GPU 测量")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    release, contract = load_contract(args.release)
    use_contacts = args.contact_weight > 0

    foot_indices = None
    if use_contacts:
        skel_path = Path(contract["source_dataset"]) / "skeleton.json"
        if not skel_path.is_file():
            skel_path = Path("/home/acear/MotionWeaverData/full-train-v1/skeleton.json")
        skel = json.loads(skel_path.read_text(encoding="utf-8"))
        names = [b["name"] for b in skel["bones"]]
        roles = skel["roles"]
        foot_indices = [names.index(roles[r]) for r in
                        ("left_foot", "left_toe", "right_foot", "right_toe")]

    data = MotionWindows(release, contract, args.split, seed=42, scenario="random",
                         include_contacts=use_contacts, mmap_root=Path(args.mmap_root))
    stats = contract["stats"]["pose_m"]
    pose_std_np = np.load(release / "contract" / "stats" / stats["std"]).astype(np.float32)
    pose_mean_np = np.load(release / "contract" / "stats" / stats["mean"]).astype(np.float32)
    pose_std = torch.from_numpy(pose_std_np).to(device)
    pose_mean = torch.from_numpy(pose_mean_np).to(device)
    total_windows = len(data)
    print(json.dumps({"stage": "setup", "device": str(device), "torch": torch.__version__,
                      "torch_threads": torch.get_num_threads(),
                      "split": args.split, "windows": total_windows,
                      "contacts": use_contacts, "foot_indices": foot_indices}), flush=True)

    rows = []
    for batch_size in [int(x) for x in args.batch_sizes.split(",")]:
        for workers in [int(x) for x in args.workers.split(",")]:
            loader = make_loader(data, batch_size, workers, chunk=args.chunk_size)
            record = {"batch_size": batch_size, "workers": workers, "chunk": args.chunk_size}
            if not args.skip_data:
                rate, elapsed, n = bench_data(loader, args.data_steps)
                record["data_samples_per_s"] = round(rate, 1)
                record["data_batches"] = args.data_steps
                record["data_seconds"] = round(elapsed, 2)
                record["epoch_minutes"] = round(total_windows / max(rate, 1e-9) / 60, 1)
            del loader
            if args.data_only:
                print(json.dumps(record), flush=True)
                rows.append(record)
                continue

            loader = make_loader(data, batch_size, workers, chunk=args.chunk_size)
            it = iter(loader)
            first_batch = next(it)
            del it

            model = ReferenceGuidedPose(contract["bone_count"], args.width).to(device)
            opt = torch.optim.AdamW(model.parameters(), lr=3e-4, weight_decay=0.01)

            if not args.skip_gpu:
                gpu_s = bench_gpu(model, opt, first_batch, device, reps=20)
                record["gpu_ms_per_step"] = round(gpu_s * 1000, 2)

            per_step = bench_end_to_end(model, opt, loader, device, args.steps,
                                        contacts=use_contacts, foot_indices=foot_indices,
                                        pose_std=pose_std, pose_mean=pose_mean,
                                        contact_weight=args.contact_weight)
            sps = len(per_step) / sum(per_step)
            record["step_ms_median"] = round(statistics.median(per_step) * 1000, 1)
            record["step_ms_p90"] = round(sorted(per_step)[int(len(per_step) * 0.9)] * 1000, 1)
            record["steps_per_s"] = round(sps, 3)
            record["windows_per_s"] = round(sps * batch_size, 0)
            record["epoch_minutes"] = round(total_windows / (sps * batch_size) / 60, 1)
            if not args.skip_gpu:
                record["loader_share_pct"] = round(
                    max(0.0, (record["step_ms_median"] - record["gpu_ms_per_step"])
                        / record["step_ms_median"]) * 100, 1)
            print(json.dumps(record), flush=True)
            rows.append(record)
            del loader, model, opt
            torch.cuda.empty_cache()

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"结果已写入 {args.json_out}", flush=True)


if __name__ == "__main__":
    main()
