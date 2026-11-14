"""将站蹲 B1 试验权重导出为无源姿态参考的固定形状 ONNX，并核对数值。"""

import argparse
import json
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
import torch
from data.runtime.unreal_dataset import read_json
from torch import nn

from data.runtime.conditioned_motion import sha256
from motionweaver.refpose import StanceGuidedPose


class StancePilotExport(nn.Module):
    """只暴露在线可见条件；goal 用 float 以避免运行时整型张量。"""

    def __init__(self, model):
        super().__init__()
        self.model = model

    def forward(self, history, root_plan, goal_crouch):
        _, state = self.model.history(history)
        batch = history.shape[0]
        reference = history.new_zeros(batch, 24, self.model.features)
        mask = history.new_zeros(batch, 24, 1)
        control = torch.cat((root_plan, reference, mask), dim=-1)
        stand = self.model.goal_stance.weight[0].reshape(1, 1, -1)
        crouch = self.model.goal_stance.weight[1].reshape(1, 1, -1)
        stance = stand + goal_crouch * (crouch - stand)
        hidden = self.model.control(control) + state[-1].unsqueeze(1) + self.model.time + stance
        return self.model.output(self.model.temporal(hidden))


def export(checkpoint_path, stance_contract_dir, output_dir):
    checkpoint_path, stance_contract_dir, output_dir = map(Path, (checkpoint_path, stance_contract_dir, output_dir))
    if output_dir.exists():
        raise FileExistsError(output_dir)
    contract = read_json(stance_contract_dir / "contract.json")
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    config = checkpoint["config"]
    if config["stance_contract_sha256"] != sha256(stance_contract_dir / "contract.json"):
        raise ValueError("B1 权重与站蹲契约不匹配")
    if contract["window"] != {"history_frames": 24, "future_frames": 24, "stride_frames": 4,
                              "history_first_frame_padding": True, "future_padding": False}:
        raise ValueError("窗口契约不受支持")
    base_dir = Path(contract["base_contract"])
    base = read_json(base_dir / "contract.json")
    if sha256(base_dir / "contract.json") != config["base_contract_sha256"]:
        raise ValueError("来源契约不匹配")
    stats = base["stats"]["pose_m"]
    mean_path, std_path = (base_dir / "stats" / stats[key] for key in ("mean", "std"))
    if sha256(mean_path) != stats["mean_sha256"] or sha256(std_path) != stats["std_sha256"]:
        raise ValueError("归一化统计量不匹配")
    skeleton_path = Path(contract["source_dataset"]) / "skeleton.json"
    if sha256(skeleton_path) != contract["skeleton_sha256"]:
        raise ValueError("骨架不匹配")
    skeleton = read_json(skeleton_path)
    model = StanceGuidedPose(contract["bone_count"], checkpoint["model"]["history.weight_ih_l0"].shape[0] // 3)
    model.load_state_dict(checkpoint["model"])
    wrapper = StancePilotExport(model.eval()).eval()
    generator = torch.Generator().manual_seed(42)
    history = torch.randn(1, 24, contract["bone_count"] * 9 + 7, generator=generator)
    root = torch.randn(1, 24, 7, generator=generator)
    root[..., 3:] = torch.nn.functional.normalize(root[..., 3:], dim=-1)
    goal = torch.randint(0, 2, (1, 24, 1), generator=generator).float()
    with torch.no_grad():
        expected = wrapper(history, root, goal).numpy()
        baseline = model(history, root, torch.zeros(1, 24, model.features),
                         torch.zeros(1, 24, 1), goal.squeeze(-1).long()).numpy()
    wrapper_difference = float(np.max(np.abs(expected - baseline)))
    if wrapper_difference > 1e-5:
        raise ValueError(f"在线输入包装与原 B1 前向不一致：max_abs={wrapper_difference}")
    output_dir.mkdir(parents=True)
    onnx_path = output_dir / "model.onnx"
    torch.onnx.export(wrapper, (history, root, goal), str(onnx_path), opset_version=17,
                      input_names=["history", "future_root_local", "goal_crouch"],
                      output_names=["future_pose_normalized"], do_constant_folding=True, dynamo=False)
    onnx.checker.check_model(str(onnx_path))
    session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    actual = session.run(None, {"history": history.numpy(), "future_root_local": root.numpy(),
                                "goal_crouch": goal.numpy()})[0]
    if not isinstance(actual, np.ndarray):
        raise TypeError("ONNX pose output must be a dense array")
    maximum = float(np.max(np.abs(actual - expected)))
    if actual.shape != (1, 24, contract["bone_count"] * 9) or maximum > 0.01:
        raise ValueError(f"ONNX 数值或形状不匹配：{actual.shape}, max_abs={maximum}")
    pose_mean = np.load(mean_path).astype(np.float32)
    pose_std = np.load(std_path).astype(np.float32)
    np.save(output_dir / "pose_mean_m.npy", pose_mean)
    np.save(output_dir / "pose_std_m.npy", pose_std)
    manifest = {"format": "motionweaver_stance_pilot_onnx_v1", "checkpoint_sha256": sha256(checkpoint_path),
                "stance_contract_sha256": sha256(stance_contract_dir / "contract.json"),
                "base_contract_sha256": config["base_contract_sha256"], "onnx_sha256": sha256(onnx_path),
                "skeleton_sha256": contract["skeleton_sha256"], "fps": contract["fps"],
                "history_frames": 24, "future_frames": 24, "bone_count": contract["bone_count"],
                "array_axes_from_ue": base["array_axes_from_ue"], "root_bone": skeleton["root_bone"],
                "bones": skeleton["bones"], "pose_mean_m": pose_mean.tolist(), "pose_std_m": pose_std.tolist(),
                "pose_mean_sha256": sha256(output_dir / "pose_mean_m.npy"),
                "pose_std_sha256": sha256(output_dir / "pose_std_m.npy"),
                "maximum_cpu_abs_difference": maximum, "maximum_wrapper_abs_difference": wrapper_difference,
                "training_root": "source_future_oracle", "training_stance": "inferred_goal_proxy_from_source_animation"}
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--stance-contract", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    torch.set_num_threads(4)
    print(json.dumps(export(args.checkpoint, args.stance_contract, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
