# MotionWeaver local acceptance probes

> 状态：实验仪器，冻结新增。边界见 [STATUS.md](STATUS.md)。

These scripts render the prepared 79-bone UE skeleton in world space. They do
not exercise a skinned UE character, live CMC prediction, IK, or network play.
Each generated clip uses four historical source poses and scores only the next
24 frames. The old direct model uses 24 historical poses, so model comparisons
have different history lengths.

## Inputs and coordinate contract

- The default same-trajectory test uses the held-out Walk 1403, Run 820, and
  Crouch 145 clips. Future independent Actor Root transforms come from their
  UE exports. The model receives no future source pose or pose-feature Root.
- The CMC probe uses the preserved
  `docs/experiments/assets/2026-09-28-motionweaver-a800-actor-root/cmc_root_capture_20260925_v2.csv`,
  frames 44–71.
  The first four Actor transforms remain the source history. Future transforms
  come from the separate CMC recording, rigidly aligned to history frame 3.
  CMC capsule center minus capsule half height supplies a ground-level Actor
  origin before UE cm/Z-up to Motion m/Y-up conversion. Its crouch flag is
  recorded for analysis but is **not** a model input.
- The CMC video's source panel is the source pose transplanted onto the CMC
  path, a counterfactual visual aid. It is not ground truth for that path.
  World body turning includes externally applied Actor rotation. The report's
  `actor_condition_local_joint_rms_delta_cm` compares model poses before that
  composition to test whether the model reacted to the changed plan.

## Commands from the repository root

Use the checkpoint under test for `$mwPose`. The old baseline may be reused
only if the dataset manifest and skeleton hashes match; the comparison script
checks them and checks the exact source joints, Actor Root, and contact mask.

```powershell
$mwPython = '.venv_motionweaver/Scripts/python.exe'
$mwData = 'E:/AIAnimationSystemData/prepared/native30_relative_v2_worldcontacts'
$mwVq = 'D:/AILocomotonSystem/MotionWeaver/training/runs/relative_v2_vqvae_contract_20260909/checkpoints/final.ckpt'
$mwPose = 'PATH/TO/REVIEWED/final.ckpt'

& $mwPython -m inference.visualization.motionweaver_preview --pose-checkpoint $mwPose --vqvae-checkpoint $mwVq --dataset $mwData --output output/motionweaver_actor_teacher_RUN --run-label 'RUN LABEL' --device cuda
& $mwPython -m inference.visualization.cmc_controller_preview --pose-checkpoint $mwPose --vqvae-checkpoint $mwVq --dataset $mwData --cmc-capture docs/experiments/assets/2026-09-28-motionweaver-a800-actor-root/cmc_root_capture_20260925_v2.csv --output output/motionweaver_actor_cmc_RUN --run-label 'RUN LABEL' --device cuda
& $mwPython -m inference.visualization.motionweaver_preview --pose-checkpoint $mwPose --vqvae-checkpoint $mwVq --dataset $mwData --output output/motionweaver_decoder_oracle_RUN --device cuda --debug-decoder-pose-root-oracle --no-video

& $mwPython -m inference.visualization.compare_motionweaver --old output/motionweaver_old_baseline_20260928_v2 --motionweaver output/motionweaver_actor_teacher_RUN --output output/motionweaver_comparison_RUN --video
```

The decoder oracle ablation supplies the **true future pose-feature Root only
to the VQ decoder** while keeping Pose Tokens fixed. It is diagnostic leakage.
The comparison script rejects an oracle report as a causal model result.

For contact-rich held-out stops, use a separate output directory:

```powershell
& $mwPython -m inference.visualization.motionweaver_preview --pose-checkpoint $mwPose --vqvae-checkpoint $mwVq --dataset $mwData --output output/motionweaver_actor_contact_RUN --run-label 'RUN LABEL' --device cuda --no-video --clip-index Walk=1653 --clip-index Run=898 --clip-index Crouch=411
```

These are Walk Reface Stop, Run Stop, and Crouch Stop. The default Run Loop
window has no consecutive source-contact pair, and the default Crouch Loop has
only two. Always report pair counts next to contact speed. Stop samples test
planting; they do not establish steady-running foot quality.

## Metric meaning

- Joint RMSE is the RMS Euclidean distance between corresponding world joints
  over frames 4–27. It compares to the source only on the same exported Actor
  path.
- Contact foot speed is the mean world-space speed of left/right foot joints
  over *consecutive* source-contact frame pairs. Contacts are source labels;
  the metric is omitted when no pair exists. On a changed CMC path, labels
  become a heuristic, and the counterfactual source is not a valid target.
- Foot velocity RMSE is the RMS length of generated-minus-source world foot
  velocity vectors. Seam velocity error compares source and generated joint
  displacement across history frame 3 to generated frame 4.
- Facing uses the source skeleton hip axis as a reference and does not assume
  the character should face travel direction. Backward and strafe clips are
  valid.
- `forward_timing` gives synchronized Python eager model-forward P50/P95 on
  the named local device after one warmup per clip. It excludes UE skinning,
  data preparation, rendering, dispatch, and replanning integration.

Each report includes checkpoint and data hashes. The comparison output adds
three-panel Source/Old/MotionWeaver videos and frame-15 PNGs for quick visual
review. A 2,000-step run is a pilot; use the 20,000-step checkpoint for the
formal training comparison.

The completed 20,000-step acceptance result is
`output/motionweaver_20k_acceptance_summary_20260928.json`, assembled from
the default three-clip comparison, the three contact-rich stops, the recorded
CMC turn probe, and the decoder oracle diagnostic. The underlying output
directories and checkpoint hashes are preserved in those reports.
