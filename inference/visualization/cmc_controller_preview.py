"""Probe MotionWeaver with a recorded CMC controller path instead of source Root.

The first four pose and Actor Root frames come from a held-out source clip.
Only the future Actor plan comes from an independent UE CMC CSV capture. Its
capsule center is converted to a ground level Actor path and rigidly aligned
at the last historical frame. Source future pose, pose-feature Root, and
source future Actor Root are not supplied to inference. The left video panel
transplants source pose onto the CMC path for visual context; it is a
counterfactual and is not a ground-truth quality target for that path.
"""

import argparse
import csv
import json
from pathlib import Path

import numpy as np
import torch
from scipy.spatial.transform import Rotation

from data.runtime.unreal_dataset import apply_authoritative_root
from inference.visualization.motionweaver_preview import (
    build_conditions,
    render_video,
    select_clips,
    sha256,
    timed_predict,
    timing_summary,
    world_pose_metrics,
)

UE_TO_MOTION = np.array(((0, -1, 0), (0, 0, 1), (1, 0, 0)), dtype=np.float64)
DEFAULT_CASES = ("Walk:1403:center", "Run:820:center", "Crouch:430:0")


def parse_cases(specifications):
    cases = []
    for specification in specifications:
        parts = specification.split(":")
        if len(parts) != 3 or not parts[1].isdecimal() or not (parts[2].isdecimal() or parts[2] == "center"):
            raise ValueError(f"Expected CATEGORY:INDEX:START_OR_CENTER, got {specification!r}")
        cases.append((parts[0], int(parts[1]), parts[2]))
    if len({category for category, _, _ in cases}) != len(cases):
        raise ValueError("CMC probe cases must have distinct categories")
    return cases


def load_cmc_segment(path, scenario, start, frames):
    """Return CMC ground-level Actor transforms in the training Motion axes."""
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        rows = [row for row in csv.DictReader(stream) if row["scenario"] == scenario]
    by_frame = {int(row["frame"]): row for row in rows}
    requested = tuple(range(start, start + frames))
    if any(frame not in by_frame for frame in requested):
        raise ValueError(f"CMC capture has no complete {scenario} frames {requested[0]}..{requested[-1]}")
    rows = [by_frame[frame] for frame in requested]
    center_ue_cm = np.asarray([[float(row[column]) for column in
                                ("capsule_x_cm", "capsule_y_cm", "capsule_z_cm")]
                               for row in rows], dtype=np.float64)
    half_height_cm = np.asarray([float(row["capsule_half_height_cm"]) for row in rows])
    # CMC records the capsule center. The exported animation Actor path has a
    # ground-level origin; otherwise the crouch capsule height change would
    # add a second vertical displacement to the generated crouch pose.
    ground_ue_cm = center_ue_cm.copy()
    ground_ue_cm[:, 2] -= half_height_cm
    ue_quat = np.asarray([[float(row[column]) for column in
                           ("quat_x", "quat_y", "quat_z", "quat_w")]
                          for row in rows], dtype=np.float64)
    if not np.allclose(np.linalg.norm(ue_quat, axis=-1), 1, atol=1e-3):
        raise ValueError("CMC capture contains a non-unit quaternion")
    ue_rotation = Rotation.from_quat(ue_quat).as_matrix()
    motion_rotation = UE_TO_MOTION @ ue_rotation @ UE_TO_MOTION.T
    track = np.concatenate((ground_ue_cm @ UE_TO_MOTION.T * 0.01,
                            Rotation.from_matrix(motion_rotation).as_quat()), axis=-1)
    state = np.asarray([int(row["is_crouched"]) for row in rows], dtype=np.int8)
    return track, state


def align_cmc_future(source_actor_history, cmc_track):
    """Align a captured CMC plan to source frame 3, retaining real history."""
    if source_actor_history.shape != (4, 7) or cmc_track.shape[1:] != (7,):
        raise ValueError("Expected four historical Actor transforms and a CMC [T,7] track")
    anchor = 3
    source_rotation = Rotation.from_quat(source_actor_history[anchor, 3:])
    capture_rotation = Rotation.from_quat(cmc_track[anchor, 3:])
    alignment = source_rotation * capture_rotation.inv()
    aligned_position = source_actor_history[anchor, :3] + alignment.apply(
        cmc_track[:, :3] - cmc_track[anchor, :3],
    )
    aligned_rotation = (alignment * Rotation.from_quat(cmc_track[:, 3:])).as_quat()
    aligned = np.concatenate((aligned_position, aligned_rotation), axis=-1).astype(np.float32)
    aligned[:4] = source_actor_history
    if not np.allclose(aligned[3], source_actor_history[3], atol=1e-5):
        raise AssertionError("CMC alignment changed the final historical Actor transform")
    return aligned


def horizontal_unit(values):
    horizontal = values[..., (0, 2)]
    return horizontal / np.maximum(np.linalg.norm(horizontal, axis=-1, keepdims=True), 1e-8)


def signed_angle_deg(left, right):
    cross = left[..., 0] * right[..., 1] - left[..., 1] * right[..., 0]
    dot = np.sum(left * right, axis=-1)
    return np.degrees(np.arctan2(cross, dot))


def turn_response(actor_root, generated_world, hip_indices):
    """Compare body turn with Actor yaw change from the same four-frame seed."""
    actor_forward = horizontal_unit(Rotation.from_quat(actor_root[:, 3:]).apply((0, 0, 1)))
    left_hip, right_hip = hip_indices
    up = np.asarray((0, 1, 0), dtype=np.float32)
    body_forward = horizontal_unit(np.cross(
        up, generated_world[:, right_hip] - generated_world[:, left_hip],
    ))
    actor_delta = signed_angle_deg(actor_forward[3], actor_forward[4:])
    body_delta = signed_angle_deg(body_forward[3], body_forward[4:])
    # Compare signed yaw deltas with wrapping; backward and strafe seeds keep
    # their initial body/Actor offset rather than being forced to face travel.
    response_error = (body_delta - actor_delta + 180) % 360 - 180
    return {
        "actor_turn_final_deg": float(actor_delta[-1]),
        "generated_body_turn_final_deg": float(body_delta[-1]),
        "body_turn_minus_actor_turn_final_deg": float(response_error[-1]),
        "body_turn_response_rmse_deg": float(np.sqrt(np.mean(np.square(response_error)))),
    }, actor_delta.astype(np.float32), body_delta.astype(np.float32)


@torch.no_grad()
def evaluate_case(model, dataset_dir, clip, start, frames, capture, cmc_start,
                  skeleton, device, timing_repeats):
    motion = np.load(dataset_dir / clip["file"], mmap_mode="r", allow_pickle=False)
    source_features = np.asarray(motion[start:start + frames], dtype=np.float32).copy()
    if len(source_features) != frames:
        raise ValueError("Source clip does not contain the requested window")
    source_unscaled, source_pose_root, pose_slots, _ = build_conditions(
        model, torch.from_numpy(source_features)[None], frames, device,
    )
    with np.load(dataset_dir / clip["raw_file"], allow_pickle=False) as raw:
        source_positions = raw["positions"][start:start + frames].copy()
        source_rotations_first4 = raw["rotations"][:4].copy()
        source_positions_first4 = raw["positions"][:4].copy()
        source_actor = raw["root_track"][start:start + frames].copy()
    category = clip["labels"]["category"]
    capture_track, crouch_state = load_cmc_segment(capture, category.lower(), cmc_start, frames)
    actor_plan = align_cmc_future(source_actor[:4], capture_track)
    actor_tensor = torch.from_numpy(actor_plan)[None].to(device)
    (predicted, tokens), forward_samples_ms = timed_predict(
        lambda: model.predict_with_actor_root(
            actor_tensor, source_pose_root[:, :4], pose_slots[:, :4],
            config={"pose_token_sampling_use_argmax": True, "num_inference_step": 1},
        ), device, timing_repeats,
    )
    # Same seed under the exported source Actor plan. Compare decoded local
    # poses before world composition, which otherwise supplies the turn.
    source_actor_prediction, source_actor_tokens = model.predict_with_actor_root(
        torch.from_numpy(source_actor)[None].to(device),
        source_pose_root[:, :4], pose_slots[:, :4],
        config={"pose_token_sampling_use_argmax": True, "num_inference_step": 1},
    )
    if predicted.shape != source_unscaled.shape or not torch.isfinite(predicted).all():
        raise ValueError("MotionWeaver returned invalid Actor-conditioned features")
    _, initial = model.motion_rep(
        {"posed_joints": torch.from_numpy(source_positions_first4)[None].to(device),
         "global_joint_rots": torch.from_numpy(source_rotations_first4)[None].to(device)},
        to_normalize=False, lengths=torch.tensor([4], device=device),
        return_init_heading_info=True,
    )
    source_inverse = model.global_motion_rep.inverse(
        source_unscaled, is_normalized=False, init_heading_info=dict(initial),
    )["posed_joints"][0].detach().cpu().numpy()
    inverse_error = float(np.sqrt(np.mean(np.square(source_inverse - source_positions))))
    if inverse_error > 0.03:
        raise ValueError(f"Feature inverse audit error {inverse_error:.3f} m")
    generated_relative = model.global_motion_rep.inverse(
        predicted, is_normalized=False, init_heading_info=dict(initial),
    )["posed_joints"][0].detach().cpu().numpy()
    source_plan_relative = model.global_motion_rep.inverse(
        source_actor_prediction, is_normalized=False, init_heading_info=dict(initial),
    )["posed_joints"][0].detach().cpu().numpy()
    source_counterfactual = apply_authoritative_root(source_positions, actor_plan).astype(np.float32)
    generated_world = apply_authoritative_root(generated_relative, actor_plan).astype(np.float32)
    contact_labels = model.global_motion_rep.extract_foot_contacts(
        source_unscaled, is_normalized=False, contact_thresh=None,
    )[0].detach().cpu().numpy()
    names = [bone["name"] for bone in skeleton["bones"]]
    roles = skeleton["roles"]
    feet = [names.index(roles[key]) for key in ("left_foot", "right_foot")]
    hips = [names.index(roles[key]) for key in ("left_hip", "right_hip")]
    counterfactual_metrics = world_pose_metrics(
        source_counterfactual, generated_world, actor_plan, contact_labels,
        feet, hips, model.motion_rep.fps,
    )
    response, actor_turn, body_turn = turn_response(actor_plan, generated_world, hips)
    local_delta = generated_relative[4:] - source_plan_relative[4:]
    valid_token_count = frames // 4
    response.update(
        actor_condition_local_joint_rms_delta_cm=float(
            np.sqrt(np.mean(np.sum(np.square(local_delta), axis=-1))) * 100,
        ),
        actor_condition_future_token_difference_fraction=float(np.mean(
            tokens[0, 1:valid_token_count].detach().cpu().numpy()
            != source_actor_tokens[0, 1:valid_token_count].detach().cpu().numpy(),
        )),
    )
    speed = np.linalg.norm(np.diff(actor_plan[:, :3], axis=0), axis=-1) * model.motion_rep.fps
    response.update(
        cmc_future_actor_mean_speed_mps=float(speed[3:].mean()),
        cmc_first_future_step_cm=float(np.linalg.norm(actor_plan[4, :3] - actor_plan[3, :3]) * 100),
        source_history_last_step_cm=float(np.linalg.norm(actor_plan[3, :3] - actor_plan[2, :3]) * 100),
        cmc_future_crouched_frames=int(crouch_state[4:].sum()),
        source_inverse_vs_raw_rmse_m=inverse_error,
    )
    return {
        "source_counterfactual": source_counterfactual,
        "generated": generated_world,
        "actor_plan": actor_plan,
        "source_actor": source_actor,
        "source_plan_generated_relative": source_plan_relative,
        "cmc_plan_generated_relative": generated_relative,
        "contacts": contact_labels,
        "actor_turn": actor_turn,
        "body_turn": body_turn,
        "tokens": tokens[0].detach().cpu().numpy(),
        "cmc_state": crouch_state,
        "controller_metrics": response,
        "counterfactual_metrics": counterfactual_metrics,
        "forward_samples_ms": forward_samples_ms,
    }


def run(args):
    from inference.runtime.motionweaver import load_motionweaver_pose

    dataset_dir = Path(args.dataset).resolve()
    output = Path(args.output).resolve()
    capture = Path(args.cmc_capture).resolve()
    if output.exists():
        raise FileExistsError(output)
    manifest_path = dataset_dir / "dataset.json"
    skeleton_path = dataset_dir / "skeleton.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    skeleton = json.loads(skeleton_path.read_text(encoding="utf-8"))
    if manifest.get("training_contract") != "pose_only_root_authoritative":
        raise ValueError("CMC probe requires the UE Actor Root dataset contract")
    cases = parse_cases(args.case or DEFAULT_CASES)
    model = load_motionweaver_pose(
        args.pose_checkpoint, vqvae_checkpoint=args.vqvae_checkpoint,
        dataset_dir=dataset_dir, device=args.device,
    )
    pose_state = torch.load(args.pose_checkpoint, map_location="cpu", weights_only=False)
    training_steps = pose_state.get("global_step")
    output.mkdir(parents=True)
    report = {
        "schema_version": 1,
        "scope": "offline recorded CMC Root controller probe; no live UE skin or gameplay",
        "future_root_source": "recorded_ue_cmc_capture_rigidly_aligned_at_history_frame_3",
        "cmc_capture_sha256": sha256(capture),
        "cmc_capture_frame_start": args.cmc_start,
        "cmc_capture_frame_end": args.cmc_start + args.frames - 1,
        "cmc_capsule_to_actor_root": "capsule_center_z_minus_capsule_half_height; UE cm to Motion m/Y-up",
        "input_visibility": {
            "historical_source_pose_frames": 4,
            "historical_source_actor_root_frames": 4,
            "future_source_pose_frames": 0,
            "future_source_pose_feature_root_frames": 0,
            "future_source_actor_root_frames": 0,
            "future_recorded_cmc_actor_root_frames": args.frames - 4,
        },
        "limitations": [
            "CMC recording is a separate scripted UE capture, aligned to a held-out animation seed; this is not live control",
            "Crouch state is not an explicit model input; any crouch response cannot be attributed to a crouch button",
            "Source pose on the CMC path is a counterfactual visual context, not ground truth for the controller path",
            "Body turn response assumes body orientation should follow Actor yaw relative to its seed; strafing may intentionally differ",
            "World-space body turning includes the externally applied Actor transform; actor-condition local pose delta isolates model response",
            "Source contact labels on a changed Root path are a heuristic and not a calibrated sliding score",
        ],
        "pose_checkpoint_sha256": sha256(args.pose_checkpoint),
        "pose_checkpoint_training_steps": training_steps,
        "pose_checkpoint_has_training_steps": isinstance(training_steps, int) and training_steps > 0,
        "run_label": args.run_label,
        "vqvae_checkpoint_sha256": sha256(args.vqvae_checkpoint),
        "dataset_manifest_sha256": sha256(manifest_path),
        "skeleton_sha256": sha256(skeleton_path),
        "fps_source": manifest["fps"],
        "fps_playback": args.playback_fps,
        "clips": [],
    }
    for category, index, requested_start in cases:
        _, _, clip = select_clips(manifest, (category,), args.frames,
                                  indices={category: index})[0]
        start = ((clip["source_frames"] - args.frames) // 2
                 if requested_start == "center" else int(requested_start))
        if start < 0 or start + args.frames > clip["source_frames"]:
            raise ValueError(f"Clip {index} cannot supply frames {start}..{start + args.frames - 1}")
        result = evaluate_case(model, dataset_dir, clip, start, args.frames,
                               capture, args.cmc_start, skeleton, args.device,
                               args.timing_repeats)
        stem = f"{category.lower()}_{index:05d}_cmc"
        np.savez_compressed(
            output / f"{stem}.npz",
            source_pose_on_cmc_root=result["source_counterfactual"],
            generated_world=result["generated"],
            actor_plan=result["actor_plan"],
            source_actor_root=result["source_actor"],
            source_plan_generated_relative=result["source_plan_generated_relative"],
            cmc_plan_generated_relative=result["cmc_plan_generated_relative"],
            source_contact_labels=result["contacts"],
            actor_turn_deg=result["actor_turn"],
            generated_body_turn_deg=result["body_turn"],
            cmc_crouch_state=result["cmc_state"],
            tokens=result["tokens"],
        )
        entry = {
            "category": category, "clip_index": index, "asset": clip["asset"],
            "source_frame_start": start, "source_frames": args.frames,
            "arrays": f"{stem}.npz",
            "controller_metrics": result["controller_metrics"],
            "counterfactual_pose_diagnostics": result["counterfactual_metrics"],
            "forward_samples_ms": result["forward_samples_ms"],
        }
        if not args.no_video:
            video = output / f"{stem}.mp4"
            poster = render_video(
                video, result["source_counterfactual"], result["generated"],
                result["actor_plan"], result["contacts"], skeleton, category,
                clip["asset"].rsplit("/", 1)[-1].split(".", 1)[0], args.playback_fps,
                generated_label=(f"MW {args.run_label} CMC" if args.run_label
                                 else "MOTIONWEAVER CMC" if report["pose_checkpoint_has_training_steps"]
                                 else "UNTRAINED MW CMC SMOKE"),
                source_label="SOURCE POSE ON CMC PATH",
                root_description="Aligned recorded CMC Root",
                condition_text="4 source pose frames; future from recorded CMC turn; NO future pose. Crouch state NOT input.",
            )
            entry.update(video=video.name, video_sha256=sha256(video), poster=poster.name)
        report["clips"].append(entry)
        print(json.dumps(entry, ensure_ascii=False), flush=True)
    report["forward_timing"] = timing_summary(report["clips"], args.timing_repeats,
                                               args.device)
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pose-checkpoint", type=Path, required=True)
    parser.add_argument("--vqvae-checkpoint", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--cmc-capture", type=Path, default=".build/experiments/cmc_root_capture_20260925_v2.csv")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cmc-start", type=int, default=44)
    parser.add_argument("--frames", type=int, default=28)
    parser.add_argument("--case", action="append", default=[], metavar="CATEGORY:INDEX:START_OR_CENTER")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--playback-fps", type=int, default=15)
    parser.add_argument("--timing-repeats", type=int, default=3)
    parser.add_argument("--run-label", default="")
    parser.add_argument("--no-video", action="store_true")
    run(parser.parse_args())
