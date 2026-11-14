#!/usr/bin/env python3
"""MotionWeaver 开发入口：环境检查、测试、Lint 与本地安装。

统一入口的目的不是包一层命令，而是把容易被忘掉的前提说清楚：

- 测试统一走 pytest。仓库里已经有 pytest 风格的 fixture / parametrize，
  `unittest discover` 会静默漏掉它们；
- lint 规则固定在 `pyproject.toml`，不随本机 ruff 版本的默认规则漂移；
- `.venv` 存在时优先使用它，避免“系统 Python 缺 pytest”这种假故障；
- 数据与权重缺失属于正常状态，doctor 只给警告，不阻止纯代码开发。
"""
from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"

REQUIRED_PACKAGES = ["torch", "numpy", "scipy", "einops", "hydra-core"]
TRAINING_PACKAGES = [
    "pytorch-lightning",
    "transformers",
    "vector-quantize-pytorch",
    "adam-atan2-pytorch",
    "colorlog",
]
DEMO_PACKAGES = ["mujoco", "pynput"]
EXPORT_PACKAGES = ["onnx", "onnxruntime"]
DEV_PACKAGES = ["pytest", "ruff", "pre-commit"]

USAGE = """MotionWeaver 开发入口

用法:
  python tools/dev.py doctor [--strict]   检查 Python、依赖、数据与权重
  python tools/dev.py bootstrap [选项]    创建 .venv 并安装开发依赖
  python tools/dev.py setup [选项]        在当前/仓库虚拟环境安装本仓库
  python tools/dev.py test [pytest 参数]  运行完整 pytest 测试
  python tools/dev.py lint [--fix] [路径]  运行项目固定的 ruff 规则
  python tools/dev.py check [pytest 参数] 先 lint，再跑完整测试

setup / bootstrap 选项:
  --minimal              只安装基础依赖 + dev，不装 training 附加项
  --demo                 额外安装 MuJoCo / G1 演示依赖
  --export               额外安装 ONNX 导出依赖
  --system-site-packages bootstrap 时复用系统 site-packages（例如已有 CUDA torch）

示例:
  .venv/bin/python tools/dev.py doctor
  .venv/bin/python tools/dev.py test -q
  .venv/bin/python tools/dev.py test tests/test_train_resume.py -q
"""

_tool_python_notice_shown = False


def _venv_python() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def _tool_python() -> str:
    """优先使用仓库 .venv，避免调用者忘了激活虚拟环境。"""
    global _tool_python_notice_shown
    candidate = _venv_python()
    in_repo_venv = Path(sys.prefix).resolve() == VENV.resolve()
    if candidate.exists() and not in_repo_venv:
        if not _tool_python_notice_shown:
            print(f"[info] 使用仓库虚拟环境: {candidate}")
            _tool_python_notice_shown = True
        return str(candidate)
    return sys.executable


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _module_available(python: str, name: str) -> bool:
    result = subprocess.run(
        [python, "-c", f"import {name}"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def _run(command: list[str], *, check: bool = True) -> int:
    print("$ " + " ".join(command), flush=True)
    try:
        return subprocess.run(command, cwd=ROOT, check=check).returncode
    except FileNotFoundError:
        print(f"找不到命令: {command[0]}")
        return 2
    except subprocess.CalledProcessError as exc:
        return exc.returncode or 1


def _status(kind: str, label: str, detail: str = "") -> None:
    suffix = f": {detail}" if detail else ""
    print(f"[{kind:<4}] {label}{suffix}")


def _is_lfs_pointer(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        with path.open("rb") as handle:
            return handle.read(80).startswith(b"version https://git-lfs.github.com/spec/v1")
    except OSError:
        return False


def _check_package_group(label: str, names: list[str], failures: list[str], warnings: list[str],
                         required: bool) -> None:
    missing = [name for name in names if _package_version(name) is None]
    if not missing:
        versions = ", ".join(f"{name}={_package_version(name)}" for name in names)
        _status("ok", label, versions)
        return
    detail = ", ".join(missing)
    if required:
        _status("fail", label, "缺少 " + detail)
        failures.append(f"{label}: {detail}")
    else:
        _status("warn", label, "缺少 " + detail)
        warnings.append(f"{label}: {detail}")


def doctor(strict: bool = False) -> int:
    failures: list[str] = []
    warnings: list[str] = []
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    print("MotionWeaver 环境检查")
    print(f"仓库根: {ROOT}")
    _status("info", "解释器", f"{sys.executable} ({platform.python_version()})")
    _status("info", "平台", f"{platform.system()} {platform.machine()}")

    if sys.version_info < (3, 10):
        _status("fail", "Python 版本", "需要 >= 3.10")
        failures.append("Python < 3.10")
    elif sys.version_info >= (3, 14):
        _status("warn", "Python 版本", "3.14+ 上 torch.jit / 部分导出路径可能受限，训练优先 3.10-3.13")
        warnings.append("Python 3.14+")
    else:
        _status("ok", "Python 版本", platform.python_version())

    venv_python = _venv_python()
    if venv_python.exists():
        in_venv = Path(sys.prefix).resolve() == VENV.resolve()
        _status("ok" if in_venv else "warn", "仓库虚拟环境",
                str(venv_python) if in_venv else f"存在但当前未使用，建议 {venv_python} tools/dev.py doctor")
        if not in_venv:
            warnings.append("未使用 .venv")
    else:
        _status("warn", "仓库虚拟环境", "不存在；可运行 `python tools/dev.py bootstrap`")
        warnings.append(".venv 不存在")

    _check_package_group("核心依赖", REQUIRED_PACKAGES, failures, warnings, required=True)

    spec = importlib.util.find_spec("motionweaver")
    if spec is None:
        _status("warn", "本仓库包", "未安装 editable；仓库外无法 import，建议 pip install -e .")
        warnings.append("motionweaver 未安装")
    else:
        _status("ok", "本仓库包", spec.origin or "(namespace)")

    _check_package_group("训练依赖", TRAINING_PACKAGES, failures, warnings, required=False)
    _check_package_group("演示依赖", DEMO_PACKAGES, failures, warnings, required=False)
    _check_package_group("导出依赖", EXPORT_PACKAGES, failures, warnings, required=False)
    _check_package_group("开发依赖", DEV_PACKAGES, failures, warnings, required=False)

    if importlib.util.find_spec("torch") is not None:
        try:
            import torch

            if torch.cuda.is_available():
                _status("ok", "CUDA", f"{torch.version.cuda}; {torch.cuda.get_device_name(0)}")
            else:
                _status("warn", "CUDA", "不可用；CPU 训练/测试仍可运行")
                warnings.append("CUDA 不可用")
        except Exception as exc:  # noqa: BLE001 - doctor 要报告任何导入失败
            _status("warn", "torch 导入", repr(exc))
            warnings.append("torch 导入失败")
    else:
        _status("fail", "torch 导入", "未安装")
        failures.append("torch 未安装")

    data_value = os.environ.get("MW_DATA_ROOT", "").strip()
    try:
        from benchmark import paths as benchmark_paths

        data_root = benchmark_paths.DATA
        weights = benchmark_paths.WEIGHTS
    except Exception:  # noqa: BLE001 - doctor 不应因为路径模块导入失败而中断
        data_root = Path(data_value or "D:/MotionWeaverData").expanduser()
        weights = data_root / "model_weight"
    _status("info", "MW_DATA_ROOT", data_value or f"(未设置，默认 {data_root})")
    if data_root.is_dir():
        _status("ok", "数据根", str(data_root))
    else:
        _status("warn", "数据根", f"{data_root} 不存在；设置 MW_DATA_ROOT 或接受数据测试 skip")
        warnings.append("数据根不存在")

    if weights.is_dir():
        _status("ok", "权重目录", str(weights))
    else:
        _status("warn", "权重目录", f"{weights} 不存在；设置 MW_DATA_ROOT 或接受评测/导出测试 skip")
        warnings.append("权重目录不存在")

    animation_root = os.environ.get("AIANIMATION_ROOT", "").strip()
    if animation_root:
        animation_path = Path(animation_root).expanduser()
        if animation_path.is_dir():
            _status("ok", "AIANIMATION_ROOT", str(animation_path))
        else:
            _status("warn", "AIANIMATION_ROOT", f"{animation_path} 不存在")
            warnings.append("AIANIMATION_ROOT 不存在")
    else:
        _status("info", "AIANIMATION_ROOT", "(未设置，使用仓库根)")

    local_hparams = ROOT / "model-weight/base/motionbricks/motionbricks_vqvae/version_1/hparams.yaml"
    if local_hparams.is_file():
        _status("ok", "本地 G1 配置", str(local_hparams.relative_to(ROOT)))
    else:
        _status("fail", "本地 G1 配置", f"缺少 {local_hparams.relative_to(ROOT)}")
        failures.append("G1 配置缺失")

    manifest = ROOT / "benchmark/manifest/refpose_manifest_v1.json"
    if manifest.is_file():
        _status("ok", "refpose 清单", str(manifest.relative_to(ROOT)))
    else:
        _status("fail", "refpose 清单", f"缺少 {manifest.relative_to(ROOT)}")
        failures.append("refpose 清单缺失")

    lfs_candidates = [
        ROOT / "model-weight/base/motionbricks/G1-clip.ckpt",
        ROOT / "inference/assets/skeletons/g1/meshes/pelvis.STL",
    ]
    pointers = [path for path in lfs_candidates if _is_lfs_pointer(path)]
    if pointers:
        _status("warn", "Git LFS", "发现指针文件；运行 `git lfs pull` 拉取真实权重/网格")
        warnings.append("Git LFS 指针未拉取")
    elif shutil.which("git"):
        result = subprocess.run(["git", "lfs", "version"], cwd=ROOT,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if result.returncode == 0:
            _status("ok", "Git LFS", "已安装")
        else:
            _status("warn", "Git LFS", "未安装；大权重/网格无法拉取")
            warnings.append("Git LFS 未安装")

    print()
    if failures:
        print(f"结果: {len(failures)} 项必需条件失败, {len(warnings)} 项警告")
        for item in failures:
            print(f"  - {item}")
    elif warnings:
        print(f"结果: 基础环境可用, {len(warnings)} 项警告（数据/可选依赖缺失通常不影响纯代码开发）")
    else:
        print("结果: 环境完整")

    return 1 if strict and failures else 0


def _extras(minimal: bool, demo: bool, export: bool) -> str:
    names = ["dev"]
    if not minimal:
        names.insert(0, "training")
    if demo:
        names.append("demo")
    if export:
        names.append("export")
    return ",".join(names)


def setup(minimal: bool = False, demo: bool = False, export: bool = False) -> int:
    python = _tool_python()
    target = f"{ROOT}[{_extras(minimal, demo, export)}]"
    _status("info", "安装目标", target)
    return _run([python, "-m", "pip", "install", "-e", target])


def bootstrap(system_site_packages: bool = False, minimal: bool = False,
              demo: bool = False, export: bool = False) -> int:
    python = sys.executable
    if _venv_python().exists():
        _status("info", "虚拟环境", f"已存在，跳过创建: {VENV}")
    else:
        command = [python, "-m", "venv"]
        if system_site_packages:
            command.append("--system-site-packages")
        command.append(str(VENV))
        code = _run(command)
        if code:
            return code

    venv_python = str(_venv_python())
    code = _run([venv_python, "-m", "pip", "install", "--upgrade", "pip"])
    if code:
        return code
    return setup(minimal=minimal, demo=demo, export=export)


def test(pytest_args: list[str]) -> int:
    python = _tool_python()
    if not _module_available(python, "pytest"):
        _status("fail", "pytest", "未安装；运行 `python tools/dev.py setup`")
        return 2
    return _run([python, "-m", "pytest", *pytest_args])


def lint(paths: list[str], fix: bool = False) -> int:
    python = _tool_python()
    if _module_available(python, "ruff"):
        command = [python, "-m", "ruff", "check"]
    elif shutil.which("ruff"):
        _status("warn", "ruff", "仓库解释器未安装 ruff，改用 PATH 中的版本")
        command = ["ruff", "check"]
    else:
        _status("fail", "ruff", "未安装；运行 `python tools/dev.py setup`")
        return 2
    if fix:
        command.append("--fix")
    command.extend(paths or ["."])
    return _run(command)


def check(pytest_args: list[str]) -> int:
    code = lint([])
    if code:
        return code
    return test(pytest_args)


def _parse_bool_flags(args: list[str], allowed: set[str]) -> set[str] | None:
    selected: set[str] = set()
    unknown: list[str] = []
    for item in args:
        if item in allowed:
            selected.add(item)
        else:
            unknown.append(item)
    if unknown:
        print(f"未知选项: {' '.join(unknown)}")
        return None
    return selected


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help", "help"}:
        print(USAGE)
        return 0

    command, rest = args[0], args[1:]
    if command == "doctor":
        flags = _parse_bool_flags(rest, {"--strict"})
        if flags is None:
            return 2
        return doctor(strict="--strict" in flags)

    if command in {"setup", "bootstrap"}:
        allowed = {"--minimal", "--demo", "--export"}
        if command == "bootstrap":
            allowed.add("--system-site-packages")
        flags = _parse_bool_flags(rest, allowed)
        if flags is None:
            return 2
        options = {
            "minimal": "--minimal" in flags,
            "demo": "--demo" in flags,
            "export": "--export" in flags,
        }
        if command == "bootstrap":
            return bootstrap(system_site_packages="--system-site-packages" in flags, **options)
        return setup(**options)

    if command == "test":
        return test(rest)

    if command == "lint":
        fix = "--fix" in rest
        paths = [item for item in rest if item != "--fix"]
        return lint(paths, fix=fix)

    if command == "check":
        return check(rest)

    print(f"未知命令: {command}")
    print(USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main())
