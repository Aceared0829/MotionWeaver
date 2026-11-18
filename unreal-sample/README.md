# Unreal 实验宿主与资产边界

`AIMotionSample/AIMotionSample.uproject` 是 UE 5.8 的实验宿主，用于逐步测试 UE 内实际推理及动画工具链，验证后再考虑正式使用；**不承担动作数据导入、清洗或数据集处理**。

## 当前状态：GASP 副本 + 已挂载 MotionWeaver 插件

`AIMotionSample` 是 Epic 官方 GASP（Game Animation Sample）工程的副本，`EpicSampleNameHash` 为 `1673045636`：

| 项 | 当前状态 |
| --- | --- |
| `Content/` | 3,898 个文件，与 `D:/GameAnimationSample/Content` 一致；不入 Git |
| `Config/` | 7 个 ini 随副本带入；`DefaultEditor.ini` 由 UE 写入本机预览配置，不入 Git |
| `Source/` | UE 生成的空项目模块（`AIMotionSample` + 两个 Target），已入库 |
| `Plugins/MotionWeaver/` | 已挂载并在 `.uproject` 中启用；本目录是唯一开发源 |
| `Plugins/Developer/RiderLink/` | Rider 自动装入的 IDE 桥接插件（约 500 文件 / 140 MB），**不入 Git** |
| `Binaries/`、`Intermediate/`、`Saved/`、`DerivedDataCache/` | UE 生成物，不入 Git |

该宿主目前能编译 UE 项目模块与 `MotionWeaver` 插件，但项目模块仍是 UE 新建工程的空壳，**尚不含 CMC 角色与动画逻辑**，也尚未接入模型包或推理 AnimGraph。默认地图为 `/Game/Levels/DefaultLevel`。

上一代宿主 `UMWSamplePreview` 已于 2026-11 移除：其 52 个 git 追踪文件（`Source/` 45 个 C++/配置文件、`Plugins/MotionWeaver/`、`MOTIONMATCHING_MIGRATION.md` 与 `Config/`）按维护者决定一并删除，不迁入 `AIMotionSample`。需要查阅这些代码时从 git 历史取回（`git show e7330af:unreal-sample/UMWSamplePreview/<路径>`）。

原 `MotionMatchingInCpp` 的 Gameplay 源码曾迁入 `UMWSamplePreview` 的项目模块，随该工程一并移除；调整目录不改变这些外部代码与动画资产的原有授权。

外部 `D:/GameAnimationSample` 是已有数据流程使用的工程，不迁入本宿主。

## Linux 开发环境（UE 5.8）

仓库只保存工程配置，不提交引擎、`Binaries/`、`Intermediate/` 与 `Saved/`。
Linux 侧统一通过 `scripts/ue.sh` 操作宿主工程：

```bash
export UE_ROOT=/path/to/UE_5.8     # 或写入被忽略的 .vscode/ue-root
bash scripts/ue.sh Check
bash scripts/ue.sh BuildEditor      # 首次构建，同时按源码清单刷新 clangd 索引
bash scripts/ue.sh LaunchEditor
```

| 命令 | 作用 |
| --- | --- |
| `Check` | 打印引擎、工程、产物路径与 clangd `query-driver` |
| `BuildEditor` | `Build.sh AIMotionSampleEditor Linux Development` |
| `GenerateClangDatabase` | 生成 `unreal-sample/AIMotionSample/.vscode/compile_commands.json` |
| `WatchClangDatabase` | 后台轮询源码清单，增删/改名时自动重建数据库（VS Code 后台任务用） |
| `GenerateProjectFiles` | 生成 IDE 工程文件 |
| `LaunchEditor` | 带 `DISPLAY` / `XDG_RUNTIME_DIR` 启动编辑器 |
| `RunTests` | 以 NullRHI 跑 `MotionWeaver` 自动化测试 |

`.vscode/tasks.json` 里有同名任务。clangd 只读 UBT 生成的数据库；新增、改名或
删除 C++ 文件后重跑 `GenerateClangDatabase`，或在 VS Code 里允许自动任务、
由 `UE: watch clang database` 常驻监听（只比较文件清单，改内容不重建）。
若 clangd 报系统头缺失，把
`Check` 输出的 `--query-driver=...` 加进个人 VSCode User Settings 的
`clangd.arguments`，不要把绝对引擎路径提交到仓库。

编辑器启动后由 `Config/DefaultEditorPerProjectUserSettings.ini` 自动开启 UE 内置
MCP 服务（`http://127.0.0.1:8000/mcp`），仓库根的 `.mcp.json` 与
`.vscode/mcp.json` 已指向它。验证：

```bash
curl -sS -X POST http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}'
```

返回 JSON-RPC result 表示服务在线；connection refused 时先确认编辑器在运行、
`ModelContextProtocol` 插件已编译，以及 `Saved/Config/LinuxEditor/` 下是否有
覆盖 MCP 设置的旧文件。

### 运行与调试（VS Code）

`.vscode/launch.json` 提供四个 UE 配置：

| 配置 | 用途 |
| --- | --- |
| `UE: Debug editor (Linux)` | 以 gdb 启动编辑器，启动前自动增量构建 |
| `UE: Run editor (Linux, no debug)` | 不挂调试器直接运行 |
| `UE: Attach to editor (gdbserver :1234)` | 附加到由任务 `UE: launch editor under gdbserver` 启动的进程 |
| `UE: Debug plugin tests (NullRHI)` | 以 NullRHI 启动并调试 `MotionWeaver` 自动化测试 |

引擎位置由 `scripts/ue.sh` 维护的 `.vscode/ue-engine` 链接提供，`launch.json`
里不出现绝对路径。本机 `kernel.yama.ptrace_scope=1` 时会拒绝按 PID 直接附加，
所以附加统一走 gdbserver；若要用 `cppdbg` 的 process attach，需要
`sudo sysctl kernel.yama.ptrace_scope=0`（重启后失效）。

插件模块在编辑器启动后才加载，配置里已打开 pending breakpoints，断点可以直接
下在 `Plugins/MotionWeaver/Source/` 与引擎源码上。
第一次启动或附加时 gdb 要加载引擎与插件的调试符号，耗时会明显长于普通启动；
附加配置已用 `set sysroot /` 从本地读取 `.debug`，不再经 gdbserver 传输。

Windows 侧仍用
`Engine\Build\BatchFiles\Build.bat AIMotionSampleEditor Win64 Development`；
`.clangd`、MCP 配置与 PIE 输入设置跨平台通用。

## 已移除的历史 GASP 宿主实验

`MotionWeaverCMCPreview` 插件及其 4 个驱动脚本（`create_cmc_preview.py`、`add_stance_model_preview.py`、`verify_cmc_preview.py`、`Run-StanceModelPreview.ps1`）已于 2026-11 移除。它们只服务于外部 `D:/GameAnimationSample` 宿主的 CMC 观测与 B1 站蹲预览，生成的 `/MotionWeaverCMCPreview/` 资产从未入库。当时的结论仍可从执行记录查阅：[UE CMC 单机演示实验](../docs/experiments/2026-09-28-ue-cmc-preview.md)、[UE B1 在线预览实验](../docs/experiments/2026-09-28-ue-b1-online-preview.md)；代码本体从 git 历史取回（`git show e7330af:unreal-script/MotionWeaverCMCPreview/...`）。

`inspect_gasp_level.py` 保留：它是只读的 GASP 关卡与 CMC 角色检查脚本，不依赖任何已移除的插件。
