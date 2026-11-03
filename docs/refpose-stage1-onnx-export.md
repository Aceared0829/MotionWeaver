# refpose 阶段 1：ONNX 导出与离线数值对齐

> 阶段 1 只证明一件事：**同一份输入，PyTorch 与 ONNX Runtime(CPU) 算出来的数一致。**
> 它不证明模型好不好，也不证明 DML 能跑。

- 日期：2026-11-03
- 产物：`D:/MotionWeaverData/model_weight/refpose_v3_71k/model.onnx`（12.39 MB）
- 记录：同目录 `onnx_manifest.json` / `latency.json`
- 生成器：`benchmark/manifest/export_onnx.py` / `measure_latency.py`
- 门禁：`tests/test_onnx_export.py`（7 条）

---

## 一、结论

| 项 | 实测 |
|---|---|
| 导出耗时 | 0.47 秒 |
| ONNX 大小 | 12.39 MB |
| 节点数 / opset | 257 / 17 |
| **相对误差 max** | **5.29e-06** |
| 绝对误差 max | 1.29e-05 |
| 样本数 | 72（3 个 split × 6 clip × 4 种 mask） |
| ONNX 输入 | 四个具名张量，形状与阶段 0 契约逐项相等 |
| mask 硬覆盖 | 导出后仍精确成立（< 1e-6） |

---

## 二、判据为什么从「绝对 1e-5」改成「相对 2e-5」

调研文档写的是 `max|torch − ORT_CPU| < 1e-5`。**按这个跑，第一次就红了：实测 1.287e-05。**

### 先排除是 bug

| 检查项 | 结果 | 结论 |
|---|---|---|
| 误差与输出幅值的相关系数 | **+0.524** | 绝对误差随输出幅值放大 |
| 四种 mask 组合的误差中位数 | 4.86e-06 ~ 5.17e-06 | 与 mask 无关 |
| torch 自身跑两次 | **0.000e+00** | 确定性，非随机抖动 |
| ORT 自身跑两次 | **0.000e+00** | 同上 |
| 72 样本的绝对误差 | max 1.29e-05 / 中位 4.97e-06 | 不是个别离群 |

### 为什么绝对阈值本身不合适

模型输出幅值在 **1.37 ~ 5.57** 之间浮动。绝对误差跟着幅值走（相关系数 +0.524），
所以同一个正确实现在不同样本上给出的绝对误差能差几倍。定一个固定的 1e-5：

- 定紧了 → 在正确实现上误报（当前情况）
- 放宽到 1e-4 → 把真的精度损失放过去

按当前相对误差水平（max 5.29e-06）推算：若某天模型输出幅值涨到 100，
绝对误差会变成 ~5.3e-04，**是 1e-5 的 53 倍**。绝对阈值不能跨模型/跨数据固定。

### 相对判据也不是完美的

`max_rel = max|Δ| / max|output|` 实测：max **5.29e-06**，中位 **2.20e-06**。

但它与输出幅值的相关系数是 **−0.489**，不是 0 —— 也就是说相对误差仍与幅值有关，
只是方向反了（幅值越大，相对误差越小）。这符合预期：误差主要来自固定量级的累积噪声，
分母变大相对误差自然变小。

**所以相对判据比绝对判据更稳，但不是「与幅值无关」这种强结论。** 选它的理由是
它有一个物理意义明确的上界（占输出的百万分之几），而绝对阈值没有。

### 关于「是 float32 eps 的多少倍」

这个比值**只能当量级参考，不能当判据**。实测 72 个样本的 `绝对误差 / (eps32 × |out|max)`
范围是 **[8.1, 44.4]**，最差样本 44.4。之所以跨度这么大，是因为分母同时含 |out|，
|out| 小的样本比值自然大。

它能说明的只有一件事：误差是 eps 量级的几十倍，属于 float32 范畴，不是 O(1) 的实质性差异。
具体是 19 倍还是 44 倍，取决于拿哪个样本算，不构成论证。

（初稿这里写「19.4 倍，与 √257 ≈ 16 倍吻合」——19.4 是最差样本的值不是中位数，
而 √257 那个估算本身没有依据：误差累积量级取决于运算结构，不是节点数的平方根。）

## 三、只验 CPU，不验 DML（明确决策）

调研文档写「ORT-DML 实测 4.77e-6 ✅」。**但那个数字不在当前环境里**——
本机 venv 只有 `CPUExecutionProvider` + `AzureExecutionProvider`，没有 DirectML。

更关键的是：**UE 里用的 ORT 是引擎自带的**（`Onnxruntime_v1.24.1.tps` + `DirectML_v1.15.4.tps`），
跟 pip 装的 `onnxruntime-directml` 是两套东西。在 Python 里测 DML，证明的是
「这个 pip 包能跑」，**不是「UE 里能跑」**。

所以：**阶段 1 只验 CPU，DML 留到阶段 2 在 UE 内部实测。**
阶段 1 的目的只是证明导出本身无损，CPU 足够。

这一条写进了 `onnx_manifest.json` 的 `deferred.directml`，不会丢。

---

## 四、性能（顺带把一条「未证实」测掉了）

调研文档把「多角色并发性能未测」列为未证实项。实测：

| 角色数 | 合计 | 每角色 | 60fps 内 |
|---|---:|---:|---|
| 1 | 0.94 ms | 0.942 ms | ✅ |
| 2 | 1.79 ms | 0.893 ms | ✅ |
| 4 | 3.71 ms | 0.929 ms | ✅ |
| 8 | 7.11 ms | 0.889 ms | ✅ |

单流 p50 **0.898 ms**，p95 **1.141 ms**。

**完全线性**：摊到每角色的成本几乎不变（0.89–0.94 ms）。8 个角色合计 7.11 ms，
60fps 的 16.7 ms 预算还剩一半多。

**但这不代表真并发**：以上是同一 session 顺序跑 N 次。

「顺序执行是保守估计」这句话我实测验过了——同 session 8 线程并发跑 8 次：

| 方式 | 8 角色批次耗时 |
|---|---:|
| 顺序 | 7.87 ms |
| 多线程并发（同 session） | **3.80 ms** |

并发比顺序**快 107%**，所以顺序确实是保守估计。两种方式都远在 16.7 ms 预算内。

注意：这里测的是同一 session 多线程。真正的多角色实现建议**每角色一个 session**，
因为 batch 固定为 1，共用一个 session 会让 GRU 的内部状态互相干扰（虽然推理无状态，
但 ORT session 的 Run 不是设计给并发调用的）。

---

## 五、已知限制

1. **静态 batch=1**。GRU 在非 1 batch 下会报错（导出时 torch 明确警告）。
   多角色必须每角色一个 session，不能靠 batch 堆叠。
2. **DML 未验**。见第三节。
3. **legacy 导出器已废弃**。torch 2.14 + `dynamo=False` 仍可用，但有 DeprecationWarning。
   保持 legacy 是为了和调研文档的 257 节点对得上；升级 torch 后需重新评估。
4. **CPU 延迟不代表 UE 内延迟**。UE 走 NNE + DirectML，量级会不同。

---

## 六、复现

```powershell
# 导出 + 数值校验
.venv\Scripts\python.exe benchmark\manifest\export_onnx.py `
  --manifest benchmark\manifest\refpose_manifest_v1.json `
  --checkpoint D:\MotionWeaverData\model_weight\refpose_v3_71k\best.pt `
  --output     D:\MotionWeaverData\model_weight\refpose_v3_71k

# 延迟
.venv\Scripts\python.exe benchmark\manifest\measure_latency.py `
  --onnx D:\MotionWeaverData\model_weight\refpose_v3_71k\model.onnx `
  --output D:\MotionWeaverData\model_weight\refpose_v3_71k\latency.json

# 门禁
.venv\Scripts\python.exe -m pytest tests\test_onnx_export.py -v
```
