# SOMA full-v1 in-betweening 基线报告

> **本报告不含任何训练模型的结果。** 它记录的是两个零成本基线（Hold 与 Lerp/线性插值）在完整数据集上的表现，用途是给后续训练出来的模型立一把尺子。在看到模型数字之前，这里的任何数字都不代表本项目的能力。

- 生成时间：2026-10-26
- 数据集：`D:\MotionWeaverData\soma_train_v1\full-v1`
- 契约：`contracts/fps120`、`contracts/fps30`（`reference_guided_root_pose_v1`）
- 评测脚本：`training/evaluation/evaluate_conditioned_baselines_parallel.py`
- 原始数据：`eval/full-v1-fps120/report.json`、`eval/full-v1-fps30/report.json`

## 1. 数据集与评测规模

| 项 | fps120 | fps30 |
| --- | ---: | ---: |
| clips（全库） | 71,127 | 1,806 |
| 窗口（全库） | 1,227,549 | 17,055 |
| **test split clips** | 6,935 | 202 |
| **test split 窗口** | **121,058** | **1,952** |
| 评测样本（窗口 × 4 场景 × 2 基线） | **968,464** | **15,616** |

训练集共 72,933 条 / 窗口 1,166,446，四场景分布 A 28,233 / B 14,820 / C 2,743 / D 27,137。构建阶段拒绝 168 条（0.23%），全部是帧数不足窗口长度的引擎姿势资产（PoseAssets、AimOffset、CurveContainer、Ragdoll 短片段）。

> **两组帧率不可混比。** fps30 来自 UE Humanoid 重定向、时间分辨率更低，误差系统性偏大。下文始终分开呈现。

## 2. 评测协议

每个窗口取一段真实动作，把历史帧公开，再按四档公开参考：

| 档位 | 公开内容 | 难度 |
| --- | --- | --- |
| `no_reference` | 仅历史帧 | 最难 |
| `middle_reference` | 历史 + 中间一帧 | 中 |
| `end_reference` | 历史 + 终点帧 | 较易 |
| `middle_and_end` | 历史 + 中间帧 + 终点帧 | 最易（上界） |

两个基线：

- **Hold**：把历史最后一帧原样保持到未来，不做任何推断。
- **Lerp**：在已公开的参考帧之间做线性插值（位置和旋转分别插值）。

本质上是离线上界评测：Root 轨迹取自真实动作，参考帧来自目标本身，因此**不能当作在线生成能力**。

## 3. 主结果：fps120

### 3.1 关节 RMSE（cm）

| 档位 | Hold | Lerp | Lerp 相对 Hold |
| --- | ---: | ---: | ---: |
| `no_reference` | 20.704 | 20.704 | 0.0% |
| `middle_reference` | 20.693 | 10.918 | −47.2% |
| `end_reference` | 20.480 | 11.727 | −42.7% |
| **`middle_and_end`** | 20.465 | **6.593** | **−67.8%** |

### 3.2 旋转误差（度）

| 档位 | Hold | Lerp |
| --- | ---: | ---: |
| `no_reference` | 1.640 | 1.640 |
| `middle_reference` | 1.636 | 0.703 |
| `end_reference` | 1.621 | 0.843 |
| `middle_and_end` | 1.616 | 0.396 |

### 3.3 速度误差（cm/帧）

| 档位 | Hold | Lerp |
| --- | ---: | ---: |
| `no_reference` | 1.731 | 1.731 |
| `middle_reference` | 4.706 | 1.571 |
| `end_reference` | 4.557 | 1.560 |
| `middle_and_end` | 6.360 | 1.382 |

### 3.4 接触期脚速与脚滑倍数

源动作接触期脚速 **0.0261 m/s**，是判断脚滑的基准。

| 档位 | Hold 脚速 | Hold 倍数 | Lerp 脚速 | Lerp 倍数 |
| --- | ---: | ---: | ---: | ---: |
| `no_reference` | 0.814 | 31× | 0.814 | 31× |
| `middle_reference` | 1.377 | 53× | 0.664 | 25× |
| `end_reference` | 1.278 | 49× | 0.675 | 26× |
| `middle_and_end` | 1.841 | 71× | **0.545** | **21×** |

## 4. 分场景结果：fps120

场景标签在构建时按内容分类：A 常规移动、B 大姿态转换、C 跑酷攀爬、D 开放交互（手势/舞蹈/物体操作）。

### 4.1 `middle_and_end` / Lerp（最好的档位）

| 场景 | RMSE (cm) | 旋转 (度) | 窗口数 | 占比 |
| --- | ---: | ---: | ---: | ---: |
| **A 常规移动** | **10.062** | 0.522 | 49,016 | 40.5% |
| C 跑酷攀爬 | 5.597 | 0.405 | 2,918 | 2.4% |
| B 大姿态转换 | 4.848 | 0.367 | 17,978 | 14.9% |
| **D 开放交互** | **4.137** | 0.285 | 51,146 | 42.3% |

### 4.2 全场景 × 全档位（Lerp，RMSE cm）

| 场景 | no_reference | middle_reference | end_reference | middle_and_end |
| --- | ---: | ---: | ---: | ---: |
| A | 26.169 | 15.267 | 16.640 | 10.062 |
| B | 18.041 | 8.771 | 9.433 | 4.848 |
| C | 20.735 | 9.968 | 10.721 | 5.597 |
| D | 16.678 | 7.797 | 8.131 | 4.137 |

### 4.3 全场景 × 全档位（Hold，RMSE cm）

| 场景 | no_reference | middle_reference | end_reference | middle_and_end |
| --- | ---: | ---: | ---: | ---: |
| A | 26.169 | 26.145 | 25.927 | 25.898 |
| B | 18.041 | 18.036 | 17.826 | 17.817 |
| C | 20.735 | 20.727 | 20.491 | 20.478 |
| D | 16.678 | 16.677 | 16.469 | 16.463 |

## 5. 副结果：fps30（内部参考，不与主表混比）

### 5.1 关节 RMSE（cm）

| 档位 | Hold | Lerp | Lerp 相对 Hold |
| --- | ---: | ---: | ---: |
| `no_reference` | 44.443 | 44.443 | 0.0% |
| `middle_reference` | 44.180 | 25.323 | −42.7% |
| `end_reference` | 42.823 | 26.336 | −38.5% |
| `middle_and_end` | 42.378 | **16.790** | **−60.4%** |

### 5.2 脚滑（源动作脚速 0.0795 m/s）

| 档位 | Lerp 脚速 | 倍数 |
| --- | ---: | ---: |
| `no_reference` | 2.350 | 30× |
| `middle_reference` | 1.732 | 22× |
| `end_reference` | 1.819 | 23× |
| `middle_and_end` | **1.274** | **16×** |

### 5.3 分场景（middle_and_end / Lerp）

| 场景 | RMSE (cm) | 旋转 (度) | 窗口数 |
| --- | ---: | ---: | ---: |
| A | 19.730 | 1.971 | 1,236 |
| B | 10.551 | 1.548 | 582 |
| C | 16.317 | 2.625 | 124 |
| D | 9.937 | 2.305 | 10 |

> fps30 的 D 场景只有 10 个窗口，任何结论都不具备统计意义，仅作记录。

## 6. 结论

### 6.1 参考帧越多，Lerp 越好，梯度单调

fps120 上 Lerp 的 RMSE 严格随参考信息增加而下降：20.70 → 10.92 → 11.73 → 6.59。`middle_and_end` 最优、`no_reference` 最差，且四档之间差距显著。这说明评测本身有区分度，协议是有效的。

### 6.2 `no_reference` 档位没有区分度

该档位下 Hold 与 Lerp 的每一项指标完全相同（20.704 cm、1.640°、1.731 cm/帧、0.814 m/s）。原因是没有任何参考帧时，两者都退化为「保持最后一帧」。**这一档无法用基线之间的比较来说明任何事，只能作为模型与基线的对比场。**

### 6.3 脚滑是所有基线最严重的缺陷

Lerp 最好的档位（`middle_and_end`）脚速仍是源动作的 **21 倍**，Hold 高达 71 倍。插值补出来的姿态脚部不在地面上——这直接印证了「插值锁定了可播放质量的上限」。任何模型只要能把这个倍数压到 5 倍以内，就能在游戏可播放性上明显胜过 Lerp。

### 6.4 in-betweening 的难度主要由位移量决定，而不是姿态变化幅度

这是本轮最反直觉的发现：

- **A 场景（常规移动：走跑跳）最难**，`middle_and_end` 下 10.06 cm；
- **D 场景（手势舞蹈等原地动作）最容易**，4.14 cm，仅为 A 的 41%；
- B 场景姿态变化最剧烈（站→蹲→趴），但只有 4.85 cm。

解释：A 场景在数秒内发生米级位移，线性插值在长距离上误差累积；B/D 多为原地或小位移动作，插值天然更准。因此**决定 in-betweening 难度的主因是空间位移量，而非关节角度变化幅度**。这一点对训练重点和评测报告的解读方式都有直接影响。

### 6.5 两帧率组的差距

fps30 的 RMSE 是 fps120 的 2.0–2.6 倍，旋转误差是 4.5–4.8 倍。原因有两层：帧率低导致时间分辨率粗糙，且该组来自 UE 重定向而非原生 SOMA。**两组必须分开报告。**

## 7. 模型的目标线

下表是训练出来的模型必须超越的门槛（fps120，`middle_and_end` / Lerp）：

| 指标 | 基线值 | 模型应有表现 |
| --- | ---: | --- |
| 关节 RMSE | 6.59 cm | < 6.59 cm，理想 < 5 cm |
| 旋转误差 | 0.40° | < 0.40° |
| 速度误差 | 1.38 cm/帧 | < 1.38 |
| **脚滑倍数** | **21×** | **显著低于 21×，目标 < 5×** |

同时必须报告 `no_reference` 档位。只报 `middle_and_end` 会系统性夸大在线能力——在线推理时未来参考帧并不存在。

## 8. 局限

1. Root 轨迹取自原动作，是理想控制输入，不代表玩家或 CMC 的实际轨迹误差。
2. 参考帧直接来自目标动作本身；只有被选中公开的姿态帧作为条件。
3. Hold 与 Lerp 都是非学习基线，不保证骨长一致、不含 IK，也不代表实际游戏可播放质量。
4. 场景标签来自内容分类启发式，A/B/C/D 的边界存在少量歧义样本。
5. 来源分区的非 Traversal 语义变体可能跨分区，不能宣称完全独立泛化。
6. fps30 组样本量小（test 仅 202 clips / 1,952 窗口），其中 D 场景仅 10 窗口。

## 9. 复现方式

```powershell
cd D:\AILocomotonSystem\MotionWeaver
$env:PYTHONIOENCODING = 'utf-8'

# 契约（32 进程并行，约 12 分钟）
.\.venv\Scripts\python.exe -m data.tools.build_soma_contract_parallel `
  --dataset D:\MotionWeaverData\soma_train_v1\full-v1 `
  --output D:\MotionWeaverData\soma_train_v1\full-v1\contracts --workers 32

# 评测（16 并发 + 每窗口 20ms 休眠，约 16 分钟）
.\.venv\Scripts\python.exe -m training.evaluation.evaluate_conditioned_baselines_parallel `
  --contract D:\MotionWeaverData\soma_train_v1\full-v1\contracts\fps120 `
  --output D:\MotionWeaverData\eval\full-v1-fps120 `
  --split test --per-clip 0 --workers 16 --io-pause-ms 20
```

并发参数说明：32 进程同时读盘会让吞吐从约 6,350 条/分钟降到约 1,400 条/分钟（I/O 队列被打满）。16 进程配合每窗口 20ms 休眠可全程稳定，且不会把磁盘压到无响应。
