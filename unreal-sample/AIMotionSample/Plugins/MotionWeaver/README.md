# MotionWeaver 插件

UE 5.8 动画工具链插件。当前提供「姿势代理（Pose Proxy）」这一条链路：
从动画预览的暂停帧抽出全身姿态，存成独立的 `.uasset`，拖进关卡即可显示。
不接推理、不做骨架转换、不碰 AnimGraph。

本文件说明源码怎么分目录，以及新功能该放哪。

## 目录

```text
Source/
├── MotionWeaver/                 # Runtime 模块：打包后也要活着的部分
│   ├── Public/
│   │   ├── MotionWeaver.h        # 模块入口
│   │   ├── Core/                 # 横切基础设施（日志等）
│   │   └── Pose/                 # 姿势代理这一条能力
│   │       ├── MotionWeaverPoseTypes.h       # 纯数据结构（USTRUCT，无依赖）
│   │       ├── MotionWeaverPoseAsset.h       # UDataAsset，落盘的那份数据
│   │       ├── MotionWeaverPoseComponent.h   # 显示组件（PoseableMeshComponent）
│   │       └── MotionWeaverPoseActor.h       # 关卡载体 Actor
│   └── Private/                  # 与 Public/ 同构
└── MotionWeaverEditor/           # UncookedOnly 模块：编辑器专有能力
    ├── Public/
    │   ├── MotionWeaverEditor.h  # 模块入口：注册资产类型动作与工具栏
    │   └── Pose/
    │       ├── Assets/           # 资产在内容浏览器里的表现：类型动作、编辑器、拖放工厂
    │       ├── Extraction/       # 从预览抽帧：抽取逻辑 + 工具栏入口与通知
    │       └── Viewport/         # 资产编辑器的预览视口（Slate + 视口客户端）
    └── Private/                  # 与 Public/ 同构
```

数据流：`Extraction` 读出预览组件 → 写进 `PoseAsset`（`PoseTypes` 定义结构）
→ 关卡里由 `PoseActor` + `PoseComponent` 显示，资产编辑器里由 `Viewport` 显示。
`Assets` 里的类型动作负责双击打开编辑器、工厂负责拖放生成 Actor。

## 加新东西时放哪

- **运行时也要用的新能力**（例如新的数据资产、新的组件、动画系统运行时部分）：
  `Source/MotionWeaver/Public/<能力名>/`，同名 `Private/<能力名>/`。一条能力一个目录，
  不要把新文件平铺进 `Pose/` 或模块根目录。
- **只在编辑器里用的能力**（资产类型动作、工具栏、面板、预览视口）：
  `Source/MotionWeaverEditor/` 下按 `<能力名>/<子职责>/` 分。
- **两个模块都要引用同一份数据结构**：结构定义放 Runtime 模块，Editor 只 `#include` 它。
  Editor 模块不能被 Runtime 反向依赖（`MotionWeaver` 的 Build.cs 里没有、也不该有 `UnrealEd`）。

## 约定

- 文件夹只做分类，不新建 UE 模块。新增模块要改 `.uplugin`、两个 `Build.cs` 和所有宿主的
  `*.Target.cs`，代价远大于目录的收益；只有在「打包体积」或「编辑器依赖隔离」真的出问题时再拆模块。
- `Public/` 与 `Private/` 的子目录结构保持一致，找 `X.cpp` 时它的头文件永远在镜像路径上。
- 跨子目录引用用模块内相对路径（`#include "Pose/MotionWeaverPoseAsset.h"`），
  同目录内用短名。UHT 生成的 `*.generated.h` 永远短名，不跟目录。
- 头文件在 `Public/` 下被**移动过目录**时，引用它的其他模块（宿主工程里的 `#include`）要同步改；
  本仓库目前只有插件内部引用，宿主工程未直接包含这些头文件。
