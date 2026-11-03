"""把闭环结果导成与导出批次一致的结构，交给 motion_review_server 验收界面显示。

不重写查看器：只产出 skeleton.json 与 clips/<id>.npz（frames/root_frames/fps）。
"""

import json
import sys

import numpy as np
import paths
import torch

ROOT = paths.ROOT
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "inference" / "visualization"))
import paths
import render_refpose_gifs as g
from refpose_closed_loop import compose, localize

from tools.train_root_plan import RootPlanHead
from training.pretrain.train_conditioned_pose import ReferenceGuidedPose

POSE_W = paths.POSE_WEIGHT
ROOT_W = paths.ROOT_WEIGHT
OUT = paths.REVIEWS
WINDOW, STRIDE, SECONDS = 24, 8, 10


def rollout(root_net, pose_net, mean, std, positions, rotations, root_all, seed_start):
    seed = slice(seed_start, seed_start + WINDOW)
    fp = [p for p in ((positions[seed] - mean) / std).astype(np.float32)]
    fr = [r for r in rotations[seed].astype(np.float32)]
    fk = [r for r in root_all[seed].astype(np.float32)]
    local_pose, abs_root = [], []
    steps = int(SECONDS * 120 / STRIDE)
    with torch.no_grad():
        for _ in range(steps):
            pose_arr = np.stack(fp[-WINDOW:])
            rot_arr = np.stack(fr[-WINDOW:])
            root_arr = np.stack(fk[-WINDOW:])
            origin = root_arr[-1]
            combined = np.concatenate((pose_arr, rot_arr), axis=-1).reshape(WINDOW, -1)
            history = np.concatenate((combined, localize(root_arr, origin)), axis=-1).astype(np.float32)
            history_t = torch.from_numpy(history[None])
            plan = root_net(history_t).numpy()[0]
            plan_abs = compose(origin, plan)
            out = pose_net(history_t, torch.from_numpy(plan[None]),
                           torch.zeros(1, WINDOW, 702), torch.zeros(1, WINDOW, 1)).numpy()[0]
            out = out.reshape(WINDOW, 78, 9)
            for k in range(STRIDE):
                fp.append(out[k, ..., :3]); fr.append(out[k, ..., 3:]); fk.append(plan_abs[k])
                local_pose.append(out[k, ..., :3]); abs_root.append(plan_abs[k])
    return np.stack(local_pose) * std + mean, np.stack(abs_root)


def main():
    clips_dir = OUT / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    skeleton_src = json.loads((g.MMAP / "skeleton.json").read_text(encoding="utf-8"))
    (OUT / "skeleton.json").write_text(json.dumps(skeleton_src, ensure_ascii=False), encoding="utf-8")

    mean = np.load(g.STATS / "pose_m.npy").astype(np.float32)
    std = np.load(g.STATS / "pose_s.npy").astype(np.float32)
    pose_ck = torch.load(POSE_W, map_location="cpu", weights_only=False)
    pose_net = ReferenceGuidedPose(78, pose_ck["width"]).eval(); pose_net.load_state_dict(pose_ck["model"])
    root_ck = torch.load(ROOT_W, map_location="cpu", weights_only=False)
    root_net = RootPlanHead(root_ck["width"]).eval(); root_net.load_state_dict(root_ck["model"])

    jobs = [("020545", 120, "walk_arc_cw_loop_002 · 闭环"), ("028436", 300, "crouch_walk · 闭环")]
    rows = ["asset,name,status,raw,processed,fps,frames,joints,duration"]
    for raw, seed_start, label in jobs:
        folder = g.MMAP / raw
        positions = np.load(folder / "p.npy")
        rotations = np.load(folder / "r.npy")
        root_all = np.load(folder / "k.npy")
        pose_local, root_abs = rollout(root_net, pose_net, mean, std, positions, rotations, root_all, seed_start)
        clip_id = f"closed_{raw}"
        target = clips_dir / clip_id
        target.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(target / "motion.npz", frames=pose_local.astype(np.float32),
                            root_frames=root_abs.astype(np.float32), fps=np.float64(120.0))
        print(json.dumps({"clip": clip_id, "frames": len(pose_local),
                          "travel_m": round(float(np.linalg.norm(root_abs[-1, :2] - root_abs[0, :2])), 3)}, ensure_ascii=False))
        rows.append(f"{label},{clip_id},,{folder},{target},120.0,{len(pose_local)},78,{len(pose_local)/120:.3f}")
    (OUT / "export-index.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(json.dumps({"index": str(OUT / "export-index.csv")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
