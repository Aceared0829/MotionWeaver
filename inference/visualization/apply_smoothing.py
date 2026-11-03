
"""只平滑主链，末端指节/眼/颌保持原样（避免过度平滑）。"""
import json
import sys
from pathlib import Path

import numpy as np
from scipy.signal import savgol_filter
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"inference"/"visualization"))
import render_refpose_gifs as g

SRC = ROOT/"output"/"refpose_preview"/"matrix"
OUT = ROOT/"output"/"refpose_preview"/"matrix_smooth"
OUT.mkdir(parents=True, exist_ok=True)

skel = json.loads((g.MMAP/"skeleton.json").read_text(encoding="utf-8"))
names = [b["name"] for b in skel["bones"]]
GRID = json.loads((g.MMAP/"grid.json").read_text(encoding="utf-8")) if (g.MMAP/"grid.json").is_file() else None

def compose(local, root):
    r = Rotation.from_quat(root[:, 3:])
    return np.einsum("tij,tkj->tki", r.as_matrix(), local[:, :, :3]) + root[:, None, :3]

# 平滑范围：排除末端指节/眼/颌/Root
def is_tip(nm):
    return ("Hand" in nm) or ("Eye" in nm) or ("Jaw" in nm) or nm == "Root"

smooth_idx = [i for i, nm in enumerate(names) if not is_tip(nm)]
MAIN = ["Hips","Spine1","Spine2","Chest","Neck1","Neck2","Head",
        "LeftShoulder","LeftArm","LeftForeArm","LeftHand",
        "RightShoulder","RightArm","RightForeArm","RightHand",
        "LeftLeg","LeftShin","LeftFoot","RightLeg","RightShin","RightFoot"]
midx = [names.index(m) for m in MAIN if m in names]

def accel(W):
    a = np.diff(np.diff(W, axis=0), axis=0)
    return float(np.median(np.linalg.norm(a, axis=-1)[:, midx]) * 120*120)

P = np.load(g.MMAP/"027106"/"p.npy").astype(float)
K = np.load(g.MMAP/"027106"/"k.npy").astype(float)

def run(tag, win, poly):
    d = np.load(SRC/f"{tag}.npz")
    local = d["frames"].astype(np.float64)
    root = d["root_frames"].astype(np.float64)
    before = compose(local, root)
    sm = local.copy()
    for i in smooth_idx:
        sm[:, i, :] = savgol_filter(local[:, i, :], win, poly, axis=0, mode="interp")
    after = compose(sm, root)
    gt = compose(P[144:144+len(before)], K[144:144+len(before)])
    e0 = float((np.linalg.norm(before-gt, axis=-1)*100).mean())
    e1 = float((np.linalg.norm(after-gt, axis=-1)*100).mean())
    a0, a1 = accel(before), accel(after)
    return sm, root, a0, a1, e0, e1

SAVE_WIN = 11
print(f"主链平滑（末端保持原样），保存 win={SAVE_WIN}：")
for tag in ("p4_N1", "p4_N2"):
    for win, poly in ((11,3),(21,3),(31,3)):
        sm, root, a0, a1, e0, e1 = run(tag, win, poly)
        print(f"  {tag} win={win:2d}: 加速度 {a0:5.1f} → {a1:5.1f}   误差 {e0:.2f} → {e1:.2f} cm")
    # 保存 win=11 版本（延迟 ±46 ms，精度几乎无损）
    sm, root, a0, a1, e0, e1 = run(tag, SAVE_WIN, 3)
    np.savez_compressed(OUT/f"{tag}_smooth.npz",
                        frames=sm.astype(np.float32),
                        root_frames=root.astype(np.float32),
                        fps=np.float32(120.0))
print("saved")