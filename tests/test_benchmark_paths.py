"""路径覆盖必须跨平台可预测，不能把 Windows 盘符拼到 POSIX 仓库根下面。"""
import os
from pathlib import Path

import pytest

from benchmark import paths as benchmark_paths
from inference.visualization import paths as visualization_paths


@pytest.mark.parametrize("module", [benchmark_paths, visualization_paths])
def test_windows_default_is_not_anchored_on_posix(module):
    if os.name == "nt":
        pytest.skip("Windows 上 D:/ 本来就是绝对路径")
    result = module._env_path("MW_DATA_ROOT", "D:/MotionWeaverData", anchor=Path("/repo"))
    assert result == Path("D:/MotionWeaverData")


@pytest.mark.parametrize("module", [benchmark_paths, visualization_paths])
def test_relative_override_is_anchored_to_repo(module, monkeypatch):
    monkeypatch.setenv("MW_DATA_ROOT", "local-data")
    result = module._env_path("MW_DATA_ROOT", "D:/MotionWeaverData", anchor=Path("/repo"))
    assert result == Path("/repo/local-data")


@pytest.mark.parametrize("module", [benchmark_paths, visualization_paths])
def test_blank_override_falls_back_to_default(module, monkeypatch):
    monkeypatch.setenv("MW_DATA_ROOT", "   ")
    result = module._env_path("MW_DATA_ROOT", "D:/MotionWeaverData", anchor=Path("/repo"))
    assert result == Path("D:/MotionWeaverData")
