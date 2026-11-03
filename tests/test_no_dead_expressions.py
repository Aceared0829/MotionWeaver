"""守护：inference/visualization 下不许有裸表达式（死语句）。

起因是 2026-11-03 的真实漏网：`ruff --fix --unsafe-fixes` 处理 F841 时会把
`x = expr` 整个删掉，`expr` 就变成一条裸语句；其中 `len(frame_root) - 1`
在 `refpose_closed_loop.py` 里活过了两轮清理。

为什么不用 ruff 的 B018 兜底：实测（ruff 0.17.1）B018 只对**字面量**和
**裸名字**报错，`len(x) - 1` 这种 BinOp 它一声不吭。所以这里自己走 AST。

判据：模块层与每个函数体内，任何表达式语句必须是**调用**（可能有副作用）
或**字符串字面量**（docstring）。其余一律视为死代码。
"""
import ast
from pathlib import Path

import pytest

VISUALIZATION = Path(__file__).resolve().parents[1] / "inference" / "visualization"


def dead_expressions(path: Path):
    """返回 (行号, 源码片段) 列表。"""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr):
            continue
        value = node.value
        # docstring / 裸字符串
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            continue
        # 调用可能有副作用（print、mkdir、write_text…），不算死代码
        if isinstance(value, ast.Call):
            continue
        # await / yield 也是有效果的
        if isinstance(value, (ast.Await, ast.Yield, ast.YieldFrom)):
            continue
        found.append((node.lineno, ast.get_source_segment(source, node) or ""))
    return found


def test_visualization_scripts_have_no_dead_expressions():
    scripts = sorted(p for p in VISUALIZATION.glob("*.py") if p.name != "__init__.py")
    assert scripts, "没找到任何 visualization 脚本，路径可能错了"

    offenders = []
    for script in scripts:
        for lineno, snippet in dead_expressions(script):
            offenders.append(f"{script.name}:{lineno}  {snippet.strip()}")

    assert not offenders, "发现裸表达式（算了不用，纯粹是死代码）：\n  " + "\n  ".join(offenders)


def test_probe_detects_a_planted_dead_expression(tmp_path):
    """反例测试：确保上面那个检查真的会报错，而不是永远通过。"""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "def f():\n"
        "    x = [1, 2, 3]\n"
        "    len(x) - 1\n"
        "    return len(x)\n",
        encoding="utf-8",
    )
    found = dead_expressions(probe)
    assert found, "检查器没报出植入的死语句，说明它失效了"
    assert found[0][0] == 3


def test_probe_accepts_calls_and_docstrings(tmp_path):
    """正常代码不该被误报。"""
    probe = tmp_path / "ok.py"
    probe.write_text(
        '"""模块 docstring。"""\n'
        "import pathlib\n"
        "\n"
        "\n"
        "def f(p):\n"
        '    """函数 docstring。"""\n'
        "    pathlib.Path(p).write_text('x', encoding='utf-8')\n"
        "    print(p)\n"
        "    return p\n",
        encoding="utf-8",
    )
    assert dead_expressions(probe) == []


def test_visualization_scripts_are_import_safe():
    """import 这些脚本不该产生副作用（写盘、打印、跑推理）。

    2026-11-03 发现 apply_smoothing.py 和 export_segment_reset.py 在模块级
    直接干活：任何 import 都会跑完整管线并覆盖 output/ 下的产物。其余 18 个
    脚本都有 __main__ 守卫，这两个是例外。
    """
    offenders = []
    for script in sorted(VISUALIZATION.glob("*.py")):
        if script.name in {"__init__.py", "paths.py"}:
            continue
        source = script.read_text(encoding="utf-8")
        if "__name__" not in source:
            offenders.append(script.name)
    assert not offenders, "这些脚本没有 __main__ 守卫，import 即执行：" + ", ".join(offenders)
