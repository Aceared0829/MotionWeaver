"""为闭环推理结果导出只读 3D 数据；不改验收工具，也不写 review_web。"""

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

sys.path.insert(0, str(ROOT / "inference" / "visualization"))
import paths
from scipy.spatial.transform import Rotation

STATS = paths.STATS
MMAP = paths.MMAP
SOURCES = [
    ("closed_020545", "walk_arc_cw_loop_002 · 闭环", "020545", 120),
]
OUT = ROOT / "output" / "refpose_preview" / "viewer3d"


def load_clip(clip_id):
    path = paths.REVIEWS / "clips" / clip_id / "motion.npz"
    with np.load(path, allow_pickle=False) as archive:
        local = archive["frames"].astype(np.float64)
        roots = archive["root_frames"].astype(np.float64)
        fps = float(archive["fps"])
    rotation = Rotation.from_quat(roots[:, 3:])
    world = np.einsum("tij,tkj->tki", rotation.as_matrix(), local[:, :, :3]) + roots[:, None, :3]
    return world, fps


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    skeleton = json.loads((MMAP / "skeleton.json").read_text(encoding="utf-8"))
    names = [bone["name"] for bone in skeleton["bones"]]
    parents = [bone["parent"] for bone in skeleton["bones"]]
    clips = []
    for clip_id, label, raw, seed in SOURCES:
        world, fps = load_clip(clip_id)
        # 逐帧存；每帧 78 个关节 × 3 分量。保留 4 位小数，米制。
        frames = np.round(world, 4).reshape(len(world), -1).tolist()
        clips.append({
            "id": clip_id,
            "label": label,
            "source": raw,
            "seedFrame": seed,
            "fps": fps,
            "frames": len(world),
            "joints": frames,
        })
    payload = {"jointNames": names, "parents": parents, "clips": clips}
    target = OUT / "motion.json"
    target.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"file": str(target), "mb": round(target.stat().st_size / 1024 / 1024, 2),
                      "clips": len(clips), "frames": [c["frames"] for c in clips],
                      "joints": len(names)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
