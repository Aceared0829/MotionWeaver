"""第一方包的依赖方向守卫。

已知例外在 Phase 2 抽离模型后删除；在此之前只允许名单内的文件，
新增的跨层 import 会直接失败。
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Phase 2 要处理的已知跨层依赖；不要往里加新文件。
ALLOWED_CROSS_LAYER = {
    ROOT / "inference/runtime/experiment/experiment.py",
    ROOT / "data/runtime/unreal_quality.py",
}


def _is_type_checking(node):
    if isinstance(node, ast.Name):
        return node.id == "TYPE_CHECKING"
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "typing"
        and node.attr == "TYPE_CHECKING"
    )


class _Imports(ast.NodeVisitor):
    def __init__(self):
        self.found = []

    def visit_If(self, node):
        if _is_type_checking(node.test):
            # TYPE_CHECKING 分支在运行时不会执行，不算部署依赖。
            for child in node.orelse:
                self.visit(child)
            return
        self.generic_visit(node)

    def visit_Import(self, node):
        for alias in node.names:
            self.found.append((node.lineno, alias.name.split(".")[0]))

    def visit_ImportFrom(self, node):
        if node.level == 0 and node.module:
            self.found.append((node.lineno, node.module.split(".")[0]))


def _violations(root, forbidden, *, allow=ALLOWED_CROSS_LAYER):
    allowed = {path.resolve() for path in allow}
    found = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts or path.resolve() in allowed:
            continue
        visitor = _Imports()
        visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
        for lineno, package in visitor.found:
            if package in forbidden:
                found.append((path, lineno, package))
    return found


def test_inference_runtime_and_export_are_standalone():
    forbidden = {"training", "tools", "benchmark"}
    found = _violations(ROOT / "inference/runtime", forbidden)
    found += _violations(ROOT / "inference/export", forbidden)
    assert not found, "\n".join(
        f"{path.relative_to(ROOT)}:{lineno} imports {package}"
        for path, lineno, package in found
    )


def test_data_runtime_does_not_depend_on_training_or_inference():
    forbidden = {"training", "inference", "benchmark", "tools"}
    found = _violations(ROOT / "data/runtime", forbidden)
    assert not found, "\n".join(
        f"{path.relative_to(ROOT)}:{lineno} imports {package}"
        for path, lineno, package in found
    )


def test_boundary_scanner_detects_a_planted_violation(tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text("from training import nope\n", encoding="utf-8")
    assert _violations(tmp_path, {"training"}, allow=())


def test_type_checking_import_is_not_a_runtime_dependency(tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from training import nope\n",
        encoding="utf-8",
    )
    assert not _violations(tmp_path, {"training"}, allow=())

