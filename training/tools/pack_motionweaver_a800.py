"""Build the base archive for MotionWeaver Pose Token training.

The archive deliberately excludes raw UE audit tracks and holdout target
outputs. Generate and transfer the Actor Root sidecar separately with
data/tools/ingest/prepare_motionweaver_actor_roots.py before running training.
"""

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

SOURCE_DIRECTORIES = (
    "model/MotionWeaver",
    "data/runtime",
    "training/common",
    "training/configs/pretrain",
    "training/models",
    "training/pretrain",
    "inference/runtime/backbone",
    "inference/runtime/experiment",
    "inference/demo",
)
SOURCE_FILES = (
    "setup.py",
    "pyproject.toml",
    "data/__init__.py",
    "training/__init__.py",
    "inference/__init__.py",
    "inference/runtime/__init__.py",
    "tools/compat/motionbricks/__init__.py",
)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--vqvae", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    project, dataset, vqvae = (path.resolve() for path in (args.project, args.dataset, args.vqvae))
    manifest = json.loads((dataset / "dataset.json").read_text(encoding="utf-8"))
    checkpoint_signature = hashlib.sha256(
        b"".join((dataset / name).read_bytes() for name in ("skeleton.json", "stats/mean.npy", "stats/std.npy"))
    ).hexdigest()
    if manifest.get("training_contract") != "pose_only_root_authoritative":
        raise ValueError("Expected a pose-only authoritative Root dataset")
    if manifest.get("training_signature") != checkpoint_signature:
        raise ValueError("Dataset skeleton/statistics signature mismatch")

    source_files = [project / name for name in SOURCE_FILES]
    for directory in SOURCE_DIRECTORIES:
        source_files.extend(path for path in (project / directory).rglob("*")
                            if path.suffix in {".py", ".yaml"} and "__pycache__" not in path.parts)
    source_files = sorted(set(source_files))
    dataset_files = [dataset / name for name in ("dataset.json", "skeleton.json", "stats/mean.npy", "stats/std.npy")]
    dataset_files.extend(dataset / item["file"] for item in manifest["clips"])
    missing = [str(path) for path in source_files + dataset_files + [vqvae] if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Training archive has missing inputs: {missing[:5]}")

    metadata = {
        "format": "motionweaver_a800_pose_v1",
        "training_signature": checkpoint_signature,
        "manifest_sha256": sha256(dataset / "dataset.json"),
        "vqvae_sha256": sha256(vqvae),
        "clip_count": len(manifest["clips"]),
        "source_sha256": {str(path.relative_to(project)).replace("\\", "/"): sha256(path) for path in source_files},
        "actor_root_sidecar": "Generate separately from matching raw UE Actor root tracks",
        "train_command": (
            "cd project && python -m pip install '.[training]' && "
            "AIANIMATION_ROOT=$PWD python -m training.pretrain.train_unreal --model pose "
            "--dataset ../dataset --actor_root_sidecar ../actor_root_sidecar "
            "--vqvae ../weights/vqvae.ckpt "
            "--output ../runs/motionweaver_actor_root_compact_20k_v1 --motionweaver_online "
            "--max_steps 20000 --batch_size 8 --checkpoint_every 2000 "
            "--accelerator gpu --precision bf16-mixed --seed 42"
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(args.output, "w", compression=ZIP_DEFLATED, compresslevel=1, allowZip64=True) as archive:
        for path in source_files:
            archive.write(path, f"project/{path.relative_to(project).as_posix()}")
        for path in dataset_files:
            archive.write(path, f"dataset/{path.relative_to(dataset).as_posix()}")
        archive.write(vqvae, "weights/vqvae.ckpt")
        archive.writestr("archive_manifest.json", json.dumps(metadata, indent=2, ensure_ascii=False))
    print(json.dumps({"archive": str(args.output), "bytes": args.output.stat().st_size,
                      "sha256": sha256(args.output), "clip_count": len(manifest["clips"]),
                      "source_files": len(source_files)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
