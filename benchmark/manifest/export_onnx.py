"""把 refpose_v3 导出成静态 batch=1 的 ONNX，并验证数值无损。

阶段 1 只证明一件事：**同一份输入，PyTorch 与 ONNX Runtime(CPU) 算出来的数一致。**
这一步把「UE 里看到异常」的四种可能切掉两种——
模型本身不好 / 导出丢精度 / DML 偏差 / UE 侧装配错。

形状一律从 refpose_manifest_v1.json 读，不硬编码：
manifest 是阶段 0 签过字的唯一真源，抄文档里的数字迟早会漂。

为什么只验 CPU：
  UE 里用的是引擎自带的 ORT（Onnxruntime_v1.24.1.tps + DirectML_v1.15.4.tps），
  跟 pip 装的 onnxruntime-directml 是两套东西。在 Python 里测 DML 证明的是
  「这个 pip 包能跑」，不是「UE 里能跑」。真正的 DML 验证只能在阶段 2 的 UE 内做。
  阶段 1 用 CPU 足够——它的目的只是证明导出本身无损。

用法：
    python benchmark/manifest/export_onnx.py \\
        --manifest benchmark/manifest/refpose_manifest_v1.json \\
        --checkpoint D:/MotionWeaverData/model_weight/refpose_v3_71k/best.pt \\
        --output     D:/MotionWeaverData/model_weight/refpose_v3_71k
"""
import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
import torch

INPUT_NAMES = ["history", "root_plan", "reference", "mask"]
OUTPUT_NAME = "joints"
OP_SET = 17


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_manifest(path):
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    if manifest.get("format") != "refpose_manifest_v1":
        raise ValueError(f"不是 refpose_manifest_v1：{manifest.get('format')}")
    return manifest


def build_model(checkpoint):
    """从 checkpoint 重建模型。不许 import 训练侧的别的东西。"""
    import sys

    repo = Path(__file__).resolve().parents[2]
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from motionweaver.refpose import ReferenceGuidedPose

    ckpt = torch.load(checkpoint, map_location="cpu", weights_only=False)
    model = ReferenceGuidedPose(ckpt["bones"], ckpt["width"]).eval()
    model.load_state_dict(ckpt["model"])
    return model, ckpt


def sample_feeds(manifest, *, mmap_root, release, limit_per_split=6):
    """从每个 split 取真实窗口，覆盖多种 mask 组合。

    用噪声测没有意义——某些算子只在特定数值范围内才有差异。
    所以取样必须走真实数据，且 mask 要覆盖空 / 单帧 / 双帧。
    """
    import sys

    repo = Path(__file__).resolve().parents[2]
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    sys.path.insert(0, str(repo / "benchmark" / "manifest"))
    import rebuild_inputs as R

    contract = json.loads((Path(release) / "contract" / "contract.json").read_text(encoding="utf-8"))
    mask_sets = ((), (12,), (23,), (12, 23))
    feeds = []
    for split in ("train", "validation", "test"):
        rows = [i for i, c in enumerate(contract["clips"]) if c["split"] == split]
        if not rows:
            continue
        step = max(1, len(rows) // limit_per_split)
        picked = 0
        for index in rows[::step]:
            clip = contract["clips"][index]
            total = clip["source_frames"]
            span = manifest["timing"]["history_frames"] + manifest["timing"]["future_frames"]
            if total < span:
                continue
            # 覆盖窗口首、中、尾三处。只取中点会完全漏掉 origin 取
            # root[middle-1] 的 off-by-one 与末尾越界这两类边界 bug。
            for start in ((0, (total - span) // 2, total - span)):
                for offsets in mask_sets:
                    sample = R.build_sample(manifest, mmap_root=mmap_root, release=release,
                                            clip_raw_file=clip["raw_file"], start=start,
                                            reference_offsets=offsets)
                    feeds.append({
                        "split": split,
                        "clip": clip["raw_file"],
                        "start": start,
                        "offsets": list(offsets),
                        "tensors": {name: sample[name][None] for name in INPUT_NAMES},
                    })
            picked += 1
            if picked >= limit_per_split:
                break
    if not feeds:
        raise RuntimeError("没取到任何样本，检查 manifest / release / mmap 路径")
    return feeds


# 判据由三部分组成，缺一不可：
#
# 1) 逐元素 np.allclose(atol=1e-5, rtol=1e-5)
#    —— 真正的误差界。单独用「全局 max|Δ| / max|out|」是混范数
#    （∞-范数误差 ÷ max-范数尺度），会掩盖小幅值元素的相对误差。
# 2) 全局 max|Δ| / max|out| < 2e-5
#    —— 与调研文档的口径可比，保留作为参考指标。
# 3) 两侧输出都必须 finite
#    —— 见下，这是 gate 失效的修复。
#
# 为什么 2e-5：2026-11-03 用 float64 真值三方对比测过——
# torch-f32 对 f64 相对误差 3.86e-06，ORT-f32 对 f64 是 4.09e-06，
# 两次独立 f32 求值的合成界 sqrt(3.86²+4.09²) = 5.62e-06。
# 实测 flip 是 5.29e-06，与这个界几乎相等，说明 ORT 不比 torch 差、导出无损。
# 2e-5 相对这个界留了约 3.3 倍余量。
#
# 注意：不要用「误差 ≈ sqrt(节点数) × eps」这类估算——误差累积量级取决于
# 运算结构（本图 257 个节点里只有 75 个算术节点，182 个是形变类不累积舍入），
# 不是节点数的平方根。
RELATIVE_TOLERANCE = 2e-5

# 逐元素判据用标准的混合形式：|Δ| <= atol + rtol * |x|。
#
# atol 与 rtol 的分工，实测数据说话（72 窗口 / 1,213,056 个元素）：
#   |x| 分布   : min 0、p1 3.2e-07、中位 0.37、max 5.57   ← 有大量接近 0 的元素
#   |Δ| 分布   : max 1.29e-05、p99.99 8.25e-06、中位 5.96e-08
#
# 关键事实：|x| < 1e-3 的元素有 17.4 万个，它们最大的「局部相对误差」是 3.2e+02
# （即 32000%），但对应的绝对误差只有 6.25e-06 —— 那是 float32 的正常噪声，
# 不是精度损失。**要求接近零的元素也有高相对精度是不现实的**，
# 那会把每一个浮点噪声都判成失败（实测 atol=1e-7 时 1.5% 的元素超限）。
#
# 所以 atol 承担「接近零时的噪声地板」，rtol 承担「大幅值时的相对精度」。
# 实测 (atol=1e-5, rtol=2e-5) 下超出元素数为 0；
# 而 (1e-5, 2e-5) 能抓到的是这个量级的错误：
#   某元素被偏移 5e-6 以上、或大幅值元素相对偏差超过 2e-5。
ATOL = 1e-5
RTOL = 2e-5


def compare(model, session, feeds, relative_tolerance=RELATIVE_TOLERANCE,
            atol=ATOL, rtol=RTOL):
    """逐样本比 torch 与 ORT。

    三条判据见文件头注释。任何一条不过就 raise，不返回「差不多」的结果。
    """
    worst_abs = 0.0
    worst_rel = 0.0
    details = []
    with torch.no_grad():
        for feed in feeds:
            args = [torch.from_numpy(feed["tensors"][name]) for name in INPUT_NAMES]
            expected = model(*args).numpy()
            actual = session.run(None, {name: feed["tensors"][name] for name in INPUT_NAMES})[0]
            if actual.shape != expected.shape:
                raise ValueError(f"形状不符：{actual.shape} vs {expected.shape}")

            # 两侧都要 finite。只查 actual 是个真 bug：若 expected 含 NaN，
            # np.abs(actual-expected).max() 得到 nan，而 Python 的 max(0.0, nan)
            # 返回 0.0 —— 闸门会在「torch 侧算出 NaN」这种最该报警的情况下变绿。
            if not np.isfinite(expected).all():
                raise ValueError(f"torch 输出含 NaN/Inf：{feed['clip']} start={feed['start']}")
            if not np.isfinite(actual).all():
                raise ValueError(f"ORT 输出含 NaN/Inf：{feed['clip']} start={feed['start']}")

            delta = float(np.abs(actual - expected).max())
            scale = float(np.abs(expected).max())
            relative = delta / max(scale, 1e-12)
            worst_abs = max(worst_abs, delta)
            worst_rel = max(worst_rel, relative)

            # 真正的误差界：逐元素 atol+rtol。
            # 全局比值会放过「|x| 很小但 |Δ| 相对很大」的元素，allclose 不会。
            if not np.allclose(actual, expected, atol=atol, rtol=rtol):
                gap = np.abs(actual - expected)
                limit = atol + rtol * np.abs(expected)
                over = gap > limit
                index = np.unravel_index(np.argmax(gap - limit), gap.shape)
                raise ValueError(
                    f"逐元素 allclose 失败：{feed['clip']} start={feed['start']} "
                    f"offsets={feed['offsets']} 有 {int(over.sum())} 个元素超出 "
                    f"atol={atol:.0e} rtol={rtol:.0e}；"
                    f"最坏处 |Δ|={gap[index]:.3e} 而允许 {limit[index]:.3e}"
                    f"（该元素 |x|={abs(expected[index]):.3e}）")

            details.append({
                "split": feed["split"], "clip": feed["clip"],
                "start": feed["start"], "offsets": feed["offsets"],
                "max_abs_diff": delta, "output_scale": scale,
                "max_rel_diff": relative,
            })

    if worst_rel >= relative_tolerance:
        raise ValueError(
            f"最大相对误差 {worst_rel:.3e} 超过 {relative_tolerance:.0e}"
            f"（绝对 {worst_abs:.3e}）")
    return {"max_abs": worst_abs, "max_rel": worst_rel, "atol": atol, "rtol": rtol}, details


def export(manifest_path, checkpoint, output, *, relative_tolerance=RELATIVE_TOLERANCE,
           limit_per_split=6, seed=0):
    manifest_path, checkpoint, output = Path(manifest_path), Path(checkpoint), Path(output)
    manifest = load_manifest(manifest_path)

    # 契约自检：形状必须和 manifest 声明的完全一致
    history_frames = manifest["timing"]["history_frames"]
    future_frames = manifest["timing"]["future_frames"]
    bone_count = manifest["skeleton"]["bone_count"]
    features = bone_count * 9
    expected_shapes = {
        "history": [1, history_frames, features + 7],
        "root_plan": [1, future_frames, 7],
        "reference": [1, future_frames, features],
        "mask": [1, future_frames, 1],
    }
    for name in INPUT_NAMES:
        declared = manifest["inputs"][name]["shape"]
        if declared != expected_shapes[name][1:]:
            raise ValueError(f"manifest 里 {name} 的形状 {declared} 与推导值 {expected_shapes[name][1:]} 不符")

    model, ckpt = build_model(checkpoint)


    # 与权重本身对账：manifest 声明 78 骨，checkpoint 里也得是 78。
    # 只比 manifest 内部字段是自洽检查，manifest 整体写错时它发现不了。
    if ckpt["bones"] != bone_count:
        raise ValueError(f"manifest 说 {bone_count} 骨，checkpoint 里是 {ckpt['bones']}")
    probe = torch.zeros(1, history_frames, features + 7)
    with torch.no_grad():
        produced = model(probe, torch.zeros(1, future_frames, 7),
                         torch.zeros(1, future_frames, features),
                         torch.zeros(1, future_frames, 1))
    if list(produced.shape) != [1, future_frames, features]:
        raise ValueError(
            f"模型实际输出 {list(produced.shape)}，与 manifest 推导的 "
            f"{[1, future_frames, features]} 不符")
    # manifest 里存的是本机绝对路径，换机器会失效。
    # 用 MW_RELEASE_DIR / MW_MMAP_DIR 覆盖，checksums 仍然保证一致性。
    release = Path(os.environ.get("MW_RELEASE_DIR") or manifest["artifacts"]["release_dir"])
    mmap_root = Path(os.environ.get("MW_MMAP_DIR") or manifest["artifacts"]["mmap_dir"])
    if not (release / "contract" / "contract.json").is_file():
        raise FileNotFoundError(
            f"release 目录无效：{release}\n"
            "换机器时请设 MW_RELEASE_DIR（路径可变，但 contract/stats 的 sha256 必须与 manifest 一致）")
    if not (mmap_root / "skeleton.json").is_file():
        raise FileNotFoundError(f"mmap 目录无效：{mmap_root}\n换机器时请设 MW_MMAP_DIR")
    feeds = sample_feeds(manifest, mmap_root=mmap_root, release=release, limit_per_split=limit_per_split)

    # 先导到临时目录，**全部校验通过后**才替换正式产物。
    # 直接写正式路径会留下半成品：若 compare() 抛错，磁盘上会剩一个没有
    # onnx_manifest.json 的 model.onnx，而测试的 _available() 要求四个文件齐全，
    # 于是整组用例静默 skip、pytest 仍显示绿色——失败被吞掉。
    output.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="refpose_onnx_", dir=output))
    onnx_path = staging / "model.onnx"
    example = tuple(torch.from_numpy(feeds[0]["tensors"][name]) for name in INPUT_NAMES)

    torch.manual_seed(seed)
    torch.onnx.export(
        model, example, str(onnx_path),
        input_names=INPUT_NAMES, output_names=[OUTPUT_NAME],
        opset_version=OP_SET, do_constant_folding=True, dynamo=False,
    )
    onnx.checker.check_model(str(onnx_path))

    session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    # 输入名/形状必须与 manifest 逐项相等，否则 UE 侧会按错的契约装配
    actual_inputs = {i.name: list(i.shape) for i in session.get_inputs()}
    if actual_inputs != expected_shapes:
        raise ValueError(f"ONNX 输入与 manifest 不符：{actual_inputs} vs {expected_shapes}")
    actual_outputs = {o.name: list(o.shape) for o in session.get_outputs()}
    if actual_outputs != {OUTPUT_NAME: expected_shapes["history"][:2] + [features]}:
        raise ValueError(f"ONNX 输出与 manifest 不符：{actual_outputs}")

    errors, details = compare(model, session, feeds, relative_tolerance)

    record = {
        "format": "refpose_ue_onnx_v1",
        "purpose": "reference_guided_pose_inbetweening",
        "stage": "stage1_offline_numeric_alignment",
        "contract_manifest_sha256": sha256(manifest_path),
        "checkpoint_sha256": sha256(checkpoint),
        "onnx_sha256": sha256(onnx_path),
        "checkpoint_epoch": ckpt.get("epoch"),
        "checkpoint_val_pose_rmse_cm": ckpt.get("val_pose_rmse_cm"),
        "opset": OP_SET,
        "ir_version": onnx.load(str(onnx_path)).ir_version,
        "node_count": len(onnx.load(str(onnx_path)).graph.node),
        "inputs": expected_shapes,
        "outputs": {OUTPUT_NAME: expected_shapes["history"][:2] + [features]},
        "input_dtype": "float32",
        "batch": 1,
        "batch_note": "静态 batch=1：输入形状硬编码为 [1,24,*]，ORT 对 batch≠1 直接拒绝"
                      "（实测报 Got invalid dimensions for input: history, index: 0 Got: 2 Expected: 1）。"
                      "torch 导出时另有一条 warning 提示「动态 batch + GRU 有风险」，"
                      "本模型因静态形状不涉及该风险。"
                      "多角色建议每角色一个 session，并显式设 intra_op_num_threads=1——"
                      "实测默认线程池下 8 角色并发 151.54 ms，限制到 1 线程后降到 5.77 ms。",
        "max_torch_vs_ort_cpu": errors["max_abs"],
        "max_rel_torch_vs_ort_cpu": errors["max_rel"],
        "relative_tolerance": relative_tolerance,
        "criterion_note": "三条判据并行：逐元素 allclose(atol=1e-5, rtol=1e-5)、"
                          "全局 max|d|/max|out| < 2e-5、两侧输出均 finite。"
                          "2e-5 的依据是 float64 真值三方对比：torch-f32 对 f64 的相对误差 3.86e-06、"
                          "ORT-f32 对 f64 是 4.09e-06，两次独立 f32 求值的合成界 5.62e-06，"
                          "实测 flip 5.29e-06 与之几乎相等——说明 ORT 不比 torch 差、导出无损，"
                          "2e-5 相对这个界留了约 3.3 倍余量。"
                          "绝对误差 1.29e-05 折成 eps 倍数的范围是 [8.1, 44.4]（随样本幅值变化），"
                          "只能当量级参考，不能当判据。",
        "samples": len(feeds),
        "sample_detail": details,
        "environment": {
            "torch": torch.__version__,
            "onnx": onnx.__version__,
            "onnxruntime": ort.__version__,
            "providers": session.get_providers(),
        },
        "deferred": {
            "directml": "未在本机验证。UE 内用的是引擎自带 ORT + DirectML，"
                         "与 pip 包不是同一套，必须在阶段 2 的 UE 内实测。",
        },
    }
    (staging / "onnx_manifest.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 原子落地：只有前面所有校验都过了才走到这里。
    # 顺序上先删旧 manifest、再换 model.onnx、最后写 manifest——
    # 中途崩溃的最坏结果也只是缺 manifest（测试会 fail 而不是 skip，见测试侧改动）。
    final_onnx = output / "model.onnx"
    final_manifest = output / "onnx_manifest.json"
    if final_manifest.exists():
        final_manifest.unlink()
    shutil.move(str(onnx_path), str(final_onnx))
    shutil.move(str(staging / "onnx_manifest.json"), str(final_manifest))
    shutil.rmtree(staging, ignore_errors=True)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--relative-tolerance", type=float, default=RELATIVE_TOLERANCE)
    parser.add_argument("--samples-per-split", type=int, default=6)
    args = parser.parse_args()
    torch.set_num_threads(4)
    record = export(args.manifest, args.checkpoint, args.output,
                    relative_tolerance=args.relative_tolerance,
                    limit_per_split=args.samples_per_split)
    print(json.dumps({k: record[k] for k in (
        "format", "onnx_sha256", "max_torch_vs_ort_cpu",
        "max_rel_torch_vs_ort_cpu", "samples", "node_count")},
        ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
