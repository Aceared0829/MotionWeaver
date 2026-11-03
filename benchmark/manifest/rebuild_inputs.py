"""只靠 manifest + numpy 重建模型四输入，用于验证对契约的理解是否正确。

**硬规矩**：本文件不许 import MotionWindows、不许 import train_conditioned_pose，
也不许调用任何训练侧装配逻辑。它必须能独立跑起来——这正是它的价值：
如果它和训练侧对上了，说明契约是可复现的，而不是「只有那段代码里那个函数知道」。

训练侧对照在 tests/test_manifest_rebuild.py 里做。
"""
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation


def load_manifest(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha256(path):
    import hashlib
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_checksums(manifest, *, checkpoint, contract, mmap, stats_dir):
    """逐个核对 manifest 里的哈希；权重或统计量换过就必须先发现。"""
    expected = manifest["checksums"]
    actual = {
        "checkpoint_sha256": _sha256(checkpoint),
        "contract_sha256": _sha256(contract),
        "pose_mean_sha256": _sha256(Path(stats_dir) / manifest["normalization"]["mean"]["file"]),
        "pose_std_sha256": _sha256(Path(stats_dir) / manifest["normalization"]["std"]["file"]),
        "skeleton_sha256": _sha256(Path(mmap) / "skeleton.json"),
    }
    mismatched = {key: (expected[key], value) for key, value in actual.items() if expected[key] != value}
    return mismatched


def decode_6d(values, manifest):
    """按 manifest 声明的 row-major 交错排布解出旋转矩阵。

    values: [..., 6]；返回 [..., 3, 3]。

    **前提：col0 / col1 已是单位正交向量。**本函数只补 col2 = cross(col0, col1)，
    不做归一化——真实数据满足这个前提（实测两半范数 1.0±2e-7、点积 ≤1.67e-07），
    但如果你把未归一化的数喂进来，得到的矩阵不会正交，而且不会报错。
    """
    spec = manifest["rotation_6d"]["decode"]
    col0 = values[..., spec["col0"]]
    col1 = values[..., spec["col1"]]
    col2 = np.cross(col0, col1)
    return np.stack((col0, col1, col2), axis=-1)


def root_local(track, origin):
    """位置与朝向都相对 origin（最后一帧历史 Root）。"""
    rotation = Rotation.from_quat(origin[3:])
    positions = rotation.inv().apply(track[:, :3] - origin[:3])
    rotations = (rotation.inv() * Rotation.from_quat(track[:, 3:])).as_quat()
    return np.concatenate((positions, rotations), axis=-1).astype(np.float32)


def load_clip(mmap_root, raw_file):
    folder = Path(mmap_root) / str(raw_file)
    return (np.load(folder / "p.npy"), np.load(folder / "r.npy"), np.load(folder / "k.npy"))


def build_sample(manifest, *, mmap_root, release, clip_raw_file, start, reference_offsets=()):
    """重建一个窗口的四输入 + target。

    参数与训练侧的含义严格一致：
      start   窗口第 0 帧（历史第一帧）
      middle  = start + history_frames
      end     = middle + future_frames
      origin  = k[middle - 1]
    """
    history_frames = manifest["timing"]["history_frames"]
    future_frames = manifest["timing"]["future_frames"]
    positions, rotations, root = load_clip(mmap_root, clip_raw_file)

    # 越界要显式报错，别让 numpy 抛一个看不出上下文的 IndexError：
    # 窗口是 [start, start+48)，而 origin 取 [middle-1]，两者都在长度范围内才行。
    span = history_frames + future_frames
    total = len(root)
    if start < 0 or start + span > total:
        raise IndexError(
            f"窗口越界：clip {clip_raw_file} 共 {total} 帧，"
            f"start={start} 需要 {span} 帧，末端 {start + span} 超出 {total - start} 帧")
    mean = np.load(Path(release) / "contract" / "stats" / manifest["normalization"]["mean"]["file"])
    std = np.load(Path(release) / "contract" / "stats" / manifest["normalization"]["std"]["file"])

    middle, end = start + history_frames, start + history_frames + future_frames
    origin = root[middle - 1]

    pose = (positions[start:end].astype(np.float32) - mean) / std
    combined = np.concatenate((pose, rotations[start:end]), axis=-1).reshape(
        history_frames + future_frames, -1).astype(np.float32)

    history = np.concatenate(
        (combined[:history_frames], root_local(root[start:middle], origin)), axis=-1)
    target = combined[history_frames:]

    # 负数下标在 numpy 里会静默回绕（-1 点亮最后一帧），这是很难发现的语义错误。
    bad = [o for o in reference_offsets if not 0 <= o < future_frames]
    if bad:
        raise ValueError(f"reference_offsets 必须是 0..{future_frames - 1} 的整数，收到 {bad}")
    mask = np.zeros((future_frames, 1), dtype=np.float32)
    mask[list(reference_offsets)] = 1
    reference = target * mask

    return {
        "history": history,
        "root_plan": root_local(root[middle:end], origin),
        "reference": reference.astype(np.float32),
        "mask": mask,
        "target": target,
    }


def assert_manifest_self_consistent(manifest):
    """manifest 内部的恒等式；不满足就 raise。

    用 raise 而不是 assert：这是对外契约，`python -O` 会把 assert 整段删掉，
    那样这些校验在最需要它们的场景下反而不生效。
    """
    bone_count = manifest["skeleton"]["bone_count"]
    features = bone_count * 9
    history_frames = manifest["timing"]["history_frames"]
    future_frames = manifest["timing"]["future_frames"]

    def require(condition, message):
        if not condition:
            raise ValueError(message)

    require(manifest["inputs"]["history"]["shape"] == [history_frames, features + 7],
            f"history 形状不符：{manifest['inputs']['history']['shape']}")
    require(manifest["inputs"]["root_plan"]["shape"] == [future_frames, 7],
            f"root_plan 形状不符：{manifest['inputs']['root_plan']['shape']}")
    require(manifest["inputs"]["reference"]["shape"] == [future_frames, features],
            f"reference 形状不符：{manifest['inputs']['reference']['shape']}")
    require(manifest["inputs"]["mask"]["shape"] == [future_frames, 1],
            f"mask 形状不符：{manifest['inputs']['mask']['shape']}")
    require(manifest["output"]["shape"] == [future_frames, features],
            f"output 形状不符：{manifest['output']['shape']}")
    require(len(manifest["skeleton"]["names"]) == bone_count, "骨骼名数量不符")
    require(len(manifest["skeleton"]["parents"]) == bone_count, "父索引数量不符")

    spec = manifest["rotation_6d"]["decode"]
    require(sorted(spec["col0"] + spec["col1"]) == list(range(6)),
            f"6D 解码索引没有覆盖 0..5：{spec}")
    require(spec["col2"] == "cross(col0, col1)", f"col2 定义异常：{spec['col2']}")

    # layout 是 UE 侧 C++ 唯一要照抄的东西，必须逐段核对：
    # position / rotation_6d / root_local 三段要首尾相接、连续覆盖 features+7。
    layout = manifest["inputs"]["history"]["layout"]
    position, rotation, root_local = layout["position"], layout["rotation_6d"], layout["root_local"]
    require(position["width"] == bone_count * 3,
            f"position 宽度应为 {bone_count * 3}，实际 {position['width']}")
    require(rotation["width"] == bone_count * 6,
            f"rotation_6d 宽度应为 {bone_count * 6}，实际 {rotation['width']}")
    require(root_local["width"] == 7, f"root_local 宽度应为 7，实际 {root_local['width']}")
    segments = [(position["offset"], position["offset"] + position["width"]),
                (rotation["offset"], rotation["offset"] + rotation["width"]),
                (root_local["offset"], root_local["offset"] + root_local["width"])]
    cursor = 0
    for start, end in segments:
        require(start == cursor,
                f"layout 段不连续：期望从 {cursor} 开始，实际 {start}（{segments}）")
        cursor = end
    require(cursor == features + 7,
            f"layout 总宽应为 {features + 7}，实际覆盖到 {cursor}")
    return True
