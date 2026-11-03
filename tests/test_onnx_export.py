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

    **为什么必须塞一个非二值 mask**：如果 mask 只取 0/1，那么
    `torch.where(mask, ref, pred)` 与 `ref*mask + pred*(1-mask)` 在 IEEE 下
    逐位相同（1*x + 0*y ≡ x）。只用 0/1 测，这条测试**原理上无法区分**
    Where 和 lerp——而它声称要防的正是 where 被重写成 lerp。

    mask=0.5 时两者差异是 O(1)：bool(0.5) 为 True 仍取 reference，
    而 lerp 会给出 0.5*ref + 0.5*pred。
    """
    import export_onnx as E
    import onnxruntime as ort

    session = ort.InferenceSession(str(ONNX_PATH), providers=["CPUExecutionProvider"])
    feeds = E.sample_feeds(manifest,
                           mmap_root=Path(manifest["artifacts"]["mmap_dir"]),
                           release=Path(manifest["artifacts"]["release_dir"]),
                           limit_per_split=1)
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

    # Where 是逐位精确的，所以门限就是 0，不是「小于某个小数」。
    assert worst == 0.0, (
        f"mask=1 的帧没有精确等于 reference，最大差 {worst:.3e}——硬覆盖语义丢了")


@requires_onnx
def test_mask_non_binary_proves_where_not_lerp(manifest):
    """用一个非二值 mask 证明图里是 Where，不是 lerp。

    这是上一条测试的补强：mask∈{0,1} 时 Where 与 lerp 逐位相同，无法区分。
    把某一帧的 mask 设成 0.5，两者会分叉到 O(1)。
    """
    import export_onnx as E
    import onnxruntime as ort

    session = ort.InferenceSession(str(ONNX_PATH), providers=["CPUExecutionProvider"])
    feeds = E.sample_feeds(manifest,
                           mmap_root=Path(manifest["artifacts"]["mmap_dir"]),
                           release=Path(manifest["artifacts"]["release_dir"]),
                           limit_per_split=1)
    feed = next(f for f in feeds if f["offsets"])
    tensors = dict(feed["tensors"])

    # 把第 12 帧的 mask 改成 0.5（reference 相应填上该帧真值）
    target = 12
    tensors["mask"] = np.zeros_like(tensors["mask"])
    tensors["mask"][0, target, 0] = 0.5
    reference = np.zeros_like(tensors["reference"])
    source_ref = feed["tensors"]["reference"][0, target]
    reference[0, target] = source_ref
    tensors["reference"] = reference

    out = session.run(None, {name: tensors[name] for name in E.INPUT_NAMES})[0]
    where_value = out[0, target]

    # 模拟 lerp：ref*mask + pred*(1-mask)。pred 用 mask=0 时的输出近似
    plain = dict(tensors)
    plain["mask"] = np.zeros_like(tensors["mask"])
    pred = session.run(None, {name: plain[name] for name in E.INPUT_NAMES})[0][0, target]
    lerp_value = source_ref * 0.5 + pred * 0.5

    to_where = float(np.abs(where_value - source_ref).max())
    to_lerp = float(np.abs(where_value - lerp_value).max())

    assert to_where == 0.0, (
        f"mask=0.5 时输出没有精确等于 reference（差 {to_where:.3e}）——不是 Where 语义")
    assert to_lerp > 1e-3, (
        f"mask=0.5 时输出与 lerp 只差 {to_lerp:.3e}——图里可能是 lerp 而非 Where")


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


def test_export_supports_path_override():
    """换机器时 manifest 里的绝对路径会失效，脚本必须支持环境变量覆盖。

    这条只读源码，不需要数据。
    """
    source = (REPO / "benchmark" / "manifest" / "export_onnx.py").read_text(encoding="utf-8")
    assert "MW_RELEASE_DIR" in source, "应有 MW_RELEASE_DIR 覆盖 release 路径"
    assert "MW_MMAP_DIR" in source, "应有 MW_MMAP_DIR 覆盖 mmap 路径"
    # 路径无效时应给出可操作的错误，而不是裸 FileNotFoundError
    assert "换机器时请设" in source, "错误信息应告诉用户怎么修"


@requires_onnx
def test_all_four_inputs_actually_affect_output(manifest):
    """四个输入必须都真的影响输出。

    这条防的是一种很难发现的失败：模型表面能跑、形状对、数值也没 NaN，
    但某个输入在导出时被优化掉了（比如 mask 恒 0 时 reference 分支被死代码消除）。
    那样前面所有的数值测试都会「通过」，因为两边都错得一样。
    """
    import export_onnx as E
    import onnxruntime as ort

    session = ort.InferenceSession(str(ONNX_PATH), providers=["CPUExecutionProvider"])
    feeds = E.sample_feeds(manifest,
                           mmap_root=Path(manifest["artifacts"]["mmap_dir"]),
                           release=Path(manifest["artifacts"]["release_dir"]),
                           limit_per_split=1)
    feed = next(f for f in feeds if f["offsets"])
    base = session.run(None, {n: feed["tensors"][n] for n in E.INPUT_NAMES})[0]

    # 每个输入分别置零，输出必须有可观测变化
    for name in E.INPUT_NAMES:
        perturbed = dict(feed["tensors"])
        perturbed[name] = np.zeros_like(perturbed[name])
        changed = session.run(None, {n: perturbed[n] for n in E.INPUT_NAMES})[0]
        delta = float(np.abs(changed - base).max())
        assert delta > 1e-3, (
            f"把 {name} 置零后输出只变了 {delta:.3e}——该输入可能没真正参与计算")

@requires_onnx
def test_recorded_tolerance_matches_code(record):
    """record 里记的阈值必须就是代码里的常量。

    否则有人用 --relative-tolerance 1e-2 重导，哈希对得上、
    manifest 自述「我用 1e-2 验过」，而 CI 仍按 2e-5 判——两边不一致且无人发现。
    """
    import export_onnx as E

    assert record["relative_tolerance"] == E.RELATIVE_TOLERANCE, (
        "record 记的阈值与代码常量不符，说明产物是用放宽/收紧的阈值导出的"
    )


@requires_onnx
def test_nan_on_torch_side_is_rejected():
    """torch 侧产出 NaN 时必须报错，不能被静默吞掉。

    这是真实 bug 的回归测试：只检查 ORT 输出 finite 不够——
    若 torch 侧含 NaN，np.abs(actual-expected).max() 得 nan，
    而 Python 的 max(0.0, nan) 返回 0.0，闸门会在最该报警时变绿。
    """

    class NanModel:
        def __call__(self, *args):
            import torch

            return torch.full((1, 24, 702), float("nan"))

    class ZeroSession:
        def run(self, _names, feed):
            return [np.zeros((1, 24, 702), dtype=np.float32)]

    import export_onnx as E

    shapes = {"history": 709, "root_plan": 7, "reference": 702, "mask": 1}
    feeds = [{
        "split": "x", "clip": "x", "start": 0, "offsets": [],
        "tensors": {n: np.zeros((1, 24, w), dtype=np.float32) for n, w in shapes.items()},
    }]
    with pytest.raises(ValueError, match="torch 输出含 NaN"):
        E.compare(NanModel(), ZeroSession(), feeds)


@requires_onnx
def test_allclose_catches_real_regression():
    """allclose 判据必须能抓到「真实量级」的精度损失。

    构造一个刻意偏离的 session：把输出整体偏移 1e-3。
    这个量级的错误在全局比值判据下也会被抓到，但这里确认 allclose 这条路径确实有效。
    """
    import export_onnx as E

    class Baseline:
        def __call__(self, *args):
            import torch

            return torch.ones(1, 24, 702)

    class Shifted:
        def run(self, _names, _feed):
            return [np.ones((1, 24, 702), dtype=np.float32) + 1e-3]

    shapes = {"history": 709, "root_plan": 7, "reference": 702, "mask": 1}
    feeds = [{
        "split": "x", "clip": "x", "start": 0, "offsets": [],
        "tensors": {n: np.zeros((1, 24, w), dtype=np.float32) for n, w in shapes.items()},
    }]
    with pytest.raises(ValueError, match="allclose"):
        E.compare(Baseline(), Shifted(), feeds)


def test_atol_covers_float_noise_near_zero():
    """接近零的元素只要求噪声地板精度，不要求高相对精度。

    实测：|x| < 1e-3 的元素有 17.4 万个，最大「局部相对误差」是 32000%，
    但对应绝对误差只有 6.25e-06 —— 那是 float32 正常噪声，不是精度损失。
    若把 atol 压到 1e-7，1.5% 的元素会被误判为失败。
    """
    import export_onnx as E

    assert E.ATOL >= 1e-5, "atol 太小会把接近零元素的浮点噪声判成失败"
    assert E.RTOL == E.RELATIVE_TOLERANCE, "rtol 应与相对阈值一致"
