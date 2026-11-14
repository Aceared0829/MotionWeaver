"""用封盘 refpose v3 权重渲染一段走路补间，专门看接触期脚是否还停在地上。

只读权重和 mmap，不改训练数据。位置通道直接反归一化后套 Root，不从 6D 做 FK。
"""

import base64
import io
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import paths
import torch
from matplotlib import font_manager
from PIL import Image
from scipy.spatial.transform import Rotation

ROOT = paths.ROOT
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "inference" / "visualization"))
import paths

from motionweaver.refpose import ReferenceGuidedPose

WEIGHT = paths.POSE_WEIGHT
MMAP = paths.MMAP
CONTRACT = paths.CONTRACT
STATS = paths.STATS
OUT = ROOT / "output" / "refpose_preview"
FPS = 120.00480019200768
WINDOW = 24
N_WINDOWS = 12
SKIP_DRAW = ("finger", "index", "middle", "ring", "pinky", "thumb", "eye", "jaw", "tongue")


def use_chinese_font():
    font = Path(r"C:\Windows\Fonts\msyh.ttc")
    if font.is_file():
        font_manager.fontManager.addfont(str(font))
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei"]
    plt.rcParams["axes.unicode_minus"] = False


def load_contract_clip():
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    chosen = None
    for clip in contract["clips"]:
        name = clip["asset"].lower()
        if clip["split"] == "test" and "neutral_walk_ff" in name and "grab" not in name:
            chosen = clip
            break
    if chosen is None:
        raise RuntimeError("没有找到 test 集的 neutral_walk_ff 片段")
    return chosen


def contacts_of(clip):
    packed = np.frombuffer(base64.b64decode(clip["contact_bits"]), dtype=np.uint8)
    count = clip["source_frames"] * 4
    return np.unpackbits(packed)[:count].reshape(clip["source_frames"], 4).astype(np.float32)


def root_local(track, origin):
    reference = Rotation.from_quat(origin[3:])
    positions = reference.inv().apply(track[:, :3] - origin[:3])
    rotations = (reference.inv() * Rotation.from_quat(track[:, 3:])).as_quat()
    return np.concatenate((positions, rotations), axis=-1).astype(np.float32)


def apply_root(local, root_plan):
    rotation = Rotation.from_quat(root_plan[:, 3:])
    flat = local.reshape(-1, 3)
    repeated = np.repeat(rotation.as_quat(), local.shape[1], axis=0)
    world = Rotation.from_quat(repeated).apply(flat).reshape(local.shape)
    return world + root_plan[:, None, :3]


def contact_speed(world_feet, contact):
    speed = np.linalg.norm(np.diff(world_feet, axis=0), axis=-1) * FPS
    pair = contact[1:] * contact[:-1]
    total = pair.sum()
    if total < 1:
        return None
    return float((speed * pair).sum() / total)


def main():
    use_chinese_font()
    OUT.mkdir(parents=True, exist_ok=True)
    clip = load_contract_clip()
    skeleton = json.loads((MMAP / "skeleton.json").read_text(encoding="utf-8"))
    names = [bone["name"] for bone in skeleton["bones"]]
    parents = [bone["parent"] for bone in skeleton["bones"]]
    roles = skeleton["roles"]
    foot_names = [roles[key] for key in ("left_foot", "left_toe", "right_foot", "right_toe")]
    foot_ids = [names.index(name) for name in foot_names]
    draw = [
        index for index, name in enumerate(names)
        if index > 0 and not any(token in name.lower() for token in SKIP_DRAW)
    ]
    mean = np.load(STATS / "pose_m.npy").astype(np.float32)
    std = np.load(STATS / "pose_s.npy").astype(np.float32)
    folder = MMAP / clip["raw_file"]
    positions = np.load(folder / "p.npy")
    rotations = np.load(folder / "r.npy")
    root = np.load(folder / "k.npy")
    contact = contacts_of(clip)
    start0 = 240
    if start0 + WINDOW + N_WINDOWS * WINDOW >= len(positions):
        raise RuntimeError(f"片段太短：{clip['asset']}")

    checkpoint = torch.load(WEIGHT, map_location="cpu", weights_only=False)
    model = ReferenceGuidedPose(78, checkpoint["width"])
    model.load_state_dict(checkpoint["model"])
    model.eval()

    pred_local = []
    gt_local = []
    absolute_roots = []
    contact_future = []
    with torch.no_grad():
        for step in range(N_WINDOWS):
            start = start0 + step * WINDOW
            middle, end = start + WINDOW, start + 2 * WINDOW
            origin = root[middle - 1]
            pose = (positions[start:end] - mean) / std
            combined = np.concatenate((pose, rotations[start:end]), axis=-1).reshape(48, -1)
            history = np.concatenate((combined[:WINDOW], root_local(root[start:middle], origin)), axis=-1)
            future = root_local(root[middle:end], origin)
            zeros = np.zeros((1, WINDOW, 702), dtype=np.float32)
            mask = np.zeros((1, WINDOW, 1), dtype=np.float32)
            predicted = model(
                torch.from_numpy(history[None]),
                torch.from_numpy(future[None]),
                torch.from_numpy(zeros),
                torch.from_numpy(mask),
            ).numpy()[0]
            pred_local.append(predicted.reshape(WINDOW, 78, 9)[..., :3] * std + mean)
            gt_local.append(positions[middle:end])
            absolute_roots.append(root[middle:end])
            contact_future.append(contact[middle:end])
    pred_local = np.concatenate(pred_local)
    gt_local = np.concatenate(gt_local)
    absolute_roots = np.concatenate(absolute_roots)
    contact_future = np.concatenate(contact_future)
    # 输出和 p.npy 一样，是每帧自己的 Root 水平系。画到同一地面必须用绝对 Root，不能用每窗不同的原点。
    pred_world = apply_root(pred_local, absolute_roots)
    gt_world = apply_root(gt_local, absolute_roots)

    left_contact = np.maximum(contact_future[:, 0], contact_future[:, 1])
    right_contact = np.maximum(contact_future[:, 2], contact_future[:, 3])
    speeds = {
        "gt_left": contact_speed(gt_world[:, foot_ids[0]], left_contact),
        "pred_left": contact_speed(pred_world[:, foot_ids[0]], left_contact),
        "gt_right": contact_speed(gt_world[:, foot_ids[2]], right_contact),
        "pred_right": contact_speed(pred_world[:, foot_ids[2]], right_contact),
    }
    if any(value is None or value > 0.2 for value in (speeds["gt_left"], speeds["gt_right"])):
        raise RuntimeError(f"真值接触脚没有停住，坐标系可能错了：{speeds}")

    travel = absolute_roots[-1, [0, 2]] - absolute_roots[0, [0, 2]]
    axis = travel / max(np.linalg.norm(travel), 1e-6)
    lateral = np.array([-axis[1], axis[0]])

    def project(points):
        horizontal = points[..., [0, 2]]
        along = horizontal @ axis
        side = horizontal @ lateral
        return along, points[..., 1], side

    gt_along, gt_up, gt_side = project(gt_world)
    pred_along, pred_up, pred_side = project(pred_world)
    frames = np.arange(len(gt_world))
    seconds = frames / FPS

    def drift(line, planted):
        out = np.full(len(line), np.nan)
        bands = np.where(planted > 0.5)[0]
        if not len(bands):
            return out
        spans = np.split(bands, np.where(np.diff(bands) > 1)[0] + 1)
        for span in spans:
            if len(span) < 4:
                continue
            out[span] = line[span] - line[span[0]]
        return out

    fig, axes = plt.subplots(2, 1, figsize=(12.5, 7.2), dpi=130, sharex=True)
    fig.patch.set_facecolor("#10141a")
    for ax, title, gt_line, pred_line, planted in (
        (axes[0], "左脚：相对落点又往前滑了多少", gt_along[:, foot_ids[0]], pred_along[:, foot_ids[0]], left_contact),
        (axes[1], "右脚：相对落点又往前滑了多少", gt_along[:, foot_ids[2]], pred_along[:, foot_ids[2]], right_contact),
    ):
        ax.set_facecolor("#10141a")
        ax.axhline(0, color="#314158", lw=1)
        ax.plot(seconds, drift(gt_line, planted) * 100, color="#8fb8ff", lw=2.2, label="原动作")
        ax.plot(seconds, drift(pred_line, planted) * 100, color="#ffb15a", lw=2.4, label="模型生成")
        ax.set_title(title + "    空白 = 这只脚在摆动，不计入", color="#e8eef7", loc="left", fontsize=13)
        ax.tick_params(colors="#b7c3d4")
        ax.grid(color="#243041", lw=0.6)
        for spine in ax.spines.values():
            spine.set_color("#314158")
        ax.set_ylabel("厘米", color="#b7c3d4")
    axes[1].set_xlabel("秒", color="#b7c3d4")
    axes[0].legend(facecolor="#18202b", edgecolor="#314158", labelcolor="#e8eef7", loc="upper left")
    ratio_l = speeds["pred_left"] / speeds["gt_left"]
    ratio_r = speeds["pred_right"] / speeds["gt_right"]
    fig.suptitle(
        f"无参考补间 · 着地之后脚还在往前蹭    左 {ratio_l:.1f}×    右 {ratio_r:.1f}×",
        color="#f4f7fb", fontsize=16, y=0.98,
    )
    fig.text(
        0.01, 0.01,
        "0 是这只脚刚着地的位置。原动作应贴着 0；橙色往上走就是脚在地上滑。未来 Root 用的是原动作真值，不是玩家轨迹。",
        color="#8ea0b8", fontsize=9,
    )
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    still = OUT / "foot_slide.png"
    fig.savefig(still, facecolor=fig.get_facecolor())
    plt.close(fig)

    sheet = plt.figure(figsize=(13, 6.4), dpi=120)
    sheet.patch.set_facecolor("#10141a")
    picks = np.linspace(8, len(gt_world) - 2, 6).astype(int)
    for plot_index, frame in enumerate(picks, 1):
        ax = sheet.add_subplot(1, 6, plot_index)
        ax.set_facecolor("#10141a")
        for index in draw:
            parent = parents[index]
            gt_len = np.hypot(gt_along[frame, index] - gt_along[frame, parent], gt_up[frame, index] - gt_up[frame, parent])
            if gt_len > 0.45:
                continue
            ax.plot(
                [gt_along[frame, parent], gt_along[frame, index]],
                [gt_up[frame, parent], gt_up[frame, index]],
                color="#6f93c9", lw=1.1, alpha=0.85, zorder=2,
            )
            ax.plot(
                [pred_along[frame, parent], pred_along[frame, index]],
                [pred_up[frame, parent], pred_up[frame, index]],
                color="#ffb15a", lw=1.5, alpha=0.95, zorder=3,
            )
        ground = min(gt_up[:, foot_ids[0]].min(), gt_up[:, foot_ids[2]].min())
        ax.axhline(ground, color="#314158", lw=1)
        for foot, planted, color in (
            (foot_ids[0], left_contact[frame], "#ff5d4a"),
            (foot_ids[2], right_contact[frame], "#ff5d4a"),
        ):
            if planted > 0.5:
                ax.scatter(pred_along[frame, foot], pred_up[frame, foot], s=28, color=color, zorder=4)
                ax.scatter(gt_along[frame, foot], gt_up[frame, foot], s=16, color="#8fb8ff", zorder=4)
        center = gt_along[frame, 1]
        ax.set_xlim(center - 0.85, center + 0.85)
        ax.set_ylim(-0.05, 1.85)
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_color("#243041")
        ax.set_title(f"{seconds[frame]:.2f}s", color="#d5deea", fontsize=10)
    sheet.suptitle("同一时刻叠在一起：蓝 = 原动作，橙 = 模型，红点 = 应该着地的生成脚", color="#f4f7fb", fontsize=13)
    sheet.tight_layout(rect=(0, 0, 1, 0.94))
    overlay = OUT / "pose_overlay.png"
    sheet.savefig(overlay, facecolor=sheet.get_facecolor())
    plt.close(sheet)

    gif_frames = []
    shown = range(0, len(gt_world), 4)
    trail = 18
    for frame in shown:
        fig, axes = plt.subplots(1, 2, figsize=(11.2, 4.6), dpi=90)
        fig.patch.set_facecolor("#10141a")
        ax = axes[0]
        ax.set_facecolor("#10141a")
        begin = max(0, frame - trail)
        for index in draw:
            parent = parents[index]
            bone_len = np.hypot(pred_along[frame, index] - pred_along[frame, parent], pred_up[frame, index] - pred_up[frame, parent])
            if bone_len > 0.45:
                continue
            ax.plot(
                [pred_along[frame, parent], pred_along[frame, index]],
                [pred_up[frame, parent], pred_up[frame, index]],
                color="#ffb15a", lw=1.7, zorder=3,
            )
            ax.plot(
                [gt_along[frame, parent], gt_along[frame, index]],
                [gt_up[frame, parent], gt_up[frame, index]],
                color="#8fb8ff", lw=1.0, alpha=0.55, zorder=2,
            )
        ax.plot(pred_along[begin:frame + 1, foot_ids[0]], pred_up[begin:frame + 1, foot_ids[0]], color="#ff5d4a", lw=1.2)
        ax.plot(pred_along[begin:frame + 1, foot_ids[2]], pred_up[begin:frame + 1, foot_ids[2]], color="#ffd166", lw=1.2)
        center = gt_along[frame, 1]
        ax.set_xlim(center - 0.9, center + 0.9)
        ax.set_ylim(-0.05, 1.9)
        ax.set_aspect("equal")
        ax.set_title("侧面：橙生成 / 蓝原动作", color="#e8eef7", loc="left", fontsize=11)
        ax.tick_params(colors="#8ea0b8")
        top = axes[1]
        top.set_facecolor("#10141a")
        top.plot(gt_along[:frame + 1, foot_ids[0]], gt_side[:frame + 1, foot_ids[0]], color="#8fb8ff", lw=1.0, alpha=0.7)
        top.plot(pred_along[:frame + 1, foot_ids[0]], pred_side[:frame + 1, foot_ids[0]], color="#ff5d4a", lw=1.6)
        top.plot(gt_along[:frame + 1, foot_ids[2]], gt_side[:frame + 1, foot_ids[2]], color="#6f93c9", lw=1.0, alpha=0.7)
        top.plot(pred_along[:frame + 1, foot_ids[2]], pred_side[:frame + 1, foot_ids[2]], color="#ffd166", lw=1.6)
        planted = np.where((left_contact[:frame + 1] > 0.5) | (right_contact[:frame + 1] > 0.5))[0]
        if len(planted):
            top.scatter(pred_along[planted, foot_ids[0]], pred_side[planted, foot_ids[0]], s=8, color="#ff5d4a")
        top.set_title("俯视脚轨迹：红左 / 黄右，淡色是原动作", color="#e8eef7", loc="left", fontsize=11)
        top.set_aspect("equal")
        top.tick_params(colors="#8ea0b8")
        for panel in axes:
            for spine in panel.spines.values():
                spine.set_color("#314158")
            panel.grid(color="#243041", lw=0.5)
        fig.suptitle(f"{seconds[frame]:.2f} 秒    接触期脚滑 左 {ratio_l:.1f}×  右 {ratio_r:.1f}×", color="#f4f7fb", fontsize=12)
        fig.tight_layout(rect=(0, 0, 1, 0.93))
        buf = io.BytesIO()
        fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
        plt.close(fig)
        buf.seek(0)
        gif_frames.append(Image.open(buf).convert("RGB"))
    gif = OUT / "walk_preview.gif"
    gif_frames[0].save(gif, save_all=True, append_images=gif_frames[1:], duration=66, loop=0, optimize=False)
    report = {
        "asset": clip["asset"],
        "raw_file": clip["raw_file"],
        "split": clip["split"],
        "start_frame": start0,
        "future_frames": len(gt_world),
        "reference": "none",
        "contact_speed_m_s": speeds,
        "slide_ratio_left": ratio_l,
        "slide_ratio_right": ratio_r,
        "images": [str(still), str(overlay), str(gif)],
    }
    (OUT / "metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
