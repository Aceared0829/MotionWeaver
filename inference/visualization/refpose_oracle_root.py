
"""Oracle-Root 模式：Root 用真值，姿态网只补身体。这是正确用法下的效果。

与闭环脚本的区别只有一处：plan 用未来真值 Root，而不是 Root 头自预测。
"""
import json
import sys
from pathlib import Path

import numpy as np
import torch
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT/"inference"/"visualization"))
import paths as P
import render_refpose_gifs as g

from training.pretrain.train_conditioned_pose import ReferenceGuidedPose

POSE_W = P.POSE_WEIGHT
OUT = ROOT / "output" / "refpose_preview" / "oracle_root"
WINDOW, STRIDE, SECONDS = 24, 8, 8


def localize(track, origin):
    r = Rotation.from_quat(origin[3:])
    return np.concatenate((r.inv().apply(track[:, :3] - origin[:3]),
                           (r.inv()*Rotation.from_quat(track[:, 3:])).as_quat()), -1).astype(np.float32)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    mean = np.load(g.STATS/"pose_m.npy").astype(np.float32)
    std = np.load(g.STATS/"pose_s.npy").astype(np.float32)
    ck = torch.load(POSE_W, map_location="cpu", weights_only=False)
    net = ReferenceGuidedPose(78, ck["width"]).eval(); net.load_state_dict(ck["model"])

    raw = "020545"
    P = np.load(g.MMAP/raw/"p.npy").astype(np.float32)
    RT = np.load(g.MMAP/raw/"r.npy").astype(np.float32)
    K = np.load(g.MMAP/raw/"k.npy").astype(np.float32)
    # 步数上限要满足：最后一步的 nxt-1+WINDOW <= len(K)。
    # nxt = s + WINDOW + st*STRIDE，故 st <= (len(K) - s - WINDOW - WINDOW + 1)/STRIDE。
    s = 120
    max_steps = (len(K) - s - 2*WINDOW + 1) // STRIDE
    steps = max(1, min(int(SECONDS*120/STRIDE), max_steps))
    print(f"步数 {steps} → {steps*STRIDE/120:.2f} 秒（真值片段共 {len(K)} 帧）", flush=True)

    fp = [np.asarray(x) for x in ((P[s:s+WINDOW]-mean)/std)]
    fr = [np.asarray(x) for x in RT[s:s+WINDOW]]
    fk = [np.asarray(x) for x in K[s:s+WINDOW]]
    out_p, out_r, out_k = [], [], []

    with torch.no_grad():
        for st in range(steps):
            pa = np.stack(fp[-WINDOW:]); ra = np.stack(fr[-WINDOW:]); ka = np.stack(fk[-WINDOW:])
            origin = ka[-1]
            hist = np.concatenate((np.concatenate((pa, ra), -1).reshape(WINDOW, -1), localize(ka, origin)), -1).astype(np.float32)
            t = torch.from_numpy(hist[None])
            nxt = s + WINDOW + st * STRIDE
            # 控制分支要 24 帧，且第 0 帧须对齐"当前时刻"，即 nxt-1 起算。
            plan = localize(K[nxt - 1:nxt - 1 + WINDOW], origin)   # ← 真值 Root，不预测
            assert hist.shape == (WINDOW, 709), f"step {st} hist={hist.shape}"
            assert plan.shape == (WINDOW, 7), f"step {st} plan={plan.shape} nxt={nxt} Klen={len(K)}"
            if st == 0:
                print(f"  step0 hist={hist.shape} plan={plan.shape} nxt={nxt}", flush=True)
            o = net(t, torch.from_numpy(plan[None]), torch.zeros(1, WINDOW, 702), torch.zeros(1, WINDOW, 1)).numpy()[0].reshape(WINDOW, 78, 9)
            for k in range(STRIDE):
                fp.append(o[k, :, :3]); fr.append(o[k, :, 3:]); fk.append(K[nxt + k])
                out_p.append(o[k, :, :3]); out_r.append(o[k, :, 3:]); out_k.append(K[nxt + k])

    pose = np.stack(out_p) * std + mean
    root = np.stack(out_k)
    world = g.place(pose, root)
    np.savez_compressed(OUT/"oracle_motion.npz", frames=pose.astype(np.float32),
                        root_frames=root.astype(np.float32), fps=np.float32(120.0))
    print(json.dumps({"frames": len(world), "out": str(OUT/"oracle_motion.npz")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
