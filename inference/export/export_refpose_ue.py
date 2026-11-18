"""把 RefPose 权重导出为 UE NNE 数值验证包。

产物不包含网格或骨架映射：阶段二只验证同输入下 Python ORT 与 UE NNE
的输出一致。阶段三再消费 skeleton/stats 做蒙皮适配。

用法：
    .venv/bin/python -m inference.export.export_refpose_ue \
        --checkpoint /path/to/best.pt \
        --release /path/to/full_release_v2 \
        --mmap /path/to/full_mmap_v2 \
        --output /path/to/refpose_ue
"""
import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
import onnxruntime as ort

REPO = Path(__file__).resolve().parents[2]
for extra in (REPO, REPO / "benchmark" / "manifest"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from inference.export.refpose.manifest import build, sha256
from inference.export.refpose.onnx import INPUT_NAMES, export
from inference.export.refpose.inputs import (
    assert_manifest_self_consistent,
    build_sample,
    verify_checksums,
)

FORMAT = "refpose_ue_nne_stage2_v2"
SCENARIOS = ((), (12,), (23,), (12, 23))


def write_f32(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    value.astype("<f4", copy=False).tofile(path)


def select_clips(contract, split, limit):
    clips = [c for c in contract["clips"] if c["split"] == split and c["source_frames"] >= 48]
    if limit < 1 or len(clips) < limit:
        raise ValueError(f"{split} 可用 clip {len(clips)}，不足以生成每组 {limit} 个")
    return [clips[index] for index in np.linspace(0, len(clips) - 1, limit, dtype=int)]


def build_package(checkpoint, release, mmap, output, clips_per_split=2):
    checkpoint, release, mmap, output = map(Path, (checkpoint, release, mmap, output))
    if output.exists():
        raise FileExistsError(f"导出目录已存在，拒绝覆盖：{output}")
    contract_path = release / "contract" / "contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    stats = contract["stats"]["pose_m"]
    stats_dir = release / "contract" / "stats"
    manifest = build(release, mmap, checkpoint)
    assert_manifest_self_consistent(manifest)
    mismatch = verify_checksums(
        manifest, checkpoint=checkpoint, contract=contract_path,
        mmap=mmap, stats_dir=stats_dir)
    if mismatch:
        raise ValueError(f"契约哈希不匹配：{mismatch}")

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=output.name + "_", dir=output.parent))
    try:
        manifest_path = staging / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        onnx_record = export(manifest_path, checkpoint, staging / "onnx", limit_per_split=clips_per_split)
        session = ort.InferenceSession(str(staging / "onnx" / "model.onnx"), providers=["CPUExecutionProvider"])
        fixtures = []
        for split in ("train", "validation", "test"):
            for clip in select_clips(contract, split, clips_per_split):
                total = clip["source_frames"]
                for position, start in (("start", 0), ("middle", (total - 48) // 2), ("end", total - 48)):
                    for offsets in SCENARIOS:
                        index = len(fixtures)
                        sample = build_sample(
                            manifest, mmap_root=mmap, release=release,
                            clip_raw_file=clip["raw_file"], mmap_file=clip.get("mmap_file"),
                            start=start, reference_offsets=offsets)
                        tensors = {name: sample[name][None] for name in INPUT_NAMES}
                        expected = session.run(None, tensors)[0][0]
                        folder = staging / "fixtures" / f"sample_{index:03d}"
                        for name in INPUT_NAMES:
                            write_f32(folder / f"{name}.f32", sample[name])
                        write_f32(folder / "expected.f32", expected)
                        selected = sample["mask"][:, 0] > 0.5
                        mask_error = (float(np.abs(expected[selected] - sample["reference"][selected]).max())
                                      if selected.any() else 0.0)
                        if not np.isfinite(expected).all() or mask_error != 0.0:
                            raise ValueError(f"夹具 {index} 非有限或 mask 硬覆盖失败：{mask_error}")
                        fixtures.append({
                            "index": index, "directory": folder.name,
                            "split": split, "clip_index": contract["clips"].index(clip),
                            "asset": clip["asset"], "mmap_file": clip.get("mmap_file", clip["raw_file"]),
                            "start": start, "position": position, "reference_offsets": list(offsets),
                        })

        skeleton_path = (mmap / "skeleton.json" if (mmap / "skeleton.json").is_file()
                         else Path(contract["source_dataset"]) / "skeleton.json")
        shutil.copy2(skeleton_path, staging / "skeleton.json")
        mean = np.load(stats_dir / stats["mean"]).astype("<f4")
        std = np.load(stats_dir / stats["std"]).astype("<f4")
        write_f32(staging / "stats_mean.f32", mean)
        write_f32(staging / "stats_std.f32", std)
        write_f32(staging / "stats.bin", np.concatenate((mean.ravel(), std.ravel())))
        record = {
            "format": FORMAT,
            "selected_checkpoint": {
                "path": str(checkpoint), "sha256": sha256(checkpoint),
                "selection": "本机同契约候选中验证/test 四档 RMSE 最优的 30 epoch 检查点",
            },
            "contract": manifest,
            "onnx": onnx_record,
            "runtime": "NNERuntimeORTCpu",
            "scope": "stage2_numeric_only_no_skinning_or_animgraph",
            "fixture_count": len(fixtures),
            "fixture_file_elements": {
                **{name: list(manifest["inputs"][name]["shape"]) for name in INPUT_NAMES},
                "expected": manifest["output"]["shape"],
            },
            "fixtures": fixtures,
            "artifacts_sha256": {},
        }
        (staging / "package.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        shutil.move(str(staging / "onnx" / "model.onnx"), staging / "model.onnx")
        shutil.move(str(staging / "onnx" / "onnx_manifest.json"), staging / "onnx_manifest.json")
        shutil.rmtree(staging / "onnx")
        names = ["model.onnx", "onnx_manifest.json", "manifest.json", "skeleton.json",
                 "stats.bin", "stats_mean.f32", "stats_std.f32"]
        record["artifacts"] = [
            {"name": name, "bytes": (staging / name).stat().st_size, "sha256": sha256(staging / name)}
            for name in names
        ]
        (staging / "package.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        staging.rename(output)
        return record
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--mmap", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--clips-per-split", type=int, default=2)
    args = parser.parse_args()
    record = build_package(args.checkpoint, args.release, args.mmap, args.output, args.clips_per_split)
    print(json.dumps({
        "output": args.output, "checkpoint": record["selected_checkpoint"],
        "fixtures": record["fixture_count"],
        "max_torch_vs_ort_cpu": record["onnx"]["max_torch_vs_ort_cpu"],
        "max_rel_torch_vs_ort_cpu": record["onnx"]["max_rel_torch_vs_ort_cpu"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
