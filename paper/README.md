# MotionWeaver 技术报告

本目录存放 MotionWeaver 的技术报告，同时提供 **HTML**（在线阅读、可贴到站点）与
**PDF**（分发、打印、投稿）两种版本。作者：赵梓宁（独立研究者）。排版参照 arXiv 上常见的人工智能论文惯例：
单栏正文、Times 系衬线、booktabs 风格三线表、图表随文编号、中英双摘要、编号公式。

## 文件

| 文件 | 说明 |
| --- | --- |
| `main.html` | 报告正文。**唯一需要手工编辑的源文件**：样式与内容都在这里，不依赖 CDN。 |
| `MotionWeaver-technical-report.pdf` | 由 `main.html` 渲染出的 PDF（A4，20 页，23 个编号公式）。**不要手改**，改 HTML 后重新生成。 |
| `generate_figures.py` | 生成 `figures/` 下全部插图的脚本，数值直接读仓库内已提交的证据文件。 |
| `build_pdf.py` | 调本机 Chrome / Edge 的 headless 模式把 HTML 打印成 PDF，并校验页数、体积与哈希。 |
| `figures/` | 六张插图（PNG，220 dpi）。 |
| `vendor/tex-svg-full.js` | MathJax 3.2.2 的 TeX→SVG 组件（2.2 MiB），随仓库分发以保证离线可用。 |
| `vendor/tex-svg.js` | 同上的非 full 版本，保留作精简部署备选。 |

## 公式

正文用 LaTeX 数学语法，由**本地随附的 MathJax 3（SVG 输出）**渲染，
加载的是 `vendor/tex-svg-full.js`，不访问 CDN，离线与内网都能看。

选 SVG 而不是 HTML-CSS/CHTML 的理由：SVG 把字形路径直接写进页面，
打印成 PDF 时**不需要安装任何 TeX 字体**，也不会出现「屏幕正常、打印缺字」。
代价是 HTML 体积约 76 KiB，外加 2.2 MiB 的库文件。

排版约定：

- 行间公式用 `\[ ... \]`，编号手写 `<span class="eqno">(3.4)</span>`；
- 行内公式用 `$ ... $`；
- **数学里不要出现裸的 `<` 或 `>`**。浏览器会把 `z_{<t}` 当成 HTML 标签起始，
  该公式整段不渲染。改用 `\lt` / `\gt`——第一版就踩过这个坑；
- 多行或含 `\sum`、`\prod`、`bmatrix` 的公式加 `class="eq multi"`，
  只有单行公式才禁止跨页；否则浏览器会把整块推到下一页，出现半页乃至整页空白。

这两类问题**都不会报错**，只在渲染结果里看得出来。改完公式务必重跑
`build_pdf.py` 并翻一遍 PDF 再提交。

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
是同一份源文件的两种呈现，不会出现「两边不一致」。

## 内容边界

报告中的每个数字都来自仓库内已提交的证据文件，并在正文中标明其条件
（配对教师轨迹 / 控制器轨迹 / oracle 泄漏诊断）。以下三条是刻意的写法，改动时请保留：

1. **不把训练指标当作产品质量。** Token 交叉熵改善与「玩家可操控」是两件事，
   §8 专门列了当前证据**不能**支撑的结论。
2. **失败案例与改善案例并列。** Run / Crouch 的退化、脚滑未解决、蹲伏无响应都写进正文，
   不做平均、不选优报告。
3. **每个指标都带口径限制。** 表 6 与表 13 是「不可跨越的边界」清单，删掉会让报告失真。

## 待办

- [x] 作者署名与单位已确定：赵梓宁（独立研究者），zzn1982638980@gmail.com。
- [x] 训练视图构建完成后补一张含场景 C/D 的完整数据统计表：2026-10-26 完成。
      表 2 现为最终值（72,933 条 / 99.77% / 1,166,446 窗口，四场景齐全）。
- [ ] 若 SOMA 数据正式接入训练，§7 需新增一组 SOMA 划分下的结果，不能与现有 UE 结果混算。
- [x] 训练工程优化已写入 §6（实时数据集、四元数存储、并行上限、运行时参数）。
- [ ] bf16 与 FP32 的损失曲线对照尚未执行，§6.6 已声明为未做项。
- [ ] 附录 B 的证据索引需随新实验补充。
- [ ] 训练链路性能分析（`docs/soma-training-performance-analysis.md`）中的
      `num_workers` / bf16 / 缓存三项优化尚未实施；实施后需在 §5 补一节说明吞吐变化。
