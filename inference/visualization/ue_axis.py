
"""坐标轴转换：ours(forward=+Z, up=+Y, left=+X) -> UE(forward=+X, up=+Z, right=+Y)。

实测确认（027106 逐帧行进方向与 R*轴点积统计）：
  ours: forward=+Z, up=+Y, right=-X   （左手系）
  UE  : forward=+X, up=+Z, right=+Y   （左手系）
映射：
  ue_x =  ours_z
  ue_y = -ours_x
  ue_z =  ours_y
"""
import numpy as np
from scipy.spatial.transform import Rotation

# ours -> ue 的基变换：ue = C @ ours
C = np.array([[0.0, 0.0, 1.0],
              [-1.0, 0.0, 0.0],
              [0.0, 1.0, 0.0]])


def vec_to_ue(v):
    v = np.asarray(v, dtype=np.float64)
    return v @ C.T


def to_ue_pos(p):
    return vec_to_ue(np.asarray(p, dtype=np.float64))


def to_ue_quat(q_xyzw):
    q = np.asarray(q_xyzw, dtype=np.float64)
    single = (q.ndim == 1)
    if single:
        q = q[None]
    mats = Rotation.from_quat(q).as_matrix()
    mats_ue = np.einsum("ij,njk,lk->nil", C, mats, C)
    out = Rotation.from_matrix(mats_ue).as_quat()
    return out[0] if single else out


def to_ue_root(root):
    root = np.asarray(root, dtype=np.float64)
    pos = to_ue_pos(root[:, :3])
    quat = to_ue_quat(root[:, 3:])
    return np.concatenate((pos, quat), axis=-1)


def to_ue_joints(local, root_ue):
    """局部关节位置(在 root 局部系下) + UE root -> UE 世界坐标。"""
    jl = vec_to_ue(np.asarray(local, dtype=np.float64))
    r = Rotation.from_quat(root_ue[:, 3:])
    return np.einsum("nij,nkj->nki", r.as_matrix(), jl) + root_ue[:, None, :3]


if __name__ == "__main__":
    import json

    import paths
    MM = paths.MMAP
    skel = json.loads((MM/"skeleton.json").read_text(encoding="utf-8"))
    names = [b["name"] for b in skel["bones"]]
    idx = {v: i for i, v in enumerate(names)}
    P = np.load(MM/"027106"/"p.npy").astype(np.float64)
    K = np.load(MM/"027106"/"k.npy").astype(np.float64)

    f = 300
    root_ue = to_ue_root(K[f:f+2])
    W = to_ue_joints(P[f:f+2], root_ue)
    hips, head = W[0, idx["Hips"]], W[0, idx["Head"]]
    lf, rf = W[0, idx["LeftFoot"]], W[0, idx["RightFoot"]]
    R_ue = Rotation.from_quat(root_ue[0, 3:])
    fwd = R_ue.apply([1, 0, 0])

    travel = K[f+60, :3] - K[f-60, :3]
    travel = vec_to_ue(travel / np.linalg.norm(travel))

    print(f"UE 坐标验证（帧 {f}）：")
    print("  Hips        =", np.round(hips, 3))
    print(f"  up (head-hips) Z 分量 = {head[2] - hips[2]:.3f}  (>0 正确)")
    print("  fwd (R*+X)  =", np.round(fwd, 3))
    print("  行进方向    =", np.round(travel, 3))
    print(f"  fwd·行进    = {np.dot(fwd, travel):.3f}  (应接近 +1)")
    print(f"  左脚-右脚 Y = {lf[1] - rf[1]:.3f}  (应 <0，因为左在 -Y)")
