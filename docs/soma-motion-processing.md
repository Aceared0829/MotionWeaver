# SOMA 轻量数据处理 v1

2026-10-17：按用户确认的范围收敛。来源粗语义直接复用，不再建立详细 LLM 分段验收。原始动作、历史预测文件和人工备注保留；专用请求生成、返回契约和模型验收界面已移除。

## 当前实现

- `data/runtime/motion_features.py`：数值运动与有效朝向，不含语言模型或语义分类。
- `data/runtime/motion_processing.py`：可逆 Root 位置表示、脚部多高度稳定候选、手部低速线索。
- `data/tools/ingest/prepare_soma_processing.py`：全库非镜像来源语义索引、30 条处理试点、参考候选、任务候选索引及新审核版本。
- 原有原生导入、FK、来源双语说明和动作审核复用。没有引入 UE、网络服务或新依赖。

## Root 与时间的离线契约

版本 `soma_processing_v1` 固定如下计算规则：

1. 保留来源声明的秒数和 FPS，当前试点不校准为名义 120 FPS，也不重采样或补尾帧。未来训练重采样必须独立记录版本，所有条件使用同一时间轴。
2. Root 代理位置取记录空间 Hips 的 X/Z，Y 为零；朝向取左右髋形成的前向，在肩髋一致、躯干向上等有效性检查通过时使用。
3. 朝向无效时不填造真实方向：旋转占位为单位旋转，`heading_valid=false`。此处可能不连续，不能作为有效朝向条件或自动用于连续控制。
4. 身体位置为 `R_yaw^-1 × (recorded_position - root_proxy_position)`，全部录制高度留在身体位置中。重合成必须恢复完整录制位置，最大绝对误差门槛为 `1e-9 m`。
5. `body_positions` 是 Root 相对的关节位置表示，不是替换后的局部 TRS；原始局部旋转、缩放及层级继续由原生运动保存。

这固定的是可验证的**离线代理表示**，不是恢复了真实 Actor Root。站蹲、跳跃、登高的高度均能回读，但如何把垂直运动分配给 UE 胶囊与身体仍须运行时验证。不能直接用 Y=0 的代理去驱动攀爬角色。

## 接触候选

逐帧状态为 `-1 unknown`、`0 noncontact_candidate`、`1 contact_candidate`。这里的逐帧结果是程序计算，无需逐帧人工标注。

- 脚使用踝/趾代理点在完整录制空间的速度，不能用骨盆相对速度。稳定区间要求持续时间、进入/离开速度和高度变化同时满足规则。
- 每只脚可保留多个稳定代理高度，避免把高台上的脚因远离全局最低点而一律判为腾空。代理高度不等于实际地面或台阶表面。
- 快速升降且远离稳定代理高度时标为非接触候选，其余区间保留未知。脚滑、移动平台、坡面、悬停等仍可能误判。
- 手部始终保持接触未知；稳定区间只提供运动线索。来源文本提及撑手也不能升级成接触真值。
- `contact_known_mask` 只表示有候选值，**不表示人工确认或传感器真值**。若后续选择使用伪标签损失，未知区间必须屏蔽；本试点没有修改训练损失。
- 不修改或锁定原始手脚，不自动执行 IK，不调用 LLM 或 VLM。

阈值以米和秒配置，随报告保存。报告的候选比例不是准确率或校准置信度。

## 来源语义、参考与任务索引

`source-semantics.csv` 复用所有非镜像动作的类别、姿态、风格、短说明和首条自然语言描述；完整其余字段继续从原 CSV 读取。保存来源文件哈希、缺失字段统计和动作路径匹配情况，保留原英文，不把非空等同语义正确。现有 30 条的中文释义与来源事件继续显示；未翻译的新文本不猜测中文。

`reference-candidates.json` 只引用源动作、帧号、来源哈希和选帧原因，不复制完整骨架或动作。端点及骨盆高度极值是待筛选候选：`constraint=none`，没有自动选中硬参考，也没有自动创建接触目标。

`task-index.json` 将明确的来源标签整理为普通移动/交互/未分配候选路线，保留过渡，不按类别机械切碎动作。当前没有正式 train/validation/test 分区或滑窗采样，所有项 `training_ready=false`；未来必须先按来源家族/会话/镜像组划分，再生成窗口。`condition_reference_ids=[]`，未选未来姿态不作为条件泄露。

原生来源骨架签名继续保留。SOMA 中的参考骨架变体尚未重定向为统一 rest，UEFN→SOMA 也没有在这里实现。候选索引不是已经适配网络的正式训练集。

## 数据目录与运行

```text
E:/MotionWeaverData/
  raw/                                      原始导入，不改写
  processed/soma_observation/<原批次>/        既有局部 TRS
  processed/soma_processing/<新批次>/<id>.npz Root/身体位置/接触候选及掩码
  reports/soma_observation/<新批次>/
    run.json                                来源、参数、哈希、回读与误差
    acceptance.json                         基础回读和重合成结果
    export-index.csv                        引用原运动，接入现有查看器
    source-semantics.csv                     全库非镜像粗语义索引
    reference-candidates.json                少量选帧候选
    task-index.json                         条件可见性明确的任务候选
    <id>.json                               来源观察、处理摘要、历史备注
  reviews/soma_observation/<新批次>/          新抽检结论，不继承旧通过
```

```powershell
./.venv/Scripts/python.exe -m data.tools.ingest.prepare_soma_processing --index E:/MotionWeaverData/reports/soma_observation/20261017_semantic_review_v2/export-index.csv --run-id 20261017_processing_v1
./.venv/Scripts/python.exe -m data.tools.review.motion_review_server --library E:/MotionWeaverData --native-index E:/MotionWeaverData/reports/soma_observation/20261017_processing_v1/export-index.csv --port 8781
```

已有批次拒绝覆盖；重新执行需新 run ID。历史数据库只读，备注保留原审核身份，在新页面单独展示，不继承其结论。

## 下一阶段

先抽查 30 条的 Root、脚部候选和异常，确定是否需要调整规则，再扩量。随后精选软/硬参考，验证真实参考条件生成；Root 代理、参考骨架变体和条件掩码均通过后，才适配正式训练出口。UEFN→SOMA 另以小样本验证。全库精细语义、逐帧人工接触、渲染骨架 VLM、完整物理优化均不作为当前工作。
