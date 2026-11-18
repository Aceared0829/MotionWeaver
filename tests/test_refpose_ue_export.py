import json
from pathlib import Path

import numpy as np
import pytest
import torch

from inference.export.refpose.manifest import build
from inference.export.refpose import inputs as R
from data.runtime.refpose_windows import MotionWindows
from inference.export.export_refpose_ue import build_package


def write_npy(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(path, value)


def make_release(tmp_path):
    release = tmp_path / "release"
    source = tmp_path / "source"
    mmap = tmp_path / "mmap"
    skeleton = {
        "bone_count": 78,
        "bones": [
            {"name": "Root" if i == 0 else f"Bone{i}", "parent": i - 1}
            for i in range(78)
        ],
    }
    source.mkdir(parents=True)
    (source / "skeleton.json").write_text(json.dumps(skeleton), encoding="utf-8")
    write_npy(release / "contract/stats/pose_mean.npy", np.zeros((78, 3), np.float32))
    write_npy(release / "contract/stats/pose_std.npy", np.ones((78, 3), np.float32))
    write_npy(mmap / "clips/fps120/item/p.npy", np.zeros((48, 78, 3), np.float32))
    rotations = np.zeros((48, 78, 6), np.float32)
    rotations[..., [0, 3]] = 1
    write_npy(mmap / "clips/fps120/item/r.npy", rotations)
    root = np.zeros((48, 7), np.float32)
    root[..., 3] = 1
    write_npy(mmap / "clips/fps120/item/k.npy", root)
    clip = {
        "split": "train", "source_frames": 48, "raw_file": "unused.npz",
        "mmap_file": "clips/fps120/item", "category": "A",
    }
    contract = {
        "bone_count": 78, "fps": 120.0, "source_dataset": str(source),
        "window": {"history_frames": 24, "future_frames": 24},
        "window_count": 1, "window_split_counts": {"train": 1},
        "stats": {"pose_m": {"mean": "pose_mean.npy", "std": "pose_std.npy"}},
        "clips": [clip],
    }
    (release / "contract/contract.json").write_text(json.dumps(contract), encoding="utf-8")
    (release / "contract/windows.jsonl").write_text(
        json.dumps({"clip_index": 0, "start": 0}) + "\n", encoding="utf-8")
    return release, mmap, contract


def test_mmap_file_and_source_skeleton_match_training(tmp_path):
    release, mmap, contract = make_release(tmp_path)
    checkpoint = tmp_path / "model.pt"
    torch.save({"bones": 78}, checkpoint)
    manifest = build(release, mmap, checkpoint)
    clip = contract["clips"][0]
    sample = R.build_sample(
        manifest, mmap_root=mmap, release=release,
        clip_raw_file=clip["raw_file"], mmap_file=clip["mmap_file"], start=0)
    dataset = MotionWindows(release, contract, "train", mmap_root=mmap, scenario="none")
    for name, expected in zip(("history", "root_plan", "reference", "mask", "target"), dataset[0]):
        assert np.array_equal(expected.numpy(), sample[name])


def test_mmap_file_takes_precedence_over_raw_file(tmp_path):
    mmap = tmp_path / "mmap"
    expected = np.ones((1, 78, 3), np.float32)
    write_npy(mmap / "right/p.npy", expected)
    write_npy(mmap / "right/r.npy", np.ones((1, 78, 6), np.float32))
    write_npy(mmap / "right/k.npy", np.ones((1, 7), np.float32))
    write_npy(mmap / "wrong/p.npy", np.zeros_like(expected))
    positions, _, _ = R.load_clip(mmap, "wrong", "right")
    assert np.array_equal(positions, expected)


def test_package_is_atomic_and_binds_checkpoint(tmp_path, monkeypatch):
    release, mmap, _ = make_release(tmp_path)
    checkpoint = tmp_path / "model.pt"
    torch.save({"bones": 78}, checkpoint)
    output = tmp_path / "package"

    def fail_export(*args, **kwargs):
        raise RuntimeError("stop before publishing")

    monkeypatch.setattr("inference.export.export_refpose_ue.export", fail_export)
    with pytest.raises(RuntimeError, match="stop before publishing"):
        build_package(checkpoint, release, mmap, output)
    assert not output.exists()
    assert not list(tmp_path.glob("package_*"))


def test_package_rejects_existing_output(tmp_path):
    output = tmp_path / "package"
    output.mkdir()
    with pytest.raises(FileExistsError):
        build_package(tmp_path / "model.pt", tmp_path / "release", tmp_path / "mmap", output)
