# refpose 阶段 0：输入契约（sign-off 版）

> **这份文档 + `benchmark/manifest/refpose_manifest_v1.json` 是整条链上唯一一份
> 可以签字的「模型吃什么」。** 除此之外的一切（`__getitem__`、调研文档正文、
> 口头解释）都不是契约，只是契约的实现或描述。
>
> 签这个字的意思是：**任何人拿着这份 manifest 和一个独立的 numpy 脚本，
> 能从原始数据拼出与训练侧逐位相同的输入。** 做不到就不是契约。

- 日期：2026-11-03
- manifest：`benchmark/manifest/refpose_manifest_v1.json`
- 生成器：`benchmark/manifest/build_manifest.py`
- 独立重建：`benchmark/manifest/rebuild_inputs.py`
- 门禁测试：`tests/test_manifest_rebuild.py`

---

## 一、判据与实测结果

| 判据 | 阈值 | 实测 |
|---|---|---|
| 重建四输入 vs 训练侧（train） | `max|Δ| < 1e-6` | **0.00e+00** |
| 同上（validation） | 同上 | **0.00e+00** |
| 同上（test） | 同上 | **0.00e+00** |
| manifest 里 5 个 SHA-256 vs 当前产物 | 全等 | 全等 |
| 反向测试：把 6D 排布换成「列拼接」 | 必须红 | 反解不正交（gram 偏差 > 1e-2），如期变红 |
| 独立脚本不得 import 训练侧 | 0 处 | 0 处 |

**误差是精确的 0.00e+00，不是「小于 1e-6」。** 两边用同一组 float32 数据、
同一套运算顺序，没有理由不逐位相同。如果哪天变成 1e-7 而不是 0，说明某处的
dtype 或运算顺序变了，值得停下来看一眼。

---

## 二、契约本身

### 2.1 时间

| 项 | 值 |
|---|---|
| fps | **120.00480019200768**（不是 120） |
| history / future | 各 24 帧 |
| 窗口长度 | 24 / 120.0048 = **199.99 ms** |

写 120.0 会引入 8 µs 误差。单独看无关紧要，但它是一个标记：
**凡是写「120 fps」的地方，都没真正读过 contract.json。**

### 2.2 四个输入的构成

```
history    [24, 709] = 78x9（归一化位置 234 + 旋转 6D 468） + root_local 7
root_plan  [24, 7]   = xyz(3) + xyzw(4)，相对 origin
reference  [24, 702] = 78x9，未选中帧全 0
mask       [24, 1]   = 0/1
```

### 2.3 归一化只作用于位置

- 位置（234 维）：`(positions - mean) / std`，逐骨逐轴
- 旋转 6D（468 维）：**不归一化**
- root_local（7 维）：**不归一化**

统计量绑定训练分区，**不许重算**。实测用 3 条 clip 反推的经验统计量与契约统计量
最大差 43 cm（均值）/ 147 cm（标准差）。

### 2.4 6D 旋转排布（最容易写错的一处）

```
顺序：m00, m01, m10, m11, m20, m21     （row-major 交织）
解码：col0 = (v0, v2, v4)
      col1 = (v1, v3, v5)
      col2 = cross(col0, col1)
```

**仓库自带的 `matrix_to_cont6d` 是「按列拼接」，与这个排布不兼容。**
误用后果实测（判据是 **|col0 × col1|**，不是单列范数——col2 由叉乘得到，
叉乘模长为 0 才会除零。四条 clip：000000 / 019534 / 019857 / 027106）：

| 骨 | 名称 | min\|col0×col1\| | 后果 |
|---|---|---:|---|
| 0 | Root | **0** | 除零 |
| 34 | LeftHandRingEnd | **0** | 除零 |
| 52 | RightHandIndexEnd | **0** | 除零 |
| 62 | RightHandRingEnd | **0** | 除零 |

另有 19 根骨落在 1e-2 以下（共 23 根），但**正好为 0 的只有上面 4 根**。

> **这两个数字都改过两次，原因值得记下来：**
> 我最初用「后半段单列范数」当判据，写「骨 42 会除零」——错的，
> 骨 42 实测 `|col0×col1| = 1.48e-04`，不为零。
> 修正后我又手写了一版新数字，被 CR 复算打回：判据换成叉乘后，
> 会除零的是 Root 和三个叶骨（`*End` 没有子骨，旋转本身不确定）。
> **现在这个字段由 `build_manifest.py::probe_6d_degeneration` 现算，不再手写。**

反向测试保证了这个坑一旦踩到就会立刻变红。

### 2.5 mask 是硬覆盖，不是软条件

`forward` 末行 `torch.where(mask.bool(), reference, predicted)`。
实测 mask=1 的帧，输出与 reference **逐元素精确 0.0 误差**。

UE 侧必须照这个实现：给关键帧就是**钉死**，不是「倾向」。

### 2.6 Root 权威与 `p[:,0]`（此前一直挂着的问题，已查清）

```
p[:,0] = -R(k)^T · k_pos
```

四条 clip 上最大误差 **1.16e-06**。也就是说：

- `p[:,0]` **不是**第二条轨迹，它完全由 `k` 决定，不含独立信息
- `p` 是 root-local 表示：`world = R(k)·p + k_pos`，回代误差 < 1e-15
- `k[:,1]` 恒为 0：垂直高度全部留在身体姿态里

之前判「方向相反、无法用轴置换对齐」，是因为只比了首尾两点的**位移差**，
被两帧之间 `R` 的不同旋转带偏了——**比较量本身选错了**。

**UE 侧结论**：以 `k.npy` 为 Root 唯一权威，`p[:,0]` 直接忽略即可。

### 2.7 anchor

`origin = root[middle - 1]`，`middle = start + 24`。

位置与朝向都相对它。用 `root[middle]` 会让整条 `root_plan` 偏一帧——
这是重建测试能抓到的那类错误。

---

## 三、这份契约怎么用

| 角色 | 该做什么 |
|---|---|
| 想确认「模型吃什么」 | 只读 manifest，不要读 `__getitem__` |
| UE 侧装配输入 | 按 manifest 实现，`rebuild_inputs.py` 是参考实现 |
| 改了数据管线 | 重跑 `build_manifest.py`；哈希变化会被测试立刻抓到 |
| 换权重 | 同上；checkpoint_sha256 对不上会红 |

---

## 四、边界：这份契约不承诺什么

1. **不承诺数值正确性**——它只说明「输入怎么拼」。模型准不准是评测的事。
2. **不覆盖训练侧的随机 scenario**——reference 的 offset 是训练时随机的，
   manifest 只声明语义，不声明该选哪几帧。
3. **不含 ONNX 导出**——那是阶段 1。
4. **不含 UE 内存布局**——float32 的排列顺序由 ONNX 输入名决定，阶段 2 再定。

---

## 五、复现命令

```powershell
# 重新生成 manifest（改了数据/权重之后必须重跑）
.venv\Scripts\python.exe benchmark\manifest\build_manifest.py `
  --release D:\MotionWeaverData\full_release `
  --mmap D:\MotionWeaverData\full_mmap `
  --checkpoint D:\MotionWeaverData\model_weight\refpose_v3_71k\best.pt `
  --out benchmark\manifest\refpose_manifest_v1.json

# 门禁
.venv\Scripts\python.exe -m pytest tests\test_manifest_rebuild.py -v
```
