
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

NOTES = {1: "每窗重置", 4: "", 8: "", 0: "完整闭环"}


def error_by_n():
    """从 segment_reset/report.json 读每档的平均关节误差。

    以前这里是四个写死的字符串（"1.8cm / 5.7cm / 9.7cm / 53.5cm"），
    和同目录 report.json 记的 2.94 / 7.75 / 12.52 / 73.08 一个都对不上。
    查看器上给人看的数字必须可复算，所以改成读报告。
    """
    report = SRC / "report.json"
    if not report.is_file():
        return {}
    rows = json.loads(report.read_text(encoding="utf-8"))
    out = {}
    for row in rows:
        tag = str(row.get("tag", ""))
        head = tag.split("(")[0].replace("N=", "").strip()
        if head.isdigit() and "joint_err_mean_cm" in row:
            out[int(head)] = float(row["joint_err_mean_cm"])
    return out


ERR = error_by_n()
clips = []
for N in (1, 4, 8, 0):
    path = SRC/f"N{N}.npz"
    if not path.is_file(): continue
    d = np.load(path)
    W = compose(d["frames"].astype(float), d["root_frames"].astype(float))
    note = NOTES[N]
    err = ERR.get(N)
    label = f"N={N}" + (f" {note}" if note else "") + (f" · {err:.2f}cm" if err is not None else "")
    clips.append({
        "id": f"N{N}",
        "label": label,
        "fps": 120.0,
        "frames": int(W.shape[0]),
        "joints": np.round(W, 4).reshape(W.shape[0], -1).tolist(),
    })

payload = {"jointNames": names, "parents": parents, "clips": clips}
target = OUT/"motion.json"
target.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
print(json.dumps({"file": str(target), "mb": round(target.stat().st_size/1024/1024, 2),
                  "clips": [c["id"] for c in clips], "frames": [c["frames"] for c in clips]}, ensure_ascii=False))
