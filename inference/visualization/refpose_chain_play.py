
"""方案 B：按 MotionBricks 的链式做法——每代只播一段，下一代从"实际播过"的姿态接续。

与当前做法的差别：
  旧：每代生成 24 帧，取前 STRIDE(8) 帧，但下一代的 24 帧历史里有 16 帧是"生成过却没播过"的输出。
  新：每代生成 24 帧，只播前 PLAY 帧（丢弃尾部），下一代的上下文全部取自实际播过的姿态。

这样上下文里不含任何"未播出的预测"，误差反馈路径和官方一致。
Root 仍用真值（隔离姿态网问题），以便单独考察链式接续本身。
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
import paths
import render_refpose_gifs as g

from training.pretrain.train_conditioned_pose import ReferenceGuidedPose

POSE_W = paths.POSE_WEIGHT
OUT = ROOT/"output"/"refpose_preview"/"chain_play"
WINDOW = 24


def localize(track, origin):
    r = Rotation.from_quat(origin[3:])
    return np.concatenate((r.inv().apply(track[:, :3]-origin[:3]),
                           (r.inv()*Rotation.from_quat(track[:, 3:])).as_quat()), -1).astype(np.float32)


def run(raw, seed_start, seconds, play, overlap):
    """play: 每代实际播出的帧数；overlap: 下一代上下文与已播部分的期望重叠（= WINDOW-play）。"""
    mean = np.load(g.STATS/"pose_m.npy").astype(np.float32)
    std = np.load(g.STATS/"pose_s.npy").astype(np.float32)
    ck = torch.load(POSE_W, map_location="cpu", weights_only=False)
    net = ReferenceGuidedPose(78, ck["width"]).eval(); net.load_state_dict(ck["model"])

    P = np.load(g.MMAP/raw/"p.npy").astype(np.float32)
    RT = np.load(g.MMAP/raw/"r.npy").astype(np.float32)
    K = np.load(g.MMAP/raw/"k.npy").astype(np.float32)

    s = seed_start
    target_frames = int(seconds * 120)
    # 每代推进 play 帧，直到凑够 target_frames
    max_gen = (len(K) - s - 2*WINDOW)//max(1, play) + 1

    # 已播出姿态缓冲（世界/归一化），初始 24 帧取真值种子
    played_pose = [np.asarray(x) for x in ((P[s:s+WINDOW]-mean)/std)]
    played_rot  = [np.asarray(x) for x in RT[s:s+WINDOW]]
    played_root = [np.asarray(x) for x in K[s:s+WINDOW]]

    out_p, out_r, out_k = [], [], []
    gen = 0
    cursor = s + WINDOW          # 已消耗到的真值帧（用于 root 真值与误差对照）

    with torch.no_grad():
        while len(out_p) < target_frames and gen < max_gen:
            pa = np.stack(played_pose[-WINDOW:])
            ra = np.stack(played_rot[-WINDOW:])
            ka = np.stack(played_root[-WINDOW:])
            origin = ka[-1]
            hist = np.concatenate((np.concatenate((pa, ra), -1).reshape(WINDOW, -1),
                                   localize(ka, origin)), -1).astype(np.float32)
            # 真值 Root 的 24 帧计划（以"接下来第一帧"为起点）
            plan_start = cursor - 1
            plan = localize(K[plan_start:plan_start+WINDOW], origin)
            if plan.shape[0] < WINDOW:
                break

            o = net(torch.from_numpy(hist[None]), torch.from_numpy(plan[None]),
                    torch.zeros(1, WINDOW, 702), torch.zeros(1, WINDOW, 1)).numpy()[0].reshape(WINDOW, 78, 9)

            take = min(play, target_frames - len(out_p))
            for k in range(take):
                out_p.append(o[k, :, :3]); out_r.append(o[k, :, 3:])
                out_k.append(K[cursor + k])
                # 只有播出的帧才进上下文
                played_pose.append(o[k, :, :3]); played_rot.append(o[k, :, 3:])
                played_root.append(K[cursor + k])
            cursor += take
            gen += 1
            if cursor + WINDOW >= len(K):
                break

    pose = np.stack(out_p)*std+mean
    root = np.stack(out_k)
    return pose, root, gen, play


def evaluate(pose, root, raw, seed_start, tag):
    world = g.place(pose, root)
    P = np.load(g.MMAP/raw/"p.npy").astype(np.float32)
    K = np.load(g.MMAP/raw/"k.npy").astype(np.float32)
    gs = seed_start + WINDOW
    n = min(len(world), len(P)-gs)
    gt = g.place(P[gs:gs+n], K[gs:gs+n])
    per = (np.linalg.norm(world[:n]-gt, axis=-1)*100).mean(1)
    return {"tag": tag, "frames": int(n),
            "mean_cm": round(float(per.mean()), 2),
            "first_s_cm": round(float(per[:120].mean()), 2),
            "last_s_cm": round(float(per[-120:].mean()), 2),
            "p90_cm": round(float(np.percentile(per, 90)), 2),
            "max_cm": round(float(per.max()), 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="027106")
    ap.add_argument("--seed", type=int, default=120)
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--plays", default="8,16,20", help="每代播出的帧数")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    report = []
    for play in [int(x) for x in args.plays.split(",") if x]:
        pose, root, gen, _ = run(args.raw, args.seed, args.seconds, play, WINDOW-play)
        st = evaluate(pose, root, args.raw, args.seed, f"play={play}")
        st["generations"] = int(gen)
        report.append(st)
        np.savez_compressed(OUT/f"play{play}.npz", frames=pose.astype(np.float32),
                            root_frames=root.astype(np.float32), fps=np.float32(120.0))
        print(json.dumps(st, ensure_ascii=False), flush=True)
    (OUT/"report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
