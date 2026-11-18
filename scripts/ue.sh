#!/usr/bin/env bash
# UE 5.8 宿主的 Linux 构建 / 索引 / 启动脚本。
#
# 引擎路径只从环境读取，不写死进仓库：
#   export UE_ROOT=/path/to/UE_5.8
# 或把绝对路径写进被忽略的 .vscode/ue-root。
set -euo pipefail

PROJECT_SUBDIR="unreal-sample/AIMotionSample"
PROJECT_NAME="AIMotionSample"
PLATFORM="Linux"
CONFIGURATION="Development"
TARGET="${PROJECT_NAME}Editor"

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_DIR="$REPO_ROOT/$PROJECT_SUBDIR"
PROJECT="$PROJECT_DIR/$PROJECT_NAME.uproject"

UE_ROOT="${UE_ROOT:-$(cat "$REPO_ROOT/.vscode/ue-root" 2>/dev/null || true)}"
if [[ -z "$UE_ROOT" || ! -f "$UE_ROOT/Engine/Build/Build.version" ]]; then
    echo "error: 未找到引擎。请 export UE_ROOT=/path/to/UE_5.8，" >&2
    echo "       或把绝对路径写入 $REPO_ROOT/.vscode/ue-root（该文件不入库）。" >&2
    exit 1
fi
UE_ROOT="${UE_ROOT%/}"
UE_ROOT="$(cd "$UE_ROOT" && pwd)"

# launch.json 通过这个链接定位引擎，仓库里不出现绝对路径。
# 链接本身被 .vscode/* 忽略。
mkdir -p "$REPO_ROOT/.vscode"
ln -sfn "$UE_ROOT" "$REPO_ROOT/.vscode/ue-engine"

BUILD_SH="$UE_ROOT/Engine/Build/BatchFiles/Linux/Build.sh"
GENERATE_PROJECTS_SH="$UE_ROOT/Engine/Build/BatchFiles/Linux/GenerateProjectFiles.sh"
EDITOR="$UE_ROOT/Engine/Binaries/Linux/UnrealEditor"
GDB_PORT="${GDB_PORT:-1234}"

# clangd 只读 UBT 主动生成的编译数据库；放在工程目录下，
# 与 unreal-script/ 的镜像插件数据库互不影响。
CLANG_DB_DIR="$PROJECT_DIR/.vscode"
CLANG_DB="$CLANG_DB_DIR/compile_commands.json"
CLANG_DB_SNAPSHOT="$CLANG_DB_DIR/.clangdb-source-list"

QUERY_DRIVER="$(find "$UE_ROOT/Engine/Extras/ThirdPartyNotUE/SDKs/HostLinux" \
    -path '*/x86_64-unknown-linux-gnu/bin/clang++' 2>/dev/null | head -n 1 || true)"

source_file_list() {
    find "$PROJECT_DIR/Source" "$PROJECT_DIR/Plugins/MotionWeaver/Source" \
        -type f \( -name '*.h' -o -name '*.cpp' -o -name '*.cs' \) 2>/dev/null | sort
}

generate_clang_database() {
    mkdir -p "$CLANG_DB_DIR"
    "$BUILD_SH" -Mode=GenerateClangDatabase \
        "-Project=$PROJECT" "-Target=$TARGET $PLATFORM $CONFIGURATION" \
        "-OutputDir=$CLANG_DB_DIR" -WaitMutex
    source_file_list > "$CLANG_DB_SNAPSHOT"
}

refresh_clang_database_if_stale() {
    local current
    current="$(source_file_list)"
    # 比较文件清单而不是 mtime：构建和同步都会改时间戳，内容编辑则不需要重建。
    if [[ -f "$CLANG_DB" && -f "$CLANG_DB_SNAPSHOT" ]] \
        && diff -q <(printf '%s\n' "$current") "$CLANG_DB_SNAPSHOT" >/dev/null 2>&1; then
        return
    fi
    echo "源码清单变化，重新生成编译数据库..." >&2
    generate_clang_database
    printf '%s\n' "$current" > "$CLANG_DB_SNAPSHOT"
}

require_project() {
    [[ -f "$PROJECT" ]] || { echo "error: 找不到 $PROJECT" >&2; exit 1; }
}

# 本机可能只有 Xwayland（例如 :1），/tmp/.X11-unix 里也可能残留失效的 :0。
# 优先用外部 DISPLAY，否则挑第一个真的能连上的。
resolve_display() {
    if [[ -n "${DISPLAY:-}" ]]; then
        echo "$DISPLAY"
        return
    fi
    local sock display
    for sock in /tmp/.X11-unix/X*; do
        [[ -S "$sock" ]] || continue
        display=":${sock##*/X}"
        if command -v xdpyinfo >/dev/null 2>&1; then
            timeout 2 xdpyinfo -display "$display" >/dev/null 2>&1 || continue
        fi
        echo "$display"
        return
    done
    echo ":0"
}

case "${1:-}" in
    Check)
        echo "repo:     $REPO_ROOT"
        echo "engine:   $UE_ROOT"
        echo "project:  $PROJECT"
        echo "target:   $TARGET $PLATFORM $CONFIGURATION"
        echo "display:  $(resolve_display)"
        echo
        [[ -f "$PROJECT" ]] || { echo "error: 工程文件缺失" >&2; exit 1; }
        if [[ -x "$EDITOR" ]]; then
            echo "editor:   已存在"
        else
            echo "editor:   尚未编译（先运行 $0 BuildEditor）"
        fi
        if [[ -f "$CLANG_DB" ]]; then
            echo "clang db: $CLANG_DB"
        else
            echo "clang db: 缺失（运行 $0 GenerateClangDatabase）"
        fi
        if command -v gdbserver >/dev/null 2>&1; then
            echo "debug:    gdbserver 可用（$0 DebugServer，端口 $GDB_PORT）"
        else
            echo "debug:    未安装 gdbserver，只能用 launch 方式调试"
        fi
        if [[ -n "$QUERY_DRIVER" ]]; then
            echo "query-driver: $QUERY_DRIVER"
            echo "  settings.json 中 clangd.arguments 若报系统头缺失，加上："
            echo "  \"--query-driver=$QUERY_DRIVER\""
        else
            echo "query-driver: 未在引擎目录下找到 HostLinux clang++"
        fi
        SAVED_SETTINGS="$PROJECT_DIR/Saved/Config/LinuxEditor/EditorPerProjectUserSettings.ini"
        if [[ -f "$SAVED_SETTINGS" ]] && grep -q "ModelContextProtocol" "$SAVED_SETTINGS"; then
            echo
            echo "注意: $SAVED_SETTINGS 含 ModelContextProtocol 段，"
            echo "      它会覆盖 Config/DefaultEditorPerProjectUserSettings.ini 里的 MCP 设置。"
        fi
        ;;
    BuildEditor)
        require_project
        refresh_clang_database_if_stale
        "$BUILD_SH" "$TARGET" "$PLATFORM" "$CONFIGURATION" \
            "-Project=$PROJECT" -WaitMutex -NoHotReloadFromIDE -Progress
        ;;
    GenerateClangDatabase)
        require_project
        generate_clang_database
        ;;
    WatchClangDatabase)
        # VS Code 的后台任务：只在源码清单（增删/改名/C# 依赖）变化时重建数据库，
        # 内容编辑不触发。单次生成约 3 秒，轮询开销只有一次 find。
        require_project
        refresh_clang_database_if_stale || true
        echo "监听 $PROJECT_DIR 下的源码清单，变化时重建编译数据库（Ctrl+C 退出）..." >&2
        while true; do
            sleep 5
            refresh_clang_database_if_stale || echo "warn: 编译数据库刷新失败，稍后重试" >&2
        done
        ;;
    GenerateProjectFiles)
        require_project
        "$GENERATE_PROJECTS_SH" "-project=$PROJECT" -game -engine -progress
        ;;
    LaunchEditor)
        require_project
        DISPLAY="$(resolve_display)" \
        XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}" \
            "$EDITOR" "$PROJECT" -log
        ;;
    DebugServer)
        require_project
        command -v gdbserver >/dev/null 2>&1 || {
            echo "error: 未安装 gdbserver" >&2
            exit 1
        }
        echo "gdbserver 监听 127.0.0.1:$GDB_PORT；在 VS Code 里用 UE: Attach 调试配置连接。" >&2
        DISPLAY="$(resolve_display)" \
        XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}" \
            gdbserver --once --no-startup-with-shell "127.0.0.1:$GDB_PORT" \
                "$EDITOR" "$PROJECT" -log
        ;;
    RunTests)
        require_project
        "$EDITOR" "$PROJECT" -unattended -nop4 -nosplash -NullRHI -log \
            -ExecCmds="Automation RunTests MotionWeaver;Quit"
        ;;
    *)
        echo "usage: $0 {Check|BuildEditor|GenerateClangDatabase|WatchClangDatabase|GenerateProjectFiles|LaunchEditor|DebugServer|RunTests}" >&2
        exit 2
        ;;
esac
