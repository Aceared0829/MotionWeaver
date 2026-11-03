# RefPose 生态与竞品调研（MotionBricks / AnimGen / 同类开源方案）

> 调研日期：2026-11-02
> 范围：MotionWeaver 上游 MotionBricks 的第三方生态、UE 5.8 官方 AnimGen 定位、可用于「条件姿态生成 / 运动补间」的开源方案对照。
> 方法：仓库事实来自本机文件与 GitHub REST API / raw 文件；文档事实来自 Epic 官方文档页与 arXiv。**凡属推断的段落均以【推断】开头**，未标注者是查到的原文或 API 返回值。
> 未做的事：没有运行任何模型、没有下载权重、没有修改仓库源码。

---

## 结论摘要

1. **MotionBricks 是「论文 + 开源代码库 + 预训练权重」三件套，代码与权重分属两套许可。** 代码 Apache-2.0，权重 NVIDIA Open Model Agreement。**商用与闭源分发是被明确允许的**，但有三个硬约束：随附协议副本、随附指定署名句、承认授权是 revocable。**这决定它能进 UE 插件，但不能被表述成自有成果。**

2. **官方至今没有发布 UE 插件，第三方只出现一个骨架级项目。** NVIDIA 仓库 issue #131 中作者回复「正在内部讨论是否发布 UE5 demo」后关闭；GitHub 上 `motionbricks` + `unreal` 只有 1 个结果 `SarthakKalla/GR00T-Unreal-Plugin`（★3），其 README 自述 sidecar 是 Stub、插件是 Scaffold、可玩 demo 为 Not started。**UE 侧的真空是真实存在的。**

3. **生态热点不在 UE，在「把模型搬出 NVIDIA 栈」。** 第三方投入集中在推理后端：C++/GGML 移植（★348，Apache-2.0）、three.js 网页版（★19）、Blender 插件（★10，GPL-3.0）、Unity 桥（★0）、Rust/MLX/CoreML 绑定，以及 Hugging Face 上 5 个 Space。**社区要的是「能离线、能进引擎、能脱离 TensorRT」，与我们的 UE 落地诉求同向。**

4. **「官方 AnimGen」是 Epic 第一方实验插件，不是产品名，也不是补间工具。** 本机实存 `D:\UE_5.8\Engine\Plugins\Experimental\Animation\AnimGen`，Epic 官方文档原文说它「uses flow-matching and a neural-network to auto-regressively produce the character's pose frame by frame」。它由 **Behavior（TrajectoryFollow / VelocityFollow / MoveToTarget / Tagged / Multi…）** 这类**连续控制信号**驱动，**不是靠稀疏参考帧锚点驱动**。对 `in-between / Keyframe / Anchor / ReferencePose` 在其头文件中 grep 为 **0 命中**。**结论：它不能直接做我们的 refpose 任务。**

5. **同类方案里，同时满足「条件补间 + 可商用 + 可导 ONNX」的只有 NVIDIA 自家三兄弟。** CondMDI、MoMask、MDM、AnyTop 都是 MIT，但**全部是 SMPL/HumanML3D 20 fps 的离线模型，无 ONNX、无 UE 路径**；MotionDiffuse 是 S-Lab 非商用，ASE 是 NVIDIA 非商用，PULSE 与 MoConVQ **根本没有许可证文件**——这三类直接用会踩雷。

6. **判断：继续自研 refpose，把「数据表示 + 评测口径」与上游对齐，把「UE 承载层」做成 AnimGen 同构的形态。** 78 骨 / 120 fps / RMSE 2.17–5.28 cm 是别人没有的（官方 30 骨 30 fps），换开源等于降规格重做；但脚滑 6.9–8.7× 与「未做 UE 实机验证」这两项，正好是开源方案有现成答案的地方——**该抄方法，不该抄权重。**

---

## A. 有没有第三方在用 MotionBricks

### A.1 MotionBricks 到底是什么

| 维度 | 事实 | 证据 |
|---|---|---|
| 性质 | SIGGRAPH 2026 / ACM TOG 论文 + 官方代码库 + 预训练权重 | arXiv 页面 Comments 字段「ACM Transactions on Graphics; SIGGRAPH 2026」：https://arxiv.org/abs/2604.24833 |
| 论文标题 | MotionBricks: Scalable Real-Time Motions with Modular Latent Generative Model and Smart Primitives | 同上；本仓库 `CITATION.cff:28` |
| 作者 | Tingwu Wang 等 16 人（含 Xue Bin Peng、Yuke Zhu、Simon Yuen） | `CITATION.cff:29-61` |
| 首版时间 | v1 2026-04-27 | arXiv Submission history |
| 官方仓库（新） | `NVlabs/motionbricks`，★800 / fork 73，创建 2026-04-21，最后 push 2026-10-26，最新 commit `54fc563a`「v0.2.0」(2026-10-23) | GitHub API `/repos/NVlabs/motionbricks`、`/commits` |
| 官方仓库（旧宿主） | `NVlabs/GR00T-WholeBodyControl`，★3865 / fork 630，最后 push 2026-10-26 | GitHub API `/repos/NVlabs/GR00T-WholeBodyControl` |
| 本仓库关系 | 派生自 GR00T 仓库中的 motionbricks 组件，共同祖先 `4141c34` | `引用与许可说明.md:11`、`NOTICE:7` |
| 能力 | 给定 4 个最近姿态 + 1–4 个目标姿态，生成 0.6–1.9 秒过渡，30 Hz 运行，声称 15,000 FPS / 2 ms | `NVlabs/motionbricks` README「How MotionBricks works」；arXiv 摘要 |
| 是否接受贡献 | 「This project is currently not accepting contributions.」 | `NVlabs/motionbricks` README:271 |

**能力定性：** 官方 README 首段原文是「MotionBricks is a modular latent generative model ... It **fills in the motion between four recent poses and up to four target poses**.」即**双端约束的补间**。这与 refpose 的任务定义**同族**，差别在规格：官方 30 骨（SOMA）或 34 关节（G1）/ 30 fps，我们 78 骨 / 120 fps。

### A.2 许可证：逐条原文

| 资产 | 许可证 | 原文出处 |
|---|---|---|
| **源代码**（含文档内代码片段） | **Apache-2.0** | `NVlabs/motionbricks` README:247：「**Code:** [Apache 2.0](LICENSE), including code examples and snippets in the documentation.」 |
| **文档与媒体**（图片/视频/截图） | **CC-BY-4.0** | README:248：「NVIDIA-authored documentation text and media ... use Creative Commons Attribution 4.0 International (CC-BY-4.0).」 |
| **模型权重**（SOMA + G1 检查点、ONNX） | **NVIDIA Open Model Agreement** | README:249：「**Models:** [NVIDIA Open Model Agreement]... It permits commercial use and redistribution of modified models, subject to the agreement's terms.」；`motionbricks/assets/onnx/LICENSE.md`：「The MotionBricks ONNX models in this folder are licensed under the NVIDIA Open Model Agreement.」 |
| **示例动作数据**（BVH / 重定向 G1 CSV） | **NVIDIA Sample Data Evaluation License**（仅评估测试，禁止再分发） | README:250；`assets/motions/LICENSE.txt` 第 1 条「solely to evaluate and test NVIDIA technologies」，第 2 条 (a)(c) 明文禁止其他用途与再分发 |

**权重许可具体条款（本仓库继承的 `LICENSE` 内嵌 2025-10-24 版全文）：**

- 授权范围 `LICENSE:79-83`：「grants You a perpetual, worldwide, non-exclusive, no-charge, royalty-free, **revocable** license to ... use, create derivative works of, make, have made, **sell, offer for sale, distribute** and import the Model.」
- 再分发条件 `LICENSE:93-112`：
  - 3(a)：「You must include a copy of this Agreement.」
  - 3(b)：「You must include the following attribution notice ... **Licensed by NVIDIA Corporation under the NVIDIA Open Model License.**」
  - 3(c)：若分发 Cosmos 模型须标注「Built on NVIDIA Cosmos」（是否触发取决于实际权重来源）
  - 3(d)：「You may add Your own copyright statement to Your modifications and **may provide additional or different license terms and conditions** for use, reproduction, or distribution of Your modifications or for any Derivative Models as a whole」← **闭源分发衍生模型的直接依据**
- 商标 `LICENSE:125-130`：不得使用 NVIDIA 商号商标，除非为说明来源。
- 终止 `LICENSE:69-77`：主动发起专利诉讼或绕过安全护栏，授权即终止。
- 输出归属 `LICENSE:89-91`：「NVIDIA owns the original Model and NVIDIA's Derivative Models. **You own Your Derivative Models. NVIDIA makes no claim of ownership to outputs.**」

### A.3 直接回答：能不能在 UE 插件里商用 / 闭源分发？

**可以，但必须满足四条：**

1. **代码层可以闭源。** Apache-2.0 允许以任意条款分发衍生作品，只要求保留许可证副本、版权与署名声明（`引用与许可说明.md:35` 已引 Apache 4(b)：被修改文件须带醒目修改说明）。把代码编译进 UE 插件二进制并只发二进制，是允许的。
2. **权重层可以商用、可以改、可以再分发。** 依据 README:249 与 `LICENSE:79-83`。**但必须随附协议副本 + 那句署名**（`LICENSE:98-102`）。本仓库已在 `NOTICE:27` 放好该句，做法正确。
3. **不能把示例动作数据打包进产品。** 那是 `NVIDIA Sample Data Evaluation License`，第 2(c) 条明文禁止「sell, rent, sublicense, transfer, distribute」。**这是最容易踩的一条**：`inference/assets/gifs/*` 与 `assets/motions/*` 不能跟插件走。
4. **授权是 revocable 的，且不能借 NVIDIA 商标背书。** `LICENSE:80` 用的是 revocable 而非 irrevocable。**可以商用，但不适合当作不可撤销的长期底座写进商业承诺。**

> **【推断】** 若项目未来要商业化（而非仅实验/毕设/软著），**自有权重 + 自研训练管线**的价值远高于「免费用 NVIDIA 权重」。这与建议 1 同向。

### A.4 第三方生态盘点（经 GitHub API / HF API 核实）

GitHub 全站搜索 `motionbricks`：in:readme 共 **134** 个仓库，in:name 共 **28** 个。按与 UE 的相关度排序：

| 项目 | ★ | 许可 | 形态 | 最后 push | 判断 |
|---|---:|---|---|---|---|
| `SarthakKalla/GR00T-Unreal-Plugin` | 3 | Apache-2.0（`LICENSE` 实读） | UE 5.4–5.8 桥，Python sidecar + UDP/JSON | 2026-07-08 | **唯一 UE 项目，但是骨架**：README 状态表 sidecar=「Stub」、插件=「Scaffold」、可玩 demo=「Not started」 |
| `localai-org/motion-bricks.cpp` | **348** | Apache-2.0（实读全文） | C++23/GGML 移植，稳定 C ABI，CPU/Vulkan | 2026-10-19 | 生态中最认真的工程；可被引擎以 C ABI 调用 |
| `repalash/three-motionbricks` | 19 | Apache-2.0 | three.js 网页运行时 | 2026-10-31 | 浏览器侧参考实现 |
| `Aero-Ex/Nvidia_MotionBricks` | 15 | NOASSERTION | Python 复刻/封装 | 2026-08-26 | 未声明许可 |
| `hyun2xyz/motionbricks-blender` | 10 | **GPL-3.0** | Blender 4.2+ 插件 | 2026-10-31 | GPL 传染性，**不能进闭源商业管线** |
| `ninja9998877/motionbricks-unity-bridge` | 0 | Apache-2.0 | Unity C# + FastAPI sidecar | 2026-11-01 | Unity 侧唯一桥 |
| `ylwl1997/motionbricks-wiki` | 1 | 无 | Astro 社区文档站 motionbricks.wiki | 2026-10-14 | 社区指南，含 UE5 章节 |
| `limeflash/motionbricks-studio` | 2 | MIT | 关键姿态→离线动画工作流 | 2026-10-28 | 定位与我们最接近的工具态项目 |
| `neeboo/motionbricks-rs` / `-mlx` | 1 / 0 | NOASSERTION | Rust 绑定 / Apple MLX | 2026-10-13 | 后端多样性 |
| `cg25/motionbricks-coreml` | 0 | Apache-2.0 | Swift + Core ML | 2026-08-19 | Apple 平台 |
| `zeljkovranjes/sbox-motionbricks-weights` | 0 | 无（权重声明 NVIDIA Open） | s&box 引擎权重包 | 2026-08-19 | 游戏引擎侧尝试 |
| `apekshik/motionbricks-parkour-playground` | 0 | — | 浏览器驱动 G1 跑酷 | 2026-10-28 | demo |

**Hugging Face：**

- 官方模型 `nvidia/MotionBricks`（likes 115，tags 含 `arxiv:2604.24833`、`pose-conditioned`）：https://huggingface.co/nvidia/MotionBricks
- 第三方 Space 5 个（HF API `/api/spaces?search=motionbricks`）：`townbox/motionbricks-playground`（17）、`monoradio/motionbricks-live`（7）、`Jaya123/motionbricks-playground`、`townbox/motionbricks-sword-duel`、`townbox/motionbricks-athletics`
- 第三方权重重分发：`monoradio/motionbricks-soma-fp16-onnx`（`license:other`）、`suvadityamuk/motionbricks-ckpts`（`license:other`）、`Aero-Ex/NV_MotionBricks`

**官方对 UE 的态度（一手证据）：** `NVlabs/GR00T-WholeBodyControl` issue #131「MotionBricks: Are there any plans to release the UE5 interactive demo?」（2026-04-30 提出，状态 closed），作者 `tingwuwang` 于 2026-04-30T20:03:33Z 回复：

> 「we are currently discussion internally if we should release the UE5 demo. there's no conclusions yet but please stayed tune!」

https://github.com/NVlabs/GR00T-WholeBodyControl/issues/131

**→ 截至 2026-11-02，官方未发布 UE 插件；生态中唯一的 UE 项目尚未完成。**

### A.5 A 节结论

- **有生态，但不是 UE 生态。** 投入集中在推理后端（GGML / three.js / MLX / CoreML / Unity）与内容工具（Blender），**UE 侧近乎空白**。
- **许可上「能用」，但「能闭源商用」要接受 revocable 与署名义务**，示例数据必须排除。
- **最值得读而非抄的两份**：`localai-org/motion-bricks.cpp`（如何把这类模型做成无 Python 依赖的 C ABI）与 `SarthakKalla/GR00T-Unreal-Plugin` 的 `PLAN.md`（它把「为什么必须 sidecar」写清楚了：MotionBricks 依赖 PyTorch/TensorRT，UE 内嵌不现实）。

---

## B. 官方 AnimGen（UE 侧）

### B.1 定位：Epic 第一方实验插件，不是产品

| 事实 | 证据 |
|---|---|
| 本机实存 | `D:\UE_5.8\Engine\Plugins\Experimental\Animation\AnimGen\` |
| 清单 | `AnimGen.uplugin`：`FriendlyName`=AnimGen、`Category`=Animation、`CreatedBy`=Epic Games, Inc.、`EnabledByDefault`=false、`IsExperimentalVersion`=true、`VersionName`=0.1 |
| 模块 | `AnimGen`（Runtime）+ `AnimGenEditor`（UncookedOnly） |
| 依赖 | PythonFoundationPackages、PythonMLPackages、AnimationWarping、LearningCore、NNERuntimeBasicCpu、AnimDatabase、TensorBoard、DrawDebugLibrary、PoseSearch、UAF、UAFAnimGraph、UAFPoseSearch |
| 官方文档 | https://dev.epicgames.com/documentation/en-us/unreal-engine/API/Plugins/AnimGen （标题「AnimGen | Unreal Engine 5.8 Documentation」，Location 明写 `/Engine/Plugins/Experimental/Animation/AnimGen/Source/AnimGen/`） |

**核心技术描述（Epic 官方文档原文，`UAnimGenController` 类页）：**

> 「The Controller class represents a trained character controller that uses **flow-matching** and a neural-network to **auto-regressively produce the character's pose frame by frame**.」

https://dev.epicgames.com/documentation/en-us/unreal-engine/API/Plugins/AnimGen/UAnimGenController

**训练脚本佐证（本机文件）：** `Content\Python\train_controller.py:17-48` 定义 `DenoiserNetwork`（12 层 MLP、hidden 2048、GELU、残差），正是 flow-matching 去噪器的形状；`train_autoencoder.py:39` 从 `nne_runtime_basic_cpu_pytorch` 导入 `NeuralNetwork, SparseMixtureOfExperts, LipschiztLinear`。

### B.2 它解决哪类问题：靠 Behavior 驱动，不靠参考帧

`UAnimGenBehavior` 类注释（`AnimGenBehavior.h:17-23`）原文：

> 「A Behavior represents a specific control structure that can be used to control a character (for example - **a trajectory to follow, a target point to move towards**).」

内置 Behavior 全集（`AnimGenBehavior.h` grep `DisplayName`）：

| 行号 | Behavior | 注释原文 |
|---:|---|---|
| 62 | Uncontrolled Behavior | 「Produces a random-walk of the dataset.」 |
| 79 | Idle Behavior | 「Behavior for idling.」 |
| 86 | **Trajectory Follow** | 「following a trajectory which can either be generated from Mover/CharacterMovementComponent or a path to follow」 |
| 140 | **Velocity Follow** | 「following a velocity and angular velocity. More useful for offline generation.」 |
| 172 | **Move to Target** | 「moving toward a target with an optional facing direction」 |
| 281 | Tagged Behavior | 「supply tags to control the style of a sub-behavior」 |
| 331 | Optional Behavior | 「optionally provide controls to another Behavior」 |
| 377 | Encoded Behavior | 「encodes the controls of some sub-behavior」 |
| 452 | Multi Behavior | 「made up of multiple other behaviors」 |

Epic 文档页列出 `UAnimGenBehavior_TrajectoryInteraction`（「following a trajectory while also interacting with an object or prop」）。**本机已核实存在**：`AnimGenBehavior.h:207` 的 `DisplayName = "Trajectory Interaction Behavior"`，`:208` 的 `class UAnimGenBehavior_TrajectoryInteraction`，`:529-531` 还有配套的 `MakeTrajectoryInteractionBehaviorControl`。（2026-11-03 订正：此前写「未见」，是漏查了同一文件的后半段。）

**关键否证：** 对 `AnimGen\Source` 下全部 `.h` grep `in-between` / `InBetween` / `Keyframe` / `KeyFrame` / `Anchor` / `ReferencePose` / `bReference` → **全部 0 命中**。

**→ AnimGen 没有任何「稀疏参考帧/锚点」概念。** 它的控制输入是**逐帧可观测的连续信号**（轨迹、速度、目标点、标签、事件、枚举），训练时从 `AnimDatabase` 的帧区间采样（`AnimGenBehavior.h:38-39` 的 `MakeControlSets(...)`）。这与「给 0–2 帧硬参考、生成其余 22–24 帧」是两回事。

### B.3 能不能用来做「参考引导的姿态补间生成」？

**不能直接用来做。** 三点理由：

1. **控制语义不匹配**：无 anchor / reference-frame 输入通道，也无 mask 语义（「这帧用户硬指定，其余自由生成」）。
2. **它生成的是「控制信号→逐帧姿态」的策略**，不是「两端锚点→中间过渡」的插值。要让其接受锚点，须自写 `UAnimGenBehavior` 子类，把锚点编码进 control schema。
3. **数据侧要求不同**：依赖 `AnimDatabase`（逐帧姿态库 + frame attribute）与 `UAnimGenAutoEncoder` 联合训练，是「在自己数据库上重训一套控制器」，不是「加载现成模型推理」。

> **【推断】** 第 2 点机制上可行（`UAnimGenBehavior` 是 `BlueprintNativeEvent`，见 `AnimGenBehavior.h:24-58`），但**成本等于重写训练管线**：自定义 Behavior → 生成 control set → 重训 autoencoder + controller → 导出 LearnedNeuralNetworkData → 挂 AnimNode。好处是免费拿到 Epic 的运行时承载层（`AnimNode_AnimGenController` 已是动画工作线程安全的 AnimGraph 节点，可输出 curve / attribute / bone / AnimNotify，见 `AnimNode_AnimGenController.h:24-110`）。**但我们自己的 `AIAnimation` 插件已用 NNE + NNERuntimeORT/DirectML 走通这条路**（`AIAnimation.Build.cs:10` 依赖 `NNE`；`unreal-script/AIAnimation/README.md:27` 记录 30 Hz / 16 帧历史 / 4 帧步长 / DirectML 推理），**没必要为此换到 AnimGen。** 该可行性推断未做验证。

### B.4 与本机其他插件的关系（命令行取证）

`D:\UE_5.8\Engine\Plugins\Experimental\Animation\` 下 18 个 `.uplugin`，相关者：

| 插件 | Description（`.uplugin` 原文） | 与我们 refpose 的关系 |
|---|---|---|
| **AnimGen** | （空） | **无关**（控制信号驱动，非锚点补间） |
| **MotionTrajectory** | 「Generate predictions and track history of character motion.」 | **互补**——正好提供 root_plan 需要的「未来轨迹」；依赖 PoseSearch |
| **Locomotor** | 「Procedural animation for Control Rig.」 | 互补（程序化修正层） |
| **AnimDatabase** | （空） | 无关（AnimGen 的数据容器） |
| **AnimationLayering** | （空） | 潜在互补（分层混合） |
| MLDeformer / RigMapper | — | 无关 |

其它目录：

| 插件 | 位置 | Description 原文 | 判断 |
|---|---|---|---|
| **PoseSearch** | `Plugins\Animation\PoseSearch` | 「Framework for indexing and searching pose features. Used in techniques such as **Motion Matching**.」 | **非学习的对照组**（检索式） |
| **UAF** | `Plugins\Experimental\UAF\UAF\UAF.uplugin` | 「**Unreal Animation Framework** (UAF)」→「Framework for defining functional data flow for animation systems」 | UE 下一代动画框架（实验） |
| UAFAnimGraph / UAFPoseSearch / UAFWarping / UAFLayering / UAFChooser … | `Plugins\Experimental\UAF\` 下 13 个子插件 | 如「Pose Search integration for UAF.」 | 同上 |
| **NNERuntimeORT** | `Plugins\NNE\NNERuntimeORT` | 「ONNX Runtime backed runtime for the Neural Network Engine (NNE), accelerated by the CPU and **DirectML** execution providers.」 | **我们的模型走这条** |
| MoverAnimNext | `Plugins\Experimental\MoverAnimNext` | — | Mover + 动画衔接 |

**关于 AnimNext：** 本机**没有**名为 `AnimNext` 的插件目录，存在的是 `Experimental\MoverAnimNext` 与整套 `Experimental\UAF\*`。
> **【推断】** UE 早期的 AnimNext（下一代动画框架代号）在 5.8 已演进为 **UAF**；AnimGen 的 `.uplugin` 依赖同时出现 `UAF`/`UAFAnimGraph`/`UAFPoseSearch`，与该推断一致。**未找到 Epic 明文改名公告，属推断。**

**UE 5.8 发行说明核实：** 抓取 https://dev.epicgames.com/documentation/en-us/unreal-engine/unreal-engine-5-8-release-notes ，页面在 100,000 字符处被截断（「What's New」与贡献者名单之后），可见文本中 `AnimGen` / `UAF` / `Locomotor` / `Motion Trajectory` **均 0 命中**。
> **注意：这是「页面被截断 + 未命中」，不能据此断定发行说明中没有。** 可确证的只是：**AnimGen 未处于 5.8 release notes 的高曝光位置，它是纯 Experimental、默认关闭的插件。**

### B.5 B 节结论

> **AnimGen = Epic 第一方、5.8 实验性、默认关闭的「flow-matching 自回归角色控制器」插件。它解决的是「给定连续控制信号（轨迹/速度/目标点/标签），在自有动作库上训练逐帧生成姿态的控制器」，不是「给定稀疏参考帧补间」。**
>
> **与 refpose 的关系：任务层无关（不同问题），承载层可借鉴（control schema + AnimNode 的工程形态），但我们已有自己的 NNE 通路，替换成本 > 收益。**

---

## C. 同类开源方案对照

### C.1 对照表（存在性与许可证均经 GitHub API 核实）

| # | 名称 | 论文 / 团队 | 许可证（核实来源） | 输入条件类型 | UE 可部署性（ONNX / NNE） | 骨架与 fps 假设 | 脚滑 / 接触处理 | 复现成本 |
|---|---|---|---|---|---|---|---|---|
| 1 | **MotionBricks** | Wang et al., SIGGRAPH 2026 / ACM TOG；NVlabs | 代码 Apache-2.0 + 权重 NVIDIA Open Model Agreement（README:247,249；`assets/onnx/LICENSE.md`） | 4 个 context 姿态 + 1–4 个 target 姿态（**双端补间**） | **最好**：官方发 **ONNX opset 17, FP32/FP16**，输入输出张量表公开（`docs/onnx_inference.md`）；官方运行时是 TensorRT，需 NVIDIA GPU | SOMA **30 骨** 或 Unitree G1 **34 关节**，**30 fps**，输出 24–64 原始帧 | **模型直接预测 foot contacts**（左右 heel/toe 4 通道，`docs/soma_model.md:120`）；官方 demo 不做 IK 修正 | **低**（唯一「拿来就能挂 NNE」的） |
| 2 | **Kimodo** | NVIDIA SIL，技术报告 | 代码 **Apache-2.0**（`/license`）；权重 NVIDIA Open Model | 文本 + **全身姿态关键帧** + 末端执行器位姿 + 2D 路径/waypoint | **中**：纯 PyTorch，README 未提供 ONNX 导出；需 ~17 GB VRAM | SOMA **77 关节**（`somaskel77`）、SMPL-X、G1；**离线非实时** | **有**：`--no-postprocess` 关闭 foot skate 与 constraint 清理（README:121）；输出 `foot_contacts [T,4]` | **中高**（17 GB 显存、非实时） |
| 3 | **ARDY** | NVIDIA，SIGGRAPH 2026（arXiv 2607.08741） | 代码 **Apache-2.0**；权重 NVIDIA Open Model | 实时自回归 + 交互控制 | **中**：有 `.[trt]` TensorRT extra（README:35-36）与 `torch.compile`；未提 ONNX | Core / G1 / 多骨架，**20 fps** 与 **25 fps** 两档，horizon 8/40/52 | **有**：post-processing 降 foot skating（:177）、contact threshold（:179）、可视化 foot contacts（:183） | **中**（比 Kimodo 轻） |
| 4 | **CondMDI** | Cohan, Tevet, Reda, Peng, van de Panne；SIGGRAPH 2024（arXiv 2405.11126） | **MIT**（`LICENSE` 实读；README:261） | **关键帧 + 文本**，正是补间任务 | **差**：纯 PyTorch，无 ONNX/TRT | SMPL / HumanML3D，**20 fps** | 无专门接触处理 | **中**（需 SMPL 授权 + HumanML3D 预处理） |
| 5 | **MoMask** | Guo et al., CVPR 2024（arXiv 2312.00063） | **MIT**（README:243） | 文本 + 长度（**无关键帧条件**） | **差**：无 ONNX | SMPL 22 关节 / HumanML3D，**20 fps** | **有但粗糙**：README:123「We also apply naive foot ik ... It sometimes works well, but sometimes will fail.」；:133 提到 rokoko 重定向误差大 | **低-中**（有 HF CPU demo，但任务不匹配） |
| 6 | **MDM** | Tevet et al., ICLR 2023（arXiv 2209.14916） | **MIT**（README:541） | 文本 / 动作类别；`--prefix/--suffix` 可做补间（inpainting） | **差**：无 ONNX | SMPL，**20 fps** | 无 | **中**（★4130，社区生态最大） |
| 7 | **MotionDiffuse** | Zhang et al.（arXiv 2208.15001） | **S-Lab License 1.0 — 非商用**（`LICENSE` 实读：「Redistribution and use **for non-commercial purpose** ... are permitted」） | 文本 + 身体部位/时间细粒度控制 | **差** | SMPL / HumanML3D | 无 | — **商用直接排除** |
| 8 | **MotionGPT** | Jiang et al., NeurIPS 2023（arXiv 2306.14795） | **MIT**（`/license`） | 文本 / 动作 → 文本；评测含 `inbetween` 任务 | **差** | SMPL，**20 fps**（README:351 明确） | 无 | **中高**（LLM 底座） |
| 9 | **ASE** | Peng et al., SIGGRAPH 2022；nv-tlabs ★1131 | **NVIDIA 源代码许可 — 非商用**：`LICENSE.txt` 3.3「The Work and any derivative works thereof **only may be used or intended for use non-commercially** ... 'non-commercially' means for research or evaluation purposes only.」 | 动作片段 → 物理可执行技能潜空间 | **差**（Isaac Gym 仿真） | SMPL，物理仿真 30/60 Hz | **物理引擎天然处理接触**（其强项） | **高**（Isaac Gym 依赖、需重训） |
| 10 | **MimicKit** | Peng 等，ASE 官方推荐继任者 | **Apache-2.0** | 动作片段模仿 → 控制器 | 差（仿真训练） | 多骨架，物理仿真 | 物理仿真 | 高 |
| 11 | **PULSE** | Luo et al., ICLR 2024 spotlight（arXiv 2310.04582） | **查不到**：`/license` 404，README 全文无 license 字样 | 动作片段 → 物理控制潜空间 | 差 | SMPL，物理仿真 | 物理仿真 | 高 |
| 12 | **MoConVQ** | Yao et al., SIGGRAPH 2024 Journal Track（arXiv 2310.10198） | **查不到**：无 LICENSE 文件（根目录实读），README 无 license 段 | 动作 / 文本 → 物理控制 | 差 | SMPL | 物理仿真 | 高 |
| 13 | **AnyTop** | arXiv 2502.17327；`Anytop2025/Anytop` ★466 | **MIT**（README:253） | 文本 + **任意拓扑骨架**（唯一独特价值） | 差 | **任意拓扑**，HumanML3D 系 fps | 无专门处理；README:62 承认骨架特定调整仍需手工 | 中高 |
| 14 | **GPC** | Kurtz & Burdick（arXiv 2502.13406） | **MIT** | 状态 → 动作（flow matching 策略） | 差 | 非角色动画（cartpole/walker/humanoid 控制） | 物理仿真 MPC | 中（**任务不对口**） |
| 15 | **exbody2** | `edpsw/exbody2` ★69 | **Apache-2.0**（`LICENSE` 实读，注明 built upon ASE/PHC） | 参考动作 → 人形全身控制 | 差 | Unitree G1 等 | 物理仿真 | 高 |
| 16 | **PoseSearch / Chooser / MotionTrajectory**（UE 非学习） | Epic | UE EULA（引擎自带） | 轨迹 / 姿态特征检索（**Motion Matching**） | **原生**（UE 插件） | 任意骨架、任意 fps | **无**（匹配不保证接触，需 AnimationWarping/IK 补） | **最低** |

### C.2 必须点名的坑

1. **「Move Mimic」查不到。** arXiv 检索 `all:"Move Mimic"` 与 `ti:"motion mirror"` 均无结果，GitHub 检索无相关仓库。**标记为查不到，不编造。**
2. **MoConVQ 与 PULSE 没有许可证文件。** 二者 GitHub `/license` 均 404，根目录列表与 README 全文均无 license 段落。**在著作权法默认「保留所有权利」的语境下不能假定可商用**——这是本表最危险的两项。
3. **MotionDiffuse 组织已迁移**（现为 `MotrixLab/MotionDiffuse`），但许可仍是 **S-Lab 非商用**。
4. **ASE 已官方废弃**（README 首行「**This codebase is now deprecated.** Checkout MimicKit」），MimicKit 是 Apache-2.0。**要物理动画走 MimicKit，别走 ASE。**
5. **学术方案 fps 几乎全被 HumanML3D 锁在 20 fps**，我们 120 fps 是其 6 倍，直接迁移会先掉一半质量。

### C.3 两个关键空白

- **空白一：没有「高 fps + 稀疏锚点补间 + 可商用 + 可导 ONNX」的第四方方案。** 同时满足后两项的只有 NVIDIA 三兄弟；同时满足前三项的**一个都没有**。
- **空白二：没有「78 骨」的任何开源方案。** 全部是 SMPL 22 / SOMA 30 或 77 / G1 34。**78 骨本身就是差异化资产。**

---

## 冲突与不确定项

| # | 项目 | 状态 | 说明 |
|---|---|---|---|
| 1 | 权重许可名称不一致 | **未解决** | 本仓库继承的 `LICENSE:34-38` 标题是 **「NVIDIA OPEN MODEL LICENSE AGREEMENT」**（Last Modified: October 24, 2025）；而 `NVlabs/motionbricks` README:249 与 `assets/onnx/LICENSE.md` 全部写 **「NVIDIA Open Model Agreement」** 并链接 `.../nvidia-open-model-agreement/`。**两份是否同一文本我未能逐字核对**——NVIDIA 那两个页面都是 JS 渲染，`web_fetch` 只拿到导航骨架，正文未取到。**结论：视为同一协议族，但分发前应确认。** |
| 2 | `revocable` 的实际含义 | **不确定** | `LICENSE:80` 原文「perpetual, worldwide, non-exclusive, no-charge, royalty-free, **revocable** license」。perpetual 与 revocable 并存于同一句，法律效果如何**超出本调研资质，不做判断**。建议按「可商用但不保证永久」处理。 |
| 3 | 上游仓库的 SPDX | **不确定** | GitHub API 对 `NVlabs/GR00T-WholeBodyControl` 与 `NVlabs/motionbricks` 均返回 `"spdx_id": "NOASSERTION"`。**任何工具化许可证扫描都会报「未知」，CI/合规流水线需人工白名单。** |
| 4 | AnimGen 是否在 5.8 release notes 中 | **未证实** | 抓取到的发行说明页被截断在 100,000 字符，`AnimGen` 0 命中。**只能确认它不在高曝光位置，不能确认官方从未提及。** |
| 5 | AnimNext 是否等于 UAF | **推断** | 本机无 `AnimNext` 目录，只有 `MoverAnimNext` 与 `Experimental\UAF\*`。**未找到 Epic 明文改名公告。** |
| 6 | AnimGen 能否扩展出「锚点补间」 | **推断** | 机制上可行（`BlueprintNativeEvent` 可继承），**但未验证 control set 是否支持「逐帧可选性」语义，也未跑过训练。属可行性推断。** |
| 7 | `UAnimGenBehavior_TrajectoryInteraction` | **已核实一致** | 本机 `AnimGenBehavior.h:207` 的 `DisplayName = "Trajectory Interaction Behavior"`、`:208` 类定义、`:529-531` 控制元素工厂函数。（2026-11-03 订正，原判「未见」为漏查。） |
| 8 | 官方 UE 插件 | **已确证「无」** | issue #131 作者回复「内部讨论中」，issue 已 closed；GitHub `motionbricks+unreal` 搜索仅 1 个第三方骨架项目。**这是查到的否定证据，不是没查到。** |
| 9 | Move Mimic | **查不到** | arXiv 与 GitHub 均无结果。**不编造。** |
| 10 | MotionBricks 权重的 HF 许可标签 | **未核实** | `nvidia/MotionBricks` 页面 JS 渲染，只拿到 stylesheet 级内容；tags 中无 `license:*` 字段。**只能引用 GitHub 侧明文声明。** |
| 11 | C.1 表中「UE 可部署性」评级 | **推断成分** | 除 MotionBricks（官方发 ONNX）与 ARDY（官方有 TensorRT extra）外，其余**未实际做过 ONNX 导出验证**，评级依据是「README 是否提及 ONNX/TRT + 是否纯 PyTorch」。标为判断，非实测。 |

---

## 对我们的建议

> 前置：基线取自 `README.md:42-53`——78 骨 / 120 fps / 24 帧窗口 / test RMSE 2.17–5.28 cm / 脚滑 6.9–8.7× / 未做 UE 实机验证。

### 建议 1：**继续自研，不换模型；但把「接口」与「评测」对接上游口径**

| 路径 | 收益 | 代价 |
|---|---|---|
| **继续自研 refpose** | 保住 78 骨 / 120 fps 的差异化规格；RMSE 2.17–5.28 cm 已在 test 257,171 窗口验证；权重完全自有（无 revocable 风险）；已有 ONNX + DirectML 通路（`AIAnimation.Build.cs:10`） | 脚滑 6.9–8.7× 待处理；UE 实机未验证；3.09 M 参数可能已到容量顶 |
| **换用 MotionBricks** | 立得 30 fps 双端补间 + foot contact 预测 + ONNX；工程成熟 | **从 78 骨/120 fps 降到 30 骨/30 fps**，规格腰斩；权重 revocable + 署名义务；G1 权重是 34 关节机器人骨架，与人形 78 骨不通用，需重定向；**放弃已有全部评测资产** |
| **混合：refpose 主干 + 借接触预测** | 直接命中最大短板（脚滑） | 需对齐 SOMA 30 骨的 contact 语义再映射回 78 骨 |

**判断：换模型是用确定的降规格，换取不确定的工程便利——不划算。该抄方法，不该抄权重。**

### 建议 2：**脚滑优先「抄方法」，三条现成路径**

按投入产出排序：

1. **借 NVIDIA 的后处理思路（最省力）。** Kimodo 用 `--no-postprocess` 关掉「foot skate + constraint cleanup」（README:121），ARDY 用 post-processing 降 foot skating（:177）+ contact threshold（:179）。**两者都是在推理后加一层根位移修正，不改模型。** 我们 6.9–8.7× 的差距，很可能靠这层吃掉一部分。
2. **借 contact 预测头（中等）。** MotionBricks 的 SOMA 模型直接输出 4 通道 foot contact（`docs/soma_model.md:120`：LeftFoot / LeftToeBase / RightFoot / RightToeBase）。**我们目前是「接触约束已接入训练」（README:31），但没有显式 contact 输出头**——加一个 4 通道预测头，比调损失更可控。
3. **UE 端兜底（已计划，别改）。** README:31 写「余量交由 UE 端 Foot Locking / Two-Bone IK 补齐」是对的——**MotionBricks 与 Kimodo 自己也是这么兜的**：`unitree_g1_model.md:131` 明说「The foot contacts are model predictions. They are not contact sensor readings and do not guarantee stable contact」。

### 建议 3：**UE 承载层沿用自家 AIAnimation，不迁 AnimGen**

1. **任务定义对不上**（B.5）：AnimGen 要 Behavior 控制信号，我们要锚点掩码；让它跑 refpose 等于重写训练管线。
2. **我们已有等价通路**：`AIAnimation` 用 NNE + NNERuntimeORT/DirectML，已在 GASP 场景验证 AnimGraph 节点、动画工作线程推理、延迟融合播放（`unreal-script/AIAnimation/README.md:27,52,56`）。
3. **架构本就不同**：AnimGen 依赖 `PythonMLPackages` + `LearningCore` + `TensorBoard`，是「编辑器内训练」设计；我们是「训练在 Python、推理在 UE」。

**唯一值得从 AnimGen 借的是形态：** `AnimDatabase + AutoEncoder + Behavior/Control schema + AnimNode` 这套分层，是 Epic 认可的「学习型动画进 UE」官方长相。**建议把 `AIAnimation` 在文档层面按此分层重述**（数据层 / 表征层 / 控制层 / 节点层），成本近零，但对外讲述时立刻与官方同构。

### 建议 4：**MotionTrajectory 值得实际看一眼（唯一低成本正向机会）**

`MotionTrajectory.uplugin` 自我描述为「Generate predictions and track history of character motion.」，依赖 PoseSearch，**正好对应 `root_plan [24,7]` 的输入来源**。README:53 承认「Root 轨迹取自源动作，是理想控制输入」——**这是最该用引擎能力替换的一块，且不必改模型。**

> **【推断】** 若能替代「从源动作取未来轨迹」，我们的「理想输入」短板即可补上，评测可信度显著提升。**需单独验证其 API 能否给出 24 帧 @ 120 fps 的轨迹**（它通常对标 Motion Matching 的 30/60 fps 场景），**此点未查证。**

### 建议 5：**合规上做三件事，成本低但必须做**

1. **把 `legal/` 的结论落成「发布物料表」**：随插件走的（代码 Apache-2.0 → 带 `legal/Apache-2.0.txt`；权重 → 带协议副本 + `NOTICE:27` 的署名句），**绝对不能走的**（`inference/assets/gifs/*` 与 `assets/motions/*` 属 Sample Data Evaluation License，明文禁止再分发）。
2. **CI 加一条许可证扫描白名单**：上游两个仓库都被 GitHub 识别为 `NOASSERTION`，自动扫描必报红，需人工规则。
3. **对外话术守住 `NOTICE:17-19` 的边界**：「independent downstream project ... does not indicate an official NVIDIA product, endorsement, or ownership」——**这句与 issue #131 的官方回复互相印证：官方没有背书，我们也不能暗示有。**

### 建议 6：**做一次「规格对标」实验，把自研价值量化**

目前「我们比开源好」只是断言。建议补一个可证伪实验：

- 取 MotionBricks 的 SOMA ONNX（30 骨 / 30 fps，Apache + Open Model 双重可商用），**在同一 test split 上做骨架重定向后跑同样的 RMSE / 脚滑口径**。
- 若 refpose 在 78 骨 / 120 fps 上仍胜出 → **自研价值被量化，论文与软著都拿到硬证据**。
- 若 MotionBricks 重定向后反超 → **说明 3.09 M 参数确实到顶，那时再谈借用才有依据**。

> **【推断】** 成本主要不在推理（ONNX 现成），而在「78 骨 ↔ SOMA 30 骨」双向重定向与评测口径对齐，预计 1–2 人日。**这是目前最缺、也最能一劳永逸回答「该不该自己做」的证据。**

---

## 附：取证清单

- **本机文件**：`README.md`、`引用与许可说明.md`、`CITATION.cff`、`LICENSE`、`NOTICE`、`legal/README.md`、`docs/motionbricks/README.md`、`docs/refpose-v3-training-evaluation.md`、`unreal-script/AIAnimation/{README.md,Source/AIAnimation/AIAnimation.Build.cs}`
- **本机 UE**：`D:\UE_5.8\Engine\Plugins\Experimental\Animation\AnimGen\`（`.uplugin`、`Source/AnimGen/Public/*.h`、`Content/Python/*.py`）、`...\Animation\{PoseSearch,MotionTrajectory,Locomotor,AnimDatabase}\`、`...\Experimental\UAF\*`、`...\NNE\NNERuntimeORT\`
- **GitHub API**：`/repos/NVlabs/{motionbricks,GR00T-WholeBodyControl}`、`/repos/NVlabs/motionbricks/commits`、`/repos/NVlabs/GR00T-WholeBodyControl/issues/131(+comments)`，以及 A.4 / C.1 表中每个仓库的 `/repos/...` 与 `/repos/.../license`
- **GitHub 搜索**：`q=motionbricks` 的 `in:name`(28) / `in:readme`(134) / `+unreal`(1) / `+unity`(2) / `+plugin`(1) / `+ue5`(1) / `+blender`(1)
- **Hugging Face API**：`/api/models?search=motionbricks`、`/api/spaces?search=motionbricks`
- **Epic 官方文档**：`.../API/Plugins/AnimGen`、`.../API/Plugins/AnimGen/UAnimGenController`、`.../unreal-engine-5-8-release-notes`
- **arXiv**：`2604.24833`、`2405.11126`、`2312.00063`、`2209.14916`、`2208.15001`、`2306.14795`、`2310.04582`、`2310.10198`、`2502.17327`、`2502.13406`、`2607.08741`
- **原始许可文本**：`NVlabs/motionbricks` 的 `LICENSE` / `assets/onnx/LICENSE.md` / `assets/motions/LICENSE.txt`；`nv-tlabs/ASE` 的 `LICENSE.txt`；`MotrixLab/MotionDiffuse` 的 `LICENSE`；`setarehc/diffusion-motion-inbetweening` 的 `LICENSE`；`localai-org/motion-bricks.cpp` 的 `LICENSE`；`SarthakKalla/GR00T-Unreal-Plugin` 的 `LICENSE` 与 `PLAN.md`
