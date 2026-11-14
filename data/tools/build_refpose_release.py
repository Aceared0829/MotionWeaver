"""把 SOMA 训练视图变成 refpose 可直接训练的「契约 + 统计量 + mmap」发布目录。

## 为什么要这个工具

Windows 上那条全量发布链（full_release / full_mmap）的产物没有入库，生成脚本
也没入库（docs/refpose-ue-export-research.md:441 记为 U1 风险）。仓库里现存两条
契约生成路径都对不上 refpose 训练入口：

- `data/runtime/conditioned_motion.build_index`：只吃 Schema **v2** 的 UE 数据集，
  产出带 stats / window_split_counts 的契约 —— 但 SOMA 训练视图是 Schema **v3**，
  直接调会在 `schema_version != 2` 处拒绝。
- `data/tools/build_soma_contract.py`：吃 Schema v3，但**不产出** `stats`
  与 `window_split_counts`，窗口是 24 历史 + 48 未来；而 `MotionWindows`
  在 `__init__` 里就 `contract["stats"]["pose_m"]`、`contract["window_split_counts"]`，
  在 `__getitem__` 里硬编码 `middle = start + 24, end = start + 48`。
  两者拼不上：拿它的产物去训练会在 KeyError 上直接死。

本工具一次读完 clip，直接产出 `MotionWindows` 真正需要的那份契约，
并顺手把训练用的 p/r/k.npy 铺成 mmap 目录，避免每次训练先把 96 GB NPZ 解压一遍。

## 产物结构

    <release>/
      contract/
        contract.json        window={history_frames:24, future_frames:24}
        windows.jsonl        {"clip_index": i, "start": s}
        stats/pose_mean.npy  (78, 3) float32，只用 train 分区的帧
        stats/pose_std.npy   (78, 3) float32，下限 1e-4
    <mmap>/
      clips/fps120/<clip_id>/{p.npy, r.npy, k.npy}   float32，整条 clip 全部帧
    <dataset>/skeleton.json   缺失时从 --skeleton 生成（含 roles，供脚部索引）

## 两个约定，改了会静默读错数据

1. **窗口步长 = future_frames = 24**，起点从 0 开始。docs 记的全量口径是
   71,127 clip / 2,490,688 窗口 = 35.0 窗口/条，平均 841.6 帧/条，
   841.6 / 24 = 35.07 —— 与「步长 24」逐位吻合。
2. **raw_file 与 mmap_file 是两个字段，语义不能混。**
   `raw_file` 必须是 NPZ 路径（`clips/fps120/<clip_id>/arrays.npz`）——
   `load_window` / `evaluate_conditioned_baselines` / `inference.runtime.conditioned_pose`
   都按 `contained_file(source, clip["raw_file"])` 读它，写成 mmap 目录会让评测与推理
   全部抛「数据文件不存在」。mmap 目录单独放在 `mmap_file`（`clips/fps120/<clip_id>`），
   训练侧优先用它；没有它时回落到 NPZ，两条路都能跑。

## 用法

    python -m data.tools.build_refpose_release \
        --dataset /run/media/acear/THUNDEROBOT/MotionWeaverData/processed/soma_train_v1/full-v1 \
        --release <数据根>/full_release --mmap <数据根>/full_mmap --workers 24

先 `--limit 200` 跑通再放全量；不给 `--mmap` 就只出契约（NPZ 直读也能训练）。
混合帧率的数据集必须用 `--fps-group fps120` 选一种，否则构建阶段就会拒绝。
"""

import argparse
import base64
import hashlib
import json
import time
from pathlib import Path

import numpy as np

CONTRACT_ID = "reference_guided_root_pose_v1"
# 120.0048 而不是整数 120：24 帧 = 199.992 ms，按 120 算会引入 8 µs 误差。
FPS = 120.00480019200768
HISTORY_FRAMES = 24
FUTURE_FRAMES = 24
SPLIT_RATIOS = (80, 10)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def split_of(asset, *, train_ratio=SPLIT_RATIOS[0], validation_ratio=SPLIT_RATIOS[1]):
    """按动作家族哈希划分 80/10/10，同一家族不跨分区。

    与 build_soma_contract.split_of 同一套规则：去掉结尾的序号，只对「家族名」取哈希。
    两处必须保持一致，否则同一份数据的 split 会随工具不同而变。
    """
    import re

    family = re.sub(r"(?:_?d+)+$", "", str(asset).split(".")[0].lower())
    bucket = int(hashlib.sha256(family.encode()).hexdigest()[:8], 16) % 100
    if bucket < train_ratio:
        return "train"
    if bucket < train_ratio + validation_ratio:
        return "validation"
    return "test"


def mmap_rel(clip):
    """mmap 子目录相对路径：clips/<fps_group>/<clip_id>。与 raw_file（NPZ）分开，语义不混。"""
    return f"clips/{clip.get('fps_group', 'fps120')}/{clip['clip_id']}"


def read_clips(dataset_root):
    """读 clips.jsonl（优先）或 dataset.json，返回 clip 记录列表。

    clips.jsonl 是 full-v1 唯一存在的清单；dataset.json 只在小样本目录里有。
    两者字段名一致，因此这里统一成同一形状。
    """
    dataset_root = Path(dataset_root)
    journal = dataset_root / "clips.jsonl"
    if journal.is_file():
        rows = []
        with journal.open(encoding="utf-8") as stream:
            for line in stream:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        if not rows:
            raise ValueError(f"clips.jsonl 是空的：{journal}")
        return rows, journal
    manifest = dataset_root / "dataset.json"
    if manifest.is_file():
        return json.loads(manifest.read_text(encoding="utf-8"))["clips"], manifest
    raise FileNotFoundError(f"{dataset_root} 下既没有 clips.jsonl 也没有 dataset.json")


class SkeletonView:
    """world_foot_contacts 只用到 names 与左右脚关节索引。

    必须是模块级类：ProcessPoolExecutor 要把这个对象 pickle 进子进程，
    闭包里定义的类 pickl 不了（会抛 "Can't pickle local object"）。
    """

    def __init__(self, names, roles):
        self.names = list(names)
        self.roles = dict(roles)
        self.left_foot_joint_idx = [self.names.index(roles["left_foot"]),
                                    self.names.index(roles["left_toe"])]
        self.right_foot_joint_idx = [self.names.index(roles["right_foot"]),
                                     self.names.index(roles["right_toe"])]


def skeleton_view(skeleton_path):
    """从骨架契约取出 names 与脚部索引。"""
    skeleton = json.loads(Path(skeleton_path).read_text(encoding="utf-8"))
    return SkeletonView(skeleton["skeleton"]["names"], skeleton["roles"]), skeleton


def write_skeleton_json(dataset_root, skeleton_path, skeleton):
    """在数据集里补 skeleton.json（bones 列表 + roles），供 --bone-json 默认查找。

    字段形状与 build_soma_contract.write_skeleton_view 保持一致：评测/训练都按
    `bones[*].name` 与 `roles` 定位脚部，与 SOMA 骨架契约的 names/parents 不同名。
    """
    target = Path(dataset_root) / "skeleton.json"
    if target.is_file():
        return target
    names = skeleton["skeleton"]["names"]
    parents = skeleton["skeleton"]["parents"]
    rest_positions = skeleton["skeleton"]["rest_positions"]
    rest_rotations = skeleton["skeleton"]["rest_rotations"]
    bones = [{"name": name, "parent": int(parents[index]),
              "position": [float(v) for v in rest_positions[index]],
              "rotation": [float(v) for v in rest_rotations[index]]}
             for index, name in enumerate(names)]
    view = {"schema_version": 3, "bone_count": len(names), "bones": bones,
            "roles": {key: value for key, value in skeleton["roles"].items()}}
    target.write_text(json.dumps(view, ensure_ascii=False), encoding="utf-8")
    return target


def window_starts(source_frames, history=HISTORY_FRAMES, future=FUTURE_FRAMES):
    """窗口起点：0 起步、步长 = future（与全量发布的 35.0 窗口/条口径一致）。"""
    last = source_frames - history - future
    if last < 0:
        return []
    return list(range(0, last + 1, future))


def _worker(job):
    """读一条 clip、写 mmap、算接触位、累加统计量。异常转成返回字符串，不炸整个池。"""
    (index, dataset_root, raw_rel, mmap_file, source_frames, bone_count,
     mmap_root, want_contacts, view) = job
    npz = Path(dataset_root) / raw_rel
    try:
        with np.load(npz, allow_pickle=False) as archive:
            positions = np.asarray(archive["positions"], dtype=np.float64)
            rotations = np.asarray(archive["rotations"], dtype=np.float64)
            root_track = np.asarray(archive["root_track"], dtype=np.float64)
        if positions.shape != (source_frames, bone_count, 3):
            return {"index": index, "error": f"positions 形状 {positions.shape} 与清单 {source_frames}x{bone_count}x3 不符"}
        if root_track.shape != (source_frames, 7):
            return {"index": index, "error": f"root_track 形状 {root_track.shape} 异常"}

        digest = sha256(npz)

        contact_bits = ""
        if want_contacts:
            from data.runtime.unreal_dataset import world_foot_contacts

            contacts = world_foot_contacts(positions, root_track, view, FPS)
            if contacts is None:
                return {"index": index, "error": "无法派生脚部接触"}
            packed = np.packbits((contacts.numpy() > 0.5).astype(np.uint8).ravel())
            contact_bits = base64.b64encode(packed.tobytes()).decode("ascii")

        if mmap_root is not None:
            target = Path(mmap_root) / mmap_file
            target.mkdir(parents=True, exist_ok=True)
            # r.npy 必须是 **6D row-major 交错**（每骨 6 个数），不是 3x3 矩阵。
            # MotionWindows.__getitem__ 直接把 r[start:end] 与 pose 拼成特征，
            # npz 回退路径也做的是 raw["rotations"][..., :2].reshape(frames, bone, 6)。
            # 存成 3x3 会得到 (frames, bone, 3, 3)，拼接时 ndim 不匹配直接抛 ValueError。
            six_d = rotations[..., :2].reshape(source_frames, bone_count, 6)
            for name, array in (("p", positions), ("r", six_d), ("k", root_track)):
                np.save(target / f"{name}.npy", array.astype(np.float32))

        return {"index": index, "digest": digest, "contact_bits": contact_bits,
                "windows": len(window_starts(source_frames)),
                "sum": positions.sum(0), "squares": (positions ** 2).sum(0),
                "frames": source_frames}
    except Exception as error:  # noqa: BLE001 —— 单条坏数据不该拖垮全量构建
        return {"index": index, "error": f"{type(error).__name__}: {error}"}


def build(dataset_root, release, mmap_root=None, skeleton_path=None, limit=None,
          workers=1, want_contacts=True, fps_group=None):
    dataset_root = Path(dataset_root).resolve()
    release = Path(release).resolve()
    mmap_root = Path(mmap_root).resolve() if mmap_root else None
    skeleton_path = Path(skeleton_path).resolve() if skeleton_path else None

    clips, manifest_path = read_clips(dataset_root)
    if limit:
        clips = clips[:limit]

    # 单一帧率是硬前提，不是风格问题：
    # contract["fps"] 只有一个值，MotionWindows.load_raw 拿它校验每条 clip 的时间轴
    # （abs(diff(ts) - 1/fps) <= 2e-6）。混帧率的数据集在这里就失败，报错点会落在
    # 训练第一次取数上、离真正原因很远，所以提前拒绝并说清楚。
    if fps_group:
        before = len(clips)
        clips = [clip for clip in clips if clip.get("fps_group", "fps120") == fps_group]
        if not clips:
            raise ValueError(f"没有 fps_group={fps_group} 的 clip（原 {before} 条）")
        print(json.dumps({"filtered_to": fps_group, "clips": len(clips)}), flush=True)

    rates = {round(float(clip["fps"]), 6) for clip in clips}
    if len(rates) > 1:
        groups = {}
        for clip in clips:
            groups.setdefault(clip.get("fps_group", "?"), 0)
            groups[clip.get("fps_group", "?")] += 1
        raise ValueError(
            f"数据集混了多个帧率 {sorted(rates)}（{groups}）。契约只能声明一个 fps，"
            "请先把同一数据集的 clip 归到同一 fps_group，或分多次构建。")
    print(json.dumps({"clips": len(clips), "manifest": manifest_path.name,
                      "fps": sorted(rates)}), flush=True)

    if skeleton_path is None:
        skeleton_path = Path(__file__).resolve().parents[2] / "data/skeletons/soma_uniform_v1.json"
    view, skeleton = skeleton_view(skeleton_path)
    bone_count = len(view.names)
    skeleton_json = write_skeleton_json(dataset_root, skeleton_path, skeleton)

    rows = []
    for clip in clips:
        source_frames = int(clip["source_frames"])
        rows.append({"clip": clip, "source_frames": source_frames,
                     "starts": window_starts(source_frames),
                     "split": split_of(clip["asset"]),
                     "category": clip.get("scene", "A")})

    jobs = []
    for index, row in enumerate(rows):
        clip = row["clip"]
        jobs.append((index, str(dataset_root), clip["file"], mmap_rel(clip),
                     row["source_frames"], bone_count, str(mmap_root) if mmap_root else None,
                     want_contacts, view))

    started = time.perf_counter()
    results = {}
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor, as_completed

        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_worker, job): job[0] for job in jobs}
            for done, future in enumerate(as_completed(futures), 1):
                out = future.result()
                results[out["index"]] = out
                if done % 200 == 0 or done == len(jobs):
                    rate = done / max(time.perf_counter() - started, 1e-6)
                    print(json.dumps({"converted": done, "total": len(jobs),
                                      "per_sec": round(rate, 1)}), flush=True)
    else:
        for position, job in enumerate(jobs, 1):
            results[job[0]] = _worker(job)
            if position % 200 == 0 or position == len(jobs):
                rate = position / max(time.perf_counter() - started, 1e-6)
                print(json.dumps({"converted": position, "total": len(jobs),
                                  "per_sec": round(rate, 1)}), flush=True)

    failed = {index: out["error"] for index, out in results.items() if "error" in out}
    if failed:
        sample = list(failed.items())[:5]
        raise RuntimeError(f"{len(failed)} 条 clip 失败（前 5 条）：{sample}")

    # ---- 统计量：只用 train 分区的真实帧，与 stats_scope 的既有口径一致 ----
    total = squares = None
    frames = 0
    for index, row in enumerate(rows):
        out = results[index]
        if row["split"] != "train":
            continue
        total = out["sum"] if total is None else total + out["sum"]
        squares = out["squares"] if squares is None else squares + out["squares"]
        frames += out["frames"]
    if frames == 0:
        raise ValueError("train 分区没有任何帧，无法计算归一化统计量")

    contract_dir = release / "contract"
    stats_dir = contract_dir / "stats"
    stats_dir.mkdir(parents=True, exist_ok=True)
    mean = (total / frames).astype(np.float32)
    variance = np.maximum((squares / frames) - (total / frames) ** 2, 0.0)
    std = np.maximum(np.sqrt(variance), 1e-4).astype(np.float32)
    np.save(stats_dir / "pose_mean.npy", mean)
    np.save(stats_dir / "pose_std.npy", std)

    contract_clips = []
    window_rows = []
    split_counts = {"train": 0, "validation": 0, "test": 0}
    window_split_counts = {"train": 0, "validation": 0, "test": 0}
    for index, row in enumerate(rows):
        clip = row["clip"]
        out = results[index]
        contract_clips.append({
            "asset": clip["asset"],
            "category": row["category"],
            "split": row["split"],
            "source_frames": row["source_frames"],
            # raw_file 必须是 NPZ 路径：load_window / evaluate_conditioned_baselines /
            # inference.runtime.conditioned_pose 都按 contained_file(source, raw_file) 读它。
            # mmap 目录单独放在 mmap_file，训练侧优先用它。
            "raw_file": clip["file"],
            "mmap_file": mmap_rel(clip),
            "raw_sha256": out["digest"],
            "contact_bits": out["contact_bits"],
            "fps": float(clip["fps"]),
            "scene": clip.get("scene", "A"),
        })
        for start in row["starts"]:
            window_rows.append({"clip_index": index, "start": int(start)})
            window_split_counts[row["split"]] += 1
        split_counts[row["split"]] += 1

    contract = {
        "schema_version": 2,
        "contract_id": CONTRACT_ID,
        "source_dataset": str(dataset_root),
        "source_manifest_sha256": sha256(manifest_path),
        "source_manifest_name": manifest_path.name,
        "skeleton_sha256": sha256(skeleton_json),
        "bone_count": bone_count,
        "fps": float(rows[0]["clip"]["fps"]),
        "window": {"history_frames": HISTORY_FRAMES, "future_frames": FUTURE_FRAMES},
        "stats": {"pose_m": {
            "mean": "pose_mean.npy", "mean_sha256": sha256(stats_dir / "pose_mean.npy"),
            "std": "pose_std.npy", "std_sha256": sha256(stats_dir / "pose_std.npy"),
            "valid_frames": frames,
        }},
        "stats_scope": "train_real_frames_once_per_clip_no_held_padding",
        "split_counts": split_counts,
        "window_split_counts": window_split_counts,
        "window_count": len(window_rows),
        "contact_source": ("world_foot_contacts_derived_from_root_track"
                           if want_contacts else "not_written"),
        "root_status": "offline_proxy_not_observed_actor_root",
        "clips": contract_clips,
    }

    contract_dir.mkdir(parents=True, exist_ok=True)
    (contract_dir / "contract.json").write_text(
        json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")
    with (contract_dir / "windows.jsonl").open("w", encoding="utf-8") as stream:
        for row in window_rows:
            stream.write(json.dumps(row) + "\n")

    return {"release": str(release), "mmap": str(mmap_root) if mmap_root else None,
            "clips": len(rows), "windows": len(window_rows),
            "split_counts": split_counts, "window_split_counts": window_split_counts,
            "stats_frames": frames,
            "elapsed_s": round(time.perf_counter() - started, 1)}


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", required=True, help="SOMA 训练视图（含 clips.jsonl）")
    parser.add_argument("--release", required=True, help="发布目录（写 contract/）")
    parser.add_argument("--mmap", help="mmap 输出目录；不给则只出契约（训练时需要它）")
    parser.add_argument("--skeleton", help="骨架契约，默认 data/skeletons/soma_uniform_v1.json")
    parser.add_argument("--limit", type=int, help="只处理前 N 条，先跑通再放量")
    parser.add_argument("--workers", type=int, default=1, help="并行进程数")
    parser.add_argument("--fps-group", default=None,
                        help="只取该 fps_group（如 fps120）；混合帧率数据集必须指定其一")
    parser.add_argument("--no-contacts", dest="contacts", action="store_false",
                        help="跳过脚部接触位（默认生成）。跳过会快约 5 倍，"
                             "但 evaluate_conditioned_baselines 的 load_window 会解不动空的 contact_bits，"
                             "contact_weight>0 的训练也用不了")
    args = parser.parse_args()
    result = build(args.dataset, args.release, mmap_root=args.mmap,
                   skeleton_path=args.skeleton, limit=args.limit,
                   workers=args.workers, want_contacts=args.contacts,
                   fps_group=args.fps_group)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
