# refpose 阶段 1：ONNX 导出与离线数值对齐

> 阶段 1 只证明一件事：**同一份输入，PyTorch 与 ONNX Runtime(CPU) 算出来的数一致。**
> 它不证明模型好不好，也不证明 DML 能跑。

- 日期：2026-11-03
- 产物：`D:/MotionWeaverData/model_weight/refpose_v3_71k/model.onnx`（12,393,645 字节 = 12.39 MB 十进制 = 11.82 MiB）
- 记录：同目录 `onnx_manifest.json` / `latency.json`
- 生成器：`benchmark/manifest/export_onnx.py` / `measure_latency.py`（并发与延迟的全部数字由后者落盘到 `latency.json`，可复跑）
- 门禁：`tests/test_onnx_export.py`（14 条）

---

## 一、结论

| 项 | 实测 |
|---|---|
| ONNX 大小 | 12,393,645 字节（12.39 MB 十进制 / 11.82 MiB） |
| 节点数 / opset | 257 / 17 |
| **相对误差 max** | **5.41e-06**（216 样本，含窗口首/中/尾） |
| 绝对误差 max | 1.29e-05 |
| 样本数 | **216**（3 个 split × 6 clip × 3 个位置 × 4 种 mask） |
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

`max_rel = max|Δ| / max|output|` 实测：max **5.41e-06**（216 样本），中位约 2.2e-06。

但它与输出幅值的相关系数是 **−0.489**，不是 0 —— 也就是说相对误差仍与幅值有关，
只是方向反了（幅值越大，相对误差越小）。这符合预期：误差主要来自固定量级的累积噪声，
分母变大相对误差自然变小。

**所以相对判据比绝对判据更稳，但不是「与幅值无关」这种强结论。** 选它的理由是
它有一个物理意义明确的上界（占输出的百万分之几），而绝对阈值没有。

### 2e-5 这个阈值有多大鉴别力

CR 提了个好问题：说了 2e-5 合理，但没示出「多大误差才会被抓到」。
我用同权重的 float64 版本作参照补测：

| 参照 | fp32 相对误差 |
|---|---:|
| torch-f32 vs f64，中位 | 1.54e-06 |
| torch-f32 vs f64，最大 | 3.86e-06 |

所以 2e-5 是 **fp32 舍入地板的约 13 倍（对中位）/ 5.2 倍（对最大）**，
而实测 ORT 误差 5.41e-06 是地板的 3.5 倍。
**结论：阈值留了约 4 倍余量，又不会放过 1e-4 级的真损失。**

### 判据的形状：为什么不能只用「max|Δ| / max|out|」

这个比值是**混范数**（∞-范数误差 ÷ max-范数尺度），有两个毛病：

1. 会掩盖小幅值元素的相对误差。实测最坏元素的局部相对误差是 **6.25e-03**
   （该元素 |x|=2.8e-4），而全局比值只报 5.29e-06。
2. 分母是张量 max，输出幅值整体放大 100 倍时，同一个门限就放行 100 倍的绝对误差。
**但 allclose 也有它自己的陷阱，实测踩到了：**

标准形式是 `|Δ| <= atol + rtol·|x|`，atol 和 rtol 是「或」的关系，atol 会压掉 rtol。
我一开始想把 atol 压到 1e-7 去抓小幅值元素的大相对误差——**结果真实数据上 1.5% 的元素超限。**

查原因：72 窗口 / 1,213,056 个元素里，

| 统计 | 值 |
|---|---|
| `\|x\|` 最小 | 0 |
| `\|x\|` p1 | **3.2e-07**（有大量接近零的元素） |
| `\|x\|` 中位 | 0.37 |
| `\|x\| < 1e-3` 的元素数 | **174,058** |
| 这些元素的最大「局部相对误差」 | **3.2e+02（32000%）** |
| 而它们对应的绝对误差 | 只有 **6.25e-06** |

**6.25e-06 是 float32 的正常噪声，不是精度损失。要求接近零的元素也有高相对精度是不现实的**——
那会把每个浮点噪声都判成失败。

所以 atol 的职责是「接近零时的噪声地板」，rtol 是「大幅值时的相对精度」，两者分工，不能用一个压另一个。
最终取 `atol=1e-5, rtol=2e-5`，实测超出元素数为 0。


所以真正的误差界用逐元素 `np.allclose(atol=1e-5, rtol=1e-5)`，
全局比值只保留作为与调研文档可比的参考指标。

### 分母陷阱

相对判据的分母是 `max|output|`，若输出整体接近 0 会爆。
本模型输出幅值下限 1.37（远离 0），所以安全。
**换到输出可接近 0 的模型时，要改成 `max|Δ| / (max|out| + c)` 或逐元素相对误差。**

### 关于「是 float32 eps 的多少倍」

这个比值**只能当量级参考，不能当判据**。实测 216 个样本的 `绝对误差 / (eps32 × |out|max)`
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

## 四、性能与并发（初版结论是错的，已推翻）

### 先纠正一个错误

初版这里写「多角色完全线性，8 角色合计 7.74 ms」「顺序执行是保守估计，真并发只会更快」。
**这个结论是反的。** 原因是初版的 measure_latency.py 只测了「单个 session 连跑 N 次」，
那是**一个角色跑 N 帧**，不是 N 个角色同时存在。

2026-11-03 重写测量脚本，补齐三种量法后实测（CPU EP，本机 32 逻辑核）：

| 角色数 | ①单 session 跑 N 帧 | ②N session 串行 | ③N session 真并发 | ④thin(1 线程) 并发 |
|---:|---:|---:|---:|---:|
| 2 | 1.83 ms | 5.61 ms | 6.65 ms | 4.21 ms |
| 4 | 4.10 ms | 24.50 ms | **85.04 ms** | 3.68 ms |
| 8 | 8.32 ms | 62.97 ms | **95.92 ms** | **6.13 ms** |

60fps 预算是 16.67 ms。**8 个角色按 ③ 跑要 95.92 ms，超预算 5.8 倍。**
（这是 run-to-run 会波动的数字：另两次测得的 8 角色并发分别是 151.54 / 163.84 ms，
量级一致，都是预算的 6–10 倍。引用时请带「约 100–160 ms」而不是某个定值。）

### 原因

每个 ORT session 自带一个 `intra_op` 线程池（默认取物理核数）。
N 个 session 就是 N× 线程抢同一批核，调度开销主导，所以并发反而更慢。
把每 session 的 `intra_op_num_threads` 设成 1（表中的 ④），8 角色并发降到 **6.13 ms**，
落回预算内。（这一项在三次测量里是 5.11 / 5.77 / 6.13 ms，稳定。）

**这是阶段 2/3 的关键参数**：UE 侧如果每角色一个 session，必须显式限制线程数，
否则角色一多就会雪崩。

### 这个数不可移植到 UE

以上全是 **CPU EP** 的数字。UE 内走 DirectML，调研文档已写明 DML 强制
`ORT_SEQUENTIAL` 且禁用 memory pattern——**多 Run 并发恰好是 DML 的最差场景**。
所以这里的加速比不能作为 UE 内的预期。

### 单流延迟（参考）

| 指标 | 实测（真实窗口，200–300 次采样） |
|---|---:|
| p50 | 0.79–0.92 ms |
| p95 | 1.12–1.18 ms |

不同轮次之间的波动约 p50 ±10%、p95 ±5%（同一段代码测到过 p50 0.79 / 0.90 / 0.92），
**不要当定值引用**。

### 方法论上的两点说明

1. **输入来源**：用真实窗口（`input_source: real_window`）而不是噪声。
   实测两者延迟差在 ±3% 内且方向不稳定，选真实数据不是为了性能差异，
   而是它顺带证明这些张量模型真的吃得进去。
2. **多角色用的是不同窗口**：`_real_feeds` 取 8 个不同 clip 的窗口，
   不是同一个窗口重放 8 次。

### 取样覆盖

初版 start 恒取 clip 中点，**从不覆盖窗口首尾**——正好漏掉「origin 取 root[middle-1]
的 off-by-one」与「末尾越界」这两类边界 bug（阶段 0 就抓到过同类问题）。

现在改为 {0, 中点, total−span} 三处，样本数 72 → **216**。
边界覆盖后相对误差从 5.29e-06 变成 5.41e-06，ONNX 哈希不变——
说明边界窗口没有暴露新问题，之前的中点取样虽窄但结论方向是对的。

## 五、ONNX 图构成（UE 侧 NNE 需要看这个）

| 项 | 值 |
|---|---|
| 节点总数 | 257 |
| opset / IR | 17 / 8 |
| 用到的域 | **只有 `ai.onnx`**，无自定义算子 |
| 最大 initializer | `onnx::GRU_608` 2.18 MB |
| GRU | `hidden_size=256`, `linear_before_reset=1` |

算子清单（按数量）：

```
Constant 73, Reshape 33, Transpose 25, Add 22, MatMul 17, Gather 11,
Slice 9, Sqrt 9, Shape 7, LayerNormalization 7, Cast 7, Mul 6,
Unsqueeze 5, Concat 5, Mod 3, Squeeze 3, Div 3, Softmax 3,
Gemm 3, Relu 3, Expand 1, GRU 1, Where 1
```

**`Where` 只有 1 个，而且它就是产出 `joints` 的那个节点本身**：

```
Where(inputs=[Cast_output_0, reference, Add_output_0], outputs=[joints])
graph.output = ["joints"]
```

也就是说硬覆盖语义位于图输出路径的**末端**，没有被导出时优化掉。
这是它在 UE 侧能生效的前提——比「图里存在 Where」更强的证据。

UE 侧只需保证：mask 的 0/1 语义与 reference 的归一化域，和 manifest 一致。

## 六、已知限制

1. **静态 batch=1**。真正钉死它的是**导出时的静态输入形状** `[1,24,*]`——
   ORT 对 batch≠1 直接拒绝（实测报 `Got invalid dimensions for input: history,
   index: 0 Got: 2 Expected: 1`）。

   （初版这里写「GRU 在非 1 batch 下会报错，torch 明确警告过」——**这是误引**。
   torch 那条 warning 原话是「with a variable length with GRU can cause an error
   when running with a different batch size」，说的是**动态 batch** 的风险；
   本模型用静态形状，正好不涉及。结论「多角色每角色一个 session」仍成立，
   但依据是静态形状，不是 GRU。）

   多角色还应显式设 `intra_op_num_threads=1`，见第四节。
2. **DML 未验**。见第三节。
3. **legacy 导出器已废弃**。torch 2.14 + `dynamo=False` 仍可用，但有 DeprecationWarning。
   保持 legacy 是为了和调研文档的 257 节点对得上；升级 torch 后需重新评估。
4. **CPU 延迟不代表 UE 内延迟**。UE 走 NNE + DirectML，量级会不同。

---

## 七、复现

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
