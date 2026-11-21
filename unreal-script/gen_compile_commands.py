"""为 unreal-script/ 下的插件镜像生成 clangd 编译数据库。

镜像树没有挂到任何 .uproject，UBT 不会为它生成编译数据库。此前这里是硬编码
Windows 路径与 MSVC 宏的独立实现（`D:/UE_5.8`、`Win64/*.rsp`），在 Linux 上直接
退出，于是 `unreal-script/compile_commands.json` 从未生成，clangd 找不以
`CoreMinimal.h`，整片报 file not found。

现在改为复用宿主工程 UBT 生成的权威编译命令：
`unreal-sample/AIMotionSample/.vscode/compile_commands.json`。脚本把命令里的
`@xxx.rsp` 递归展开成扁平参数（去掉 `-c` / `-o` / `-MF` 这类只在真实构建里
有意义的项），保留引擎 include、UHT 目录与 `-include Definitions.h` 的绝对路径，
最后把被编译的源文件换成镜像里的同路径文件。

为什么不直接保留 `@rsp`：这条链路要跨非 ASCII 仓库路径，展开成扁平参数后
clangd 不再依赖磁盘上额外文件的位置，诊断结果可复现。

    .venv/bin/python unreal-script/gen_compile_commands.py

产物（不入库）：`unreal-script/compile_commands.json`。宿主新增源文件后重跑一次。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIRROR = ROOT / "unreal-script"
HOST_PROJECT = ROOT / "unreal-sample" / "AIMotionSample"
HOST_CDB = HOST_PROJECT / ".vscode" / "compile_commands.json"
HOST_PLUGIN = HOST_PROJECT / "Plugins" / "MotionWeaver"
MIRROR_PLUGIN = MIRROR / "MotionWeaver"
OUTPUT = MIRROR / "compile_commands.json"

# 这些参数只服务于真实构建的产物落盘；clangd 会自己加 -fsyntax-only。
DROP_WITH_VALUE = {"-o", "-MF", "-MT", "-MQ"}
DROP_FLAGS = {"-c", "-MD", "-MMD", "-MP", "-Werror", "-pipe"}


def fail(message):
    sys.exit(
        f"{message}\n"
        "先运行 bash scripts/ue.sh BuildEditor 与 bash scripts/ue.sh GenerateClangDatabase。"
    )


def split_arguments(text):
    """按 shell 规则拆参数，保留引号内的空格（仓库路径可能含中文）。"""
    import shlex

    return shlex.split(text, posix=True)


def expand_rsp(path, flags, seen):
    """把 RSP 递归展开；`@nested.rsp` 一并展开，其它参数原样追加。"""
    path = path.resolve()
    if path in seen or not path.is_file():
        return
    seen.add(path)
    for line in path.read_text(encoding="utf-8", errors="surrogateescape").splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        for argument in split_arguments(value):
            if argument.startswith("@"):
                expand_rsp(path.parent / argument[1:], flags, seen)
            else:
                flags.append(argument)


def flatten(arguments, directory):
    """展开命令中的所有 RSP，并丢弃只影响构建产物的参数。"""
    flags, seen = [], set()
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument.startswith("@"):
            expand_rsp(Path(argument[1:].strip('"')), flags, seen)
        elif argument in DROP_WITH_VALUE:
            index += 1  # 连带跳过它的取值
        elif argument in DROP_FLAGS:
            pass
        elif argument.startswith(("-MF", "-MT", "-MQ")) and len(argument) > 3:
            pass
        else:
            flags.append(argument)
        index += 1
    return _resolve_relative(flags, Path(directory))


def _resolve_relative(flags, directory):
    """把 `-I../X` 这类相对路径按 UBT 的工作目录转成绝对路径。

    clangd 会用 `directory` 作为工作目录，但展开后的命令不再带 UBT 的
    `@rsp` 上下文，提前绝对化可以避免两处对"相对谁"的理解不一致。
    """
    resolved = []
    for argument in flags:
        if argument.startswith("-I") and len(argument) > 2:
            target = Path(argument[2:].strip('"'))
            prefix = "-I"
        elif argument.startswith("-include") and len(argument) > len("-include"):
            target = Path(argument[len("-include"):].strip().strip('"'))
            prefix = "-include"
        else:
            resolved.append(argument)
            continue
        if not target.is_absolute():
            target = (directory / target)
        resolved.append(f"{prefix}{target}" if prefix == "-I" else f"-include{target}")
    return resolved


def main():
    if not HOST_CDB.is_file():
        fail(f"缺少宿主编译数据库：{HOST_CDB}")
    host_entries = json.loads(HOST_CDB.read_text(encoding="utf-8"))
    prefix = str(HOST_PLUGIN)

    entries, skipped = [], []
    for entry in host_entries:
        source = Path(entry["file"])
        if not str(source).startswith(prefix):
            continue
        relative = source.relative_to(HOST_PLUGIN)
        # 宿主的编译数据库也会收录 Intermediate/ 下的生成源码；镜像树没有
        # 这些文件，它们也不是需要索引的插件源码。
        if relative.parts and relative.parts[0] in ("Intermediate", "Binaries"):
            continue
        mirror_source = MIRROR_PLUGIN / relative
        if not mirror_source.is_file():
            skipped.append(mirror_source.relative_to(MIRROR).as_posix())
            continue
        arguments = entry.get("arguments") or entry.get("command", "").split()
        compiler, rest = arguments[0], arguments[1:]
        flags = flatten(rest, entry["directory"])
        # 展开的 RSP 里仍带着宿主的源文件；换成镜像文件，避免同时编译两份。
        flags = [flag for flag in flags if flag != str(source)]
        entries.append({
            "directory": entry["directory"],
            "file": str(mirror_source),
            "arguments": [compiler, *flags, str(mirror_source)],
        })

    if not entries:
        fail("宿主数据库里没有 MotionWeaver 插件的编译命令。")

    OUTPUT.write_text(json.dumps(entries, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT} | entries: {len(entries)}", flush=True)
    if skipped:
        print("以下宿主源文件在镜像里不存在，已跳过（先运行插件同步脚本）：", flush=True)
        for name in skipped:
            print(f"  {name}", flush=True)


if __name__ == "__main__":
    main()
