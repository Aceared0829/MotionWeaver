# MotionWeaver 技术报告

本目录存放 MotionWeaver 的技术报告，同时提供 **HTML**（在线阅读、可贴到站点）与
**PDF**（分发、打印、投稿）两种版本。作者：赵梓宁（独立研究者）。排版参照 arXiv 上常见的人工智能论文惯例：
单栏正文、Times 系衬线、booktabs 风格三线表、图表随文编号、中英双摘要。

## 文件

| 文件 | 说明 |
| --- | --- |
| `main.html` | 报告正文。**唯一需要手工编辑的源文件**：样式与内容都在这里，无外部依赖、无 CDN。 |
| `MotionWeaver-technical-report.pdf` | 由 `main.html` 渲染出的 PDF（A4，15 页）。**不要手工改这个文件**，改 HTML 后重新生成。 |
| `generate_figures.py` | 生成 `figures/` 下全部插图的脚本，数值直接读仓库内已提交的证据文件。 |
| `build_pdf.py` | 调用本机 Chrome / Edge 的 headless 模式把 HTML 打印成 PDF，并校验页数、体积与哈希。 |
| `figures/` | 六张插图（PNG，220 dpi）。 |

## 重新生成

在**仓库根目录**执行：

```powershell
# 1. 只改了证据数据 / 想重画图
.\.venv\Scripts\python.exe paper\generate_figures.py

# 2. 改了正文或图，重新出 PDF
.\.venv\Scripts\python.exe paper\build_pdf.py
```

`build_pdf.py` 会自动查找 Chrome 或 Edge；找不到时用
`--browser "C:/Program Files/Google/Chrome/Application/chrome.exe"` 显式指定。

**为什么用浏览器打印而不是 LaTeX / pandoc / wkhtmltopdf**：本报告的图表避断
（`break-inside: avoid`）、三线表、打印专用的字号与行高分支，只有完整的现代排版引擎
能正确处理。Chromium 的 `--print-to-pdf` 会真实执行 `@media print`，因此 HTML 与 PDF
是同一份源文件的两种呈现，不会出现"两边不一致"。

## 内容边界

报告中的每个数字都来自仓库内已提交的证据文件，并在正文中标明其条件
（配对教师轨迹 / 控制器轨迹 / oracle 泄漏诊断）。以下三条是刻意的写法，改动时请保留：

1. **不把训练指标当作产品质量。** Token 交叉熵改善与"玩家可操控"是两件事，
   §7 专门列了当前证据**不能**支撑的结论。
2. **失败案例与改善案例并列。** Run / Crouch 的退化、脚滑未解决、蹲伏无响应都写进正文，
   不做平均、不选优报告。
3. **每个指标都带口径限制。** 表 5 与表 12 是"不可跨越的边界"清单，删掉它们会让报告失真。

## 待办

- [x] 作者署名与单位已确定：赵梓宁（独立研究者），zzn1982638980@gmail.com。
- [ ] 训练视图构建完成后，补一张含场景 C/D 的完整数据统计表（当前表 1 是 46.7% 的快照）。
- [ ] 若 SOMA 数据正式接入训练，§6 需要新增一组 SOMA 划分下的结果，不能与现有 UE 结果混算。
