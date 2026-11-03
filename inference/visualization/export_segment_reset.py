
"""把 segment_reset 的结果导出成查看器能读的多片段格式。"""
import json
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "inference" / "visualization"))
import paths

SRC = ROOT/"output"/"refpose_preview"/"segment_reset"
OUT = ROOT/"output"/"refpose_preview"/"viewer3d"
MMAP = paths.MMAP
def compose(local, root):
    r = Rotation.from_quat(root[:, 3:])
    return np.einsum("tij,tkj->tki", r.as_matrix(), local[:, :, :3]) + root[:, None, :3]

skeleton = json.loads((MMAP/"skeleton.json").read_text(encoding="utf-8"))
names = [b["name"] for b in skeleton["bones"]]
parents = [b["parent"] for b in skeleton["bones"]]

LABELS = {1: "N=1 每窗重置 · 1.8cm", 4: "N=4 · 5.7cm", 8: "N=8 · 9.7cm", 0: "N=0 完整闭环 · 53.5cm"}
clips = []
for N in (1, 4, 8, 0):
    path = SRC/f"N{N}.npz"
    if not path.is_file(): continue
    d = np.load(path)
    W = compose(d["frames"].astype(float), d["root_frames"].astype(float))
    clips.append({
        "id": f"N{N}",
        "label": LABELS[N],
        "fps": 120.0,
        "frames": int(W.shape[0]),
        "joints": np.round(W, 4).reshape(W.shape[0], -1).tolist(),
    })

payload = {"jointNames": names, "parents": parents, "clips": clips}
target = OUT/"motion.json"
target.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
print(json.dumps({"file": str(target), "mb": round(target.stat().st_size/1024/1024, 2),
                  "clips": [c["id"] for c in clips], "frames": [c["frames"] for c in clips]}, ensure_ascii=False))
