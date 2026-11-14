"""Frozen contract verification for reference-guided pose releases."""
import hashlib
import json
from pathlib import Path

from data.runtime.conditioned_motion import verify_source_manifest


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_release(release, lock, source_override=None):
    release = Path(release).resolve()
    lock = json.loads(Path(lock).read_text(encoding="utf-8"))
    if json.loads((release / "freeze_manifest.json").read_text(encoding="utf-8")) != lock:
        raise ValueError("封版清单与仓库锁不一致")
    for name, expected in lock["artifacts_sha256"].items():
        if digest(release / name) != expected:
            raise ValueError(f"封版文件哈希不匹配：{name}")
    contract = json.loads((release / "contract" / "contract.json").read_text(encoding="utf-8"))
    source = Path(
        source_override if source_override is not None else contract["source_dataset"]
    ).resolve()
    verify_source_manifest(dict(contract, source_dataset=str(source)))
    if digest(source / "skeleton.json") != contract["skeleton_sha256"]:
        raise ValueError("来源骨架已改变")
    if contract["window"]["history_frames"] != 24 or contract["window"]["future_frames"] != 24:
        raise ValueError("当前模型要求 24+24 帧契约")
    # 位置可迁移，内容仍由冻结清单、骨架与逐条 NPZ 哈希约束。
    return dict(contract, source_dataset=str(source)), lock

