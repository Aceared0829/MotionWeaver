"""data.tools 分组迁移期间，旧模块路径必须继续指向新实现。"""
import importlib
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "data" / "tools"
GROUPS = ("common", "review", "ingest", "build", "audit", "publish", "ops", "eval")


def _migrated_modules():
    for group in GROUPS:
        for path in sorted((TOOLS / group).glob("*.py")):
            if path.name == "__init__.py":
                continue
            yield f"data.tools.{group}.{path.stem}", f"data.tools.{path.stem}"


@pytest.mark.parametrize("new_name,old_name", list(_migrated_modules()))
def test_old_module_path_points_to_new_implementation(new_name, old_name):
    assert importlib.import_module(old_name) is importlib.import_module(new_name)


@pytest.mark.parametrize(
    "module",
    ["data.tools.build_soma_training_set", "data.tools.prepare_unreal_dataset"],
)
def test_old_cli_path_still_dispatches_to_new_module(module):
    result = subprocess.run(
        [sys.executable, "-m", module, "--help"],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip()


def test_alias_keeps_identity_when_new_module_is_imported_first():
    code = """
import importlib
new = importlib.import_module("data.tools.audit.audit_conditioned_dataset")
old = importlib.import_module("data.tools.audit_conditioned_dataset")
assert old is new
old.reload_marker = 42
assert new.reload_marker == 42
assert importlib.reload(old) is new
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
