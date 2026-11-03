"""Run the existing direct Pose model on held-out 24-frame Actor-Root windows.

This creates a same-clip, same-Actor-trajectory baseline for later comparison
with MotionWeaver. Both models receive no future true pose or pose reference.
The old model receives 24 historical poses; MotionWeaver currently receives 4.
"""

import argparse
import base64
import json
from pathlib import Path

import numpy as np

from data.runtime.conditioned_motion import root_local
from data.runtime.unreal_dataset import apply_authoritative_root
from inference.runtime.conditioned_pose import ConditionedPosePredictor
from inference.visualization.motionweaver_preview import (
    parse_clip_indices,
    render_video,
    select_clips,
    sha256,
    world_pose_metrics,
)


def run(args):
    dataset_dir = Path(args.dataset).resolve()
    output = Path(args.output).resolve()
    if output.exists():
        raise FileExistsError(output)
    manifest_path = dataset_dir / "dataset.json"
    skeleton_path = dataset_dir / "skeleton.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    skeleton = json.loads(skeleton_path.read_text(encoding="utf-8"))
    if manifest.get("training_contract") != "pose_only_root_authoritative":
        raise ValueError("The baseline requires the same UE Actor Root contract")
    indices = parse_clip_indices(args.clip_index)
    if set(indices) - set(args.categories):
        raise ValueError("--clip-index category must also appear in --categories")
    selected = select_clips(manifest, args.categories, frames=72, indices=indices)
    predictor = ConditionedPosePredictor(args.checkpoint, args.release, args.lock)
    contract = predictor.contract
    if contract["bone_count"] != len(skeleton["bones"]) or contract["fps"] != manifest["fps"]:
        raise ValueError("Old model and new dataset do not share skeleton dimensions and fps")
    output.mkdir(parents=True)
    report = {
        "schema_version": 1,
        "model": "old_reference_guided_direct_pose_hybrid12",
        "scope": "held-out offline skeleton; 24-frame source-root condition; no UE skin/gameplay",
        "split": "test", "fps_source": manifest["fps"],
        "fps_playback": args.playback_fps,
        "input_visibility": {"historical_source_pose_frames": 24,
                             "future_source_pose_frames": 0,
                             "future_actor_root_frames": 24,
                             "reference_frame_mask_sum": 0},
        "comparison_contract": "Choose center 28-frame window; score last 24 frames. "
                               "MotionWeaver uses the same Actor Root and source frames, "
                               "with its own four-frame history.",
        "checkpoint_sha256": sha256(args.checkpoint),
        "dataset_manifest_sha256": sha256(manifest_path),
        "skeleton_sha256": sha256(skeleton_path),
        "clips": [],
    }
    names = [bone["name"] for bone in skeleton["bones"]]
    feet = [names.index(skeleton["roles"][role]) for role in ("left_foot", "right_foot")]
    hips = [names.index(skeleton["roles"][role]) for role in ("left_hip", "right_hip")]
    for category, index, clip in selected:
        frozen = contract["clips"][index]
        raw_path = dataset_dir / clip["raw_file"]
        if (frozen["asset"] != clip["asset"] or frozen["split"] != "test"
                or frozen["source_frames"] != clip["source_frames"]
                or frozen["raw_sha256"] != sha256(raw_path)):
            raise ValueError(f"Old model and MotionWeaver source clip differ: {index}")
        with np.load(raw_path, allow_pickle=False) as raw:
            positions = raw["positions"]
            rotations = raw["rotations"]
            actor_root = raw["root_track"]
            middle = (clip["source_frames"] - 28) // 2 + 4
            origin = actor_root[middle - 1]
            visible = {
                "history_pose": positions[middle - 24:middle].copy(),
                "history_rotations": rotations[middle - 24:middle].copy(),
                "history_root_local": root_local(actor_root[middle - 24:middle], origin),
                "future_root_plan_local": root_local(actor_root[middle:middle + 24], origin),
                "reference_pose": np.zeros((24, len(names), 3), dtype=np.float32),
                "reference_rotations": np.zeros((24, len(names), 3, 3), dtype=np.float32),
                "reference_frame_mask": np.zeros(24, dtype=np.float32),
            }
            predicted = predictor.predict(visible)
            if predicted["future_pose"].shape != (24, len(names), 3):
                raise ValueError("Old model returned an unexpected pose shape")
            interval = slice(middle - 4, middle + 24)
            source_relative = positions[interval].copy()
            generated_relative = np.concatenate((positions[middle - 4:middle],
                                                  predicted["future_pose"]), axis=0)
            root_window = actor_root[interval].copy()
        contact_bits = np.unpackbits(np.frombuffer(base64.b64decode(frozen["contact_bits"]),
                                                   dtype=np.uint8))
        contacts = contact_bits[:clip["source_frames"] * 4].reshape(
            clip["source_frames"], 4,
        )[middle - 4:middle + 24].astype(np.float32)
        source_world = apply_authoritative_root(source_relative, root_window).astype(np.float32)
        generated_world = apply_authoritative_root(generated_relative, root_window).astype(np.float32)
        scores = world_pose_metrics(source_world, generated_world, root_window,
                                    contacts, feet, hips, manifest["fps"])
        stem = f"{category.lower()}_{index:05d}"
        np.savez_compressed(output / f"{stem}.npz", source=source_world,
                            generated=generated_world, actor_root=root_window,
                            source_contacts=contacts)
        entry = {"category": category, "clip_index": index, "asset": clip["asset"],
                 "source_frame_start": middle - 4,
                 "middle_frame": middle, "source_frames": 28,
                 "future_frames_scored": 24, "raw_sha256": frozen["raw_sha256"],
                 "arrays": f"{stem}.npz", "metrics": scores}
        if not args.no_video:
            video = output / f"{stem}.mp4"
            poster = render_video(
                video, source_world, generated_world, root_window, contacts,
                skeleton, category, clip["asset"].rsplit("/", 1)[-1].split(".", 1)[0],
                args.playback_fps, generated_label="OLD DIRECT MODEL",
                condition_text="Old model: 24 source history frames + 24 Actor Root frames; NO future pose reference.",
            )
            entry.update(video=video.name, video_sha256=sha256(video), poster=poster.name)
        report["clips"].append(entry)
        print(json.dumps(entry, ensure_ascii=False), flush=True)
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--categories", nargs="+", default=["Walk", "Run", "Crouch"])
    parser.add_argument("--clip-index", action="append", default=[], metavar="CATEGORY=INDEX")
    parser.add_argument("--playback-fps", type=int, default=15)
    parser.add_argument("--no-video", action="store_true")
    run(parser.parse_args())
