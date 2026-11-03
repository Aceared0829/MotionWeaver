
"""关键帧补间：给定若干关键姿态，让模型补出中间帧与 Root。

设计取舍（与之前闭环发散的教训对齐）：
  - 只做 24 帧窗口内的补间，不做任意长程链式推理。
  - Root 用关键帧位置线性插值（不交给 Root 头，避免自回归漂移）。
  - 关键帧通过 reference + mask 通道注入，模型对它做过专门训练。
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT/"inference"/"visualization"))
import itertools

import paths as P
import render_refpose_gifs as g

from training.pretrain.train_conditioned_pose import ReferenceGuidedPose

POSE_W = P.POSE_WEIGHT
OUT = ROOT/"output"/"refpose_preview"/"keyframe_fill"
WINDOW = 24


def localize(track, origin):
    r = Rotation.from_quat(origin[3:])
    return np.concatenate((r.inv().apply(track[:, :3]-origin[:3]),
                           (r.inv()*Rotation.from_quat(track[:, 3:])).as_quat()), -1).astype(np.float32)


def slerp_roots(root_a, root_b, n):
    """在关键帧 Root 之间做位置线性插值 + 朝向球面插值，生成 n 帧的 root_plan。"""
    ra, rb = Rotation.from_quat(root_a[3:]), Rotation.from_quat(root_b[3:])
    ts = np.linspace(0.0, 1.0, n)
    pos = (1-ts)[:, None]*root_a[:3] + ts[:, None]*root_b[:3]
    rots = []
    for t in ts:
        q = (ra.inv() * rb)
        rotvec = q.as_rotvec()
        rots.append((ra * Rotation.from_rotvec(rotvec*t)).as_quat())
    return np.concatenate((pos, np.array(rots)), -1).astype(np.float32)


def fill(raw, key_frame, key_indices, seed_len=24, extra_keys=None):
    """以 key_frame 为种子窗口起点，生成一个 24 帧窗口。

    key_indices: 窗口内哪些帧是"用户指定的关键姿态"（相对窗口的 0-based 下标）。
    extra_keys: {窗口下标: 该帧的姿态数据(78,9 归一化)}，用于覆盖默认真值关键帧。
    """
    mean = np.load(g.STATS/"pose_m.npy").astype(np.float32)
    std = np.load(g.STATS/"pose_s.npy").astype(np.float32)
    ck = torch.load(POSE_W, map_location="cpu", weights_only=False)
    net = ReferenceGuidedPose(78, ck["width"]).eval(); net.load_state_dict(ck["model"])

    P = np.load(g.MMAP/raw/"p.npy").astype(np.float32)
    R = np.load(g.MMAP/raw/"r.npy").astype(np.float32)
    K = np.load(g.MMAP/raw/"k.npy").astype(np.float32)

    # 历史 = 窗口前 24 帧真值
    hs = max(0, key_frame - WINDOW)
    hist_pose = ((P[hs:hs+WINDOW] - mean)/std).astype(np.float32)
    hist_rot = R[hs:hs+WINDOW].astype(np.float32)
    hist_root = K[hs:hs+WINDOW].astype(np.float32)
    if len(hist_root) < WINDOW:
        raise ValueError("种子窗口不足 24 帧")

    origin = hist_root[-1]
    hist = np.concatenate((np.concatenate((hist_pose, hist_rot), -1).reshape(WINDOW, -1),
                           localize(hist_root, origin)), -1).astype(np.float32)

    # 目标窗口：从 key_frame 起 24 帧
    tgt_pose = ((P[key_frame:key_frame+WINDOW] - mean)/std).astype(np.float32)
    tgt_rot = R[key_frame:key_frame+WINDOW].astype(np.float32)

    # reference/mask：只在用户指定的帧给姿态
    reference = np.zeros((WINDOW, 702), np.float32)
    mask = np.zeros((WINDOW, 1), np.float32)
    for ki in key_indices:
        if not (0 <= ki < WINDOW):
            continue
        if extra_keys and ki in extra_keys:
            reference[ki] = extra_keys[ki].reshape(-1)
        else:
            # 模型特征布局是 (78, 9) 交错：位置3 + 旋转6，不是"先全部位置再全部旋转"
            reference[ki] = np.concatenate((tgt_pose[ki], tgt_rot[ki]), axis=-1).reshape(-1)
        mask[ki] = 1

    # Root 计划：关键帧之间插值
    if len(key_indices) >= 2:
        ks = sorted(key_indices)
        full = np.empty((WINDOW, 7), np.float32)
        for a, b in itertools.pairwise(ks):
            seg = slerp_roots(K[key_frame+a], K[key_frame+b], b-a+1)
            full[a:b+1] = seg
        # 两端延展
        full[:ks[0]] = full[ks[0]]
        full[ks[-1]:] = full[ks[-1]]
        plan_abs = full
    else:
        plan_abs = K[key_frame:key_frame+WINDOW].astype(np.float32)

    plan = localize(plan_abs, origin)

    with torch.no_grad():
        out = net(torch.from_numpy(hist[None]), torch.from_numpy(plan[None]),
                  torch.from_numpy(reference[None]), torch.from_numpy(mask[None]))
    out = out.numpy()[0].reshape(WINDOW, 78, 9)
    pose = out[..., :3]*std + mean
    rot = out[..., 3:]

    # 世界坐标
    r = Rotation.from_quat(plan_abs[:, 3:])
    world = np.einsum("tij,tkj->tki", r.as_matrix(), pose) + plan_abs[:, None, :3]
    return world, pose, rot, plan_abs, mask[:, 0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="027106")
    ap.add_argument("--seed", type=int, default=600, help="窗口起点帧")
    ap.add_argument("--keys", default="0,23", help="窗口内的关键帧下标（逗号分隔）")
    ap.add_argument("--tag", default="k0_23")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    key_indices = [int(x) for x in args.keys.split(",") if x != ""]
    world, pose, _rot, plan, mask = fill(args.raw, args.seed, key_indices)

    np.savez_compressed(OUT/f"{args.tag}.npz",
                        frames=pose.astype(np.float32),
                        root_frames=plan.astype(np.float32),
                        mask=mask.astype(np.float32),
                        fps=np.float32(120.0))

    # 误差：只统计被补的帧（mask=0）
    P = np.load(g.MMAP/args.raw/"p.npy").astype(np.float32)
    K = np.load(g.MMAP/args.raw/"k.npy").astype(np.float32)
    from scipy.spatial.transform import Rotation as RR
    rr = RR.from_quat(K[args.seed:args.seed+WINDOW, 3:])
    gt_world = np.einsum("tij,tkj->tki", rr.as_matrix(), P[args.seed:args.seed+WINDOW]) + K[args.seed:args.seed+WINDOW][:, None, :3]

    filled = mask < 0.5
    err = np.linalg.norm(world - gt_world, axis=-1)*100
    print(json.dumps({
        "seed": args.seed, "keys": key_indices,
        "frames": int(WINDOW), "filled": int(filled.sum()),
        "err_filled_cm": round(float(err[filled].mean()), 2) if filled.any() else None,
        "err_key_cm": round(float(err[~filled].mean()), 2) if (~filled).any() else None,
        "out": str(OUT/f"{args.tag}.npz"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()