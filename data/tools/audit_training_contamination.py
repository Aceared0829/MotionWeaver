"""扫描训练视图，检查是否有引擎姿势资产混进训练集。

被拒绝的 168 条里有大量 PoseAssets/AimOffset/CurveContainer 之类的引擎资产，
它们靠「帧数不足」被动过滤。但如果某个姿势资产恰好够长，就会漏进训练集。
这里按路径模式主动核查，输出可疑清单。

用法：
    python -m data.tools.audit_training_contamination --dataset <训练视图>
"""

import argparse
import json
import re
from collections import Counter
from pathlib import Path

# 这些是 UE 里「可叠加的姿势/数据资产」，从不作为完整动作单独播放。
CONTAMINATION_PATTERNS = (
    ("pose_asset", re.compile(r"/PoseAssets?/|_AnimSeq\b", re.IGNORECASE)),
    ("aim_offset", re.compile(r"/AimOffset/|_AO_", re.IGNORECASE)),
    ("curve_container", re.compile(r"CurveContainer", re.IGNORECASE)),
    ("ragdoll", re.compile(r"/Ragdoll/|ragdoll_poses", re.IGNORECASE)),
    ("montage", re.compile(r"/Montages?/|_Montage\b", re.IGNORECASE)),
    ("blend_space", re.compile(r"BlendSpace", re.IGNORECASE)),
)

# 这些是「确实是动作」的关键词，用于在可疑时判定是否放行。
MOTION_HINTS = re.compile(
    r"walk|run|jog|sprint|idle|jump|fall|land|crouch|turn|strafe|climb|"
    r"dance|wave|attack|hit|death|roll|slide|swim|sit|stand|lean",
    re.IGNORECASE,
)


def classify_asset(asset):
    """返回命中的污染类别列表；空列表表示未命中任何危险模式。"""
    hits = [name for name, pattern in CONTAMINATION_PATTERNS if pattern.search(asset)]
    return hits


def audit(dataset_root):
    dataset_root = Path(dataset_root).resolve()
    manifest = json.loads((dataset_root / "dataset.json").read_text(encoding="utf-8"))
    clips = manifest["clips"]

    suspects, clean = [], 0
    by_category = Counter()
    for clip in clips:
        asset = clip.get("asset", "")
        hits = classify_asset(asset)
        if not hits:
            clean += 1
            continue
        for hit in hits:
            by_category[hit] += 1
        suspects.append({
            "clip_id": clip.get("clip_id"),
            "asset": asset,
            "scene": clip.get("scene"),
            "frames": clip.get("source_frames"),
            "fps": clip.get("fps"),
            "hits": hits,
            "has_motion_keyword": bool(MOTION_HINTS.search(asset)),
            "motion_keyword": (MOTION_HINTS.search(asset).group(0)
                               if MOTION_HINTS.search(asset) else None),
        })

    # 有动作关键词的可疑条目更可能是误伤（如 /Poses/ 目录下真有动画），单列出来复核
    likely_bad = [s for s in suspects if not s["has_motion_keyword"]]
    likely_ok = [s for s in suspects if s["has_motion_keyword"]]

    return {
        "dataset": str(dataset_root),
        "total_clips": len(clips),
        "clean": clean,
        "suspect_total": len(suspects),
        "suspect_ratio": round(len(suspects) / max(len(clips), 1) * 100, 3),
        "by_category": dict(by_category),
        "likely_bad": len(likely_bad),
        "likely_ok_needs_review": len(likely_ok),
        "samples_likely_bad": likely_bad[:20],
        "samples_likely_ok": likely_ok[:10],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--dump", help="把完整可疑清单写到这个 JSON 文件")
    args = parser.parse_args()
    result = audit(args.dataset)
    if args.dump:
        Path(args.dump).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        result["dumped_to"] = args.dump
        result.pop("samples_likely_bad", None)
        result.pop("samples_likely_ok", None)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
