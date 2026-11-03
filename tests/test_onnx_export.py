"""阶段 1 门禁：ONNX 导出必须无损。

这条测试证明的是「导出本身没丢精度」，不证明模型好不好，也不证明 DML 能跑。
判据是**相对误差**而不是绝对误差——理由见 export_onnx.py 里 compare() 上方的注释。

反向测试也在里面：把 mask 的硬覆盖语义破坏掉，测试必须变红。
那条语义是模型行为的一部分（torch.where），不是实现细节，
如果 ONNX 编译器把它优化掉了，必须在门禁里被发现。
"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO, REPO / "benchmark" / "manifest"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

MANIFEST_PATH = REPO / "benchmark" / "manifest" / "refpose_manifest_v1.json"
CHECKPOINT = Path("D:/MotionWeaverData/model_weight/refpose_v3_71k/best.pt")
ONNX_DIR = Path("D:/MotionWeaverData/model_weight/refpose_v3_71k")
ONNX_PATH = ONNX_DIR / "model.onnx"
ONNX_MANIFEST = ONNX_DIR / "onnx_manifest.json"


def _available():
    return all(p.exists() for p in (MANIFEST_PATH, CHECKPOINT, ONNX_PATH, ONNX_MANIFEST))


requires_onnx = pytest.mark.skipif(not _available(), reason="本机缺少 manifest / 权重 / ONNX 产物")


@pytest.fixture(scope="module")
def manifest():
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def record():
    return json.loads(ONNX_MANIFEST.read_text(encoding="utf-8"))


@requires_onnx
def test_onnx_manifest_matches_contract(manifest, record):
    """ONNX 的输入名/形状/输出必须与阶段 0 签字的契约逐项相等。"""
    bone_count = manifest["skeleton"]["bone_count"]
    features = bone_count * 9
    history = manifest["timing"]["history_frames"]
    future = manifest["timing"]["future_frames"]

    expected = {
        "history": [1, history, features + 7],
        "root_plan": [1, future, 7],
        "reference": [1, future, features],
        "mask": [1, future, 1],
    }
    assert record["inputs"] == expected, f"ONNX 输入与契约不符：{record['inputs']}"
    assert record["outputs"] == {"joints": [1, future, features]}
    assert record["batch"] == 1, "必须是静态 batch=1"
    assert record["input_dtype"] == "float32"


@requires_onnx
def test_onnx_artifact_hash_matches_record(record):
    """产物被换过就必须发现。"""
    import hashlib

    digest = hashlib.sha256()
    with ONNX_PATH.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    assert digest.hexdigest() == record["onnx_sha256"], "model.onnx 与 onnx_manifest 记录的哈希不符"


@requires_onnx
def test_onnx_checker_passes():
    import onnx

    onnx.checker.check_model(str(ONNX_PATH))


@requires_onnx
def test_numeric_alignment_holds(record):
    """导出时算过一遍；这里独立复算，不采信 record 里的数字。"""
    import export_onnx as E
    import onnxruntime as ort

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    model, _ = E.build_model(CHECKPOINT)
    session = ort.InferenceSession(str(ONNX_PATH), providers=["CPUExecutionProvider"])
    feeds = E.sample_feeds(manifest,
                           mmap_root=Path(manifest["artifacts"]["mmap_dir"]),
                           release=Path(manifest["artifacts"]["release_dir"]),
                           limit_per_split=2)
    errors, _details = E.compare(model, session, feeds)
    assert errors["max_rel"] < E.RELATIVE_TOLERANCE, (
        f"相对误差 {errors['max_rel']:.3e} 超过阈值 {E.RELATIVE_TOLERANCE:.0e}")
    # 与导出时记录的量级一致（允许一定浮动，因为采样不同）
    assert abs(errors["max_rel"] - record["max_rel_torch_vs_ort_cpu"]) < 5e-6


@requires_onnx
def test_mask_hard_override_survives_export(manifest):
    """mask=1 的帧必须**精确**等于 reference——这是 hard override，不是软条件。

    ONNX 编译器完全可能把 torch.where 优化掉或改成近似。
    这条测试专门守住那个语义。
    """
    import export_onnx as E
    import onnxruntime as ort

    session = ort.InferenceSession(str(ONNX_PATH), providers=["CPUExecutionProvider"])
    feeds = E.sample_feeds(manifest,
                           mmap_root=Path(manifest["artifacts"]["mmap_dir"]),
                           release=Path(manifest["artifacts"]["release_dir"]),
                           limit_per_split=1)
    # 只挑有 reference 的样本
    picked = [f for f in feeds if f["offsets"]]
    assert picked, "样本里没有带 reference 的"

    worst = 0.0
    for feed in picked:
        out = session.run(None, {name: feed["tensors"][name] for name in E.INPUT_NAMES})[0]
        mask = feed["tensors"]["mask"][0, :, 0]
        reference = feed["tensors"]["reference"][0]
        selected = mask > 0.5
        assert selected.any()
        delta = float(np.abs(out[0][selected] - reference[selected]).max())
        worst = max(worst, delta)

    # 硬覆盖要求精确相等。实测在 torch 里是 0.0；ONNX 走 Where 算子也应当精确。
    # 给 1e-6 的余量以防某层做了 dtype 提升，但绝不允许是 O(1e-3) 这种量级。
    assert worst < 1e-6, f"mask=1 的帧没有精确等于 reference，最大差 {worst:.3e}——硬覆盖语义丢了"


@requires_onnx
def test_batch_size_1_is_enforced(record):
    """静态 batch=1 是硬约束：GRU 在非 1 batch 下会报错，多角色必须多 session。"""
    import onnxruntime as ort

    session = ort.InferenceSession(str(ONNX_PATH), providers=["CPUExecutionProvider"])
    for spec in session.get_inputs():
        assert spec.shape[0] == 1, f"{spec.name} 的 batch 维不是 1：{spec.shape}"
    # record 里必须写明这条限制与原因
    assert "batch" in record and record["batch"] == 1
    assert "session" in record["batch_note"], "batch_note 应说明多角色要每角色一个 session"


def test_export_script_reads_shapes_from_manifest():
    """导出脚本不许硬编码形状——形状来源必须是 manifest。

    这条不需要数据，只读源码，所以不加 requires_onnx：
    换台机器门禁会 SKIP，但这条应该一直跑。
    """
    source = (REPO / "benchmark" / "manifest" / "export_onnx.py").read_text(encoding="utf-8")
    body = source.split('if __name__ == "__main__":')[0]
    # 常见硬编码形状不该出现
    for hardcoded in ("709", "702", "[1, 24,", "24, 78"):
        assert hardcoded not in body, f"export_onnx.py 里出现了硬编码形状 {hardcoded!r}，应改为从 manifest 读"

@requires_onnx
@pytest.mark.parametrize("mutate", [
    # manifest 单个字段被改：内部推导与声明会对不上
    lambda m: m["inputs"]["history"].__setitem__("shape", [24, 702]),
    lambda m: m["skeleton"].__setitem__("bone_count", 77),
    lambda m: m["timing"].__setitem__("history_frames", 20),
])
def test_export_rejects_tampered_manifest(manifest, tmp_path, mutate):
    """manifest 被改过时，导出必须拒绝，不能产出一个形状错的 ONNX。"""
    import copy

    import export_onnx as E

    tampered = copy.deepcopy(manifest)
    mutate(tampered)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
    with pytest.raises((ValueError, IndexError, KeyError)):
        E.export(path, CHECKPOINT, tmp_path / "out")


@requires_onnx
def test_export_cross_checks_against_checkpoint(tmp_path):
    """manifest 内部自洽但和权重不符时，也必须拒绝。

    这条比上一条更硬：它证明导出脚本会拿 manifest 去对账**权重本身**，
    而不只是对账 manifest 自己。
    """
    import copy

    import export_onnx as E

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    # 同时改 timing 和 inputs——让 manifest 内部看起来自洽，但与真实权重不符
    tampered = copy.deepcopy(manifest)
    tampered["skeleton"]["bone_count"] = 77
    tampered["skeleton"]["names"] = manifest["skeleton"]["names"][:77]
    tampered["skeleton"]["parents"] = manifest["skeleton"]["parents"][:77]
    features = 77 * 9
    tampered["inputs"]["history"]["shape"] = [24, features + 7]
    tampered["inputs"]["reference"]["shape"] = [24, features]
    tampered["output"]["shape"] = [24, features]
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="骨"):
        E.export(path, CHECKPOINT, tmp_path / "out")
