"""从封版 Root/硬参考契约训练未来 Root 相对姿态的条件生成器。

模型与窗口加载已拆到 `motionweaver.refpose` 和 `data.runtime.refpose_windows`；
本模块只保留训练循环、检查点与 CLI，并继续 re-export 旧名称供历史脚本使用。
"""

import argparse
import json
import math
import random
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, WeightedRandomSampler

from data.runtime.refpose_contract import digest, verify_release
from data.runtime.refpose_windows import (
    ClipShuffleSampler,
    MotionWindows,
    balanced_window_weights,
)
from motionweaver.refpose import ReferenceGuidedPose, losses, root_world_positions

__all__ = [
    "ClipShuffleSampler",
    "MotionWindows",
    "ReferenceGuidedPose",
    "balanced_window_weights",
    "digest",
    "losses",
    "root_world_positions",
    "verify_release",
]


def capture_random_state(loader_generator):
    return {
        "python": random.getstate(),
        "numpy": np.random.get_state(),
        "torch": torch.get_rng_state(),
        "cuda": torch.cuda.get_rng_state_all(),
        "loader": loader_generator.get_state(),
    }


def restore_random_state(state, loader_generator):
    random.setstate(state["python"])
    np.random.set_state(state["numpy"])
    torch.set_rng_state(state["torch"].cpu())
    torch.cuda.set_rng_state_all([value.cpu() for value in state["cuda"]])
    loader_generator.set_state(state["loader"].cpu())


@torch.no_grad()
def validate(
    model,
    loader,
    device,
    pose_std,
    *,
    pose_mean=None,
    foot_indices=None,
    contact_weight=0.0,
    fps=30.0,
):
    model.eval()
    totals = np.zeros(3, dtype=np.float64)
    for batch in loader:
        parts = [part.to(device, non_blocking=True) for part in batch]
        history, root, reference, mask, target = parts[:5]
        contacts = parts[5] if len(parts) > 5 else None
        with torch.autocast(
            device_type="cuda", dtype=torch.bfloat16, enabled=device.type == "cuda"
        ):
            predicted = model(history, root, reference, mask)
            loss, pos, rot = losses(
                predicted.float(),
                target,
                mask,
                pose_std,
                pose_mean=pose_mean,
                root_plan=root,
                contacts=contacts,
                foot_indices=foot_indices,
                contact_weight=contact_weight,
                fps=fps,
            )
        totals += np.array([loss.item(), pos.item(), rot.item()]) * len(history)
    return (totals / len(loader.dataset)).tolist()


def train(args):
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    random.seed(args.seed)
    contract, lock = verify_release(args.release, args.lock, args.source_override)
    output = Path(args.output).resolve()
    if output.exists():
        raise FileExistsError(f"训练输出已存在：{output}")
    if not torch.cuda.is_available():
        raise RuntimeError("本轮要求可用 CUDA GPU")
    device = torch.device("cuda")
    print(f"GPU: {torch.cuda.get_device_name(0)}", flush=True)
    use_contacts = args.contact_weight > 0
    # 接触损失里的脚速要用契约的帧率换算，不能写死 30：本仓库的 full_release_v2 是 120 fps。
    fps = float(contract.get("fps") or 30.0)
    train_data = MotionWindows(
        args.release,
        contract,
        "train",
        seed=args.seed,
        scenario=args.reference_sampling,
        include_contacts=use_contacts,
    )
    valid_data = MotionWindows(
        args.release,
        contract,
        "validation",
        seed=args.seed,
        scenario="two_refs",
        include_contacts=use_contacts,
    )
    foot_indices = None
    if use_contacts:
        skeleton = json.loads(
            (Path(contract["source_dataset"]) / "skeleton.json").read_text(encoding="utf-8")
        )
        names = [bone["name"] for bone in skeleton["bones"]]
        foot_indices = [
            names.index(skeleton["roles"][role])
            for role in ("left_foot", "left_toe", "right_foot", "right_toe")
        ]
    generator = torch.Generator().manual_seed(args.seed)
    sampler = (
        WeightedRandomSampler(
            balanced_window_weights(
                train_data, args.category_power, args.clip_power
            ).tolist(),
            len(train_data),
            replacement=True,
            generator=generator,
        )
        if args.sampling == "balanced"
        else None
    )
    train_loader = DataLoader(
        train_data,
        batch_size=args.batch_size,
        shuffle=sampler is None,
        sampler=sampler,
        generator=generator,
        num_workers=0,
        pin_memory=True,
    )
    valid_loader = DataLoader(
        valid_data,
        batch_size=args.batch_size,
        shuffle=False,
        num_workers=0,
        pin_memory=True,
    )
    model = ReferenceGuidedPose(contract["bone_count"], args.width).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    pose_std = torch.from_numpy(train_data.std).to(device)
    pose_mean = torch.from_numpy(train_data.mean).to(device)
    start_epoch = 0
    if args.resume:
        checkpoint = torch.load(args.resume, map_location=device, weights_only=False)
        previous = checkpoint["config"]
        if (
            previous["contract_sha256"] != lock["artifacts_sha256"]["contract/contract.json"]
            or previous["width"] != args.width
            or previous["batch_size"] != args.batch_size
        ):
            raise ValueError("续训检查点与封版契约或模型配置不匹配")
        for key, expected in (
            ("seed", args.seed),
            ("sampling", args.sampling),
            ("reference_sampling", args.reference_sampling),
            ("category_power", args.category_power),
            ("clip_power", args.clip_power),
            ("learning_rate", args.lr),
            ("contact_weight", args.contact_weight),
        ):
            if previous.get(
                key,
                "uniform"
                if key == "sampling"
                else "random"
                if key == "reference_sampling"
                else 0.5
                if key in ("category_power", "clip_power")
                else 0.0
                if key == "contact_weight"
                else None,
            ) != expected:
                raise ValueError(f"续训配置不一致：{key}")
        model.load_state_dict(checkpoint["model"])
        optimizer.load_state_dict(checkpoint["optimizer"])
        start_epoch = checkpoint["epoch"]
        if "random_state" in checkpoint:
            restore_random_state(checkpoint["random_state"], generator)
        else:
            print("警告：旧检查点缺少随机状态，续训无法逐步复现未中断训练", flush=True)
        if args.epochs <= start_epoch:
            raise ValueError("目标训练轮数必须大于续训检查点轮数")
    output.mkdir(parents=True, exist_ok=False)
    config = {
        "release_id": lock["release_id"],
        "contract_sha256": lock["artifacts_sha256"]["contract/contract.json"],
        "seed": args.seed,
        "batch_size": args.batch_size,
        "width": args.width,
        "epochs": args.epochs,
        "learning_rate": args.lr,
        "train_windows": len(train_data),
        "validation_windows": len(valid_data),
        "source_dataset_runtime": contract["source_dataset"],
        "model": "GRU history + temporal Transformer; Root path and sparse full-pose references",
        "rotation_representation": "first_two_matrix_columns_6d",
        "control_mode": "oracle_future_root_plan",
        "validation_references": [12, 23],
        "sampling": args.sampling,
        "reference_sampling": args.reference_sampling,
        "category_power": args.category_power,
        "clip_power": args.clip_power,
        "contact_weight": args.contact_weight,
        "contact_fps": fps if use_contacts else None,
        "logged_pose_metric": "coordinate_axis_rmse_cm_not_joint_distance_rmse",
        "resumed_from": str(Path(args.resume).resolve()) if args.resume else None,
    }
    (output / "config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    started = time.monotonic()
    for epoch in range(start_epoch, args.epochs):
        train_data.epoch = epoch
        model.train()
        totals = np.zeros(3, dtype=np.float64)
        for step, batch in enumerate(train_loader, 1):
            parts = [part.to(device, non_blocking=True) for part in batch]
            history, root, reference, mask, target = parts[:5]
            contacts = parts[5] if len(parts) > 5 else None
            optimizer.zero_grad(set_to_none=True)
            with torch.autocast(device_type="cuda", dtype=torch.bfloat16):
                predicted = model(history, root, reference, mask)
                loss, pos, rot = losses(
                    predicted.float(),
                    target,
                    mask,
                    pose_std,
                    pose_mean=pose_mean,
                    root_plan=root,
                    contacts=contacts,
                    foot_indices=foot_indices,
                    contact_weight=args.contact_weight,
                    fps=fps,
                )
            if not torch.isfinite(loss):
                raise ValueError(f"非有限损失：epoch={epoch + 1} step={step}")
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            totals += np.array([loss.item(), pos.item(), rot.item()]) * len(history)
            if step % args.log_every == 0 or step == len(train_loader):
                elapsed = time.monotonic() - started
                print(
                    f"epoch {epoch + 1}/{args.epochs} step {step}/{len(train_loader)} "
                    f"loss={totals[0] / (step * args.batch_size):.5f} "
                    f"pose_axis_rmse_cm={math.sqrt(totals[1] / (step * args.batch_size)) * 100:.2f} "
                    f"elapsed_s={elapsed:.0f}",
                    flush=True,
                )
        valid = validate(
            model,
            valid_loader,
            device,
            pose_std,
            pose_mean=pose_mean,
            foot_indices=foot_indices,
            contact_weight=args.contact_weight,
            fps=fps,
        )
        valid_data.scenario = "no_refs"
        valid_no_ref = validate(
            model,
            valid_loader,
            device,
            pose_std,
            pose_mean=pose_mean,
            foot_indices=foot_indices,
            contact_weight=args.contact_weight,
            fps=fps,
        )
        valid_data.scenario = "one_ref"
        valid_one_ref = validate(
            model,
            valid_loader,
            device,
            pose_std,
            pose_mean=pose_mean,
            foot_indices=foot_indices,
            contact_weight=args.contact_weight,
            fps=fps,
        )
        valid_data.scenario = "two_refs"
        metrics = {
            "epoch": epoch + 1,
            "train_loss": totals[0] / len(train_data),
            "train_pose_axis_rmse_cm": math.sqrt(totals[1] / len(train_data)) * 100,
            "validation_loss": valid[0],
            "validation_pose_axis_rmse_cm": math.sqrt(valid[1]) * 100,
            "validation_rotation_6d_mse": valid[2],
            "elapsed_seconds": time.monotonic() - started,
        }
        metrics["validation_no_reference_pose_axis_rmse_cm"] = (
            math.sqrt(valid_no_ref[1]) * 100
        )
        metrics["validation_one_reference_pose_axis_rmse_cm"] = (
            math.sqrt(valid_one_ref[1]) * 100
        )
        with (output / "metrics.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(metrics) + "\n")
        torch.save(
            {
                "model": model.state_dict(),
                "optimizer": optimizer.state_dict(),
                "random_state": capture_random_state(generator),
                "epoch": epoch + 1,
                "config": config,
                "metrics": metrics,
            },
            output / f"epoch_{epoch + 1:03}.pt",
        )
        print("validation " + json.dumps(metrics), flush=True)
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True)
    parser.add_argument("--lock", required=True)
    parser.add_argument(
        "--source-override",
        help="跨系统迁移后的来源数据目录；逐文件哈希仍按封版契约校验",
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--width", type=int, default=256)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--sampling", choices=("uniform", "balanced"), default="uniform")
    parser.add_argument("--category-power", type=float, default=0.5)
    parser.add_argument("--clip-power", type=float, default=0.5)
    parser.add_argument(
        "--reference-sampling", choices=("random", "diverse", "hybrid"), default="random"
    )
    parser.add_argument("--contact-weight", type=float, default=0.0)
    parser.add_argument("--resume", help="相同封版契约与模型配置的上次检查点")
    parser.add_argument("--log-every", type=int, default=100)
    args = parser.parse_args()
    if min(args.epochs, args.batch_size, args.width, args.log_every) < 1 or args.contact_weight < 0:
        parser.error("训练参数必须为正数")
    train(args)


if __name__ == "__main__":
    main()
