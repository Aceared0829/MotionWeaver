"""闭环推理：Root 头自己预测 Root，姿态网补身体，身体再回灌历史。

不读原动作的未来 Root 与未来姿态，只用最初 24 帧历史当种子。
"""

import json
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFont
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "inference" / "visualization"))
import paths
import render_refpose_gifs as g

from tools.train_root_plan import RootPlanHead
from training.pretrain.train_conditioned_pose import ReferenceGuidedPose

POSE_W = paths.POSE_WEIGHT
# 优先用 20 epoch 版；盘上没有就回退到早先那版。两个值必须保持不同，
# 否则 load_models 里的 "优先 A、回退 B" 退化成「A 不存在就直接炸」。
ROOT_W = paths.ROOT_WEIGHT
ROOT_W2 = paths.ROOT_WEIGHT_ALT
OUT = ROOT / "output" / "refpose_preview" / "closed_loop"
WINDOW = 24
STRIDE = 8
SECONDS = 8
W, H = 720, 460
SCALE = 175


def load_models():
    pose_ck = torch.load(POSE_W, map_location="cpu", weights_only=False)
    pose = ReferenceGuidedPose(78, pose_ck["width"]).eval()
    pose.load_state_dict(pose_ck["model"])
    root_path = ROOT_W if ROOT_W.is_file() else ROOT_W2
    root_ck = torch.load(root_path, map_location="cpu", weights_only=False)
    root = RootPlanHead(root_ck["width"]).eval()
    root.load_state_dict(root_ck["model"])
    return pose, root, root_path, root_ck


def localize(track, origin):
    """把 Root 轨道变到 origin 那帧的局部系。"""
    reference = Rotation.from_quat(origin[3:])
    positions = reference.inv().apply(track[:, :3] - origin[:3])
    quats = (reference.inv() * Rotation.from_quat(track[:, 3:])).as_quat()
    return np.concatenate((positions, quats), axis=-1).astype(np.float32)


def compose(origin, local):
    """localize 的逆运算：局部系变回绝对。"""
    reference = Rotation.from_quat(origin[3:])
    positions = reference.apply(local[:, :3]) + origin[:3]
    quats = (reference * Rotation.from_quat(local[:, 3:])).as_quat()
    return np.concatenate((positions, quats), axis=-1).astype(np.float32)


def build_step(pose_norm, rot6d, root_abs, origin_index, mean, std):
    """一步的 709 输入：身体归一化位置 + 6D 旋转 + 相对末帧的 Root。"""
    combined = np.concatenate((pose_norm, rot6d), axis=-1)
    origin = root_abs[origin_index]
    history_root = localize(root_abs[:origin_index + 1], origin)
    return combined, history_root, origin


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    index = g.load_index()
    mean = np.load(g.STATS / "pose_m.npy").astype(np.float32)
    std = np.load(g.STATS / "pose_s.npy").astype(np.float32)
    skeleton = json.loads((g.MMAP / "skeleton.json").read_text(encoding="utf-8"))
    parents = [bone["parent"] for bone in skeleton["bones"]]
    pose_net, root_net, root_path, root_ck = load_models()
    print(f"Root 头权重：{root_path}  训练验证 {root_ck['val_root_rmse_cm']:.2f} cm / {root_ck['val_heading_deg']:.2f} deg", flush=True)

    raw = "020545"
    if raw not in index:
        raise KeyError(f"clip {raw} 不在 contract 里，无法闭环推演")
    folder = g.MMAP / raw
    positions = np.load(folder / "p.npy")
    rotations = np.load(folder / "r.npy")
    root_all = np.load(folder / "k.npy")
    seed_start = 120
    seed = slice(seed_start, seed_start + WINDOW)
    seed_speed = float(np.linalg.norm(root_all[seed_start + WINDOW - 1, :2] - root_all[seed_start, :2]) / (WINDOW / 120))
    print(f"起始段速度 {seed_speed:.2f} m/s", flush=True)

    pose_hist = ((positions[seed] - mean) / std).astype(np.float32)
    rot_hist = rotations[seed].astype(np.float32)
    root_hist = root_all[seed].astype(np.float32)

    steps = int(SECONDS * 120 / STRIDE)
    pred_pose, pred_rot, pred_root = [], [], []
    frame_pose = [p for p in pose_hist]
    frame_rot = [r for r in rot_hist]
    frame_root = [r for r in root_hist]

    with torch.no_grad():
        for step in range(steps):
            pose_arr = np.stack(frame_pose[-WINDOW:])
            rot_arr = np.stack(frame_rot[-WINDOW:])
            root_arr = np.stack(frame_root[-WINDOW:])
            origin = root_arr[-1]
            combined = np.concatenate((pose_arr, rot_arr), axis=-1).reshape(WINDOW, -1)
            history = np.concatenate((combined, localize(root_arr, origin)), axis=-1).astype(np.float32)
            history_t = torch.from_numpy(history[None])
            plan = root_net(history_t).numpy()[0]
            plan_abs = compose(origin, plan)
            zeros = torch.zeros(1, WINDOW, 702)
            mask = torch.zeros(1, WINDOW, 1)
            out = pose_net(history_t, torch.from_numpy(plan[None]), zeros, mask).numpy()[0]
            out = out.reshape(WINDOW, 78, 9)
            body_norm = out[..., :3]
            body_rot = out[..., 3:]
            for k in range(STRIDE):
                frame_pose.append(body_norm[k])
                frame_rot.append(body_rot[k])
                frame_root.append(plan_abs[k])
                pred_pose.append(body_norm[k])
                pred_rot.append(body_rot[k])
                pred_root.append(plan_abs[k])

    pred_pose = np.stack(pred_pose)
    pred_root = np.stack(pred_root)
    world = g.place(pred_pose * std + mean, pred_root)

    gt_pose_start = seed_start + WINDOW
    gt_pose = positions[gt_pose_start:gt_pose_start + len(pred_pose)]
    gt_root = root_all[gt_pose_start:gt_pose_start + len(pred_pose)]
    gt_world = g.place(gt_pose, gt_root)
    n = min(len(world), len(gt_world))
    world, gt_world = world[:n], gt_world[:n]
    pred_root, gt_root = pred_root[:n], gt_root[:n]

    root_err = np.linalg.norm(pred_root[:20, :3] - gt_root[:20, :3], axis=-1)
    travel_pred = np.linalg.norm(pred_root[:, :2] - pred_root[0, :2], axis=-1)
    travel_gt = np.linalg.norm(gt_root[:, :2] - gt_root[0, :2], axis=-1)

    font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 18)
    small = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 15)
    frames = []
    for frame in range(0, n, 4):
        image = Image.new("RGB", (W, H), "#10141a")
        draw = ImageDraw.Draw(image)
        draw.text((16, 10), "闭环：Root 头自己预测 Root，姿态网补身体", fill="#f4f7fb", font=font)
        draw.text((16, 38), f"{frame/120:.1f} / {SECONDS:.0f} 秒   橙 = 闭环生成   蓝 = 原动作", fill="#8ea0b8", font=small)
        ground = 380
        draw.line((30, ground, W - 30, ground), fill="#314158", width=2)
        center = gt_root[frame][0] if frame < len(gt_root) else 0.0
        for joints, color, width in ((gt_world[frame], "#8fb8ff", 2), (world[frame], "#ffb15a", 4)):
            along = joints[:, 0]
            sx = W / 2 + (along - center) * SCALE
            sy = ground - joints[:, 1] * SCALE
            for i, p in enumerate(parents):
                if p < 0:
                    continue
                if np.linalg.norm(joints[i] - joints[p]) > 0.45:
                    continue
                draw.line((float(sx[p]), float(sy[p]), float(sx[i]), float(sy[i])), fill=color, width=width)
        frames.append(image)
    gif = OUT / "closed_loop.gif"
    frames[0].save(gif, save_all=True, append_images=frames[1:], duration=66, loop=0)

    still = Image.new("RGB", (W, H), "#10141a")
    d = ImageDraw.Draw(still)
    d.text((50, 30), "Root 头自预测的行走距离 vs 原动作", fill="#f4f7fb", font=font)
    left, right, top, bottom = 90, W - 50, 80, 400
    d.rectangle((left, top, right, bottom), outline="#314158")
    xs = np.linspace(0, n - 1, n)
    maxv = max(travel_gt.max(), travel_pred.max(), 0.1)
    for series, color in ((travel_gt, "#8fb8ff"), (travel_pred, "#ffb15a")):
        pts = [(left + (x / max(1, n - 1)) * (right - left), bottom - (v / maxv) * (bottom - top)) for x, v in zip(xs, series)]
        d.line(pts, fill=color, width=3)
    d.text((left, bottom + 12), "时间 →", fill="#8ea0b8", font=small)
    d.text((left, top - 32), f"累计位移，最大 {maxv:.2f} 米", fill="#8ea0b8", font=small)
    d.text((50, bottom + 44), f"前 20 帧 Root 位置误差 {root_err.mean()*100:.1f} cm；{SECONDS:.0f} 秒后里程差 {abs(travel_pred[-1]-travel_gt[-1])*100:.0f} cm", fill="#ffb15a", font=small)
    still_path = OUT / "root_travel.png"
    still.save(still_path)

    report = {
        "root_weights": str(root_path),
        "pose_weights": str(POSE_W),
        "seconds": SECONDS,
        "frames": int(n),
        "root_err_first20_cm": float(root_err.mean() * 100),
        "travel_pred_m": float(travel_pred[-1]),
        "travel_gt_m": float(travel_gt[-1]),
        "travel_diff_cm": float(abs(travel_pred[-1] - travel_gt[-1]) * 100),
        "gif": str(gif),
        "still": str(still_path),
    }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
