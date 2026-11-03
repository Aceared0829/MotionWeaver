"""批量渲染几种动作的对照 GIF。左为原动作，右为模型生成的身体。"""

import json
import sys

import numpy as np
import paths
import torch
from PIL import Image, ImageDraw, ImageFont
from scipy.spatial.transform import Rotation

ROOT = paths.ROOT
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "inference" / "visualization"))
import paths

from training.pretrain.train_conditioned_pose import ReferenceGuidedPose

WEIGHT = paths.POSE_WEIGHT
MMAP = paths.MMAP
CONTRACT = paths.CONTRACT
STATS = paths.STATS
OUT = ROOT / "output" / "refpose_preview" / "gifs"
FPS = 120.0
WINDOW = 24
N_WINDOWS = 8
PANEL_W, PANEL_H = 360, 420
SCALE = 190


def load_index():
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    return {clip["raw_file"]: clip for clip in contract["clips"]}


def find_raw(index, includes, excludes=()):
    for clip in index.values():
        name = clip["asset"].lower()
        if clip["split"] != "test" or clip["source_frames"] < 700:
            continue
        if all(token in name for token in includes) and not any(token in name for token in excludes):
            return clip["raw_file"]
    raise RuntimeError(f"找不到片段：{includes}")


def root_local(track, origin):
    reference = Rotation.from_quat(origin[3:])
    positions = reference.inv().apply(track[:, :3] - origin[:3])
    rotations = (reference.inv() * Rotation.from_quat(track[:, 3:])).as_quat()
    return np.concatenate((positions, rotations), axis=-1).astype(np.float32)


def place(local, root_abs):
    rotation = Rotation.from_quat(root_abs[:, 3:])
    flat = local.reshape(-1, 3)
    repeated = np.repeat(rotation.as_quat(), local.shape[1], axis=0)
    world = Rotation.from_quat(repeated).apply(flat).reshape(local.shape)
    return world + root_abs[:, None, :3]


def choose_start(root, kind, positions=None):
    speed = np.linalg.norm(np.diff(root[:, [0, 2]], axis=0), axis=-1) * FPS
    if kind == "jump":
        peak = int(np.argmax(positions[:, 1, 1]))
        return max(24, min(peak - 72, len(root) - WINDOW * (N_WINDOWS + 1) - 2))
    if kind == "idle":
        return 240
    window = 48
    scores = np.convolve(speed, np.ones(window) / window, mode="valid")
    return int(np.argmax(scores)) + 12


def generate(model, mean, std, positions, rotations, root, start):
    pred_chunks, gt_chunks, roots = [], [], []
    with torch.no_grad():
        for step in range(N_WINDOWS):
            begin = start + step * WINDOW
            middle, end = begin + WINDOW, begin + 2 * WINDOW
            origin = root[middle - 1]
            pose = (positions[begin:end] - mean) / std
            combined = np.concatenate((pose, rotations[begin:end]), axis=-1).reshape(48, -1)
            history = np.concatenate((combined[:WINDOW], root_local(root[begin:middle], origin)), axis=-1)
            future = root_local(root[middle:end], origin)
            zeros = torch.zeros(1, WINDOW, 702)
            mask = torch.zeros(1, WINDOW, 1)
            predicted = model(torch.from_numpy(history[None]), torch.from_numpy(future[None]), zeros, mask)
            local = predicted.numpy()[0].reshape(WINDOW, 78, 9)[..., :3] * std + mean
            pred_chunks.append(local)
            gt_chunks.append(positions[middle:end])
            roots.append(root[middle:end])
    return np.concatenate(pred_chunks), np.concatenate(gt_chunks), np.concatenate(roots)


def draw_pose(canvas, origin_x, joints, parents, color, axis, label, font, small):
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((origin_x + 8, 8, origin_x + PANEL_W - 8, PANEL_H - 8), radius=12, outline="#314158", width=2)
    draw.text((origin_x + 18, 16), label, fill=color, font=font)
    hips = joints[1]
    relative = joints - hips
    horizontal = relative[:, [0, 2]] @ axis
    screen_x = origin_x + PANEL_W / 2 + horizontal * SCALE
    screen_y = 330 - joints[:, 1] * SCALE
    ground = 330 - 0 * SCALE
    draw.line((origin_x + 28, ground, origin_x + PANEL_W - 28, ground), fill="#314158", width=2)
    for index, parent in enumerate(parents):
        if parent < 0:
            continue
        length = np.linalg.norm(joints[index] - joints[parent])
        if length > 0.45 or length < 1e-4:
            continue
        draw.line((screen_x[parent], screen_y[parent], screen_x[index], screen_y[index]), fill=color, width=3)
    draw.text((origin_x + 18, PANEL_H - 28), small, fill="#8ea0b8", font=font)


def render_gif(path, title, gt_world, pred_world, parents, font):
    travel = gt_world[-1, 1, [0, 2]] - gt_world[0, 1, [0, 2]]
    if np.linalg.norm(travel) < 0.2:
        travel = np.array([0.0, 1.0])
    axis = travel / np.linalg.norm(travel)
    frames = []
    for frame in range(0, len(gt_world), 4):
        image = Image.new("RGB", (PANEL_W * 2, PANEL_H), "#10141a")
        draw = ImageDraw.Draw(image)
        draw.text((16, 4), title, fill="#f4f7fb", font=font)
        draw_pose(image, 0, gt_world[frame], parents, "#8fb8ff", axis, "原动作", font, "没有改")
        draw_pose(image, PANEL_W, pred_world[frame], parents, "#ffb15a", axis, "模型生成", font, "无参考帧")
        frames.append(image)
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=66, loop=0, optimize=False)
    return len(frames)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    index = load_index()
    run_raw = find_raw(index, ("jog_ff",), ("injured", "grab", "avoid", "dance"))
    jobs = [
        ("walk", "000201", "走", "walk"),
        ("run", run_raw, "跑", "run"),
        ("crouch", "028436", "蹲着走", "walk"),
        ("jump", "028411", "跳高", "jump"),
        ("idle", "000002", "抱臂站着", "idle"),
    ]
    skeleton = json.loads((MMAP / "skeleton.json").read_text(encoding="utf-8"))
    parents = [bone["parent"] for bone in skeleton["bones"]]
    mean = np.load(STATS / "pose_m.npy").astype(np.float32)
    std = np.load(STATS / "pose_s.npy").astype(np.float32)
    checkpoint = torch.load(WEIGHT, map_location="cpu", weights_only=False)
    model = ReferenceGuidedPose(78, checkpoint["width"]).eval()
    model.load_state_dict(checkpoint["model"])
    font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 18)
    written = []
    for slug, raw, title, kind in jobs:
        clip = index[raw]
        folder = MMAP / raw
        positions = np.load(folder / "p.npy")
        rotations = np.load(folder / "r.npy")
        root = np.load(folder / "k.npy")
        start = choose_start(root, kind, positions)
        if start + WINDOW * (N_WINDOWS + 1) >= len(positions):
            start = 48
        pred_local, gt_local, roots = generate(model, mean, std, positions, rotations, root, start)
        pred_world = place(pred_local, roots)
        gt_world = place(gt_local, roots)
        path = OUT / f"{slug}.gif"
        count = render_gif(path, title, gt_world, pred_world, parents, font)
        written.append({"slug": slug, "title": title, "asset": clip["asset"], "raw_file": raw, "start": start, "frames": count, "path": str(path), "bytes": path.stat().st_size})
        print(json.dumps(written[-1], ensure_ascii=False))
    (OUT / "index.json").write_text(json.dumps(written, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
