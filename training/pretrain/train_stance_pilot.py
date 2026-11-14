"""用少量显式站蹲过渡构建状态条件微调试验；不作为可发布的玩家模型。"""

import argparse
import json
import math
import random
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset

from data.runtime.conditioned_motion import sha256
from data.runtime.stance_motion import load_stance_window
from data.runtime.unreal_dataset import read_json
from motionweaver.refpose import StanceGuidedPose, losses


class StanceWindows(Dataset):
    def __init__(self, folder, contract, base, rows, mean, std, base_folder, source_folder):
        self.folder, self.contract, self.base = Path(folder), contract, base
        self.rows, self.mean, self.std = rows, mean, std
        self.base_folder, self.source_folder = base_folder, source_folder

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, index):
        sample = load_stance_window(self.folder, self.rows[index], contract=self.contract,
                                    base_override=self.base_folder, source_override=self.source_folder)
        visible, target = sample["input"], sample["target"]
        def pack(positions, rotations):
            pose = (positions - self.mean) / self.std
            sixd = rotations[..., :2].reshape(*rotations.shape[:2], 6)
            return np.concatenate((pose, sixd), axis=-1).reshape(len(positions), -1).astype(np.float32)
        history = np.concatenate((pack(visible["history_pose"], visible["history_rotations"]),
                                  visible["history_root_local"]), axis=-1)
        future = pack(target["future_pose"], target["future_rotations"])
        return (torch.from_numpy(history), torch.from_numpy(visible["future_root_plan_local"]),
                torch.from_numpy(visible["goal_stance_proxy"]), torch.from_numpy(future),
                torch.tensor(self.rows[index]["future_contains_transition"], dtype=torch.bool))


@torch.no_grad()
def validate(model, loader, device, pose_std):
    model.eval()
    errors, transition_errors = [], []
    for history, root, goal, target, transition in loader:
        history, root, goal, target = (part.to(device) for part in (history, root, goal, target))
        reference = torch.zeros_like(target)
        mask = torch.zeros(len(history), 24, 1, device=device)
        with torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=device.type == "cuda"):
            prediction = model(history, root, reference, mask, goal)
        delta = (prediction.float().reshape(len(history), 24, -1, 9)[..., :3]
                 - target.reshape(len(history), 24, -1, 9)[..., :3]) * pose_std
        per_window = torch.sqrt(delta.square().sum(-1).mean((1, 2))).cpu().numpy() * 100
        errors.extend(per_window.tolist())
        transition_errors.extend(per_window[transition.numpy()].tolist())
    return {"all_windows_joint_rmse_cm": float(np.mean(errors)),
            "transition_windows_joint_rmse_cm": float(np.mean(transition_errors)) if transition_errors else None,
            "windows": len(errors), "transition_windows": len(transition_errors)}


def train(args):
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    folder, output = Path(args.stance_contract).resolve(), Path(args.output).resolve()
    contract = read_json(folder / "contract.json")
    base_folder = Path(args.base_contract_override if args.base_contract_override else contract["base_contract"])
    base = read_json(base_folder / "contract.json")
    if sha256(base_folder / "contract.json") != contract["base_contract_sha256"]:
        raise ValueError("v2 来源契约已改变")
    source_folder = Path(args.source_override if args.source_override else contract["source_dataset"])
    if (sha256(source_folder / "dataset.json") != contract["source_manifest_sha256"]
            or sha256(source_folder / "skeleton.json") != contract["skeleton_sha256"]):
        raise ValueError("来源动作清单或骨架哈希不匹配")
    if any(item["status"] != "verified" for item in contract["annotations"]) and not args.allow_provisional:
        raise ValueError("存在 provisional 站蹲边界；探索性训练须显式提供 --allow-provisional")
    if output.exists():
        raise FileExistsError(f"训练输出已存在：{output}")
    if not torch.cuda.is_available():
        raise RuntimeError("本轮云端训练要求 CUDA GPU")
    if args.validation_clip not in {item["clip_index"] for item in contract["annotations"]}:
        raise ValueError("验证片段不在标注集")
    if base["clips"][args.validation_clip]["split"] != "train":
        raise ValueError("验证片段必须从来源训练分区留出，不读取冻结测试片段")
    rows = [json.loads(line) for line in (folder / "windows.jsonl").read_text(encoding="utf-8").splitlines()]
    training_rows = [row for row in rows if base["clips"][row["clip_index"]]["split"] == "train"
                     and row["clip_index"] != args.validation_clip]
    validation_rows = [row for row in rows if row["clip_index"] == args.validation_clip]
    if not training_rows or not validation_rows:
        raise ValueError("训练或验证分区为空")
    stats = base["stats"]["pose_m"]
    mean_path, std_path = (base_folder / "stats" / stats[key] for key in ("mean", "std"))
    if sha256(mean_path) != stats["mean_sha256"] or sha256(std_path) != stats["std_sha256"]:
        raise ValueError("姿态统计量哈希不匹配")
    mean, std = np.load(mean_path), np.load(std_path)
    train_data = StanceWindows(folder, contract, base, training_rows, mean, std, base_folder, source_folder)
    valid_data = StanceWindows(folder, contract, base, validation_rows, mean, std, base_folder, source_folder)
    generator = torch.Generator().manual_seed(args.seed)
    train_loader = DataLoader(train_data, batch_size=args.batch_size, shuffle=True, generator=generator, num_workers=0)
    valid_loader = DataLoader(valid_data, batch_size=args.batch_size, num_workers=0)
    device = torch.device("cuda")
    previous = torch.load(args.initialize_from, map_location="cpu", weights_only=False)
    if previous["config"]["contract_sha256"] != contract["base_contract_sha256"]:
        raise ValueError("初始化权重与来源 v1 契约不匹配")
    model = StanceGuidedPose(contract["bone_count"], previous["config"]["width"]).to(device)
    missing, unexpected = model.load_state_dict(previous["model"], strict=False)
    if missing != ["goal_stance.weight"] or unexpected:
        raise ValueError(f"旧权重结构与状态模型不一致：missing={missing}, unexpected={unexpected}")
    if args.dry_run:
        model.eval()
        shapes = {}
        with torch.no_grad():
            for name, loader in (("train", train_loader), ("validation", valid_loader)):
                history, root, goal, target, _ = next(iter(loader))
                history, root, goal, target = (part.to(device) for part in (history, root, goal, target))
                prediction = model(history, root, torch.zeros_like(target),
                                   torch.zeros(len(history), 24, 1, device=device), goal)
                if prediction.shape != target.shape or not torch.isfinite(prediction).all():
                    raise ValueError(f"{name} 首批模型输出维度或数值无效")
                shapes[name] = {"history": list(history.shape), "future_root": list(root.shape),
                                "target": list(target.shape), "goal": list(goal.shape)}
        return {"dry_run": True, "batch_shapes": shapes, "output_created": False}
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=0.01)
    pose_std = torch.from_numpy(std).to(device)
    output.mkdir(parents=True, exist_ok=False)
    config = {"stance_contract_sha256": sha256(folder / "contract.json"), "base_contract_sha256": contract["base_contract_sha256"],
              "initialize_checkpoint_sha256": sha256(args.initialize_from), "model": "StanceGuidedPose",
              "seed": args.seed, "epochs": args.epochs, "batch_size": args.batch_size, "learning_rate": args.learning_rate,
              "validation_clip": args.validation_clip, "train_windows": len(train_data), "validation_windows": len(valid_data),
              "train_clip_indices": sorted({row["clip_index"] for row in training_rows}),
              "root_condition": "source_future_oracle", "stance_condition": "inferred_goal_proxy_from_source_animation",
              "annotation_statuses": sorted({item["status"] for item in contract["annotations"]}),
              "history_valid_mask_used": False, "references": "none", "test_split_used_for_selection": False}
    (output / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    best = math.inf
    for epoch in range(1, args.epochs + 1):
        model.train()
        running = 0.0
        for history, root, goal, target, _ in train_loader:
            history, root, goal, target = (part.to(device) for part in (history, root, goal, target))
            reference = torch.zeros_like(target)
            mask = torch.zeros(len(history), 24, 1, device=device)
            optimizer.zero_grad(set_to_none=True)
            with torch.autocast(device_type="cuda", dtype=torch.bfloat16):
                predicted = model(history, root, reference, mask, goal)
                loss, _, _ = losses(predicted.float(), target, mask, pose_std)
            if not torch.isfinite(loss):
                raise ValueError(f"非有限损失：epoch={epoch}")
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            running += float(loss.item()) * len(history)
        metrics = validate(model, valid_loader, device, pose_std)
        metrics.update({"epoch": epoch, "train_loss": running / len(train_data)})
        with (output / "metrics.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(metrics, ensure_ascii=False) + "\n")
        score = metrics["transition_windows_joint_rmse_cm"]
        print(json.dumps(metrics, ensure_ascii=False), flush=True)
        if score is not None and score < best:
            best = score
            torch.save({"model": model.state_dict(), "config": config, "epoch": epoch,
                        "validation": metrics}, output / "best.pt")
    return {"best_validation_transition_joint_rmse_cm": best, "output": str(output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stance-contract", required=True)
    parser.add_argument("--base-contract-override", help="云端映射后的 v1 契约目录")
    parser.add_argument("--source-override", help="云端映射后的源动作目录")
    parser.add_argument("--initialize-from", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--validation-clip", type=int, default=429)
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=5e-5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--allow-provisional", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="只核对哈希、取窗与模型首批前向，不训练或写输出")
    args = parser.parse_args()
    torch.set_num_threads(4)
    print(json.dumps(train(args), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
