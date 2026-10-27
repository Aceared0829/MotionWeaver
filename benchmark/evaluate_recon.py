"""在四键 NPZ 训练视图上评估 VQ-VAE 的重建质量。

## 关键：`joint_positions_from="ric_data"`

这是本脚本唯一难找但对的地方。`inverse()` 默认用 `global_rot_data` 求关节位置，
而我们的训练视图直接给关节位置（相对根），对应的是 `ric_data`。

用默认值的后果是**往返误差 0.73-1.29 m**——连「把输入特征变回原动作」都做不到，
于是任何重建指标都失去意义。这个坑花了很久才定位，因此这里：
  1. 显式传 `joint_positions_from="ric_data"`；
  2. 先跑官方 `motionweaver_preview.py` 那条自校验（原始往返误差 ≤ 0.03 m），
     不通过就拒绝输出任何重建数字。

## 其他口径（都已验证）

- `to_normalize=False`：训练时 collate 产出未归一化特征，归一化在其后的
  `global_to_local(to_normalize=True)` 里做。
- `init_heading_info=initial`：编码时记录、解码时传回，否则朝向转不回来。
- 长度按训练分布取：`training_step` 随机抽 tokens ∈ [6,16]，
  frames = tokens × 2**down_t。

## 指标

- `feature_l1`：归一化特征空间的 SmoothL1，与训练损失同口径；
- `joint_rmse_cm`：关节位置 RMSE（厘米），与基线 Lerp 的 6.59 cm 可直接比较。
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, r"D:\AILocomotonSystem\MotionWeaver")

from data.runtime.soma_realtime_dataset import (
    SomaRealtimeDataset,
    quat_to_matrix,
)
from inference.runtime.checkpoint import load_vqvae
from motionweaver.representation.helper.data_training_util import (
    extract_feature_from_motion_rep,
)

# 官方 motionweaver_preview.py 用的自校验门槛
RAW_ERROR_LIMIT = 0.03


def take_middle(dataset, index, frames):
    clip = dataset.clips[index]
    with np.load(dataset.folder / clip["file"], allow_pickle=False) as archive:
        if "quat_xyzw" in archive.files:
            rotations = quat_to_matrix(
                np.asarray(archive["quat_xyzw"], dtype=np.float32))
        else:
            rotations = np.asarray(archive["rotations"], dtype=np.float32)
        positions = np.asarray(archive["positions"], dtype=np.float32)
    total = len(positions)
    if total < frames:
        return None
    start = max(0, (total - frames) // 2)
    return (positions[start:start + frames],
            rotations[start:start + frames].astype(np.float32),
            clip["asset"], clip.get("scene"))


def run_one(pose_net, motion_rep, pos, rot, frames, device):
    """单条评估，返回 (feature_l1, joint_rmse_cm, raw_roundtrip_m)。"""
    global_rep = motion_rep.dual_rep.global_motion_rep
    indices = global_rep.indices["all"]
    lengths = torch.tensor([frames]).to(device)

    pos_t = torch.from_numpy(pos[None]).to(device)
    rot_t = torch.from_numpy(rot[None]).to(device)

    with torch.no_grad():
        unscaled, initial = global_rep(
            {"posed_joints": pos_t, "global_joint_rots": rot_t,
             "foot_contacts": None},
            to_normalize=False, lengths=lengths,
            return_init_heading_info=True)
        unscaled = unscaled[..., indices]

        kwargs = {"is_normalized": False, "init_heading_info": dict(initial),
                  "joint_positions_from": "ric_data"}

        # 自校验：inverse 必须能还原原始输入
        source = global_rep.inverse(unscaled, **kwargs)["posed_joints"][0]
        raw_error = float(np.sqrt(np.mean(
            (source.cpu().numpy() - pos) ** 2)))

        local_motions = motion_rep.dual_rep.global_to_local(
            unscaled.float(), is_normalized=False, to_normalize=True,
            lengths=lengths)
        external_cond = extract_feature_from_motion_rep(
            local_motions, pose_net.motion_rep,
            pose_net.decoder_external_cond_feature_mode)
        out = pose_net(local_motions, target_cond=None, has_target_cond=None,
                       external_cond=external_cond)
        recon = out["recon_state"]

        feature_l1 = float((recon - local_motions).abs().mean().item())

        ref = global_rep.inverse(unscaled, **kwargs)["posed_joints"][0]
        # recon 在 local 空间（941 维），要回关节位置必须先转回 global（942 维）。
        # 直接把 941 维送 global_rep.inverse 会报 "This input dim is not recognized: 941"。
        recon_global = motion_rep.dual_rep.local_to_global(
            recon.float(), is_normalized=True, to_normalize=False,
            lengths=lengths)
        rec = global_rep.inverse(
            recon_global.float(), **kwargs)["posed_joints"][0]
        diff = (rec - ref).pow(2).sum(-1).sqrt()
        joint_cm = float(diff.mean().item()) * 100.0

    return feature_l1, joint_cm, raw_error


def evaluate_all(checkpoint, *, rows=(6, 8, 10, 12, 14, 16), scenes=("B", "C"),
                 clips=6, device=None,
                 views=r"D:\MotionWeaverData\soma_train_v1\full-v1-f32"):
    """按 token 长度逐档评估，返回可 JSON 化的汇总（供 benchmark.run 调用）。

    与命令行入口的区别：不做打印，只返回结构化结果。
    自校验不通过时**抛出异常**而不是返回可疑数字——宁可失败，不给假数据。
    """
    import torch

    from benchmark.primitives.metrics import summarize

    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    pose_net, motion_rep, contract = load_vqvae(str(checkpoint))
    pose_net = pose_net.to(device).eval()

    down_t = (contract.get("config", {}).get("model", {})
              .get("pose_vqvae_network", {}).get("down_t", 3))
    frames_per_token = 2 ** int(down_t)
    dataset = SomaRealtimeDataset(views,
                                  window_frames=max(rows) * frames_per_token,
                                  fps_group="fps120", scenes=list(scenes))

    stride = max(1, len(dataset) // clips)
    picks = list(range(0, len(dataset), stride))[:clips]

    table = []
    for tokens in rows:
        frames = tokens * frames_per_token
        l1s, cms, raws = [], [], []
        for index in picks:
            taken = take_middle(dataset, index, frames)
            if taken is None:
                continue
            l1, cm, raw = run_one(pose_net, motion_rep, taken[0], taken[1],
                                  frames, device)
            l1s.append(l1)
            cms.append(cm)
            raws.append(raw)
        if not l1s:
            continue
        if max(raws) > RAW_ERROR_LIMIT:
            raise RuntimeError(
                f"自校验失败：token={tokens} frames={frames} "
                f"往返误差 {max(raws):.6f} m 超过 {RAW_ERROR_LIMIT} m")
        table.append({
            "tokens": tokens,
            "frames": frames,
            "feature_l1": float(summarize(l1s)["mean"]),
            "joint_rmse_cm": float(summarize(cms)["mean"]),
            "joint_p95_cm": float(summarize(cms)["p95"]),
            "joint_max_cm": float(summarize(cms)["max"]),
            "self_check_m": float(max(raws)),
            "clips": len(l1s),
        })

    del pose_net
    torch.cuda.empty_cache()

    if not table:
        raise RuntimeError("没有成功评估任何长度")

    return {
        "checkpoint": str(checkpoint),
        "global_step": contract.get("global_step"),
        "down_t": down_t,
        "frames_per_token": frames_per_token,
        "joint_positions_from": "ric_data",
        "scenes": list(scenes),
        "table": table,
        "mean_feature_l1": float(summarize(
            [r["feature_l1"] for r in table])["mean"]),
        "mean_joint_rmse_cm": float(summarize(
            [r["joint_rmse_cm"] for r in table])["mean"]),
        "max_self_check_m": float(max(r["self_check_m"] for r in table)),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--views",
                        default=r"D:\MotionWeaverData\soma_train_v1\full-v1-f32")
    parser.add_argument("--scenes", nargs="+", default=["B", "C"])
    parser.add_argument("--clips", type=int, default=8,
                        help="每个长度抽多少条")
    parser.add_argument("--output")
    args = parser.parse_args()

    pose_net, motion_rep, contract = load_vqvae(args.checkpoint)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    pose_net = pose_net.to(device).eval()

    down_t = (contract.get("config", {}).get("model", {})
              .get("pose_vqvae_network", {}).get("down_t", 3))
    frames_per_token = 2 ** int(down_t)

    dataset = SomaRealtimeDataset(args.views,
                                  window_frames=16 * frames_per_token,
                                  fps_group="fps120", scenes=args.scenes)

    print(f"down_t={down_t}  frames_per_token={frames_per_token}")
    print(f"joint_positions_from=ric_data  "
          f"自校验门槛={RAW_ERROR_LIMIT} m\n")

    # 先跑自校验
    first = take_middle(dataset, 0, 8 * frames_per_token)
    if first is None:
        print("首条样本长度不足")
        return
    _, _, raw_error = run_one(pose_net, motion_rep, first[0], first[1],
                              8 * frames_per_token, device)
    status = "✅ 通过" if raw_error <= RAW_ERROR_LIMIT else "❌ 失败"
    print(f"自校验（首条往返误差）: {raw_error:.6f} m  {status}")
    if raw_error > RAW_ERROR_LIMIT:
        print("自校验未通过，重建指标不可信，终止。")
        return

    print(f"\n{'token':>5} {'frames':>7} {'feature L1':>12} "
          f"{'关节 RMSE cm':>14} {'自校验 m':>10}")
    print("-" * 56)

    rows = []
    stride = max(1, len(dataset) // args.clips)
    picks = list(range(0, len(dataset), stride))[:args.clips]

    for tokens in (6, 8, 10, 12, 14, 16):
        frames = tokens * frames_per_token
        l1s, cms, raws = [], [], []
        for index in picks:
            taken = take_middle(dataset, index, frames)
            if taken is None:
                continue
            l1, cm, raw = run_one(pose_net, motion_rep, taken[0], taken[1],
                                  frames, device)
            l1s.append(l1)
            cms.append(cm)
            raws.append(raw)
        if not l1s:
            print(f"{tokens:>5} {frames:>7} {'片段不足':>12}")
            continue
        rows.append({
            "tokens": tokens, "frames": frames,
            "feature_l1": float(np.mean(l1s)),
            "joint_rmse_cm": float(np.mean(cms)),
            "self_check_m": float(np.max(raws)),
            "clips": len(l1s),
        })
        print(f"{tokens:>5} {frames:>7} {np.mean(l1s):>12.4f} "
              f"{np.mean(cms):>14.2f} {np.max(raws):>10.6f}")

    if rows:
        mean_l1 = float(np.mean([r["feature_l1"] for r in rows]))
        mean_cm = float(np.mean([r["joint_rmse_cm"] for r in rows]))
        print("-" * 56)
        print(f"{'平均':>5} {'':>7} {mean_l1:>12.4f} {mean_cm:>14.2f}")
        print("\n对照：训练末段 l_recons_pose = 0.3031；"
              "基线 Lerp 关节 RMSE = 6.59 cm")

        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(json.dumps({
                "checkpoint": args.checkpoint,
                "global_step": contract.get("global_step"),
                "down_t": down_t,
                "joint_positions_from": "ric_data",
                "scenes": args.scenes,
                "rows": rows,
                "mean_feature_l1": mean_l1,
                "mean_joint_rmse_cm": mean_cm,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"已写出 {args.output}")


if __name__ == "__main__":
    main()


