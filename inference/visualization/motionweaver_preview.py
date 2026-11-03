"""Render held-out UE skeleton clips with MotionWeaver Pose Token inference.

The default Actor mode supplies the independently exported UE Actor Root as an
offline proxy for a controller plan, plus four historical pose frames. Future
source poses and future pose-feature Root are hidden. The exported Actor Root
is also used to compose and score world-space poses. This does not validate a
live CMC/Mover controller or UE skinning. An explicit pose-root-oracle mode is
available only to diagnose the older five-dimensional model interface.
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFont
from scipy.spatial.transform import Rotation

from data.runtime.unreal_dataset import apply_authoritative_root
from motionweaver.representation.helper.data_training_util import (
    extract_feature_from_motion_rep,
)

SIZE = (1280, 720)
KEEP_BONES = {
    "pelvis", "spine_01", "spine_02", "spine_03", "spine_04", "spine_05",
    "neck_01", "head", "clavicle_l", "upperarm_l", "lowerarm_l", "hand_l",
    "clavicle_r", "upperarm_r", "lowerarm_r", "hand_r", "thigh_l",
    "calf_l", "foot_l", "ball_l", "thigh_r", "calf_r", "foot_r", "ball_r",
}
RIGHT = np.array((0.8, 0.0, 0.6), dtype=np.float32)
DEPTH = np.array((-0.6, 0.0, 0.8), dtype=np.float32)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def select_clips(manifest, categories, frames, indices=None):
    """Select one deterministic, held-out clip per category."""
    clips = manifest["clips"]
    selected = []
    for category in categories:
        if indices and category in indices:
            index = indices[category]
            candidates = [(index, clips[index])]
        else:
            candidates = [(index, clip) for index, clip in enumerate(clips)
                          if clip.get("split") == "test"
                          and clip.get("labels", {}).get("category") == category
                          and clip.get("source_frames", 0) >= frames]
            candidates.sort(key=lambda pair: ("_loop_" not in pair[1]["asset"].lower(),
                                              pair[1]["asset"], pair[0]))
        if not candidates:
            raise ValueError(f"No test {category} clip has at least {frames} real frames")
        index, clip = candidates[0]
        if (clip.get("split") != "test"
                or clip.get("labels", {}).get("category") != category
                or clip.get("source_frames", 0) < frames):
            raise ValueError(f"Clip {index} does not satisfy the held-out {category} contract")
        selected.append((category, index, clip))
    return selected


def parse_clip_indices(specifications):
    """Parse explicit CATEGORY=INDEX held-out cases for repeatable comparisons."""
    indices = {}
    for specification in specifications:
        category, separator, value = specification.partition("=")
        if not separator or not category or not value.isdecimal():
            raise ValueError(f"Expected CATEGORY=INDEX, got {specification!r}")
        if category in indices:
            raise ValueError(f"Duplicate clip selection for {category}")
        indices[category] = int(value)
    return indices


def timed_predict(predict, device, repeats):
    """Warm once, then time deterministic Python eager forwards."""
    if repeats < 1:
        raise ValueError("--timing-repeats must be positive")
    result = predict()
    elapsed_ms = []
    for _ in range(repeats):
        if device == "cuda":
            torch.cuda.synchronize()
        start = time.perf_counter()
        result = predict()
        if device == "cuda":
            torch.cuda.synchronize()
        elapsed_ms.append((time.perf_counter() - start) * 1000)
    return result, elapsed_ms


def timing_summary(clips, repeats, device):
    samples = np.asarray([sample for clip in clips for sample in clip["forward_samples_ms"]])
    return {
        "device": device,
        "device_name": torch.cuda.get_device_name(0) if device == "cuda" else "CPU",
        "implementation": "Python eager; one untimed warmup per clip; synchronized forward only",
        "clips": len(clips), "measured_forwards": len(samples),
        "measured_forwards_per_clip": repeats,
        "p50_ms": float(np.percentile(samples, 50)),
        "p95_ms": float(np.percentile(samples, 95)),
        "four_frame_replan_budget_at_30hz_ms": 4 / 30 * 1000,
        "p95_below_four_frame_budget": bool(np.percentile(samples, 95) <= 4 / 30 * 1000),
    }


def build_conditions(model, normalized_global, frames, device):
    """Expose source Root for all frames and only the first four source poses."""
    motion_rep = model.motion_rep
    global_rep = model.global_motion_rep
    local_rep = model.local_motion_rep
    if normalized_global.shape[-1] != len(global_rep.indices["all"]):
        raise ValueError("Dataset is expected to contain normalized global motion features")
    source_global = normalized_global.to(device)
    source_unscaled = global_rep.unnormalize(source_global)
    root = source_unscaled[..., global_rep.indices["root"]]
    # The local-pose seed must be computed from history alone. Passing the
    # full source window here would make causality depend on the conversion
    # helper's temporal implementation.
    local_unscaled = motion_rep.dual_rep.global_to_local(
        source_global[:, :4], is_normalized=True, to_normalize=False,
        lengths=torch.tensor([4], device=device),
    )
    visible_pose = extract_feature_from_motion_rep(
        local_unscaled, local_rep, "joint_positions_and_rotations",
    )
    pose_slots = torch.zeros(
        (1, 8, visible_pose.shape[-1]), dtype=visible_pose.dtype, device=device,
    )
    pose_slots[:, :4] = visible_pose
    pose_mask = torch.zeros((1, 8), dtype=torch.bool, device=device)
    pose_mask[:, :4] = True
    return source_unscaled, root, pose_slots, pose_mask


def world_pose_metrics(source, generated, actor_root, contacts, foot_indices,
                       hip_indices, fps, context_frames=4):
    """Score generated world poses against the same Actor path and source frames.

    Body facing is compared with the source facing under the Actor transform.
    Strafing or backward movement is therefore not penalized merely for being
    different from the path direction.
    """
    if source.shape != generated.shape or source.ndim != 3:
        raise ValueError("Source and generated joints must have matching [T,J,3] shapes")
    if actor_root.shape != (len(source), 7):
        raise ValueError("Actor Root must have shape [T,7]")
    if contacts.shape != (len(source), 4):
        raise ValueError("Source contact labels must have shape [T,4]")
    if not 1 <= context_frames < len(source) - 1:
        raise ValueError("Context must precede at least two generated frames")
    joint_delta = generated[context_frames:] - source[context_frames:]
    joint_error_cm = np.linalg.norm(joint_delta, axis=-1) * 100.0
    source_foot_velocity = np.diff(source[:, foot_indices], axis=0) * fps
    generated_foot_velocity = np.diff(generated[:, foot_indices], axis=0) * fps
    source_foot_speed = np.linalg.norm(source_foot_velocity, axis=-1)
    generated_foot_speed = np.linalg.norm(generated_foot_velocity, axis=-1)
    active = (contacts[1:, (0, 2)] > 0.5) & (contacts[:-1, (0, 2)] > 0.5)
    active[:context_frames - 1] = False
    contact_pairs = int(active.sum())
    source_contact_speed = float(source_foot_speed[active].mean()) if contact_pairs else None
    generated_contact_speed = float(generated_foot_speed[active].mean()) if contact_pairs else None

    def horizontal_unit(values):
        horizontal = values[..., (0, 2)]
        magnitude = np.linalg.norm(horizontal, axis=-1, keepdims=True)
        return horizontal / np.maximum(magnitude, 1e-6)

    def angle_degrees(left, right):
        dot = np.sum(left * right, axis=-1)
        cross = left[..., 0] * right[..., 1] - left[..., 1] * right[..., 0]
        return np.degrees(np.abs(np.arctan2(cross, dot)))

    left_hip, right_hip = hip_indices
    up = np.array((0.0, 1.0, 0.0), dtype=np.float32)
    source_facing = horizontal_unit(np.cross(up, source[:, right_hip] - source[:, left_hip]))
    generated_facing = horizontal_unit(np.cross(up, generated[:, right_hip] - generated[:, left_hip]))
    actor_forward = horizontal_unit(Rotation.from_quat(actor_root[:, 3:]).apply(
        np.array((0.0, 0.0, 1.0), dtype=np.float32),
    ))
    face_error = angle_degrees(source_facing[context_frames:], generated_facing[context_frames:])
    actor_speed = np.linalg.norm(np.diff(actor_root[:, (0, 2)], axis=0), axis=-1) * fps
    trajectory_forward = horizontal_unit(np.diff(actor_root[:, :3], axis=0))
    moving = actor_speed[context_frames - 1:] > 0.1
    source_path_angle = angle_degrees(source_facing[context_frames:],
                                      trajectory_forward[context_frames - 1:])
    generated_path_angle = angle_degrees(generated_facing[context_frames:],
                                         trajectory_forward[context_frames - 1:])
    seam_source_velocity = source[context_frames] - source[context_frames - 1]
    seam_generated_velocity = generated[context_frames] - generated[context_frames - 1]
    seam_delta = seam_generated_velocity - seam_source_velocity
    prior_generated_velocity = generated[context_frames - 1] - generated[context_frames - 2]
    prior_source_velocity = source[context_frames - 1] - source[context_frames - 2]
    seam_acceleration_delta = ((seam_generated_velocity - prior_generated_velocity)
                               - (seam_source_velocity - prior_source_velocity))
    return {
        "evaluated_frames": len(source) - context_frames,
        "joint_rmse_cm": float(np.sqrt(np.mean(np.square(joint_error_cm)))),
        "joint_mean_error_cm": float(joint_error_cm.mean()),
        "joint_p95_error_cm": float(np.percentile(joint_error_cm, 95)),
        "first_generated_frame_jump_cm": float(np.linalg.norm(seam_generated_velocity, axis=-1).mean() * 100),
        "source_same_boundary_jump_cm": float(np.linalg.norm(seam_source_velocity, axis=-1).mean() * 100),
        "seam_velocity_error_cm_per_frame": float(np.linalg.norm(seam_delta, axis=-1).mean() * 100),
        "seam_acceleration_error_cm_per_frame2": float(np.linalg.norm(seam_acceleration_delta, axis=-1).mean() * 100),
        "source_contact_pairs": contact_pairs,
        "source_contact_foot_speed_mps": source_contact_speed,
        "generated_contact_foot_speed_mps": generated_contact_speed,
        "foot_velocity_rmse_mps": float(np.sqrt(np.mean(np.sum(np.square(
            generated_foot_velocity[context_frames - 1:] - source_foot_velocity[context_frames - 1:],
        ), axis=-1)))),
        "actor_path_mean_speed_mps": float(actor_speed[context_frames - 1:].mean()),
        "body_facing_rmse_vs_source_deg": float(np.sqrt(np.mean(np.square(face_error)))),
        "source_facing_relative_actor_mean_deg": float(angle_degrees(
            source_facing[context_frames:], actor_forward[context_frames:],
        ).mean()),
        "generated_facing_relative_actor_mean_deg": float(angle_degrees(
            generated_facing[context_frames:], actor_forward[context_frames:],
        ).mean()),
        "source_facing_relative_path_mean_deg": float(source_path_angle[moving].mean()) if moving.any() else None,
        "generated_facing_relative_path_mean_deg": float(generated_path_angle[moving].mean()) if moving.any() else None,
        "moving_frames_for_path_angle": int(moving.sum()),
    }


def metric_summary(source, generated, actor_root, source_pose_root, generated_pose_root,
                   contacts, foot_indices, hip_indices, fps):
    """Add pose-root error as a quality metric (an exact echo in oracle mode)."""
    if source_pose_root.shape != generated_pose_root.shape or source_pose_root.shape != (len(source), 5):
        raise ValueError("Pose-feature Root arrays must have matching [T,5] shapes")
    scores = world_pose_metrics(source, generated, actor_root, contacts, foot_indices,
                                hip_indices, fps)
    root_position_error = np.linalg.norm(generated_pose_root[4:, :3] - source_pose_root[4:, :3], axis=-1)
    source_angle = np.arctan2(source_pose_root[4:, 4], source_pose_root[4:, 3])
    generated_angle = np.arctan2(generated_pose_root[4:, 4], generated_pose_root[4:, 3])
    angle_error = np.abs(np.arctan2(np.sin(generated_angle - source_angle),
                                   np.cos(generated_angle - source_angle)))
    scores["pose_root_position_rmse_vs_source_m"] = float(np.sqrt(np.mean(np.square(root_position_error))))
    scores["pose_root_heading_mae_vs_source_deg"] = float(np.degrees(angle_error.mean()))
    return scores


def project(points, root_position, panel_x):
    center = root_position + np.array((0.0, 0.75, 0.0), dtype=np.float32)
    relative = points - center
    px = panel_x + 310 + 145 * np.dot(relative, RIGHT)
    py = 385 - 145 * (0.92 * relative[..., 1] - 0.2 * np.dot(relative, DEPTH))
    return np.stack((px, py), axis=-1)


def font(size):
    path = Path("C:/Windows/Fonts/segoeui.ttf")
    return ImageFont.truetype(str(path), size) if path.is_file() else ImageFont.load_default()


def draw_frame(frame, source, generated, root, contacts, edges, feet, category, asset,
               generated_label="MOTIONWEAVER", condition_text=None,
               source_label="SOURCE", root_description="Exported Actor Root"):
    image = Image.new("RGB", SIZE, "#0e1622")
    draw = ImageDraw.Draw(image)
    heading = font(22)
    small = font(15)
    root_position = root[frame, :3]
    for panel, (key, poses, color) in enumerate((
        (source_label, source, "#56cfe1"), (generated_label, generated, "#ef9c6d"),
    )):
        x = panel * 640
        draw.rectangle((x + 8, 61, x + 631, 633), fill="#152232", outline="#34445a", width=2)
        # Shared ground and camera follow the supplied Root; each foot trail uses
        # its real world positions under the current camera transform.
        x0, z0 = float(root_position[0]), float(root_position[2])
        for gx in range(int(np.floor(x0)) - 4, int(np.floor(x0)) + 5):
            a = project(np.array((gx, 0, z0 - 4)), root_position, x)
            b = project(np.array((gx, 0, z0 + 4)), root_position, x)
            draw.line((*a.tolist(), *b.tolist()), fill="#26374a", width=1)
        for gz in range(int(np.floor(z0)) - 4, int(np.floor(z0)) + 5):
            a = project(np.array((x0 - 4, 0, gz)), root_position, x)
            b = project(np.array((x0 + 4, 0, gz)), root_position, x)
            draw.line((*a.tolist(), *b.tolist()), fill="#26374a", width=1)
        coords = project(poses[frame], root_position, x)
        for parent, child in edges:
            draw.line((*coords[parent].tolist(), *coords[child].tolist()), fill=color, width=3)
        for side, bone in enumerate(feet):
            trail = project(poses[max(0, frame - 10):frame + 1, bone], root_position, x)
            trace_color = "#77eda2" if side == 0 else "#ffbe73"
            for a, b in zip(trail[:-1], trail[1:]):
                draw.line((*a.tolist(), *b.tolist()), fill=trace_color, width=2)
            px, py = coords[bone]
            radius = 7 if contacts[frame, 0 if side == 0 else 2] > 0.5 else 4
            draw.ellipse((px - radius, py - radius, px + radius, py + radius),
                         outline=trace_color, width=2)
        draw.text((x + 20, 80), key, fill=color, font=heading)
        frame_error = np.sqrt(np.mean(np.sum((generated[frame] - source[frame]) ** 2, axis=-1))) * 100
        detail = (("Reference asset" if source_label == "SOURCE"
                   else "Counterfactual pose on controller path") if panel == 0
                  else f"Pose difference this frame: {frame_error:.1f} cm")
        draw.text((x + 20, 111), detail, fill="#d5dfeb", font=small)
        draw.text((x + 20, 604), "Foot trails: last 10 frames (source contacts)",
                  fill="#a9b8c9", font=small)
    draw.text((20, 14), f"{category.upper()}  |  {asset}  |  frame {frame + 1:02d}/{len(source)}",
              fill="#eff5ff", font=heading)
    speed = np.linalg.norm(root[min(frame + 1, len(root) - 1), :3] -
                           root[max(frame - 1, 0), :3]) * 15
    draw.text((20, 650), f"{root_description} for world display  |  speed ~{speed:.2f} m/s  |  test clip",
              fill="#d5dfeb", font=small)
    if condition_text is None:
        condition_text = "Model sees source pose-feature Root, NOT Actor Root; no future pose. Skeleton only, no UE skin/IK."
    draw.text((20, 678), condition_text,
              fill="#a9b8c9", font=small)
    return image


def render_video(output, source, generated, root, contacts, skeleton, category, asset,
                 fps, generated_label="MOTIONWEAVER", condition_text=None,
                 source_label="SOURCE", root_description="Exported Actor Root"):
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required to render MotionWeaver preview videos")
    bones = skeleton["bones"]
    names = [bone["name"] for bone in bones]
    edges = []
    for index, bone in enumerate(bones):
        if bone["name"] not in KEEP_BONES:
            continue
        parent = bone["parent"]
        while parent >= 0 and bones[parent]["name"] not in KEEP_BONES:
            parent = bones[parent]["parent"]
        if parent >= 0:
            edges.append((parent, index))
    roles = skeleton["roles"]
    feet = [names.index(roles["left_foot"]), names.index(roles["right_foot"])]
    poster = output.with_suffix(".png")
    draw_frame(len(source) // 2, source, generated, root, contacts, edges, feet, category, asset,
               generated_label, condition_text, source_label, root_description).save(poster)
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo",
               "-pix_fmt", "rgb24", "-s", f"{SIZE[0]}x{SIZE[1]}", "-r", str(fps),
               "-i", "-", "-an", "-c:v", "libx264", "-crf", "22", "-pix_fmt", "yuv420p",
               "-movflags", "+faststart", str(output)]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    assert process.stdin is not None
    try:
        for frame in range(len(source)):
            process.stdin.write(draw_frame(frame, source, generated, root, contacts,
                                           edges, feet, category, asset,
                                           generated_label, condition_text,
                                           source_label, root_description).tobytes())
    finally:
        process.stdin.close()
    if process.wait() != 0:
        raise RuntimeError(f"ffmpeg failed while rendering {output}")
    return poster


@torch.no_grad()
def evaluate_clip(model, dataset_dir, clip, frames, device, skeleton, condition_mode="actor",
                  debug_decoder_pose_root_oracle=False, timing_repeats=3):
    path = dataset_dir / clip["file"]
    motion = np.load(path, mmap_mode="r", allow_pickle=False)
    start = (clip["source_frames"] - frames) // 2
    source_global = np.asarray(motion[start:start + frames], dtype=np.float32).copy()
    if source_global.shape != (frames, len(model.global_motion_rep.indices["all"])):
        raise ValueError(f"Motion feature shape differs from checkpoint: {path}")
    source_unscaled, source_pose_root, pose_slots, pose_mask = build_conditions(
        model, torch.from_numpy(source_global)[None], frames, device,
    )
    with np.load(dataset_dir / clip["raw_file"], allow_pickle=False) as raw:
        raw_positions = raw["positions"]
        raw_rotations = raw["rotations"]
        actor_root = raw["root_track"][start:start + frames].copy()
    config = {"pose_token_sampling_use_argmax": True, "num_inference_step": 1}
    if condition_mode == "actor":
        actor_tensor = torch.from_numpy(actor_root)[None].to(device)
        (predicted, tokens), forward_samples_ms = timed_predict(
            lambda: model.predict_with_actor_root(
                actor_tensor, source_pose_root[:, :4], pose_slots[:, :4],
                config=config,
                debug_decoder_pose_root=(source_pose_root if debug_decoder_pose_root_oracle else None),
                allow_offline_oracle=debug_decoder_pose_root_oracle,
            ), device, timing_repeats,
        )
    elif condition_mode == "pose-root-oracle":
        (predicted, tokens), forward_samples_ms = timed_predict(
            lambda: model.predict_with_pose_root(
                source_pose_root, pose_slots, pose_mask, config=config,
            ), device, timing_repeats,
        )
    else:
        raise ValueError(f"Unknown condition mode: {condition_mode}")
    if predicted.shape != source_unscaled.shape or not torch.isfinite(predicted).all():
        raise ValueError("MotionWeaver returned invalid global motion features")
    global_rep = model.global_motion_rep
    # Preprocessing canonicalizes the body against the asset's first frame.
    # Restore that transform before composing with the separate UE Actor Root.
    _, initial = model.motion_rep(
        {"posed_joints": torch.from_numpy(raw_positions[:4])[None].to(device),
         "global_joint_rots": torch.from_numpy(raw_rotations[:4])[None].to(device)},
        to_normalize=False, lengths=torch.tensor([4], device=device),
        return_init_heading_info=True,
    )
    source_relative = global_rep.inverse(
        source_unscaled, is_normalized=False, init_heading_info=dict(initial),
    )["posed_joints"][0].detach().cpu().numpy()
    generated_relative = global_rep.inverse(
        predicted, is_normalized=False, init_heading_info=dict(initial),
    )["posed_joints"][0].detach().cpu().numpy()
    source_raw_error = float(np.sqrt(np.mean(np.square(
        source_relative - raw_positions[start:start + frames],
    ))))
    if source_raw_error > 0.03:
        raise ValueError(f"Feature inverse differs from raw root-relative source by {source_raw_error:.3f} m")
    source_world = apply_authoritative_root(raw_positions[start:start + frames], actor_root)
    generated_world = apply_authoritative_root(generated_relative, actor_root)
    source_contacts = global_rep.extract_foot_contacts(source_unscaled, is_normalized=False,
                                                        contact_thresh=None)[0]
    root_indices = global_rep.indices["root"]
    source_pose_root_np = source_pose_root[0].detach().cpu().numpy()
    generated_root_np = predicted[0, :, root_indices].detach().cpu().numpy()
    source_np = source_world.astype(np.float32)
    generated_np = generated_world.astype(np.float32)
    contacts_np = source_contacts.detach().cpu().numpy()
    names = [bone["name"] for bone in skeleton["bones"]]
    feet = [names.index(skeleton["roles"][key]) for key in ("left_foot", "right_foot")]
    hips = [names.index(skeleton["roles"][key]) for key in ("left_hip", "right_hip")]
    scores = metric_summary(source_np, generated_np, actor_root, source_pose_root_np,
                            generated_root_np, contacts_np, feet, hips,
                            model.motion_rep.fps)
    scores["feature_inverse_vs_raw_relative_rmse_m"] = source_raw_error
    return {"source": source_np, "generated": generated_np,
            "source_pose_root": source_pose_root_np,
            "generated_pose_root": generated_root_np, "actor_root": actor_root,
            "source_contacts": contacts_np, "tokens": tokens[0].detach().cpu().numpy(),
            "scores": scores, "start": start,
            "forward_samples_ms": forward_samples_ms}


def run(args):
    from inference.runtime.motionweaver import load_motionweaver_pose

    dataset_dir = Path(args.dataset).resolve()
    output = Path(args.output).resolve()
    if output.exists():
        raise FileExistsError(output)
    manifest_path = dataset_dir / "dataset.json"
    skeleton_path = dataset_dir / "skeleton.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    skeleton = json.loads(skeleton_path.read_text(encoding="utf-8"))
    if (manifest.get("training_contract") != "pose_only_root_authoritative"
            or skeleton.get("root_policy") != "separate_authoritative"):
        raise ValueError("Preview requires the UE authoritative Root dataset contract")
    if args.frames < 24 or args.frames % 4:
        raise ValueError("--frames must be a multiple of four and at least 24")
    if args.debug_decoder_pose_root_oracle and args.condition_mode != "actor":
        raise ValueError("Decoder pose-root oracle ablation requires --condition-mode actor")
    categories = tuple(args.categories)
    # Match the old 24-history + 24-future baseline clip selection when using
    # the 28-frame common comparison window.
    indices = parse_clip_indices(args.clip_index)
    if set(indices) - set(categories):
        raise ValueError("--clip-index category must also appear in --categories")
    selected = select_clips(manifest, categories,
                            max(args.frames, 72) if args.frames == 28 else args.frames,
                            indices=indices)
    model = load_motionweaver_pose(
        args.pose_checkpoint, vqvae_checkpoint=args.vqvae_checkpoint,
        dataset_dir=dataset_dir, device=args.device,
    )
    pose_state = torch.load(args.pose_checkpoint, map_location="cpu", weights_only=False)
    training_steps = pose_state.get("global_step")
    if args.frames // 4 > model._args["max_tokens"]:
        raise ValueError("--frames exceeds the checkpoint's max_tokens")
    output.mkdir(parents=True)
    report = {
        "schema_version": 1,
        "scope": "offline UE skeleton diagnostic; not CMC/Mover control, UE skinning or gameplay",
        "condition_mode": args.condition_mode,
        "decoder_pose_root_oracle": args.debug_decoder_pose_root_oracle,
        "actor_root_source": "exported_ue_actor_root_offline_oracle",
        "world_composition_root_source": "same_exported_ue_actor_root",
        "input_visibility": {"historical_source_pose_frames": 4,
                             "future_source_pose_frames": 0,
                             "future_source_pose_feature_root_frames":
                                 args.frames - 4 if (args.condition_mode == "pose-root-oracle"
                                                     or args.debug_decoder_pose_root_oracle) else 0,
                             "future_exported_actor_root_frames":
                                 args.frames - 4 if args.condition_mode == "actor" else 0},
        "split": "test", "fps_source": manifest["fps"],
        "fps_playback": args.playback_fps,
        "metric_caveats": [
            "pose-root error is an exact input echo only in pose-root-oracle mode",
            "actor_path_mean_speed_mps describes the imposed Actor Root input",
            "source contact labels can yield few or zero consecutive contact pairs",
            "body facing is derived from the hip axis and compared with source facing; strafing is allowed",
            "foot velocity RMSE is the RMS length of the world-space velocity error vector",
            "decoder pose-root oracle is diagnostic leakage and excluded from causal comparisons",
        ],
        "pose_checkpoint_sha256": sha256(args.pose_checkpoint),
        "pose_checkpoint_training_steps": training_steps,
        "pose_checkpoint_has_training_steps": isinstance(training_steps, int) and training_steps > 0,
        "run_label": args.run_label,
        "vqvae_checkpoint_sha256": sha256(args.vqvae_checkpoint),
        "dataset_manifest_sha256": sha256(manifest_path),
        "skeleton_sha256": sha256(skeleton_path),
        "torch_version": torch.__version__, "clips": [],
    }
    for category, index, clip in selected:
        result = evaluate_clip(model, dataset_dir, clip, args.frames, args.device,
                               skeleton, args.condition_mode,
                               args.debug_decoder_pose_root_oracle,
                               args.timing_repeats)
        stem = f"{category.lower()}_{index:05d}"
        np.savez_compressed(output / f"{stem}.npz", source=result["source"],
                            generated=result["generated"],
                            source_pose_root=result["source_pose_root"],
                            generated_pose_root=result["generated_pose_root"],
                            actor_root=result["actor_root"],
                            source_contacts=result["source_contacts"], tokens=result["tokens"])
        entry = {"category": category, "clip_index": index, "asset": clip["asset"],
                 "source_frame_start": result["start"], "source_frames": args.frames,
                 "source_feature_file": clip["file"], "arrays": f"{stem}.npz",
                 "metrics": result["scores"],
                 "forward_samples_ms": result["forward_samples_ms"]}
        if not args.no_video:
            video = output / f"{stem}.mp4"
            poster = render_video(video, result["source"], result["generated"],
                                  result["actor_root"], result["source_contacts"], skeleton,
                                  category, clip["asset"].rsplit("/", 1)[-1].split(".", 1)[0],
                                  args.playback_fps,
                                  generated_label=(
                                      "DECODER ORACLE TEST" if args.debug_decoder_pose_root_oracle
                                      else f"MOTIONWEAVER {args.run_label}" if args.run_label
                                      else "MOTIONWEAVER" if report["pose_checkpoint_has_training_steps"]
                                      else "UNTRAINED MW SMOKE"
                                  ),
                                  condition_text=(
                                      "Diagnostic leakage: VQ decoder sees TRUE future pose Root. Skeleton only."
                                      if args.debug_decoder_pose_root_oracle else
                                      "4 history poses + exported Actor Root plan; NO future pose. Skeleton only, no UE skin/IK."
                                      if args.condition_mode == "actor" else
                                      "Diagnostic only: model sees source pose-feature Root; NO future pose. No UE skin/IK."
                                  ))
            entry.update(video=video.name, video_sha256=sha256(video),
                         poster=poster.name)
        report["clips"].append(entry)
        print(json.dumps(entry, ensure_ascii=False), flush=True)
    report["forward_timing"] = timing_summary(report["clips"], args.timing_repeats,
                                               args.device)
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)
                                        + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pose-checkpoint", type=Path, required=True)
    parser.add_argument("--vqvae-checkpoint", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=28)
    parser.add_argument("--condition-mode", choices=("actor", "pose-root-oracle"), default="actor")
    parser.add_argument("--categories", nargs="+", default=["Walk", "Run", "Crouch"])
    parser.add_argument("--clip-index", action="append", default=[], metavar="CATEGORY=INDEX")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--debug-decoder-pose-root-oracle", action="store_true")
    parser.add_argument("--playback-fps", type=int, default=15)
    parser.add_argument("--timing-repeats", type=int, default=3)
    parser.add_argument("--run-label", default="")
    parser.add_argument("--no-video", action="store_true")
    run(parser.parse_args())
