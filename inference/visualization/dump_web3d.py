"""把闭环推理结果导成三维关节序列，给浏览器查看器用。"""

import json
import sys
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "inference" / "visualization"))
import paths
import render_refpose_gifs as g
from refpose_closed_loop import compose, localize

from tools.train_root_plan import RootPlanHead
from training.pretrain.train_conditioned_pose import ReferenceGuidedPose

POSE_W = paths.POSE_WEIGHT
ROOT_W = paths.ROOT_WEIGHT
OUT = ROOT / "output" / "refpose_preview" / "web3d"
WINDOW, STRIDE, SECONDS = 24, 8, 10


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    mean = np.load(g.STATS / "pose_m.npy").astype(np.float32)
    std = np.load(g.STATS / "pose_s.npy").astype(np.float32)
    frame = json.loads((paths.MMAP / "skeleton.json").read_text(encoding="utf-8"))
    names = [b["name"] for b in frame["bones"]]
    parents = [b["parent"] for b in frame["bones"]]

    pose_ck = torch.load(POSE_W, map_location="cpu", weights_only=False)
    pose_net = ReferenceGuidedPose(78, pose_ck["width"]).eval()
    pose_net.load_state_dict(pose_ck["model"])
    root_ck = torch.load(ROOT_W, map_location="cpu", weights_only=False)
    root_net = RootPlanHead(root_ck["width"]).eval()
    root_net.load_state_dict(root_ck["model"])

    raw, seed_start = "020545", 120
    folder = g.MMAP / raw
    positions = np.load(folder / "p.npy")
    rotations = np.load(folder / "r.npy")
    root_all = np.load(folder / "k.npy")
    seed = slice(seed_start, seed_start + WINDOW)

    frame_pose = [p for p in ((positions[seed] - mean) / std).astype(np.float32)]
    frame_rot = [r for r in rotations[seed].astype(np.float32)]
    frame_root = [r for r in root_all[seed].astype(np.float32)]
    pred_pose, pred_root = [], []
    steps = int(SECONDS * 120 / STRIDE)
    with torch.no_grad():
        for _ in range(steps):
            pose_arr = np.stack(frame_pose[-WINDOW:])
            rot_arr = np.stack(frame_rot[-WINDOW:])
            root_arr = np.stack(frame_root[-WINDOW:])
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
                frame_pose.append(out[k, ..., :3])
                frame_rot.append(out[k, ..., 3:])
                frame_root.append(plan_abs[k])
                pred_pose.append(out[k, ..., :3])
                pred_root.append(plan_abs[k])
    pred_pose = np.stack(pred_pose)
    pred_root = np.stack(pred_root)
    world = g.place(pred_pose * std + mean, pred_root)

    start = seed_start
    gt_world = g.place(positions[start:start + len(world)], root_all[start:start + len(world)])

    # UE 坐标 (x, y 上, z) 转成浏览器习惯的 y 向上、z 向前：直接沿用 x 右 / y 上 / z 前。
    bones = []
    for i, name in enumerate(names):
        length = 0.0
        if parents[i] >= 0:
            length = float(np.linalg.norm(world[0, i] - world[0, parents[i]]))
        bones.append({"name": name, "parent": parents[i], "radius": max(0.012, length * 0.09), "length": length})

    def pack(frames):
        return [[[round(float(v), 4) for v in joint] for joint in f] for f in frames[::2]]

    payload = {
        "fps": 60,
        "bones": bones,
        "generated": pack(world),
        "reference": pack(gt_world),
        "meta": {
            "clip": raw,
            "seconds": round(len(world) / 120, 2),
            "note": "闭环：Root 头自预测 Root，姿态网补身体，身体回灌历史",
        },
    }
    out = OUT / "motion.json"
    out.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"file": str(out), "kb": round(out.stat().st_size / 1024),
                      "frames": len(world), "bones": len(bones)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
