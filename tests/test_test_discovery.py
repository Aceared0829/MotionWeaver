"""测试文件位置守卫：测试不应藏在生产包里。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IGNORED_TOP_LEVEL = {".git", ".venv", "node_modules", "__pycache__"}


def test_no_test_modules_outside_tests_directory():
    offenders = []
    for path in ROOT.rglob("test_*.py"):
        relative = path.relative_to(ROOT)
        if relative.parts[0] in IGNORED_TOP_LEVEL:
            continue
        if relative.parts[0] == "tests":
            continue
        offenders.append(str(relative))
    assert not offenders, "测试文件必须放在 tests/ 下：\n  " + "\n  ".join(offenders)


def test_pytest_only_collects_the_tests_directory():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r"^testpaths\s*=\s*\[(.*?)\]", text, re.MULTILINE)
    assert match, "pyproject.toml 缺少 tool.pytest.ini_options.testpaths"
    paths = re.findall(r'"([^"]+)"', match.group(1))
    assert paths == ["tests"]

