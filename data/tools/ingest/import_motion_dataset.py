"""独立导入与标准化入口；原始数据、处理结果和报告分区，骨架按签名共享。"""

import argparse
import hashlib
import json
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from data.runtime.importers.bvh import read_bvh_motion
from data.runtime.importers.gltf import read_gltf_motion
from data.runtime.native_motion_io import (
    read_native_motion,
    write_native_motion,
    write_sampled_motion,
)
from data.runtime.normalize_motion import normalize_motion


def load_source(item: dict, extracted: Path | None = None, blender: str | None = None):
    kind = item["format"]
    if kind in ("ue", "native"):
        return read_native_motion(extracted if extracted is not None else item["path"])
    if kind == "bvh":
        return read_bvh_motion(item["path"], item["profile"])
    if kind in ("gltf", "glb"):
        return read_gltf_motion(item["path"], item["animation_id"], item.get("skin_index", 0))
    if kind == "fbx":
        from data.runtime.importers.fbx import read_fbx_motion
        if blender is None:
            raise ValueError("FBX 需要显式传入 --blender 可执行文件")
        return read_fbx_motion(item["path"], item.get("animation_id"), blender)
    raise ValueError(f"不支持的来源格式：{kind}")


def process_clip(item, raw_folder, processed_folder, fps, blender=None):
    native = load_source(item, raw_folder if item["format"] == "ue" else None, blender)
    if item["format"] not in ("ue", "native"):
        write_native_motion(raw_folder, native, raw_folder.parent.parent / "skeletons")
    sampled = normalize_motion(native, fps)
    write_sampled_motion(processed_folder, sampled, processed_folder.parent.parent / "skeletons")
    actual_raw = Path(item["path"]).resolve() if item["format"] == "native" else raw_folder
    def hashes(folder):
        return {name: hashlib.sha256((folder / name).read_bytes()).hexdigest() for name in ("motion.json", "arrays.bin")}
    return {"status": "normalized", "source_id": native.source.source_id, "joints": len(native.skeleton.names),
            "source_duration": native.duration, "frames": len(sampled.timestamps),
            "last_sample_seconds": float(sampled.timestamps[-1]),
            "raw": str(actual_raw), "processed": str(processed_folder),
            "raw_sha256": hashes(actual_raw), "processed_sha256": hashes(processed_folder)}


def run_batch(manifest: dict, data_root, run_id: str, fps=30, extractor=None, blender=None):
    dataset = manifest["dataset"]
    for name in (dataset, run_id):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", name):
            raise ValueError("dataset 与 run_id 只能使用字母、数字、下划线和连字符")
    items = manifest["clips"]
    if not items:
        raise ValueError("没有选中的动画")
    root = Path(data_root).resolve()
    raw = root / "raw" / dataset / run_id
    processed = root / "processed" / dataset / run_id
    reports = root / "reports" / dataset / run_id
    if raw.exists() or processed.exists():
        raise FileExistsError("数据版本已存在，请使用新的 run_id")
    reports.mkdir(parents=True, exist_ok=False)
    (reports / "input-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    identifiers = [hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()[:24] for item in items]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("任务清单包含重复条目")
    started = time.monotonic()
    extraction = {}
    ue_assets = [{"asset": item["asset"], "output": str(raw / "clips" / identity), "source_id": item.get("source_id", item["asset"])}
                 for identity, item in zip(identifiers, items) if item["format"] == "ue"]
    if ue_assets:
        if extractor is None:
            raise ValueError("UE 输入需要显式传入 --extractor 已编译的 .dll 或 .exe")
        request = dict(manifest["ue"], assets=ue_assets, skeleton_directory=str(raw / "skeletons"))
        request_path = reports / "extract-request.json"
        request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
        command = (["dotnet", str(extractor)] if str(extractor).lower().endswith(".dll") else [str(extractor)])
        with (reports / "extract.log").open("w", encoding="utf-8") as log:
            result = subprocess.run(command + ["--request", str(request_path)], check=False, stdout=log, stderr=subprocess.STDOUT)
        if result.returncode not in (0, 2):
            raise RuntimeError(f"CUE4Parse 提取器启动或来源挂载失败，详见 {reports / 'extract.log'}")
        extraction = {entry["asset"]: entry for entry in json.loads(Path(str(request_path) + ".results.json").read_text(encoding="utf-8"))}
    results = []
    for identity, item in zip(identifiers, items):
        try:
            if item["format"] == "ue" and extraction[item["asset"]]["status"] != "extracted":
                entry = extraction[item["asset"]]
                record = {"status": entry["status"], "error": entry["error"], "raw": entry.get("output")}
            else:
                record = process_clip(item, raw / "clips" / identity, processed / "clips" / identity, fps, blender)
        except (OSError, ValueError, KeyError) as error:
            record = {"status": "failed", "error": str(error),
                      "raw": str(Path(item["path"]).resolve()) if item["format"] == "native" else str(raw / "clips" / identity)}
        results.append(dict(record, id=identity, input=item))
        with (reports / "clips.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(results[-1], ensure_ascii=False, allow_nan=False) + "\n")
    elapsed = time.monotonic() - started
    report = {"schema_version": 1, "dataset": dataset, "run_id": run_id, "scope": "native_import_and_normalization_no_retarget",
              "fps": fps, "selected": len(items), "normalized": sum(r["status"] == "normalized" for r in results),
              "failed": sum(r["status"] == "failed" for r in results), "seconds": elapsed,
              "unsupported": sum(r["status"] == "unsupported" for r in results),
              "raw": str(raw) if any(item["format"] != "native" for item in items) else None,
              "processed": str(processed), "results": results}
    (reports / "run.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return report


def project_manifest(project, limit=None):
    project = Path(project).resolve()
    descriptors = list(project.glob("*.uproject"))
    if len(descriptors) != 1:
        raise ValueError("UE 项目根目录需要恰好一个 .uproject")
    # 不依赖文件名或目录名推断动画类型，交给 CUE4Parse 实际解析。
    candidates = sorted((project / "Content").rglob("*.uasset"))
    files = candidates
    if limit is not None:
        if limit < 1:
            raise ValueError("limit 必须为正")
        files = files[:limit]
    return {"dataset": project.name.lower(), "scan": {"policy": "all_project_content_uassets", "candidates": len(candidates), "limit": limit},
            "ue": {"source_root": str(project), "engine": "GAME_UE5_8"},
            "clips": [{"format": "ue", "asset": descriptors[0].stem + "/" + p.relative_to(project).as_posix()[:-7] + "." + p.stem}
                      for p in files]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--manifest", type=Path)
    inputs.add_argument("--ue-project", type=Path, help="扫描项目 Content 的全部资产，由提取器判断 AnimSequence 类型")
    parser.add_argument("--data-root", type=Path, default=Path("E:/MotionWeaverData"))
    parser.add_argument("--run-id", default=datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ"))
    parser.add_argument("--fps", type=float, default=30)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--engine", default="GAME_UE5_8")
    parser.add_argument("--extractor", type=Path)
    parser.add_argument("--blender")
    args = parser.parse_args()
    manifest = project_manifest(args.ue_project, args.limit) if args.ue_project else json.loads(args.manifest.read_text(encoding="utf-8"))
    if args.ue_project:
        manifest["ue"]["engine"] = args.engine
    report = run_batch(manifest, args.data_root, args.run_id, args.fps, args.extractor, args.blender)
    print(json.dumps({key: report[key] for key in ("dataset", "run_id", "selected", "normalized", "unsupported", "failed", "seconds", "raw", "processed")}, ensure_ascii=False))
    return 2 if report["failed"] or report["unsupported"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
