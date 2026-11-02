
"""轻量训练入口：直接用冻结契约数据训练 ReferenceGuidedPose，跳过 audit/review/freeze 流程。

为什么单独写：
  官方 train_conditioned_pose.py 的 verify_release 要求一整套封版发布链
  （audit 报告 + 人工审核 sqlite + freeze_manifest），那是为重资产论文流程设计的。
  本脚本复用同一份 MotionWindows（数据切分逻辑）和同一个 ReferenceGuidedPose（模型）
  与 losses（损失），只把「封版校验」换成「契约哈希校验」，因此训练与评测口径不变。

用法：
  python tools/train_reference_pose.py --release <契约目录> --output <输出目录>
"""
import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader

sys.path.insert(0, r"D:\AILocomotonSystem\MotionWeaver")

from training.pretrain.train_conditioned_pose import (   # noqa: E402
    MotionWindows, ReferenceGuidedPose, losses, balanced_window_weights,
)


def load_contract(release):
    """只做契约哈希与结构校验，不做封版清单/审核队列校验。"""
    release = Path(release).resolve()
    contract_path = release / "contract" / "contract.json"
    if not contract_path.is_file():
        # 兼容 build_soma_contract 的 fps120 子目录布局
        alt = release / "contract.json"
        if alt.is_file():
            contract_path = alt
            release = release.parent
        else:
            raise FileNotFoundError(f"找不到 contract.json：{contract_path}")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    w = contract["window"]
    if w["history_frames"] != 24 or w["future_frames"] != 24:
        raise ValueError(f"需要 24+24 帧契约，当前 {w}")
    return release, contract


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--release", required=True, help="契约目录（含 contract/contract.json 或 contract.json）")
    ap.add_argument("--output", required=True)
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--width", type=int, default=256)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--log-every", type=int, default=50)
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--contact-weight", type=float, default=0.0,
                    help="接触/脚滑损失权重；>0 时开启。需要数据侧 include_contacts=True")
    ap.add_argument("--bone-json", default=None,
                    help="skeleton.json 路径（含 roles），用于定位脚部关节；不传则从契约目录推断")
    ap.add_argument("--mmap-root", default=None,
                    help="内存映射数据目录（每 clip 一个子目录，含 p/r/k/c.npy）。"
                         "开启后随机窗口读取 1.33 ms（npz 解压 3.75 ms），且不占常驻内存")
    args = ap.parse_args()

    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=True)

    random.seed(args.seed); np.random.seed(args.seed); torch.manual_seed(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    release, contract = load_contract(args.release)
    print(f"契约: {release}")
    print(f"clips={len(contract['clips'])} windows={contract.get('window_count', '?')} "
          f"split={contract['split_counts']}")

    use_contacts = args.contact_weight > 0
    if use_contacts:
        # 接触损失需要 foot_indices，从 skeleton.json 的 roles 解析
        bone_json = Path(args.bone_json) if args.bone_json else release.parent / "skeleton.json"
        if not bone_json.is_file():
            # 契约里的 source_dataset 下通常有 skeleton.json
            bone_json = Path(contract["source_dataset"]) / "skeleton.json"
        skel = json.loads(bone_json.read_text(encoding="utf-8"))
        names = [b["name"] for b in skel["bones"]]
        roles = skel["roles"]
        foot_indices = [names.index(roles[r]) for r in
                        ("left_foot", "left_toe", "right_foot", "right_toe")]
        print(f"接触约束开启  weight={args.contact_weight}  脚部关节={foot_indices}  ({bone_json})")
    else:
        foot_indices = None

    mmap_root = Path(args.mmap_root) if args.mmap_root else None
    if mmap_root is not None:
        if not mmap_root.is_dir():
            raise FileNotFoundError(f"--mmap-root 不存在：{mmap_root}")
        n_sub = sum(1 for p in mmap_root.iterdir() if p.is_dir())
        print(f"mmap 数据：{mmap_root}（{n_sub} 条 clip）")

    train_set = MotionWindows(release, contract, "train", seed=args.seed,
                              scenario="random", include_contacts=use_contacts,
                              mmap_root=mmap_root)
    val_set = MotionWindows(release, contract, "validation", seed=args.seed,
                            scenario="random", include_contacts=use_contacts,
                            mmap_root=mmap_root)
    print(f"train 窗口={len(train_set)}  val 窗口={len(val_set)}")

    # pin_memory=True 是必需的，不是可选项。
    # 实测（batch 256, workers 8）：不开时 H2D 拷贝 53.0 ms/步、占总耗时 61%；
    # 开启后降到 1.8 ms/步，整体 74.6 -> 39.5 ms，提速 1.89x。
    # 原因：x.to(device, non_blocking=True) 只有在源内存被 pin 住时才真正异步，
    # 否则每次都走同步 pageable 拷贝。
    train_loader = DataLoader(train_set, batch_size=args.batch_size, shuffle=True,
                              num_workers=args.workers, drop_last=True,
                              pin_memory=True,
                              persistent_workers=args.workers > 0,
                              prefetch_factor=(4 if args.workers > 0 else None))
    val_loader = DataLoader(val_set, batch_size=args.batch_size, shuffle=False,
                            num_workers=args.workers, pin_memory=True,
                            persistent_workers=args.workers > 0,
                            prefetch_factor=(4 if args.workers > 0 else None))

    bones = contract["bone_count"]
    model = ReferenceGuidedPose(bones, args.width).to(device)
    n_param = sum(p.numel() for p in model.parameters())
    print(f"模型: ReferenceGuidedPose  bones={bones} width={args.width}  {n_param/1e6:.2f}M 参数")

    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.epochs * max(1, len(train_loader)))
    pose_std = torch.from_numpy(train_set.std).float().to(device)
    pose_mean = torch.from_numpy(train_set.mean).float().to(device)

    best = float("inf")
    history = []
    t0 = time.perf_counter()
    step = 0

    for epoch in range(args.epochs):
        train_set.epoch = epoch
        model.train()
        run = 0.0; nb = 0
        for batch in train_loader:
            if use_contacts:
                (history_b, root, reference, mask, target,
                 contacts) = (x.to(device, non_blocking=True) for x in batch)
            else:
                history_b, root, reference, mask, target = (x.to(device, non_blocking=True) for x in batch)
                contacts = None
            with torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=device.type == "cuda"):
                pred = model(history_b, root, reference, mask)
            pred = pred.float(); target_f = target.float()
            total, pos, rot = losses(
                pred, target_f, mask, pose_std, pose_mean=pose_mean, root_plan=root,
                contacts=contacts, foot_indices=foot_indices,
                contact_weight=(args.contact_weight if use_contacts else 0.0))
            opt.zero_grad(set_to_none=True)
            total.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step(); sched.step()
            run += float(total.item()); nb += 1; step += 1
            if step % args.log_every == 0:
                print(f"  ep{epoch} step{step} loss={run/nb:.5f} "
                      f"pos={float(pos):.5f} rot={float(rot):.5f} "
                      f"lr={sched.get_last_lr()[0]:.2e} {time.perf_counter()-t0:.0f}s", flush=True)
                run = 0.0; nb = 0

        # 验证
        model.eval()
        tot = np.zeros(3); nv = 0
        with torch.no_grad():
            for batch in val_loader:
                # 开启接触约束时 batch 是 6 元组（多一个 contacts），验证只需要前 5 个
                history_b, root, reference, mask, target = [x.to(device) for x in batch[:5]]
                with torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=device.type == "cuda"):
                    pred = model(history_b, root, reference, mask)
                pred = pred.float().reshape(len(history_b), 24, -1, 9)
                tgt = target.float().reshape(len(history_b), 24, -1, 9)
                visible = (1 - mask).reshape(len(history_b), 24, 1, 1)
                cnt = float(visible.sum()) * pred.shape[2]
                # pose_std 形状是 (bones, 3)，需广播成 (1,1,bones,3)
                std3 = pose_std.view(1, 1, pred.shape[2], 3)
                diff = (pred[..., :3] - tgt[..., :3]) * std3
                p_se = (diff ** 2).sum(-1, keepdim=True) * visible      # 每关节平方误差(cm^2)
                tot += np.array([float(p_se.sum()), 0.0, 0.0]); nv += cnt
        # RMSE(cm) = sqrt( sum(平方误差) / 关节数 )
        val_rmse_cm = math.sqrt(float(tot[0]) / max(nv, 1)) * 100
        print(f"== epoch {epoch} 验证 关节位置 RMSE = {val_rmse_cm:.2f} cm", flush=True)
        history.append({"epoch": epoch, "val_pose_rmse_cm": val_rmse_cm})

        ckpt = {"model": model.state_dict(), "width": args.width, "bones": bones,
                "epoch": epoch, "val_pose_rmse_cm": val_rmse_cm, "args": vars(args)}
        torch.save(ckpt, out / "last.pt")
        if val_rmse_cm < best:
            best = val_rmse_cm
            torch.save(ckpt, out / "best.pt")
            print(f"   -> 新最优 {best:.2f} cm，已保存 best.pt", flush=True)
        (out / "history.json").write_text(json.dumps(history, indent=2), encoding="utf-8")

    print(f"训练完成 最优验证 {best:.2f} cm  用时 {time.perf_counter()-t0:.0f}s")
    print(f"权重: {out/'best.pt'}")


if __name__ == "__main__":
    main()
