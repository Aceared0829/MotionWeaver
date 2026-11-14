"""使用同一封版数据契约执行 Root 路径与硬参考姿态条件推理。"""

import argparse
import json
from pathlib import Path

import numpy as np
import torch

from data.runtime.conditioned_motion import load_window
from data.runtime.refpose_contract import verify_release
from data.runtime.refpose_windows import select_windows
from motionweaver.refpose import ReferenceGuidedPose


def rotation_6d_to_matrix(values):
    """6D 中两列按原矩阵行交错存储，还原成正交的列向量。"""
    first = values[..., [0, 2, 4]]
    second = values[..., [1, 3, 5]]
    norm = np.linalg.norm(first, axis=-1, keepdims=True)
    first = np.where(norm > 1e-6, first / np.maximum(norm, 1e-6), [1, 0, 0])
    second = second - np.sum(first * second, axis=-1, keepdims=True) * first
    norm = np.linalg.norm(second, axis=-1, keepdims=True)
    fallback = np.where(np.abs(first[..., :1]) < 0.9, [1, 0, 0], [0, 1, 0])
    fallback = fallback - np.sum(first * fallback, axis=-1, keepdims=True) * first
    fallback /= np.maximum(np.linalg.norm(fallback, axis=-1, keepdims=True), 1e-6)
    second = np.where(norm > 1e-6, second / np.maximum(norm, 1e-6), fallback)
    third = np.cross(first, second)
    return np.stack((first, second, third), axis=-1).astype(np.float32)


class ConditionedPosePredictor:
    def __init__(self, checkpoint_path, release, lock, source_override=None):
        self.contract, frozen = verify_release(release, lock, source_override)
        self.release = Path(release)
        checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
        self.config = checkpoint["config"]
        if self.config["contract_sha256"] != frozen["artifacts_sha256"]["contract/contract.json"]:
            raise ValueError("检查点与封版契约不匹配")
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model = ReferenceGuidedPose(self.contract["bone_count"], self.config["width"]).to(self.device)
        self.model.load_state_dict(checkpoint["model"])
        self.model.eval()
        stats = self.contract["stats"]["pose_m"]
        self.pose_mean = np.load(self.release / "contract" / "stats" / stats["mean"]).astype(np.float32)
        self.pose_std = np.load(self.release / "contract" / "stats" / stats["std"]).astype(np.float32)

    @torch.no_grad()
    def predict(self, visible):
        bones = self.contract["bone_count"]
        shapes = {"history_pose": (24, bones, 3), "history_rotations": (24, bones, 3, 3),
                  "history_root_local": (24, 7), "future_root_plan_local": (24, 7),
                  "reference_pose": (24, bones, 3), "reference_rotations": (24, bones, 3, 3),
                  "reference_frame_mask": (24,)}
        arrays = {name: np.asarray(visible[name], dtype=np.float32) for name in shapes}
        if any(arrays[name].shape != shape or not np.isfinite(arrays[name]).all()
               for name, shape in shapes.items()):
            raise ValueError("推理输入的形状或数值不符合封版契约")
        mask = arrays["reference_frame_mask"]
        if not np.isin(mask, (0, 1)).all():
            raise ValueError("参考帧遮罩必须是布尔值")
        mask = mask.reshape(24, 1).astype(np.float32)
        history_pose = (arrays["history_pose"] - self.pose_mean) / self.pose_std
        history_rotation = arrays["history_rotations"][..., :2].reshape(24, bones, 6)
        history = np.concatenate((history_pose, history_rotation), axis=-1).reshape(24, -1)
        history = np.concatenate((history, arrays["history_root_local"]), axis=-1)
        reference_pose = (arrays["reference_pose"] - self.pose_mean) / self.pose_std
        reference_rotation = arrays["reference_rotations"][..., :2].reshape(24, bones, 6)
        reference = np.concatenate((reference_pose, reference_rotation), axis=-1).reshape(24, -1) * mask
        def tensor(value):
            return torch.from_numpy(np.ascontiguousarray(value)).unsqueeze(0).to(self.device)
        with torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=self.device.type == "cuda"):
            predicted = self.model(tensor(history), tensor(arrays["future_root_plan_local"]),
                                   tensor(reference), tensor(mask))
        predicted = predicted[0].float().cpu().numpy().reshape(24, bones, 9)
        pose = predicted[..., :3] * self.pose_std + self.pose_mean
        rotation = rotation_6d_to_matrix(predicted[..., 3:])
        anchors = mask[:, 0].astype(bool)
        pose[anchors] = arrays["reference_pose"][anchors]
        rotation[anchors] = arrays["reference_rotations"][anchors]
        return {"future_pose": pose, "future_rotations": rotation,
                "future_root_plan_local": arrays["future_root_plan_local"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--lock", required=True)
    parser.add_argument("--source-override")
    parser.add_argument("--output", required=True, help="单动作预测 NPZ，必须尚不存在")
    parser.add_argument("--clip-index", type=int, help="封版测试集片段索引；默认第一条有效测试动作")
    parser.add_argument("--anchors", type=int, nargs="*", default=(12, 23))
    args = parser.parse_args()
    predictor = ConditionedPosePredictor(args.checkpoint, args.release, args.lock, args.source_override)
    rows = select_windows(Path(args.release) / "contract" / "windows.jsonl", predictor.contract, "test", 1)
    row = next((item for item in rows if args.clip_index is None or item["clip_index"] == args.clip_index), None)
    if row is None:
        parser.error("指定片段不在有效测试集中")
    sample = load_window(Path(args.release) / "contract", row, anchor_offsets=args.anchors,
                         contract=predictor.contract)
    result = predictor.predict(sample["input"])
    output = Path(args.output)
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(output, **result)
    print(json.dumps({"output": str(output.resolve()), "clip_index": row["clip_index"],
                      "start": row["start"], "anchors": args.anchors,
                      "contract_sha256": predictor.config["contract_sha256"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
