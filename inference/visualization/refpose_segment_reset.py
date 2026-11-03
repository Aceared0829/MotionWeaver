
"""分段重置历史：每 N 个窗口用一次真值历史重置，阻断自回归误差累积。

不改训练、不改模型；只在推理时控制"多久回灌一次自己的输出"。
N=1 等价于每窗独立推理（完全不复用自身输出），N 越大越接近完整闭环。
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
import paths as P
import render_refpose_gifs as g

from training.pretrain.train_conditioned_pose import ReferenceGuidedPose

POSE_W = P.POSE_WEIGHT
OUT = ROOT/"output"/"refpose_preview"/"segment_reset"
WINDOW, STRIDE = 24, 8


def localize(track, origin):
    r = Rotation.from_quat(origin[3:])
    return np.concatenate((r.inv().apply(track[:, :3]-origin[:3]),
                           (r.inv()*Rotation.from_quat(track[:, 3:])).as_quat()), -1).astype(np.float32)


def run(raw, seed_start, seconds, reset_every, data_dir=""):
    mean = np.load(g.STATS/"pose_m.npy").astype(np.float32)
    std = np.load(g.STATS/"pose_s.npy").astype(np.float32)
    ck = torch.load(POSE_W, map_location="cpu", weights_only=False)
    net = ReferenceGuidedPose(78, ck["width"]).eval(); net.load_state_dict(ck["model"])

    base = Path(data_dir) if data_dir else (g.MMAP/raw)
    P = np.load(base/"p.npy").astype(np.float32)
    RT = np.load(base/"r.npy").astype(np.float32)
    K = np.load(base/"k.npy").astype(np.float32)

    s = seed_start
    max_steps = (len(K) - s - 2*WINDOW + 1)//STRIDE
    steps = max(1, min(int(seconds*120/STRIDE), max_steps))

    # 历史缓冲：起点是真值
    fp = [np.asarray(x) for x in ((P[s:s+WINDOW]-mean)/std)]
    fr = [np.asarray(x) for x in RT[s:s+WINDOW]]
    fk = [np.asarray(x) for x in K[s:s+WINDOW]]
    out_p, out_k, resets = [], [], []

    with torch.no_grad():
        for st in range(steps):
            # ---- 分段重置：每到 N 个窗口，用真值历史覆盖缓冲 ----
            if reset_every > 0 and st > 0 and st % reset_every == 0:
                nxt = s + WINDOW + st*STRIDE
                base = nxt - WINDOW           # 重置点前的 24 帧真值
                base = max(base, 0)
                fp = [np.asarray(x) for x in ((P[base:base+WINDOW]-mean)/std)]
                fr = [np.asarray(x) for x in RT[base:base+WINDOW]]
                fk = [np.asarray(x) for x in K[base:base+WINDOW]]
                resets.append(nxt)

            pa = np.stack(fp[-WINDOW:]); ra = np.stack(fr[-WINDOW:]); ka = np.stack(fk[-WINDOW:])
            origin = ka[-1]
            hist = np.concatenate((np.concatenate((pa, ra), -1).reshape(WINDOW, -1),
                                   localize(ka, origin)), -1).astype(np.float32)
            nxt = s + WINDOW + st*STRIDE
            plan = localize(K[nxt-1:nxt-1+WINDOW], origin)      # Root 用真值，隔离姿态网问题
            assert plan.shape == (WINDOW, 7), f"step {st} plan={plan.shape}"

            o = net(torch.from_numpy(hist[None]), torch.from_numpy(plan[None]),
                    torch.zeros(1, WINDOW, 702), torch.zeros(1, WINDOW, 1)).numpy()[0].reshape(WINDOW, 78, 9)
            for k in range(STRIDE):
                fp.append(o[k, :, :3]); fr.append(o[k, :, 3:]); fk.append(K[nxt+k])
                out_p.append(o[k, :, :3]); out_k.append(K[nxt+k])

    pose = np.stack(out_p)*std+mean
    root = np.stack(out_k)
    return pose, root, np.array(resets), nxt


def evaluate(pose, root, raw, seed_start, tag, data_dir=""):
    world = g.place(pose, root)
    base = Path(data_dir) if data_dir else (g.MMAP/raw)
    P = np.load(base/"p.npy").astype(np.float32)
    K = np.load(base/"k.npy").astype(np.float32)
    gt_start = seed_start + WINDOW
    n = min(len(world), len(P)-gt_start)
    gt = g.place(P[gt_start:gt_start+n], K[gt_start:gt_start+n])
    e = np.linalg.norm(world[:n]-gt, axis=-1)*100
    per = e.mean(1)
    return {
        "tag": tag,
        "frames": int(n),
        "joint_err_mean_cm": round(float(per.mean()), 2),
        "joint_err_first_cm": round(float(per[:20].mean()), 2),
        "joint_err_last_cm": round(float(per[-20:].mean()), 2),
        "joint_err_max_cm": round(float(per.max()), 2),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="020545")
    ap.add_argument("--data-dir", default="", help="改用指定目录的四键数据（如拼接后的长序列）")
    ap.add_argument("--seed", type=int, default=120)
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--resets", default="1,4,8,16,0", help="逗号分隔的 N 列表；0 表示不重置（完整闭环）")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    report = []
    for n in [int(x) for x in args.resets.split(",") if x != ""]:
        pose, root, rs, _last = run(args.raw, args.seed, args.seconds, n, args.data_dir)
        stats = evaluate(pose, root, args.raw, args.seed, f"N={n}" + ("(完整闭环)" if n == 0 else ""), args.data_dir)
        stats["reset_count"] = len(rs)
        report.append(stats)
        OUT.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(OUT/f"N{n}.npz", frames=pose.astype(np.float32),
                            root_frames=root.astype(np.float32),
                            fps=np.float32(120.0), resets=rs)
        print(json.dumps(stats, ensure_ascii=False), flush=True)

    (OUT/"report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
