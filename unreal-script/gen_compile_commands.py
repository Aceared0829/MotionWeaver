"""为 unreal-script 下没有宿主工程的 UE 插件生成 fallback 编译数据库。

MotionWeaver 插件没有挂到任何 .uproject，UBT 无法为它产出
compile_commands.json，clangd 因此找不到引擎类型、也找不到插件自己的头。
本脚本复用主工程已验证的引擎 include 清单与 Definitions 宏（来自 UBT 生成的
.rsp），拼出一份"够 clangd 用"的编译数据库。注意：主工程已由 UMWSamplePreview
换为 AIMotionSample，而 AIMotionSample 目前还没有 Source/ 与 Plugins/，也尚未
产生 RSP/Definitions 产物，因此本脚本当前找不到输入、会直接退出。

    python unreal-script/gen_compile_commands.py

产物（都不入库）：
    unreal-script/compile_commands.json     clangd 读的编译数据库
    unreal-script/.clangd-stubs/UHT/*.generated.h   UHT 生成头的存根

注意：这不是真实编译命令，源码改动后重跑一次即可；插件一旦挂上宿主并编译过，
请改用宿主工程自己的编译数据库，那时的索引才是权威的。
"""

import glob
import json
import os
import re
import sys
from pathlib import Path

BS = chr(92)  # 反斜杠，避免在字符串里反复转义
# 脚本位于 <repo>/unreal-script/ 下，仓库根即上一级。
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))).replace(BS, "/")
UE = os.environ.get("UE_ROOT", "D:/UE_5.8").replace(BS, "/")
ENGINE_SRC = f"{UE}/Engine/Source"
# 主工程的编译数据库产物，提供引擎 include 清单与 Definitions 宏。
# 2026-11：主工程由 UMWSamplePreview 更名为 AIMotionSample。新工程尚未包含 Source/
# 与 Plugins/，也尚未用 UBT 生成过 RSP 与 Definitions 头，因此这两个路径暂时都不存在；
# 等新工程编译出第一次 Editor 目标后再跑本脚本即可。
PROJECT = "AIMotionSample"
RSP = (
    ROOT + f"/unreal-sample/{PROJECT}/.vscode/"
    f"compileCommands_{PROJECT}/{PROJECT}.0.rsp"
)
DEFS_H = (
    ROOT + f"/unreal-sample/{PROJECT}/Intermediate/Build/Win64/x64/"
    f"UnrealEditor/Development/{PROJECT}/Definitions.{PROJECT}.h"
)


def extract_dirs(rsp_path):
    """从 UBT 的 .rsp 里取出所有 /I 目录，相对路径按 Engine/Source 解析。"""
    with open(rsp_path, encoding="utf-8") as fh:
        text = fh.read()
    out = []
    for d in re.findall(r'/I "([^"]+)"', text):
        p = d if os.path.isabs(d) else os.path.normpath(os.path.join(ENGINE_SRC, d))
        p = p.replace(BS, "/")
        if os.path.isdir(p) and p not in out:
            out.append(p)
    return out


if not os.path.isfile(RSP):
    sys.exit(
        f"缺少 {RSP}\n"
        "请先在 VSCode 里跑任务「UE: 生成 C++ 索引（clangd 编译数据库）」。"
    )

engine_dirs = extract_dirs(RSP)
print("engine include dirs:", len(engine_dirs), flush=True)

plugin_roots = [
    "MotionWeaver",
]
module_names = []
plugin_includes = []
for rel in plugin_roots:
    base = f"{ROOT}/unreal-script/{rel}"
    if not os.path.isdir(base):
        print("SKIP missing plugin root:", rel, flush=True)
        continue
    for mdir in sorted(glob.glob(f"{base}/Source/*")):
        if not os.path.isdir(mdir):
            continue
        module_names.append(os.path.basename(mdir))
        for sub in ("Public", "Private", "Internal"):
            p = f"{mdir}/{sub}".replace(BS, "/")
            if os.path.isdir(p) and p not in plugin_includes:
                plugin_includes.append(p)
    for extra in (f"{base}/Source", base):
        extra = extra.replace(BS, "/")
        if extra not in plugin_includes:
            plugin_includes.append(extra)
    # 插件若曾编译过，UHT 生成头在 Intermediate 下，必须优先于下面的 fallback 存根。
    for uht in sorted(glob.glob(f"{base}/Intermediate/Build/**/UHT", recursive=True)):
        uht = uht.replace(BS, "/")
        if uht not in plugin_includes:
            plugin_includes.append(uht)
print("modules:", len(module_names), flush=True)
print("plugin include dirs:", len(plugin_includes), flush=True)

# Definitions 头里已经有 *_API 的模块不重复定义。
existing_api = set()
if os.path.isfile(DEFS_H):
    with open(DEFS_H, encoding="utf-8") as fh:
        existing_api.update(re.findall(r"#define\s+([A-Z0-9_]+)_API\b", fh.read()))

seen = set()


def add_api_defs(name, sink):
    """补 <模块>_API 与 <模块>_NON_ATTRIBUTED_API（UE 5.x 的两种导出宏）。"""
    for suffix in ("_API", "_NON_ATTRIBUTED_API"):
        macro = f"{name.upper()}{suffix}"
        # existing_api 里存的是去掉尾部 _API 的名字
        if macro[:-4] in existing_api or macro in seen:
            continue
        sink.append(f"{macro}=DLLIMPORT")
        seen.add(macro)


api_defs = []
for name in module_names:
    add_api_defs(name, api_defs)
# AIAnimation 被 AIAnimationMotionMatching 依赖，模块名在前面的循环里已收集。
for extra in ("AIANIMATION", "AIANIMATIONEDITOR"):
    add_api_defs(extra, api_defs)
print("api macros to add:", len(api_defs), flush=True)

flags = [
    "--driver-mode=cl",
    "/nologo",
    "/Zc:__cplusplus",
    "/EHsc",
    "/std:c++20",
    # 动态 CRT，与 UBT 真实编译一致：clang-cl 会据此定义 _DLL 与 _MT，
    # STL/UE 头才会按 dllimport 那一支解析。
    "/MD",
    "-DUE_BUILD_DEVELOPMENT=1",
    "-DUE_BUILD_TESTS=0",
    "-DUE_EDITOR=1",
    "-DWITH_EDITOR=1",
    "-DWITH_EDITORONLY_DATA=1",
    "-DWITH_ENGINE=1",
    "-DWITH_UNREAL_DEVELOPMENT_TOOLS=1",
    "-DWITH_UNREAL_PLUGINS=1",
    "-DPLATFORM_WINDOWS=1",
    "-DWIN32=1",
    "-D_WIN32=1",
    "-D_WIN64=1",
    "-fms-extensions",
    "-fms-compatibility",
    "-fms-compatibility-version=19.40",
    "-Wno-everything",
    "-fsyntax-only",
]
if os.path.isfile(DEFS_H):
    flags.append("/FI" + DEFS_H.replace(BS, "/"))
for d in api_defs:
    flags.append("-D" + d)
for d in engine_dirs:
    flags.append("-I" + d)

# 主工程的 .rsp 只含它自己依赖的模块路径；插件还会用到别的模块（例如
# AIAnimationEditor 的 OpenSSL、AILocomotionDatasetEditor 的
# AnimationBlueprintLibrary），缺了这些路径就会报头文件找不到。
engine_mods = {}
for d in sorted(glob.glob(f"{ENGINE_SRC}/*/*")):
    name = os.path.basename(d)
    for sub in ("Public", "Classes"):
        p = f"{d}/{sub}".replace(BS, "/")
        if os.path.isdir(p):
            engine_mods.setdefault(name, []).append(p)
for d in sorted(glob.glob(f"{ENGINE_SRC}/ThirdParty/*")):
    name = os.path.basename(d)
    cands = [f"{d}/include", f"{d}/Win64/include"]
    cands += sorted(glob.glob(f"{d}/*/include"))
    # 平台子目录，例如 OpenSSL 的 include/Unix 与 include/Win64/VS2015
    cands += sorted(glob.glob(f"{d}/*/include/*"))
    cands += sorted(glob.glob(f"{d}/*/include/Win64/*"))
    for p in cands:
        p = p.replace(BS, "/")
        if os.path.isdir(p):
            engine_mods.setdefault(name, []).append(p)

# 引擎模块的 UHT 生成头在 Intermediate/Build/.../Inc/<Module>/UHT 下，是真实存在的，
# 也要加：否则引擎头里的 #include "<Module>.generated.h" 找不到，连带引擎类型之间的
# 继承关系在 clangd 眼里断掉（报"不能把 A 当成 B 的派生类"）。
ENGINE_INC = f"{UE}/Engine/Intermediate/Build/Win64/UnrealEditor/Inc"
dep_dirs = []
dep_mods = set()
for rel in plugin_roots:
    for cs in sorted(glob.glob(f"{ROOT}/unreal-script/{rel}/Source/*/*.Build.cs")):
        with open(cs, encoding="utf-8", errors="replace") as fh:
            for mod in re.findall(r'"([A-Za-z0-9_]+)"', fh.read()):
                dep_mods.add(mod)
                uht = f"{ENGINE_INC}/{mod}/UHT"
                if os.path.isdir(uht) and uht not in dep_dirs:
                    dep_dirs.append(uht)
                for p in engine_mods.get(mod, []):
                    if p not in dep_dirs:
                        dep_dirs.append(p)
for d in dep_dirs:
    flags.append("-I" + d)
# 引擎模块的 API 宏（如 ANIMATIONBLUEPRINTLIBRARY_API）同样由 UBT 的 Definitions 头给出，
# 主工程的 Definitions 只含它自己依赖的模块，缺的在这里补。
dep_api_defs = []
for mod in sorted(dep_mods):
    add_api_defs(mod, dep_api_defs)
flags.extend("-D" + d for d in dep_api_defs)
print("dependency include dirs:", len(dep_dirs), flush=True)

for d in plugin_includes:
    flags.append("-I" + d)

# ---- UHT 生成头的存根 --------------------------------------------------------
# 这些插件从未编译，源文件里 #include 的 *.generated.h 不存在：clangd 会报
# file not found，并把宏展开成未定义的宏名（CURRENT_FILE_ID 空缺），连锁出一串
# "类型不是基类派生"之类的假错误。这里按实际被 include 的名字生成最小存根，
# 只把 UHT 该给的那几个空宏补上，不碰别的类型。include 顺序上存根在最后，
# 插件真编译过之后 Intermediate 下的真头会自动优先。
#
# 要补的宏由 UObject/ObjectMacros.h 决定，都是 CURRENT_FILE_ID + 行号拼出来的：
#   GENERATED_BODY() / GENERATED_USTRUCT_BODY()  -> <FID>_<行>_GENERATED_BODY
#   GENERATED_BODY_LEGACY() / GENERATED_*_BODY() -> <FID>_<行>_GENERATED_BODY_LEGACY
#   UCLASS() / UINTERFACE()                      -> <FID>_<行>_PROLOG
#   VINTERFACES()                                -> <FID>_<行>_VINTERFACES
STUB_DIR = f"{ROOT}/unreal-script/.clangd-stubs/UHT"
stub_boundary = Path(ROOT).resolve() / "unreal-script/.clangd-stubs"
if not Path(STUB_DIR).resolve().is_relative_to(stub_boundary):
    raise ValueError("clangd 存根目录链接越过仓库生成目录")
os.makedirs(STUB_DIR, exist_ok=True)
inc_re = re.compile(r'#include\s+"([A-Za-z0-9_]+)\.generated\.h"')
# (匹配的写法, 宏名后半段, 展开内容, 是否补 Super 别名)
stub_macros = [
    (
        re.compile(r"\bGENERATED_BODY\s*\(|\bGENERATED_USTRUCT_BODY\s*\("),
        "_GENERATED_BODY",
        "public:",
        True,
    ),
    (
        re.compile(
            r"\bGENERATED_BODY_LEGACY\s*\(|\bGENERATED_UCLASS_BODY\s*\("
            r"|\bGENERATED_UINTERFACE_BODY\s*\(|\bGENERATED_IINTERFACE_BODY\s*\("
        ),
        "_GENERATED_BODY_LEGACY",
        "public:",
        True,
    ),
    (re.compile(r"\bUCLASS\s*\(|\bUINTERFACE\s*\("), "_PROLOG", "", False),
    (re.compile(r"\bVINTERFACES\s*\("), "_VINTERFACES", "", False),
]
# UHT 的 GENERATED_BODY 里含 typedef <父类> Super;，源码的 Super::Foo() 依赖它。
class_re = re.compile(
    r"\b(?:class|struct)\s+(?:[A-Z0-9_]+_API\s+)?(\w+)(?:\s+final)?\s*:"
    r"\s*public\s+([\w:]+)"
)


def find_parent(lines, lineno):
    """从 GENERATED_BODY 所在行往上找最近的类声明，取出父类名。"""
    for i in range(lineno - 2, -1, -1):
        m = class_re.search(lines[i])
        if m:
            return m.group(2)
    return None


stub_owners = {}
for stale in glob.glob(f"{STUB_DIR}/*.generated.h"):
    if not Path(stale).resolve().is_relative_to(Path(STUB_DIR).resolve()):
        raise ValueError("clangd 旧存根链接越过生成目录")
    os.remove(stale)  # 源码删了类型之后不留旧存根
for rel in plugin_roots:
    for src in sorted(
        glob.glob(f"{ROOT}/unreal-script/{rel}/Source/**/*.h", recursive=True)
    ):
        src = src.replace(BS, "/")
        if "/Intermediate/" in src:
            continue
        with open(src, encoding="utf-8", errors="replace") as fh:
            for name in inc_re.findall(fh.read()):
                stub_owners.setdefault(name, src)

for name, owner in sorted(stub_owners.items()):
    fid = re.sub(r"[^A-Za-z0-9_]", "_", "STUB_" + owner.split("/unreal-script/", 1)[-1])
    stub = [
        (
            "// clangd fallback 用的 UHT 存根，由"
            " unreal-script/gen_compile_commands.py 生成，勿手工编辑。"
        ),
        f"// 仅当真正的 {name}.generated.h 不存在时才会被包含（真头在 include 路径更前面）。",
        "#pragma once",
        "#undef CURRENT_FILE_ID",
        f"#define CURRENT_FILE_ID {fid}",
    ]
    with open(owner, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    for idx, line in enumerate(lines, 1):
        for pat, tail, expand, want_super in stub_macros:
            if not pat.search(line):
                continue
            if want_super:
                parent = find_parent(lines, idx)
                if parent:
                    expand = f"public: typedef {parent} Super;"
            stub.append(f"#define {fid}_{idx}{tail} {expand}".rstrip())
    with open(
        f"{STUB_DIR}/{name}.generated.h", "w", encoding="utf-8", newline="\n"
    ) as fh:
        fh.write("\n".join(stub) + "\n")
flags.append("-I" + STUB_DIR)
print("UHT stubs:", len(stub_owners), "->", STUB_DIR, flush=True)

# clangd 只做语法分析，用哪个 clang-cl 都行，优先用工程主编译器那套。
CLANG_CL = (
    "C:/Program Files (x86)/Microsoft Visual Studio/2022/BuildTools/"
    "VC/Tools/Llvm/x64/bin/clang-cl.exe"
)
if not os.path.isfile(CLANG_CL):
    CLANG_CL = (
        "C:/Program Files/Microsoft Visual Studio/18/Community/VC/Tools/Llvm/x64/"
        "bin/clang-cl.exe"
    )
print("clang-cl:", CLANG_CL, "exists =", os.path.isfile(CLANG_CL), flush=True)

entries = []
for rel in plugin_roots:
    pattern = f"{ROOT}/unreal-script/{rel}/Source/**/*.cpp"
    for cpp in sorted(glob.glob(pattern, recursive=True)):
        cpp = cpp.replace(BS, "/")
        if "/Intermediate/" in cpp or "/Binaries/" in cpp:
            continue
        entries.append(
            {
                "directory": os.path.dirname(cpp),
                "file": cpp,
                "arguments": [CLANG_CL, *flags, cpp],
            }
        )

cdb_path = f"{ROOT}/unreal-script/compile_commands.json"
with open(cdb_path, "w", encoding="utf-8") as fh:
    json.dump(entries, fh, indent=1)
print("wrote", cdb_path, "| entries:", len(entries), flush=True)
