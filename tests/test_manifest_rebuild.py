"""阶段 0 门禁：独立重建 vs 训练侧，逐元素 max|Δ| < 1e-6。

这条测试是整个 refpose 链上唯一能证明「我们对模型输入契约的理解是对的」的东西。
它做三件事：

1. 用 benchmark/manifest/rebuild_inputs.py 从原始 npz 重建四输入；
2. 用真正的训练侧 MotionWindows 取同一批窗口；
3. 逐元素比对，任何一项超阈值就红。

反向测试也在这里：把 6D 排布故意改成「列拼接」，重建必须对不上——
否则无法证明这条测试真的在工作。
"""
import json
import random
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "benchmark" / "manifest"))

import rebuild_inputs as R

MANIFEST_PATH = REPO / "benchmark" / "manifest" / "refpose_manifest_v1.json"
RELEASE = Path("D:/MotionWeaverData/full_release")
MMAP = Path("D:/MotionWeaverData/full_mmap")
CHECKPOINT = Path("D:/MotionWeaverData/model_weight/refpose_v3_71k/best.pt")
STATS = RELEASE / "contract" / "stats"
TOLERANCE = 1e-6


def _available():
    return all(p.exists() for p in (MANIFEST_PATH, RELEASE, MMAP, CHECKPOINT, STATS))


requires_data = pytest.mark.skipif(not _available(), reason="本机缺少封盘数据/权重")


@pytest.fixture(scope="module")
def manifest():
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def contract():
    return json.loads((RELEASE / "contract" / "contract.json").read_text(encoding="utf-8"))


@requires_data
def test_manifest_self_consistent(manifest):
    assert R.assert_manifest_self_consistent(manifest)
    assert manifest["format"] == "refpose_manifest_v1"
    assert manifest["skeleton"]["bone_count"] == 78
    assert manifest["timing"]["history_frames"] == manifest["timing"]["future_frames"] == 24


@requires_data
def test_checksums_match_current_artifacts(manifest):
    mismatched = R.verify_checksums(
        manifest, checkpoint=CHECKPOINT, contract=RELEASE / "contract" / "contract.json",
        mmap=MMAP, stats_dir=STATS)
    assert not mismatched, f"manifest 与当前产物不一致：{mismatched}"


def _training_side_windows(split, limit=6):
    """用真正的训练侧取窗口；这是被验证的一方，不是重建的一方。"""

    from training.pretrain.train_conditioned_pose import MotionWindows

    contract = json.loads((RELEASE / "contract" / "contract.json").read_text(encoding="utf-8"))
    dataset = MotionWindows(RELEASE, contract, split, scenario="random", seed=7, mmap_root=MMAP)
    picks = random.Random(1234).sample(range(len(dataset)), min(limit, len(dataset)))
    samples = []
    for index in picks:
        *inputs, target = dataset[index]
        row = dataset.rows[index]
        samples.append({
            "index": index,
            "clip_index": row["clip_index"],
            "start": row["start"],
            "inputs": [value.numpy() for value in inputs],
            "target": target.numpy(),
        })
    return contract, samples


@requires_data
@pytest.mark.parametrize("split", ["train", "validation", "test"])
def test_rebuild_matches_training_side(manifest, contract, split):
    """三个 split 各取若干窗口；只在 train 上过可能只是那批数据不敏感。"""
    contract_live, samples = _training_side_windows(split)
    worst = 0.0
    details = []

    for sample in samples:
        clip = contract_live["clips"][sample["clip_index"]]
        # 训练侧的 reference 是按 scenario 随机选的 offset，用 mask 反推回来
        mask = sample["inputs"][3]
        offsets = tuple(int(v) for v in np.nonzero(mask[:, 0])[0])
        mine = R.build_sample(manifest, mmap_root=MMAP, release=RELEASE,
                              clip_raw_file=clip["raw_file"], start=sample["start"],
                              reference_offsets=offsets)
        for name, position in (("history", 0), ("root_plan", 1), ("reference", 2), ("mask", 3)):
            theirs = np.asarray(sample["inputs"][position], dtype=np.float32)
            ours = np.asarray(mine[name], dtype=np.float32)
            assert theirs.shape == ours.shape, f"{split}/{clip['raw_file']}/{name} 形状不符：{theirs.shape} vs {ours.shape}"
            delta = float(np.abs(theirs - ours).max())
            worst = max(worst, delta)
            details.append(f"{clip['raw_file']} start={sample['start']} {name} max|d|={delta:.3e}")
        delta = float(np.abs(np.asarray(sample["target"], dtype=np.float32)
                             - np.asarray(mine["target"], dtype=np.float32)).max())
        worst = max(worst, delta)
        details.append(f"{clip['raw_file']} start={sample['start']} target max|d|={delta:.3e}")

    assert worst < TOLERANCE, f"{split} 最大误差 {worst:.3e} 超过 {TOLERANCE:.0e}\n" + "\n".join(details)


def test_column_concat_layout_must_fail(manifest):
    """反向测试：把 6D 排布换成「列拼接」，重建必须对不上。

    这是证明「上面那些测试真的在工作」的手段。如果换了错排布还能过，
    说明测试形同虚设。
    """
    broken = json.loads(json.dumps(manifest))
    broken["rotation_6d"]["decode"] = {"col0": [0, 1, 2], "col1": [3, 4, 5]}

    from scipy.spatial.transform import Rotation

    # 造 4 个真正正交的旋转，按 manifest 声明的排布编码成 6D
    rotations = Rotation.random(num=4, random_state=0).as_matrix().astype(np.float32)
    col0 = rotations[..., :, 0]
    col1 = rotations[..., :, 1]
    values = np.empty((4, 6), dtype=np.float32)
    values[..., [0, 2, 4]] = col0
    values[..., [1, 3, 5]] = col1

    right = R.decode_6d(values, manifest)
    # 正确排布必须还原出原始旋转
    assert np.abs(right - rotations).max() < 1e-5, "正确排布解不出原旋转"

    wrong = R.decode_6d(values, broken)
    delta = float(np.abs(right - wrong).max())
    assert delta > 1e-3, f"错排布与对排布差得太小（{delta:.3e}），反向测试无效"
    # 错排布给出的矩阵必须不正交，这才能证明「排布写错会被发现」
    gram = np.einsum("...ij,...ik->...jk", wrong, wrong)
    assert np.abs(gram - np.eye(3)).max() > 1e-2, "错排布竟然也正交，反向测试无效"


@requires_data
def test_rebuild_does_not_import_training_code():
    """独立脚本必须真的独立：不许 import 训练侧装配逻辑。"""
    source = (REPO / "benchmark" / "manifest" / "rebuild_inputs.py").read_text(encoding="utf-8")
    forbidden = ["MotionWindows", "train_conditioned_pose", "conditioned_motion"]
    # 注释里会提到这些名字，所以只在 import 行上判
    import_lines = [line for line in source.splitlines()
                    if line.strip().startswith(("import ", "from "))]
    bad = [line for line in import_lines if any(name in line for name in forbidden)]
    assert not bad, f"rebuild_inputs.py 不许 import 训练侧：{bad}"
