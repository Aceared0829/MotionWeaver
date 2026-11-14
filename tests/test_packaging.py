"""安装元数据与公开包入口守卫。"""
import os
import runpy
import subprocess
import sys
import tempfile
from pathlib import Path

import setuptools

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PACKAGES = {"motionweaver", "motionbricks", "data", "training", "inference", "benchmark"}
EXPECTED_SCRIPTS = {"mw-benchmark", "mw-review", "mw-train-refpose", "mw-export-unreal"}


def setup_kwargs():
    captured = {}
    original = setuptools.setup

    def fake_setup(**kwargs):
        captured.update(kwargs)

    setuptools.setup = fake_setup
    try:
        runpy.run_path(str(ROOT / "setup.py"), run_name="__main__")
    finally:
        setuptools.setup = original
    return captured


def test_setup_lists_all_first_party_packages():
    kwargs = setup_kwargs()
    assert EXPECTED_PACKAGES <= set(kwargs["package_dir"])
    assert EXPECTED_PACKAGES <= set(kwargs["packages"])
    for name in EXPECTED_PACKAGES:
        assert (ROOT / kwargs["package_dir"][name]).is_dir(), name


def test_package_data_covers_runtime_resources():
    data = setup_kwargs()["package_data"]
    assert "skeletons/*.json" in data["data"]
    assert "annotations/*.json" in data["data"]
    assert "review_web/*" in data["data.tools.review"]
    assert "configs/pretrain/*.yaml" in data["training"]
    assert "configs/unreal/*.json" in data["training"]
    assert "manifest/*.json" in data["benchmark"]


def test_console_scripts_are_declared():
    entry_points = setup_kwargs().get("entry_points", {})
    scripts = entry_points.get("console_scripts", [])
    names = {entry.split("=", 1)[0].strip() for entry in scripts}
    assert EXPECTED_SCRIPTS <= names


def test_public_packages_import_from_any_working_directory():
    code = "import benchmark, data, inference, motionweaver, training; print('ok')"
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    with tempfile.TemporaryDirectory() as cwd:
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    assert result.returncode == 0, result.stdout + result.stderr
