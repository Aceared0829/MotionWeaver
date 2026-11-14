"""把 0.2 秒填补窗口顺着原轨迹滚成一段可看的长动画。"""

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

from motionweaver.refpose import ReferenceGuidedPose

MMAP = paths.MMAP
STATS = paths.STATS
WEIGHT = paths.POSE_WEIGHT
OUT = ROOT / "output" / "refpose_preview" / "gifs"
WINDOW = 24
STRIDE = 8
SECONDS = 8
W, H = 720, 460
SCALE = 180


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


def rollout(model, mean, std, positions, rotations, root, start, steps):
    pieces, roots = [], []
    with torch.no_grad():
        for step in range(steps):
            begin = start + step * STRIDE
            middle, end = begin + WINDOW, begin + 2 * WINDOW
            origin = root[middle - 1]
            pose = (positions[begin:end] - mean) / std
            combined = np.concatenate((pose, rotations[begin:end]), axis=-1).reshape(48, -1)
            history = np.concatenate((combined[:WINDOW], root_local(root[begin:middle], origin)), axis=-1)
            future = root_local(root[middle:end], origin)
            target = combined[WINDOW:]
            mask = np.zeros((1, WINDOW, 1), dtype=np.float32)
            mask[0, -1] = 1
            predicted = model(
                torch.from_numpy(history[None]),
                torch.from_numpy(future[None]),
                torch.from_numpy((target * mask[0])[None]),
                torch.from_numpy(mask),
            ).numpy()[0].reshape(WINDOW, 78, 9)
            pieces.append(predicted[:STRIDE, ..., :3] * std + mean)
            roots.append(root[middle:middle + STRIDE])
    return np.concatenate(pieces), np.concatenate(roots)


def draw_frame(world, frame, parents, axis, font, title):
    image = Image.new("RGB", (W, H), "#10141a")
    draw = ImageDraw.Draw(image)
    draw.text((16, 12), title, fill="#f4f7fb", font=font)
    draw.text((16, 40), f"{frame / 120:.1f} / {SECONDS:.0f} 秒   橙 = 模型补出来的身体", fill="#ffb15a", font=font)
    camera = float(world[frame, 1, [0, 2]] @ axis)
    ground = 370
    draw.line((30, ground, W - 30, ground), fill="#314158", width=2)
    joints = world[frame]
    along = joints[:, [0, 2]] @ axis
    sx = W / 2 + (along - camera) * SCALE
    sy = ground - joints[:, 1] * SCALE
    for index, parent in enumerate(parents):
        if parent < 0:
            continue
        if np.linalg.norm(joints[index] - joints[parent]) > 0.45:
            continue
        draw.line((float(sx[parent]), float(sy[parent]), float(sx[index]), float(sy[index])), fill="#ffb15a", width=4)
    return image


def render(model, mean, std, parents, font, raw, start, title, name):
    folder = MMAP / raw
    positions = np.load(folder / "p.npy")
    rotations = np.load(folder / "r.npy")
    root = np.load(folder / "k.npy")
    steps = int(SECONDS * 120 / STRIDE)
    if start + WINDOW + steps * STRIDE + WINDOW >= len(positions):
        raise RuntimeError(f"{raw} 不够长")
    local, roots = rollout(model, mean, std, positions, rotations, root, start, steps)
    world = place(local, roots)
    travel = roots[-1, [0, 2]] - roots[0, [0, 2]]
    axis = travel / max(np.linalg.norm(travel), 1e-6)
    frames = [draw_frame(world, i, parents, axis, font, title) for i in range(0, len(world), 8)]
    path = OUT / name
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=66, loop=0)
    return {"file": str(path), "seconds": round(len(world) / 120, 2), "gif_frames": len(frames), "bytes": path.stat().st_size}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    skeleton = json.loads((MMAP / "skeleton.json").read_text(encoding="utf-8"))
    parents = [bone["parent"] for bone in skeleton["bones"]]
    mean = np.load(STATS / "pose_m.npy").astype(np.float32)
    std = np.load(STATS / "pose_s.npy").astype(np.float32)
    checkpoint = torch.load(WEIGHT, map_location="cpu", weights_only=False)
    model = ReferenceGuidedPose(78, checkpoint["width"]).eval()
    model.load_state_dict(checkpoint["model"])
    font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 18)
    jobs = [
        ("000201", 180, "走 · 连续 8 秒", "walk_8s.gif"),
        ("028436", 200, "蹲着走 · 连续 8 秒", "crouch_8s.gif"),
        ("000171", 800, "跑 · 连续 8 秒", "run_8s.gif"),
    ]
    for raw, start, title, name in jobs:
        print(json.dumps(render(model, mean, std, parents, font, raw, start, title, name), ensure_ascii=False))


if __name__ == "__main__":
    main()
