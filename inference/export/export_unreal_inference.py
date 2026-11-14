"""导出无损 FP32 推理包：删除训练状态，保留完整码本、骨架、统计量与重建配置。"""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

import torch
from hydra.utils import instantiate
from motionweaver.legacy import canonical_config
from data.runtime.unreal_dataset import file_sha256, training_signature
from motionweaver.representation.helper.pl_util import load_motion_rep
from omegaconf import DictConfig, OmegaConf, open_dict

from inference.runtime.checkpoint import load_vqvae


def load_package(folder):
    folder = Path(folder).resolve()
    if (folder / "manifest.json").exists():
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf8"))
        if training_signature(folder) != manifest.get("signature"):
            raise ValueError("推理包骨架或统计量校验失败")
        if manifest.get("weights_sha256"):
            if file_sha256(folder / "weights.pt") != manifest["weights_sha256"]:
                raise ValueError("推理权重校验失败，文件已改变")
    conf = canonical_config(OmegaConf.load(folder / "config.yaml"))
    if not isinstance(conf, DictConfig):
        raise ValueError("Configuration must be a mapping")
    with open_dict(conf):
        conf.data.folder = str(folder)
        conf.skeleton.folder = str(folder)
        conf.motion_rep.stats.folder = str(folder / "stats")
    rep = load_motion_rep(conf)
    net = instantiate(conf.model.pose_vqvae_network, motion_rep=rep.dual_rep.local_motion_rep)
    if conf.get("inference_mode") == "decoder_only":
        del net.encoder
    net.load_state_dict(torch.load(folder / "weights.pt", map_location="cpu", weights_only=True), strict=True)
    return net.eval(), rep


def export(checkpoint, output, decoder_only=False):
    checkpoint, output = Path(checkpoint).resolve(), Path(output).resolve()
    net, _rep, contract = load_vqvae(checkpoint)
    output.mkdir(parents=True, exist_ok=False)
    conf = OmegaConf.create(contract["config"])
    source = Path(conf.data.folder)
    shutil.copy2(source / "skeleton.json", output / "skeleton.json")
    shutil.copytree(source / "stats", output / "stats")
    with open_dict(conf):
        conf.data.folder = "."
        conf.skeleton.folder = "."
        conf.motion_rep.stats.folder = "stats"
        conf.inference_mode = "decoder_only" if decoder_only else "encoder_decoder"
    if decoder_only:
        del net.encoder
    OmegaConf.save(conf, output / "config.yaml")
    torch.save(net.state_dict(), output / "weights.pt")
    restored, _ = load_package(output)
    for key, value in net.state_dict().items():
        if not torch.equal(value, restored.state_dict()[key]):
            raise ValueError(f"推理包权重不一致：{key}")
    codebook_digest = hashlib.sha256()
    for key, value in sorted(net.quantizer.state_dict().items()):
        codebook_digest.update(key.encode("utf8"))
        codebook_digest.update(value.cpu().contiguous().numpy().tobytes())
    weights_digest = file_sha256(output / "weights.pt")
    report = {"format": "PyTorch FP32 inference state, portable skeleton and statistics", "checkpoint_bytes": checkpoint.stat().st_size,
              "package_bytes": sum(p.stat().st_size for p in output.rglob("*") if p.is_file()),
              "parameters": sum(p.numel() for p in net.parameters()), "state_bitwise_equal": True, "inference_mode": conf.inference_mode,
              "weights_sha256": weights_digest, "token_codebook_sha256": codebook_digest.hexdigest(),
              "signature": contract["signature"], "training_contract": contract.get("training_contract", "legacy_root_in_pose"),
              "limitations": "不含优化器，不可恢复训练；不是 ONNX/TensorRT 或 UE 部署格式。decoder_only 仅接受兼容码本的 token，不能独立编码动作或从文字生成动作。"}
    (output / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--decoder_only", action="store_true", help="只保留码本和解码器，面向已经提供兼容 token 的运行时")
    args = parser.parse_args(argv)
    print(json.dumps(export(args.checkpoint, args.output, args.decoder_only), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
