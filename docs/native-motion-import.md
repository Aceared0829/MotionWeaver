# 独立原生运动导入与标准化

2026-10-18：本页仍描述保留来源骨架的导入层。后续固定目标骨架转换已实现为独立的 [UE → SOMA 重定向步骤](uefn-soma-retarget.md)，输出位于 `processed/gameanimation_soma`，不改变本页的原生存储契约。

> 实现范围：来源读取 → 原生运动 → 坐标、单位、时间标准化 → 原生数据审核。输出仍使用来源骨架，跨骨架重定向尚未实现。2026-10-17 用户确认：统一训练目标为 SOMA uniform 固定人体骨架，UEFN 导出源骨架数据作为独立镜像来源保留，详见[训练骨架方向](soma-training-data-direction.md)。

> 2026-10-17 恢复范围：从本聊天历史补丁还原 Python 运动契约、读写、标准化、格式适配、批次入口和原生审核接线及历史测试。CUE4Parse .NET 提取器源码不在本次恢复范围，下面的重新提取命令需要另行提供已编译提取器。已有 raw/processed 数据回读和审核无需提取器或 UE 环境。后面的 2026-10-15 CR 与浏览器验证是历史记录，当前恢复验证单独记录。

## 代码架构

```text
data/runtime/native_motion.py        数据类型与校验
data/runtime/native_motion_io.py     共同文件格式、共享骨架与回读
data/runtime/importers/bvh.py        复用现有 BVH 解析和局部轨道适配
data/runtime/importers/gltf.py       glTF 2.0/GLB 骨架与动画读取
data/runtime/importers/fbx.py        Blender Headless FBX 调用
data/runtime/normalize_motion.py     共用空间转换与采样
data/tools/import_motion_dataset.py  Python 批次编排
外部 --extractor                    CUE4Parse .NET 提取器边界（源码尚未恢复）
data/tools/read_fbx_native.py        Blender 环境内的 FBX 求值脚本
```

Python 的上述读取与标准化模块只需要 NumPy/SciPy，不导入 Torch、UE Python 或训练模型。UE 提取是独立子进程边界。FBX 额外需要可用的 Blender 可执行文件；其脚本在 Blender 自身 Python 中运行。

## 数据契约

`NativeMotion` 保存来源骨架参考局部 TRS、逐骨骼的独立平移/旋转/缩放时间键、单位与轴基、来源哈希、Root 索引和 Additive 类型。缺失通道保持空数组，不以零值填充原始轨道。参考姿态来自格式本身或明确来源配置，不取动作首帧替代。

`SampledMotion` 保存米制右手 Motion 空间的局部 TRS、固定 FPS 时间网格和 `[J,3]` 通道存在标记。缺失通道以参考局部变换求值，并保持掩码为 false。Rotation LINEAR 使用 Slerp，STEP 保持前一个键；平移与缩放按各自时间键插值，轨道覆盖范围外保持首末键。

标准化保留源拓扑、骨长和独立 Root，不执行骨骼语义映射、骨长统一、Root 从 Pelvis 推导或接触修正。非单位正缩放保留；零/负缩放、shear 及未支持的插值明确拒绝。轴基目前接受带符号的轴置换。

时间从 0 开始，以 `1/fps` 为固定间隔。浮点源时长允许 `1e-6` 秒端点容差，避免 UE float32 时长使整数采样末帧丢失；不拉伸时间、不额外补齐到训练窗口。输出同时记录源时长和最后采样时刻。

## E 盘目录

```text
E:/MotionWeaverData/
├── raw/
│   └── gameanimation/<run-id>/
│       ├── skeletons/<sha256>/
│       │   ├── skeleton.json
│       │   └── arrays.bin
│       └── clips/<clip-id>/
│           ├── motion.json          # 引用共享骨架
│           └── arrays.bin           # 仅该动画的轨道
├── processed/
│   └── gameanimation/<run-id>/
│       ├── skeletons/<sha256>/       # 标准化参考骨架，只存一份
│       └── clips/<clip-id>/          # 标准化时间与轨道
└── reports/
    └── gameanimation/<run-id>/
        ├── input-manifest.json
        ├── extract-request.json
        ├── extract-request.json.results.json
        ├── extract.log
        ├── clips.jsonl              # 每完成一条立即追加
        └── run.json                 # 批次最终汇总
```

`raw/` 是导出的原生运动，原工程文件仍保留在 `D:/GameAnimation`。其他数据集使用不同 dataset 目录。新运行使用新 `run-id`，已有版本拒绝覆盖。骨架共享限于同一个版本，原生和标准化骨架分别存储，因为它们的单位和空间不同。

同一来源重新标准化时使用 `format: native` 引用既有 raw 动画，避免再次复制原生轨道；报告中的每条 `raw` 路径指向实际输入。此时批次级 `raw` 为 null，表示没有生成新的 raw 版本。

交换协议为 `motionweaver_motion` v1。JSON 数组描述符包含字节偏移、形状和数据类型，`arrays.bin` 使用小端 float64、int64 或布尔数组。共享骨架引用只能位于同一数据版本的 `skeletons/` 下，并校验内容签名；读取不使用 pickle。数组数据一次按片段读入，避免逐数组反复打开 E 盘文件。

## 使用方式

重新提取需要先恢复 CUE4Parse 提取器源码并构建 Release，或显式提供现有的已编译提取器。下列命令从仓库根目录运行；仅回读已有数据时跳过提取步骤。

### UE 项目小样本

```powershell
./.venv/Scripts/python.exe -m data.tools.import_motion_dataset `
  --ue-project D:/GameAnimation `
  --engine GAME_UE5_8 `
  --limit 3 `
  --data-root E:/MotionWeaverData `
  --extractor data/tools/ue_asset_extract/bin/Release/net10.0/UEAssetExtract.dll
```

扫描器遍历项目 `Content` 的全部 `.uasset`，不按目录或名称前缀筛掉资产；是否是 AnimSequence 由 CUE4Parse 实际类型判断。非 AnimSequence、未支持的 Additive 或曲线类型会单独记录，不能将这些条目计作已导出的动画。清单记录候选数和限制；插件 Content、容器输入使用显式清单。`--limit` 只用于项目扫描，候选可能包含非动画，正式导出全部项目 Content 时移除该参数。

提取阶段每个资产的结果同步写入 `extract-request.json.results.jsonl`，每 25 项向 `extract.log` 写入进度；完成时汇总到 `.results.json`。UE 的 Linear/Step 插值分别写为 LINEAR/STEP，不能将 Step 动画按平滑轨道解释。

### 外部格式清单

```json
{
  "dataset": "external_pilot",
  "clips": [
    {
      "format": "bvh",
      "path": "E:/ExternalMotions/walk.bvh",
      "profile": {
        "meters_per_unit": 0.01,
        "basis": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        "root_joint": "Root",
        "absolute_position_joints": []
      }
    },
    {
      "format": "gltf",
      "path": "E:/ExternalMotions/character.glb",
      "animation_id": 0,
      "skin_index": 0
    }
  ]
}
```

路径和轴基是格式示例，须替换为实际来源。BVH 必须显式提供单位和轴基；`root_joint` 仅在来源存在已确认独立 Root 时填写，Hips 不自动当作 Actor Root。`absolute_position_joints` 用于明确那些平移通道已包含绝对局部位置的来源，例如经核对的 SOMA Hips；只在来源语义确认后填写。

```powershell
./.venv/Scripts/python.exe -m data.tools.import_motion_dataset `
  --manifest path/to/input.json --data-root E:/MotionWeaverData --fps 30
```

glTF 支持 float32 非 sparse 动画 accessor、LINEAR/STEP 通道、GLB/data URI/同目录依赖，保留 skin 祖先节点及其变换。CUBICSPLINE、必需扩展、morph 动画和不能分解为正缩放 TRS 的节点明确拒绝；动画中不属于所选 skin 及祖先的节点在来源信息中记录。

FBX 条目使用 `format: fbx`、`path` 和可选 Action 名称 `animation_id`，运行时显式传入 `--blender <blender.exe>`。当前支持单 Armature 和明确的单 Action/slot，按 Blender 导入后的源帧率求值。FBX 输出是已求值的局部运动，所有已求值通道有数据，不宣称保留原文件的缺失轨道分布；来源信息记录该差异。当前机器未找到可用 Blender 程序，本轮未完成实际 FBX 导入验证。

## 验证和已知边界

```powershell
./.venv/Scripts/python.exe -m unittest discover -s tests -p test_motion_import.py -v
./.venv/Scripts/python.exe -m unittest discover -s tests -p test_bvh_reader.py -v
```

恢复的历史导入测试采用四组参数化输入，覆盖 BVH 世界 FK 一致性、glTF 异步通道/STEP/祖先、骨架共享与批次回读，以及 Additive 拒绝且保留来源。

已有 UE 导出汇总位于 `E:/MotionWeaverData/reports/gameanimation/20261015-full-export/acceptance.json`。支持原生运动读取不代表完成跨骨架重定向、训练发布或游戏生成验收。

## 2026-10-15 CR 与完整项目导出

本轮 CR 修复了按目录/前缀扫描导致的漏项、控制器与骨骼名称大小写不一致、UE Step 插值被写为 LINEAR，以及编辑器动画末帧容差与 Python 标准化器不一致的问题。无骨骼变换的纯面部/属性曲线现在明确记录为 unsupported；逐资产提取结果即时落盘，未提取条目不再报告不存在的 raw 输出路径。Blender 专用脚本的 bpy 导入诊断和本次导入代码的 lint 问题也已处理。

按 VS Code 工作区的 basic 类型检查、解释器和导入路径检查了 241 个 Python 文件，结果为 0 错误、0 警告；导入链路 Ruff 检查及四组契约验收通过。C# Release 构建为 0 错误，仍保留未修改的上游源码中的 6 条 nullable 警告。这里记录的是静态检查与构建结果，不是对编辑器 Problems 面板的截图检查。

额外的全库 Ruff 扫描发现 144 条历史提示；按用户确认的范围，仅处理 3 处未使用导入、1 处未使用局部变量和 1 处循环闭包绑定提示。复查后这五项清零，剩余 139 条导入排序、命名、类型风格及其他建议不在本轮整理范围。

完整扫描 `D:/GameAnimation/Content` 的 3,890 个资产后，得到 1,970 条可提取并标准化的 AnimSequence，涉及 6 种来源骨架签名。37 条 Additive 与 1 条纯面部曲线不作为绝对骨骼运动导出；137 个 Montage 属于编排资产，未烘焙成独立运动。其余非 AnimSequence 资产同样记录实际类型。骨架仍按各数据版本共享，保留来源拓扑，尚未映射到 UEFN 79 骨。

数据版本为 `20261015-full-export`、`20261015-full-export-retry` 和 `20261015-full-export-retry-2`。主批次历史报告保留修复前失败记录；最终结果按资产路径合并补跑，并用修复版本替代存在末帧问题的旧片段，不重复计算动画数量。有效动画及其 raw/processed 路径以 `E:/MotionWeaverData/reports/gameanimation/20261015-full-export/export-index.csv` 为入口，完整回读和内容哈希审计以同目录下的 `acceptance.json` 为准。

最终回读审计通过：1,970 条有效动画、226,502 帧，raw/processed 回读错误为 0，来源端点缺失为 0；1,976 个来源文件及约 3.96 GB 的片段文件内容通过哈希校验。插值类型包含 1,968 条 LINEAR 和 2 条 STEP。6 种来源骨架的节点数为 91、161、201、354、363、875；这些是来源节点数，不能当作已完成 UEFN 79 骨统一映射。

## 人工审核这批动画

复用既有 Motion Review 页面，通过 `data/tools/native_review_store.py` 读取合并后的有效索引。查看的是标准化局部 TRS 经 FK 得到的来源骨架，保留父级缩放及 Root 位移；仅将显示坐标换为查看器使用的 UE 轴和厘米，不改动数据文件。

```powershell
./.venv/Scripts/python.exe -m data.tools.motion_review_server `
  --library E:/MotionWeaverData `
  --native-index E:/MotionWeaverData/reports/gameanimation/20261015-full-export/export-index.csv `
  --port 8766
```

浏览器打开 `http://127.0.0.1:8766/`。1,970 条动画分为 40 页，每页最多 50 条；行走动作优先展示，搜索框筛选当前页。点击播放或空格播放/暂停，左右键逐帧，F 聚焦当前帧，可调整速度、视角、骨骼名称及轨迹显示。

可以标记“人工通过／有问题／待定”并保存备注。结论保存在 `E:/MotionWeaverData/reviews/gameanimation/20261015-full-export/decisions.sqlite3`；审核名单按索引和片段内容哈希绑定版本，加载时检查 raw/processed 文件与共享骨架。页面的“导出本页审核记录”仅导出当前页记录。人工标记不触发训练发布或数据删除。

四组历史参数化验收覆盖含非均匀缩放的 FK、查看器数据、审核记录持久化和数据改动拒绝。当前为来源骨架线框审核；跨骨架重定向、蒙皮和场景效果属于后续验收。

## 2026-10-17 恢复验证

恢复模块已回读现有全部 1,970 条 UE 动画、226,502 帧，六类来源骨架各抽一条重新标准化并对照已有结果，错误为 0。审核队列哈希保持一致，1,970 条人工通过记录保留，数据库 `quick_check=ok`。恢复后的 HTTP 服务验证了首尾页和真实动画端点；历史导入、审核、BVH、查看器与预加载回归以及关键 Ruff 检查通过。

另用一条真实 SOMA uniform BVH 验证了 78 节点读取、标准化及共享骨架往返。具体结果和边界见[恢复验证汇总](experiments/2026-10-17-native-motion-restoration.json)，Root/Hips 与采样时间的后续决策见[训练骨架方向](soma-training-data-direction.md)。
