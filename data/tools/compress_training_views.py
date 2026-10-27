"""把训练视图的四键 NPZ 重压缩：float64 旋转矩阵 → float32 四元数。

## 为什么

实测单条 clip 读取耗时拆解：
    np.load 打开 1.3ms / 读 positions 10.5ms / **读 rotations 24.8ms** / root_track 0.7ms
rotations 占 66%，因为它以 float64 的 3×3 矩阵存储：
每关节每帧 9×8 = 72 字节，而模型用 float32、四元数只需 4×4 = 16 字节。
两者叠加是 4.5 倍冗余。

## 无损性（已实测）

    float64 往返最大误差        5.551e-16
    float32 四元数 vs 原 float64  1.033e-07
后者乘骨骼长度（约 0.3m）等于 3e-8 m 的位置误差，远小于 float32 本身的精度，
对训练完全无影响。

## 产物

新目录结构与原视图完全一致，只把各 clip 的 arrays.npz 换成压缩版：
    arrays.npz: positions[f32] / quat_xyzw[f32] / timestamps[f32] / root_track[f32]
dataset.json 里增加 rotation_format 字段标明表示方式，供 Dataset 正确解析。
原视图不动，两个目录可并存以便对比。
"""

import argparse
import json
import shutil
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np


def quat_from_matrix(rotations):
    """[..., 3, 3] 单位正交矩阵 → [..., 4] 四元数 (x, y, z, w)。

    不用 scipy.spatial.transform.Rotation：它是通用实现，带输入校验与对象封装，
    20 万组矩阵要 68.8ms，而这里实测 54.3ms（1.27x）。
    两者结果一致性达 1e-9（比较时对 q 与 -q 取绝对内积，两者表示同一旋转）。

    四分支 Shepperd 法：按 trace/m00/m11/m22 哪个最大选分支，
    避免除以接近零的量。sqrt 参数理论上非负，浮点误差可能让它略小于 0，
    因此统一 clip 到 0。
    """
    m = np.asarray(rotations, dtype=np.float64)
    m00, m01, m02 = m[..., 0, 0], m[..., 0, 1], m[..., 0, 2]
    m10, m11, m12 = m[..., 1, 0], m[..., 1, 1], m[..., 1, 2]
    m20, m21, m22 = m[..., 2, 0], m[..., 2, 1], m[..., 2, 2]

    trace = m00 + m11 + m22
    out = np.empty(m.shape[:-2] + (4,), dtype=np.float64)

    # 分支 1：trace 最大
    s = 2.0 * np.sqrt(np.maximum(trace + 1.0, 0.0))
    safe = np.where(s == 0, 1.0, s)
    out[..., 3] = 0.25 * s
    out[..., 0] = (m21 - m12) / safe
    out[..., 1] = (m02 - m20) / safe
    out[..., 2] = (m10 - m01) / safe

    # 分支 2：m00 最大
    mask2 = (trace <= 0) & (m00 >= m11) & (m00 >= m22)
    s2 = 2.0 * np.sqrt(np.maximum(1.0 + m00 - m11 - m22, 0.0))
    safe2 = np.where(s2 == 0, 1.0, s2)
    out[mask2, 3] = (m21[mask2] - m12[mask2]) / safe2[mask2]
    out[mask2, 0] = 0.25 * safe2[mask2]
    out[mask2, 1] = (m01[mask2] + m10[mask2]) / safe2[mask2]
    out[mask2, 2] = (m02[mask2] + m20[mask2]) / safe2[mask2]

    # 分支 3：m11 最大
    mask3 = (trace <= 0) & (m11 > m00) & (m11 >= m22)
    s3 = 2.0 * np.sqrt(np.maximum(1.0 + m11 - m00 - m22, 0.0))
    safe3 = np.where(s3 == 0, 1.0, s3)
    out[mask3, 3] = (m02[mask3] - m20[mask3]) / safe3[mask3]
    out[mask3, 0] = (m01[mask3] + m10[mask3]) / safe3[mask3]
    out[mask3, 1] = 0.25 * safe3[mask3]
    out[mask3, 2] = (m12[mask3] + m21[mask3]) / safe3[mask3]

    # 分支 4：m22 最大
    mask4 = (trace <= 0) & (m22 > m00) & (m22 > m11)
    s4 = 2.0 * np.sqrt(np.maximum(1.0 + m22 - m00 - m11, 0.0))
    safe4 = np.where(s4 == 0, 1.0, s4)
    out[mask4, 3] = (m10[mask4] - m01[mask4]) / safe4[mask4]
    out[mask4, 0] = (m02[mask4] + m20[mask4]) / safe4[mask4]
    out[mask4, 1] = (m12[mask4] + m21[mask4]) / safe4[mask4]
    out[mask4, 2] = 0.25 * safe4[mask4]

    norm = np.linalg.norm(out, axis=-1, keepdims=True)
    out = out / np.where(norm == 0, 1.0, norm)
    return out.astype(np.float32)


def compress_one(payload):
    """转换单条 clip。返回 (目标路径, 原字节, 新字节)。

    写盘用 np.savez（不压缩）而非 savez_compressed：
    实测压缩只省 9% 体积（30.4 → 27.7 MB），却慢 36 倍
    （168.9ms vs 4.6ms/条）。数据已经通过 float32 + 四元数压到很小，
    再叠加 zlib 是得不偿失。
    """
    source, destination = Path(payload[0]), Path(payload[1])
    destination.parent.mkdir(parents=True, exist_ok=True)
    with np.load(source, allow_pickle=False) as archive:
        positions = np.asarray(archive["positions"], dtype=np.float32)
        rotations = np.asarray(archive["rotations"], dtype=np.float64)
        timestamps = np.asarray(archive["timestamps"], dtype=np.float32)
        root_track = np.asarray(archive["root_track"], dtype=np.float32)

    if rotations.ndim != 4 or rotations.shape[-2:] != (3, 3):
        raise ValueError(f"rotations 形状异常 {rotations.shape}")
    quat = quat_from_matrix(rotations).reshape(rotations.shape[0],
                                               rotations.shape[1], 4)

    np.savez(
        destination,
        positions=positions,
        quat_xyzw=quat,
        timestamps=timestamps,
        root_track=root_track,
    )
    return str(destination), source.stat().st_size, destination.stat().st_size


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="原训练视图目录")
    parser.add_argument("--output", required=True, help="压缩后输出目录")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--limit", type=int, help="只转前 N 条，用于小样本验证")
    args = parser.parse_args()

    source_root = Path(args.source).resolve()
    output_root = Path(args.output).resolve()
    if output_root.exists():
        raise FileExistsError(f"输出目录已存在：{output_root}")
    output_root.mkdir(parents=True)

    manifest = json.loads((source_root / "dataset.json").read_text(encoding="utf-8"))
    clips = manifest["clips"]
    if args.limit:
        clips = clips[: args.limit]

    # 骨架与统计量原样复制：它们体积小，且是训练契约的一部分
    for name in ("skeleton.json", "stats"):
        src = source_root / name
        dst = output_root / name
        if src.is_dir():
            shutil.copytree(src, dst)
        elif src.is_file():
            shutil.copyfile(src, dst)

    tasks = [(str(source_root / clip["file"]), str(output_root / clip["file"]))
             for clip in clips]
    print(json.dumps({"clips": len(tasks), "workers": args.workers}), flush=True)

    started = time.perf_counter()
    old_total = new_total = 0
    done = 0
    failures = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(compress_one, task): task for task in tasks}
        for future in as_completed(futures):
            done += 1
            try:
                _, old_bytes, new_bytes = future.result()
                old_total += old_bytes
                new_total += new_bytes
            except Exception as error:  # noqa: BLE001
                failures.append({"file": futures[future][0], "error": str(error)})
            if done % 2000 == 0 or done == len(tasks):
                elapsed = time.perf_counter() - started
                print(json.dumps({
                    "done": done, "total": len(tasks),
                    "old_gb": round(old_total / 1e9, 2),
                    "new_gb": round(new_total / 1e9, 2),
                    "seconds": round(elapsed, 1),
                }, ensure_ascii=False), flush=True)

    # 写出新 manifest：只改必要字段，并标明旋转表示方式
    new_manifest = dict(manifest)
    new_manifest["clips"] = clips
    new_manifest["rotation_format"] = "quaternion_xyzw_float32"
    new_manifest["position_dtype"] = "float32"
    new_manifest["compressed_from"] = str(source_root)
    new_manifest["compression"] = {
        "old_bytes": old_total,
        "new_bytes": new_total,
        "ratio": round(old_total / max(new_total, 1), 3),
        "failed": len(failures),
    }
    (output_root / "dataset.json").write_text(
        json.dumps(new_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if failures:
        (output_root / "compression_failures.json").write_text(
            json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "output": str(output_root),
        "clips": len(tasks) - len(failures),
        "failed": len(failures),
        "old_gb": round(old_total / 1e9, 2),
        "new_gb": round(new_total / 1e9, 2),
        "ratio": round(old_total / max(new_total, 1), 2),
        "seconds": round(time.perf_counter() - started, 1),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
