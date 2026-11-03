
"""方案 B × 重置间隔 N 的完整矩阵。

两个正交维度：
  play  —— 每代实际播出的帧数（8/16/20）。决定"喂回去的姿态有多少是真正播过的"。
  N     —— 每隔多少代用一次真值历史重置（0 表示从不重置）。

Root 一律用真值，隔离姿态网问题，只看链式接续 + 重置的交互。
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
OUT = ROOT/"output"/"refpose_preview"/"matrix"
WINDOW = 24


def localize(track, origin):
    r = Rotation.from_quat(origin[3:])
    return np.concatenate((r.inv().apply(track[:, :3]-origin[:3]),
                           (r.inv()*Rotation.from_quat(track[:, 3:])).as_quat()), -1).astype(np.float32)


def run(net, mean, std, P, RT, K, seed_start, seconds, play, reset_every):
    s = seed_start
    target = int(seconds * 120)
    cursor = s + WINDOW

    pp = [np.asarray(x) for x in ((P[s:s+WINDOW]-mean)/std)]
    pr = [np.asarray(x) for x in RT[s:s+WINDOW]]
    pk = [np.asarray(x) for x in K[s:s+WINDOW]]

    out_p, out_r, out_k = [], [], []
    gen = 0
    resets = 0
    max_gen = 4000

    with torch.no_grad():
        while len(out_p) < target and gen < max_gen:
            # 重置：用当前游标前 24 帧真值覆盖上下文
            if reset_every > 0 and gen > 0 and gen % reset_every == 0:
                base = max(0, cursor - WINDOW)
                pp = [np.asarray(x) for x in ((P[base:base+WINDOW]-mean)/std)]
                pr = [np.asarray(x) for x in RT[base:base+WINDOW]]
                pk = [np.asarray(x) for x in K[base:base+WINDOW]]
                if len(pk) < WINDOW:
                    break
                resets += 1

            pa = np.stack(pp[-WINDOW:]); ra = np.stack(pr[-WINDOW:]); ka = np.stack(pk[-WINDOW:])
            origin = ka[-1]
            hist = np.concatenate((np.concatenate((pa, ra), -1).reshape(WINDOW, -1),
                                   localize(ka, origin)), -1).astype(np.float32)
            plan = localize(K[cursor-1:cursor-1+WINDOW], origin)
            if plan.shape[0] < WINDOW:
                break

            o = net(torch.from_numpy(hist[None]), torch.from_numpy(plan[None]),
                    torch.zeros(1, WINDOW, 702), torch.zeros(1, WINDOW, 1)).numpy()[0].reshape(WINDOW, 78, 9)

            take = min(play, target - len(out_p))
            for k in range(take):
                out_p.append(o[k, :, :3]); out_r.append(o[k, :, 3:]); out_k.append(K[cursor+k])
                pp.append(o[k, :, :3]); pr.append(o[k, :, 3:]); pk.append(K[cursor+k])
            cursor += take
            gen += 1
            if cursor + WINDOW >= len(K):
                break

    return np.stack(out_p)*std+mean, np.stack(out_k), gen, resets


def evaluate(pose, root, P, K, seed_start, play, reset_every, gen, resets):
    world = g.place(pose, root)
    gs = seed_start + WINDOW
    n = min(len(world), len(P)-gs)
    gt = g.place(P[gs:gs+n], K[gs:gs+n])
    per = (np.linalg.norm(world[:n]-gt, axis=-1)*100).mean(1)
    return {
        "play": play, "reset": reset_every, "frames": int(n),
        "gens": int(gen), "resets": int(resets),
        "mean_cm": round(float(per.mean()), 2),
        "first_s": round(float(per[:120].mean()), 2),
        "last_s": round(float(per[-120:].mean()), 2),
        "p90": round(float(np.percentile(per, 90)), 2),
        "max": round(float(per.max()), 2),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="027106")
    ap.add_argument("--seed", type=int, default=120)
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--plays", default="8,16,20")
    ap.add_argument("--resets", default="0,1,2,4,8")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    mean = np.load(g.STATS/"pose_m.npy").astype(np.float32)
    std = np.load(g.STATS/"pose_s.npy").astype(np.float32)
    ck = torch.load(POSE_W, map_location="cpu", weights_only=False)
    net = ReferenceGuidedPose(78, ck["width"]).eval(); net.load_state_dict(ck["model"])
    P = np.load(g.MMAP/args.raw/"p.npy").astype(np.float32)
    RT = np.load(g.MMAP/args.raw/"r.npy").astype(np.float32)
    K = np.load(g.MMAP/args.raw/"k.npy").astype(np.float32)

    report = []
    for play in [int(x) for x in args.plays.split(",") if x]:
        for rs in [int(x) for x in args.resets.split(",") if x != ""]:
            pose, root, gen, resets = run(net, mean, std, P, RT, K, args.seed, args.seconds, play, rs)
            st = evaluate(pose, root, P, K, args.seed, play, rs, gen, resets)
            report.append(st)
            np.savez_compressed(OUT/f"p{play}_N{rs}.npz", frames=pose.astype(np.float32),
                                root_frames=root.astype(np.float32), fps=np.float32(120.0))
            print(json.dumps(st, ensure_ascii=False), flush=True)
    (OUT/"report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
