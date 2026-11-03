
"""第一步：缩短有效上下文（context length sweep）。

模型输入的 history 张量必须保持 24 帧形状，但内容上只保留最近 K 帧"活的"播放姿态，
更早的 24-K 帧冻结为这 K 帧里最早那一帧（重复），使 GRU 看到的有效历史只有 K 帧。

Root 仍用真值（隔离姿态网），且不重置（N=0），以便单独考察上下文长度的影响。
"""
import argparse
import json
import sys

import numpy as np
import paths
import torch
from scipy.spatial.transform import Rotation

ROOT = paths.ROOT
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT/"inference"/"visualization"))
import paths
import render_refpose_gifs as g

from training.pretrain.train_conditioned_pose import ReferenceGuidedPose

POSE_W = paths.POSE_WEIGHT
OUT = ROOT/"output"/"refpose_preview"/"context_sweep"
WINDOW, STRIDE = 24, 8


def localize(track, origin):
    r = Rotation.from_quat(origin[3:])
    return np.concatenate((r.inv().apply(track[:, :3]-origin[:3]),
                           (r.inv()*Rotation.from_quat(track[:, 3:])).as_quat()), -1).astype(np.float32)


def pad_to_window(frames, k):
    """把长度 k 的序列冻结填充到 WINDOW：前 WINDOW-k 帧重复第 0 帧。"""
    k = min(k, WINDOW)
    live = frames[-k:]
    pad = [live[0]] * (WINDOW - k)
    return np.stack(pad + list(live))


def run(raw, seed_start, seconds, ctx_k):
    mean = np.load(g.STATS/"pose_m.npy").astype(np.float32)
    std = np.load(g.STATS/"pose_s.npy").astype(np.float32)
    ck = torch.load(POSE_W, map_location="cpu", weights_only=False)
    net = ReferenceGuidedPose(78, ck["width"]).eval(); net.load_state_dict(ck["model"])

    P = np.load(g.MMAP/raw/"p.npy").astype(np.float32)
    RT = np.load(g.MMAP/raw/"r.npy").astype(np.float32)
    K = np.load(g.MMAP/raw/"k.npy").astype(np.float32)

    s = seed_start
    max_steps = (len(K) - s - 2*WINDOW + 1)//STRIDE
    steps = max(1, min(int(seconds*120/STRIDE), max_steps))

    fp = [np.asarray(x) for x in ((P[s:s+WINDOW]-mean)/std)]
    fr = [np.asarray(x) for x in RT[s:s+WINDOW]]
    fk = [np.asarray(x) for x in K[s:s+WINDOW]]
    out_p, out_k = [], []

    with torch.no_grad():
        for st in range(steps):
            pa = pad_to_window(fp, ctx_k)
            ra = pad_to_window(fr, ctx_k)
            ka = pad_to_window(fk, ctx_k)
            origin = ka[-1]
            hist = np.concatenate((np.concatenate((pa, ra), -1).reshape(WINDOW, -1),
                                   localize(ka, origin)), -1).astype(np.float32)
            nxt = s + WINDOW + st*STRIDE
            plan = localize(K[nxt-1:nxt-1+WINDOW], origin)
            assert plan.shape == (WINDOW, 7)

            o = net(torch.from_numpy(hist[None]), torch.from_numpy(plan[None]),
                    torch.zeros(1, WINDOW, 702), torch.zeros(1, WINDOW, 1)).numpy()[0].reshape(WINDOW, 78, 9)
            for k in range(STRIDE):
                fp.append(o[k, :, :3]); fr.append(o[k, :, 3:]); fk.append(K[nxt+k])
                out_p.append(o[k, :, :3]); out_k.append(K[nxt+k])

    return np.stack(out_p)*std+mean, np.stack(out_k)


def evaluate(pose, root, raw, seed_start, tag):
    world = g.place(pose, root)
    P = np.load(g.MMAP/raw/"p.npy").astype(np.float32)
    K = np.load(g.MMAP/raw/"k.npy").astype(np.float32)
    gs = seed_start + WINDOW
    n = min(len(world), len(P)-gs)
    gt = g.place(P[gs:gs+n], K[gs:gs+n])
    per = (np.linalg.norm(world[:n]-gt, axis=-1)*100).mean(1)
    return {
        "tag": tag, "frames": int(n),
        "mean_cm": round(float(per.mean()), 2),
        "first_s_cm": round(float(per[:120].mean()), 2),
        "last_s_cm": round(float(per[-120:].mean()), 2),
        "p90_cm": round(float(np.percentile(per, 90)), 2),
        "max_cm": round(float(per.max()), 2),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="027106")
    ap.add_argument("--seed", type=int, default=120)
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--contexts", default="4,6,8,12,24")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    report = []
    for k in [int(x) for x in args.contexts.split(",") if x]:
        pose, root = run(args.raw, args.seed, args.seconds, k)
        st = evaluate(pose, root, args.raw, args.seed, f"K={k}")
        report.append(st)
        np.savez_compressed(OUT/f"K{k}.npz", frames=pose.astype(np.float32),
                            root_frames=root.astype(np.float32), fps=np.float32(120.0))
        print(json.dumps(st, ensure_ascii=False), flush=True)
    (OUT/"report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
