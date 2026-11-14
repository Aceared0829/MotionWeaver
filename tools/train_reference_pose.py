
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
import warnings
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader

# 仓库根按本文件位置推（tools/ 的上一级），换机器/换盘符不需要改这里。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from training.pretrain.train_conditioned_pose import (
    ClipShuffleSampler,
    MotionWindows,
    ReferenceGuidedPose,
    losses,
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


def restore_training_state(ckpt, model, opt, sched, *, width, bones, steps_per_epoch, out):
    """把检查点里的状态接回模型/优化器/调度器，返回 (起始 epoch, 最优值, 历史, 已跑步数)。

    只恢复权重是不够的：AdamW 的一二阶动量和余弦退火的 LR 位置都要接上，否则续跑
    等于「拿一个收敛的权重按初始 LR 重新热身」，已经花掉的 epoch 收益会被打散。
    老检查点（2026-11-14 之前的版本）没有 optimizer/scheduler，只能按已跑步数把
    调度器空转到同一位置——LR 能对上，动量对不上，这部分损失无法追回。

    sched 必须是调用方按「本次的 --epochs」新建的调度表：只有当它的 T_max 与检查点
    记录的一致时才直接搬检查点状态，否则按已跑 step 在新表上空转。这一点不能省——
    load_state_dict 会把 T_max 一并覆盖回旧值，--epochs 调大后余弦会越过旧 T_max 折返、
    LR 回升，等于悄悄重新热身。
    """
    if ckpt.get("width") != width or ckpt.get("bones") != bones:
        raise ValueError(
            f"检查点配置不匹配：width={ckpt.get('width')} bones={ckpt.get('bones')}，"
            f"当前 width={width} bones={bones}")
    model.load_state_dict(ckpt["model"])
    start_epoch = int(ckpt.get("epoch", -1)) + 1
    best = float(ckpt.get("best", ckpt.get("val_pose_rmse_cm", float("inf"))))
    # history.json 是唯一一份逐 epoch 记录，检查点里的副本可能更旧，优先用文件。
    history_path = Path(out) / "history.json"
    if history_path.is_file():
        history = json.loads(history_path.read_text(encoding="utf-8"))
    else:
        history = list(ckpt.get("history") or [])
    if history:
        best = min(best, min(h["val_pose_rmse_cm"] for h in history))
    step = int(ckpt.get("step", 0)) or start_epoch * steps_per_epoch
    if ckpt.get("optimizer"):
        opt.load_state_dict(ckpt["optimizer"])

    # 调度表：只有「目标 T_max 与检查点一致」时才能直接搬检查点的状态。
    # 直接 load_state_dict 会把调用方构造时给的 T_max 覆盖回旧值，于是 --epochs 调大以后
    # 余弦越过旧 T_max 会从 -1 折返，LR 沿原路回升（实测 20ep 的检查点续到 25ep，
    # LR 从 0 爬回 4.4e-5）——那是「重新升温」，不是「接着退火」。
    # T_max 一变就必须在新调度表上把进度空转到同一 step，取同一进度位置上的 LR。
    old_tmax = int((ckpt.get("scheduler") or {}).get("T_max") or 0)
    if old_tmax == int(sched.T_max):
        sched.load_state_dict(ckpt["scheduler"])
        # load_state_dict 只恢复调度器自己的 _last_lr，不会写回 optimizer 的参数组；
        # 参数组还停在构造时的 lr（命令行 --lr），而 opt.step() 用的正是参数组的值。
        # 不同步的话续训第一步会按满学习率更新一次，日志打印的却是调度器的 lr，看不出异常。
        for group in opt.param_groups:
            group["lr"] = sched.get_last_lr()[0]
    elif step >= int(sched.T_max):
        raise ValueError(
            f"已跑 {step} 步不短于目标调度表 T_max={sched.T_max}（--epochs 太小）。"
            f"续训的 --epochs 是总 epoch 数，必须大于 {start_epoch}。")
    else:
        # CosineAnnealingLR.get_lr 是**相对**的：它按 cos 比值去乘参数组当前的 lr。
        # 刚 load 进优化器状态时参数组的 lr 是保存那一刻的值（走满退火的话就是 0），
        # 相对递推乘下去永远得 0——必须先把参数组退回初值，再空转到同一进度。
        base_lrs = (ckpt.get("scheduler") or {}).get("base_lrs") or [
            group.get("initial_lr", group["lr"]) for group in opt.param_groups]
        for group, base in zip(opt.param_groups, base_lrs):
            group["lr"] = base
        # 只推进调度器、不推优化器，PyTorch 会就「step 顺序」发一条 UserWarning；
        # 这里正是刻意只退火 LR，屏蔽掉以免每次续训都刷一条无意义的告警。
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Detected call of")
            for _ in range(step):
                sched.step()
    return start_epoch, best, history, step


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
    ap.add_argument("--chunk-size", type=int, default=16,
                    help="按 clip 分块的打乱粒度：块内所有窗口一起打乱，让相邻样本落在同一条 clip 上。"
                         "0 表示退回全局 shuffle（每个样本换一条 clip，全量数据下慢 10 倍以上）")
    ap.add_argument("--mmap-root", default=None,
                    help="内存映射数据目录（每 clip 一个子目录，含 p/r/k/c.npy）。"
                         "开启后随机窗口读取 1.33 ms（npz 解压 3.75 ms），且不占常驻内存")
    ap.add_argument("--max-steps", type=int, default=0,
                    help="本次最多跑这么多优化步就停（冒烟/稳定性检查用，0 表示跑满）。"
                         "改动损失口径后先用它验一遍损失量级，不要直接开整轮。")
    ap.add_argument("--resume", default=None,
                    help="从 last.pt/best.pt 续训：恢复权重、优化器/调度器状态、epoch 与最优值。"
                         "只补跑 --epochs 里还没跑完的部分，不会从头再训。")
    args = ap.parse_args()

    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=True)

    random.seed(args.seed); np.random.seed(args.seed); torch.manual_seed(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    release, contract = load_contract(args.release)
    print(f"契约: {release}")
    print(f"clips={len(contract['clips'])} windows={contract.get('window_count', '?')} "
          f"split={contract['split_counts']}")

    # 接触损失里的脚速 = 帧间位移 × 帧率。帧率必须取契约的 fps，不能写死 30：
    # full_release_v2 是 120 fps，写死 30 会让接触项进平方后缩小 16 倍，
    # 于是 --contact-weight 0.4 实际只起 0.025 的作用（见 2026-11-14 的记录）。
    fps = float(contract.get("fps") or 30.0)
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
        # 不要只数一级子目录：本仓库的 mmap 布局是 clips/<fps_group>/<clip_id>/，
        # 只数顶层会永远打印 1 条（clips 自己）。按「含 p.npy 的目录」来数才准确。
        n_sub = sum(1 for p in mmap_root.rglob("p.npy"))
        print(f"mmap 数据：{mmap_root}（{n_sub} 条 clip）")

    train_set = MotionWindows(release, contract, "train", seed=args.seed,
                              scenario="random", include_contacts=use_contacts,
                              mmap_root=mmap_root)
    val_set = MotionWindows(release, contract, "validation", seed=args.seed,
                            scenario="random", include_contacts=use_contacts,
                            mmap_root=mmap_root)
    print(f"train 窗口={len(train_set)}  val 窗口={len(val_set)}")

    # 采样顺序才是本机真正的瓶颈，不是 pin_memory。
    # 实测（71k 全量、batch 256、workers 8、mmap）：全局 shuffle 388 ms/步、
    # 约 660 samples/s，其中 196 ms 在等 DataLoader；换 ClipShuffleSampler
    # （--chunk-size 16）后 31 ms/步、约 8,100 samples/s，GPU 从 3–21% 拉到满载。
    # 原因：每个样本换一条 clip 就要重新 open 三个 .npy（1.5–4 ms/样本，99% 是
    # np.load 的元数据开销），而每条 clip 平均有 35 个窗口，随机取样必然次次冷读。
    if args.chunk_size > 0:
        sampler = ClipShuffleSampler(train_set.rows, chunk=args.chunk_size, seed=args.seed)
        train_loader = DataLoader(train_set, batch_size=args.batch_size, sampler=sampler,
                                  num_workers=args.workers, drop_last=True,
                                  pin_memory=True,
                                  persistent_workers=args.workers > 0,
                                  prefetch_factor=(4 if args.workers > 0 else None))
    else:
        sampler = None
        train_loader = DataLoader(train_set, batch_size=args.batch_size, shuffle=True,
                                  num_workers=args.workers, drop_last=True,
                                  pin_memory=True,
                                  persistent_workers=args.workers > 0,
                                  prefetch_factor=(4 if args.workers > 0 else None))
    val_loader = DataLoader(val_set, batch_size=args.batch_size, shuffle=False,
                            num_workers=args.workers, pin_memory=True,
                            persistent_workers=args.workers > 0,
                            prefetch_factor=(4 if args.workers > 0 else None))
    print(f"采样：{'全局 shuffle' if sampler is None else f'clip 分块 chunk={args.chunk_size}'}"
          f"  每 epoch {len(train_loader)} 步  workers={args.workers}")

    bones = contract["bone_count"]
    model = ReferenceGuidedPose(bones, args.width).to(device)
    n_param = sum(p.numel() for p in model.parameters())
    print(f"模型: ReferenceGuidedPose  bones={bones} width={args.width}  {n_param/1e6:.2f}M 参数")

    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.epochs * max(1, len(train_loader)))
    pose_std = torch.from_numpy(train_set.std).float().to(device)
    pose_mean = torch.from_numpy(train_set.mean).float().to(device)

    start_epoch, best, history, step = 0, float("inf"), [], 0
    if args.resume:
        resume_path = Path(args.resume).resolve()
        if not resume_path.is_file():
            raise FileNotFoundError(f"--resume 指向的检查点不存在：{resume_path}")
        ckpt = torch.load(resume_path, map_location=device, weights_only=False)
        start_epoch, best, history, step = restore_training_state(
            ckpt, model, opt, sched, width=args.width, bones=bones,
            steps_per_epoch=max(1, len(train_loader)), out=out)
        print(f"续训：{resume_path.name}  epoch {start_epoch} 起，已跑 {step} 步，"
              f"lr={sched.get_last_lr()[0]:.2e}  历史最优 {best:.2f} cm", flush=True)

    t0 = time.perf_counter()

    for epoch in range(start_epoch, args.epochs):
        train_set.epoch = epoch
        # 采样器也要跟着换 epoch，否则每个 epoch 的遍历顺序完全相同，
        # 分组边界固化会让部分 clip 长期落在同一批 step 上。
        if sampler is not None:
            sampler.set_epoch(epoch)
        model.train()
        run = 0.0; nb = 0
        smoke_done = False
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
                contacts=contacts, foot_indices=foot_indices, fps=fps,
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
            if args.max_steps and step >= args.max_steps:
                smoke_done = True
                break
        if smoke_done:
            # 冒烟到此为止：不验证、不存检查点，输出目录只留 config.json 供事后对照。
            print(f"冒烟结束：已跑 {step} 步（--max-steps={args.max_steps}），未写检查点", flush=True)
            return

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

        # 优化器与调度器状态必须一起存：只存权重的话续训会让 LR 与动量重新起步，
        # 之前的 15 个 epoch 等于白跑。history/step/best 也存进来，方便不依赖目录内容恢复。
        # 先比完 best 再打包检查点：原实现把 ckpt 建在比较之前，存进去的是「上一次的最优」，
        # 导致 best.pt 里的 best 字段比它自己的 val 差一个 epoch（ep19 存的是 4.0577 而不是 4.0469）。
        # 续训时又从 history 取最小值兜住了，所以没出过事故，但字段本身是错的。
        improved = val_rmse_cm < best
        if improved:
            best = val_rmse_cm
        ckpt = {"model": model.state_dict(), "width": args.width, "bones": bones,
                "epoch": epoch, "step": step, "val_pose_rmse_cm": val_rmse_cm,
                "best": best, "history": history,
                "optimizer": opt.state_dict(), "scheduler": sched.state_dict(),
                "args": vars(args)}
        torch.save(ckpt, out / "last.pt")
        if improved:
            torch.save(ckpt, out / "best.pt")
            print(f"   -> 新最优 {best:.2f} cm，已保存 best.pt", flush=True)
        (out / "history.json").write_text(json.dumps(history, indent=2), encoding="utf-8")

    print(f"训练完成 最优验证 {best:.2f} cm  用时 {time.perf_counter()-t0:.0f}s")
    print(f"权重: {out/'best.pt'}")


if __name__ == "__main__":
    main()
