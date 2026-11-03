"""站立历史 + 未来 Root 轨迹 + 稀疏未来姿态，模型补中间。"""

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
import render_refpose_gifs as g

from training.pretrain.train_conditioned_pose import ReferenceGuidedPose

OUT = ROOT / "output" / "refpose_preview" / "gifs"
IDLE = "016517"
WALK = "000201"
WALK_START = 374
N_WINDOWS = 8
WINDOW = 24
W, H = 760, 460
SCALE = 170


def rebase(segment, onto):
    local = g.root_local(segment, segment[0])
    rotation = Rotation.from_quat(onto[3:])
    pos = rotation.apply(local[:, :3]) + onto[:3]
    quat = (rotation * Rotation.from_quat(local[:, 3:])).as_quat()
    return np.concatenate((pos, quat), axis=-1).astype(np.float32)


def pack_history(positions, rotations, roots, origin, mean, std):
    pose = (positions - mean) / std
    combined = np.concatenate((pose, rotations), axis=-1).reshape(WINDOW, -1)
    return np.concatenate((combined, g.root_local(roots, origin)), axis=-1).astype(np.float32)


def fill(model, mean, std, history_pose, history_rot, history_root, future_root, target_pose, target_rot):
    origin = history_root[-1]
    history = pack_history(history_pose, history_rot, history_root, origin, mean, std)
    root_plan = g.root_local(future_root, origin)
    target = np.concatenate(((target_pose - mean) / std, target_rot), axis=-1).reshape(WINDOW, -1)
    mask = np.zeros((WINDOW, 1), dtype=np.float32)
    mask[23] = 1
    reference = target * mask
    with torch.no_grad():
        predicted = model(
            torch.from_numpy(history[None]),
            torch.from_numpy(root_plan[None]),
            torch.from_numpy(reference[None]),
            torch.from_numpy(mask[None]),
        ).numpy()[0]
    local = predicted.reshape(WINDOW, 78, 9)
    pose = local[..., :3] * std + mean
    rot = local[..., 3:]
    return pose, rot, mask[:, 0] > 0.5


def screen_of(joints, camera_along, axis):
    along = joints[:, [0, 2]] @ axis
    return W / 2 + (along - camera_along) * SCALE, 360 - joints[:, 1] * SCALE


def draw_bones(draw, joints, parents, color, camera_along, axis, width=3):
    screen_x, screen_y = screen_of(joints, camera_along, axis)
    for index, parent in enumerate(parents):
        if parent < 0:
            continue
        length = np.linalg.norm(joints[index] - joints[parent])
        if length > 0.45 or length < 1e-4:
            continue
        draw.line((float(screen_x[parent]), float(screen_y[parent]), float(screen_x[index]), float(screen_y[index])), fill=color, width=width)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    index = g.load_index()
    skeleton = json.loads((g.MMAP / "skeleton.json").read_text(encoding="utf-8"))
    parents = [bone["parent"] for bone in skeleton["bones"]]
    mean = np.load(g.STATS / "pose_m.npy").astype(np.float32)
    std = np.load(g.STATS / "pose_s.npy").astype(np.float32)
    checkpoint = torch.load(g.WEIGHT, map_location="cpu", weights_only=False)
    model = ReferenceGuidedPose(78, checkpoint["width"]).eval()
    model.load_state_dict(checkpoint["model"])
    font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 20)
    small = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 16)

    idle_p = np.load(g.MMAP / IDLE / "p.npy")
    idle_r = np.load(g.MMAP / IDLE / "r.npy")
    idle_k = np.load(g.MMAP / IDLE / "k.npy")
    walk_p = np.load(g.MMAP / WALK / "p.npy")
    walk_r = np.load(g.MMAP / WALK / "r.npy")
    walk_k = np.load(g.MMAP / WALK / "k.npy")
    idle_at = 240
    origin = idle_k[idle_at + WINDOW - 1]
    future_root = rebase(walk_k[WALK_START:WALK_START + N_WINDOWS * WINDOW], origin)
    given_pose = walk_p[WALK_START:WALK_START + N_WINDOWS * WINDOW]
    given_rot = walk_r[WALK_START:WALK_START + N_WINDOWS * WINDOW]

    hist_pose = idle_p[idle_at:idle_at + WINDOW]
    hist_rot = idle_r[idle_at:idle_at + WINDOW]
    hist_root = idle_k[idle_at:idle_at + WINDOW]
    chunks, roots, given = [], [], []
    for step in range(N_WINDOWS):
        sl = slice(step * WINDOW, (step + 1) * WINDOW)
        pose, rot, mask = fill(
            model, mean, std, hist_pose, hist_rot, hist_root,
            future_root[sl], given_pose[sl], given_rot[sl],
        )
        chunks.append(pose)
        roots.append(future_root[sl])
        given.append(mask)
        hist_pose, hist_rot, hist_root = pose, rot, future_root[sl]
    pred = np.concatenate(chunks)
    roots = np.concatenate(roots)
    given = np.concatenate(given)
    world = g.place(pred, roots)
    targets = g.place(given_pose, roots)
    idle_world = g.place(idle_p[idle_at:idle_at + WINDOW], idle_k[idle_at:idle_at + WINDOW])

    travel = roots[-1, [0, 2]] - roots[0, [0, 2]]
    axis = travel / max(np.linalg.norm(travel), 1e-6)
    path_h = roots[:, [0, 2]] @ axis

    def frame_image(current, label, color, show_idle=False):
        image = Image.new("RGB", (W, H), "#10141a")
        draw = ImageDraw.Draw(image)
        draw.text((18, 14), "站立历史 + 未来 Root + 一个目标姿态，中间由模型补", fill="#f4f7fb", font=font)
        draw.text((18, 42), label, fill=color, font=small)
        camera = float(world[min(current, len(world) - 1), 1, [0, 2]] @ axis)
        ground_y = 360
        draw.line((40, ground_y, W - 40, ground_y), fill="#314158", width=2)
        path_x = W / 2 + (path_h - camera) * SCALE
        pts = [(float(x), ground_y) for x in path_x]
        draw.line(pts, fill="#3ddc97", width=3)
        upcoming = np.where(given)[0]
        upcoming = upcoming[upcoming >= current][:1]
        for hit in upcoming:
            draw_bones(draw, targets[hit], parents, "#1f8a5b", camera, axis, 2)
        if show_idle:
            draw_bones(draw, idle_world[-1], parents, "#8fb8ff", camera, axis, 3)
        else:
            draw_bones(draw, world[current], parents, "#3ddc97" if given[current] else "#ffb15a", camera, axis, 4)
        draw.text((18, H - 28), "绿线 = 给定 Root    绿骨架 = 给定姿态    橙 = 模型补的", fill="#8ea0b8", font=small)
        return image

    frames = []
    for _ in range(10):
        frames.append(frame_image(0, "先看到站立。绿线是接下来要走的 Root，绿骨架是要到达的姿态。", "#8fb8ff", show_idle=True))
    for frame_no in range(0, len(world), 2):
        label = "这一帧是给定的目标姿态" if given[frame_no] else "这一帧是模型补出来的"
        frames.append(frame_image(frame_no, label, "#3ddc97" if given[frame_no] else "#ffb15a"))
    gif = OUT / "idle_fill.gif"
    frames[0].save(gif, save_all=True, append_images=frames[1:], duration=70, loop=0, optimize=False)
    still = frame_image(11, "站立出发后，模型补向第一个目标姿态", "#ffb15a")
    still_path = OUT / "idle_fill.png"
    still.save(still_path)
    print(json.dumps({
        "gif": str(gif),
        "still": str(still_path),
        "bytes": gif.stat().st_size,
        "frames": len(frames),
        "given_frames": int(given.sum()),
        "path_meters": float(np.linalg.norm(roots[-1, :3] - roots[0, :3])),
        "idle": index[IDLE]["asset"],
        "walk": index[WALK]["asset"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
