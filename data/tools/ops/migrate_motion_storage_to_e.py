"""将 AIAnimationSystem 的本地动作数据迁移到 E 盘，并以 Junction 保留旧路径兼容性。"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Transfer:
    name: str
    source: Path
    destination: Path
    recreate_link: bool


def is_reparse(path):
    return path.is_symlink() or bool(
        getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400
    )


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def raise_walk_error(error):
    raise error


def inventory(root):
    if is_reparse(root):
        raise ValueError(f"来源已经是链接，拒绝再次迁移：{root}")
    files = []
    for current, dirs, names in os.walk(
        root, followlinks=False, onerror=raise_walk_error
    ):
        current_path = Path(current)
        for name in dirs:
            if is_reparse(current_path / name):
                raise ValueError(f"来源含链接，拒绝遗漏链接内容：{current_path/name}")
        for name in names:
            path = current_path / name
            if is_reparse(path):
                raise ValueError(f"来源含链接，拒绝遗漏链接内容：{path}")
            stat = path.stat()
            files.append(
                {
                    "relative": str(path.relative_to(root)),
                    "bytes": stat.st_size,
                    "sha256": sha256(path),
                }
            )
    return files


def verify(destination, files):
    missing = []
    for item in files:
        path = destination / item["relative"]
        if (
            not path.is_file()
            or is_reparse(path)
            or path.stat().st_size != item["bytes"]
            or sha256(path) != item["sha256"]
        ):
            missing.append(str(path))
            if len(missing) >= 8:
                break
    if missing:
        raise RuntimeError("迁移后目标缺失、大小或哈希不符：" + ", ".join(missing))


def validate_plan(jobs, plan):
    # 来源/目标必须是明确的实体目录，不能通过 Junction 越过删除边界。
    claims = {}
    for job, entry in zip(jobs, plan):
        source = job.source.absolute()
        destination = job.destination.absolute()
        if (
            source == Path(source.anchor)
            or source == destination
            or source in destination.parents
            or destination in source.parents
        ):
            raise ValueError(f"来源与目标重叠或来源为盘根：{source} -> {destination}")
        for root in (source, destination):
            for path in (root, *root.parents):
                if (path.exists() or path.is_symlink()) and is_reparse(path):
                    raise ValueError(f"目录或上级包含链接：{path}")
        for item in entry["files"]:
            path = destination / item["relative"]
            if (
                destination not in path.absolute().parents
                or ".." in Path(item["relative"]).parts
            ):
                raise ValueError(f"文件越过目标边界：{path}")
            claim = (item["bytes"], item["sha256"])
            if path in claims and claims[path] != claim:
                raise ValueError(f"多个来源的同名文件内容冲突：{path}")
            claims[path] = claim
            for parent in (path, *path.parents):
                if (parent.exists() or parent.is_symlink()) and is_reparse(parent):
                    raise ValueError(f"目标含链接：{parent}")
            if path.exists() and (
                not path.is_file()
                or path.stat().st_size != item["bytes"]
                or sha256(path) != item["sha256"]
            ):
                raise ValueError(f"目标已有不同内容，拒绝覆盖：{path}")


def robocopy(source, destination, log):
    destination.mkdir(parents=True, exist_ok=True)
    command = [
        "robocopy",
        str(source),
        str(destination),
        "/E",
        "/COPY:DAT",
        "/DCOPY:DAT",
        "/XJ",
        "/R:2",
        "/W:2",
        "/MT:16",
        "/FFT",
        "/NP",
        "/LOG:" + str(log),
    ]
    result = subprocess.run(command, creationflags=subprocess.CREATE_NO_WINDOW, check=False)
    if result.returncode >= 8:
        raise RuntimeError(f"Robocopy 失败({result.returncode})：{log}")
    return result.returncode


def remove_empty_tree(root):
    if not root.exists() and not root.is_symlink():
        return
    for current, dirs, files in os.walk(root, topdown=False, followlinks=False):
        current_path = Path(current)
        if files:
            raise RuntimeError(f"来源根仍含文件，拒绝切换链接：{current_path}")
        for name in dirs:
            child = current_path / name
            try:
                child.rmdir()
            except OSError as exc:
                raise RuntimeError(f"来源目录未清空，拒绝切换链接：{child}") from exc
    root.rmdir()


def remove_verified_source(job, files):
    # 删除前重新核对源和目标；复制失败或复制期间源文件改变时保留来源。
    if sorted(inventory(job.source), key=lambda item: item["relative"]) != sorted(
        files, key=lambda item: item["relative"]
    ):
        raise RuntimeError(f"来源在复制后发生变化，拒绝删除：{job.source}")
    validate_plan([job], [{"files": files}])
    verify(job.destination, files)
    for item in files:
        (job.source / item["relative"]).unlink()
    remove_empty_tree(job.source)


def write_report(path, report):
    for parent in (path, *path.parents):
        if (parent.exists() or parent.is_symlink()) and is_reparse(parent):
            raise ValueError(f"报告目录含链接：{parent}")
    temporary = path.with_suffix(".tmp")
    if (temporary.exists() or temporary.is_symlink()) and is_reparse(temporary):
        raise ValueError(f"报告临时文件是链接：{temporary}")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def create_junction(link, target):
    if link.exists() or link.is_symlink():
        raise RuntimeError(f"旧路径仍存在，拒绝覆盖：{link}")
    link.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    check=False)
    if result.returncode:
        raise RuntimeError(f"创建兼容 Junction 失败：{result.stdout}{result.stderr}")
    if link.resolve() != target.resolve():
        raise RuntimeError(f"Junction 目标不匹配：{link}")


def transfers(workspace, target_root):
    bones_target = target_root / "BONES-SEED"
    return [
        Transfer("c_bones_proportional", Path("C:/BONES-SEED"), bones_target, True),
        Transfer("d_bones_remaining", Path("D:/BONES-SEED"), bones_target, True),
        Transfer(
            "motion_library",
            Path("D:/MotionDataLibrary"),
            target_root / "MotionDataLibrary",
            True,
        ),
        Transfer(
            "ue_export_cache",
            Path("D:/GameAnimationSample/Saved/AILocomotionDataset"),
            target_root / "UE/AILocomotionDataset",
            True,
        ),
        Transfer(
            "prepared_datasets",
            workspace / "data/prepared",
            target_root / "prepared",
            True,
        ),
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-root", default="E:/AIAnimationSystemData")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    workspace = Path(__file__).resolve().parents[3]
    target_root = Path(args.target_root).absolute()
    if target_root.drive.upper() != "E:":
        raise ValueError("目标必须位于 E 盘")
    if shutil.disk_usage(target_root.drive + "\\").free < 600 * 1024**3:
        raise RuntimeError("E 盘可用空间不足 600 GiB，拒绝迁移")
    jobs = transfers(workspace, target_root)
    if any(not job.source.exists() for job in jobs):
        missing = [str(job.source) for job in jobs if not job.source.exists()]
        raise FileNotFoundError("来源缺失：" + ", ".join(missing))
    if any(job.source.resolve() == job.destination.resolve() for job in jobs):
        raise ValueError("来源与目标不能相同")
    plan = []
    for job in jobs:
        files = inventory(job.source)
        plan.append(
            {
                "name": job.name,
                "source": str(job.source),
                "destination": str(job.destination),
                "files": files,
                "file_count": len(files),
                "bytes": sum(item["bytes"] for item in files),
                "recreate_link": job.recreate_link,
            }
        )
    validate_plan(jobs, plan)
    report = {
        "schema_version": 2,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "target_root": str(target_root),
        "jobs": plan,
    }
    report_path = (
        target_root / "migration" / "ai_animation_system_storage_migration.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    write_report(report_path, report)
    summary = {
        "report": str(report_path),
        "files": sum(job["file_count"] for job in plan),
        "bytes": sum(job["bytes"] for job in plan),
        "jobs": [
            {"name": job["name"], "files": job["file_count"], "bytes": job["bytes"]}
            for job in plan
        ],
    }
    if not args.execute:
        print(json.dumps(summary, ensure_ascii=False))
        return
    for job, entry in zip(jobs, plan):
        log = report_path.parent / (job.name + ".robocopy.log")
        entry["robocopy_exit_code"] = robocopy(job.source, job.destination, log)
        verify(job.destination, entry["files"])
        entry["verified"] = True
        write_report(report_path, report)
    for job, entry in zip(jobs, plan):
        if not entry["verified"]:
            raise RuntimeError("存在未经验证的任务")
        remove_verified_source(job, entry["files"])
        if job.recreate_link:
            create_junction(job.source, job.destination)
        entry["compatibility_junction"] = str(job.source)
        write_report(report_path, report)
    summary["complete"] = True
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
