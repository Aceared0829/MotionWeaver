"""Extract the independent UE Actor Root track for aligned Pose Token training.

The prepared body motion may end-hold short clips to 65 frames. The sidecar
applies the same terminal hold while retaining each clip's true source length.
"""

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    dataset = args.dataset.resolve()
    output = args.output.resolve()
    manifest = json.loads((dataset / "dataset.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 2 or manifest.get("training_contract") != "pose_only_root_authoritative":
        raise ValueError("Expected UE schema v2 with independent authoritative Root")
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for index, clip in enumerate(manifest["clips"]):
        with np.load(dataset / clip["raw_file"], allow_pickle=False) as raw:
            track = np.asarray(raw["root_track"], dtype=np.float32)
            timestamps = np.asarray(raw["timestamps"], dtype=np.float64)
        source_frames = clip.get("source_frames", clip["frames"])
        if track.shape != (source_frames, 7) or timestamps.shape != (source_frames,):
            raise ValueError(f"Root/source length mismatch at clip {index}")
        if not np.isfinite(track).all() or not np.allclose(np.linalg.norm(track[:, 3:], axis=1), 1, atol=1e-3):
            raise ValueError(f"Invalid Actor Root transform at clip {index}")
        if not np.allclose(np.diff(timestamps), 1 / manifest["fps"], atol=1e-4):
            raise ValueError(f"Actor Root timestamps do not match {manifest['fps']} FPS at clip {index}")
        if source_frames < clip["frames"]:
            track = np.concatenate((track, np.repeat(track[-1:], clip["frames"] - source_frames, axis=0)))
        name = f"actor_root_{index:05d}.npy"
        path = output / name
        np.save(path, track, allow_pickle=False)
        records.append({"index": index, "file": name, "motion_file": clip["file"], "frames": clip["frames"],
                        "source_frames": source_frames, "split": clip["split"], "sha256": sha256(path)})
    sidecar = {"schema_version": 1, "source_dataset_manifest_sha256": sha256(dataset / "dataset.json"),
               "training_signature": manifest["training_signature"], "fps": manifest["fps"],
               "coordinates": "motion_m_y_up_xyzw", "columns": ["x", "y", "z", "qx", "qy", "qz", "qw"],
               "padding": "terminal_hold_to_prepared_motion_frames", "records": records}
    (output / "actor_roots_manifest.json").write_text(json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8")
    args.archive.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(args.archive, "w", compression=ZIP_DEFLATED, compresslevel=1) as archive:
        for path in sorted(output.glob("actor_root_*.npy")):
            archive.write(path, f"actor_root_sidecar/{path.name}")
        archive.write(output / "actor_roots_manifest.json", "actor_root_sidecar/actor_roots_manifest.json")
    print(json.dumps({"sidecar": str(output), "archive": str(args.archive.resolve()),
                      "bytes": args.archive.stat().st_size, "sha256": sha256(args.archive),
                      "clips": len(records)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
