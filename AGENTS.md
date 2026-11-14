# MotionWeaver Agent 约定

本文件约束在本仓库工作的编码 Agent。根目录的 `README.md`、`docs/development.md`
和 `docs/repository-layout.md` 是环境与目录的权威说明。

## 先确认环境

- 优先使用仓库 `.venv`，不要假设系统 Python 已装齐依赖：
  `.venv/bin/python tools/dev.py doctor`（Windows: `.venv\Scripts\python.exe`）。
- 统一用 pytest，不要用 `unittest discover` 作为总入口；后者会漏掉 pytest
  fixture / parametrize 用例。完整命令：`.venv/bin/python -m pytest -q`。
- lint 只跑项目固定规则：`.venv/bin/python tools/dev.py lint`。不要一次性
  `ruff format` / `ruff check --fix` 全仓，当前有大量历史格式差异。

## 数据与路径

- 大权重、训练视图和 UE Content 不在 Git 里。缺数据时测试会 skip，这是预期。
- 数据根优先读 `MW_DATA_ROOT`，仓库资源定位优先读 `AIANIMATION_ROOT`；
  不要在代码里新增 `D:/MotionWeaverData` 或用户目录硬编码。
- `.build/` 混有可重建产物和历史证据，不要整目录删除。
- 不要提交 `data/raw/`、`data/prepared/`、`training/runs/`、检查点或 UE
  `Content/`、`Binaries/`、`Intermediate/`。

## 改动与验证

- 修改训练、导出、数据契约或评测口径时，先找对应测试；没有测试就先补最小回归。
- 动 `training/`、`inference/export/` 或 `model/` 后，至少跑相关测试文件，
  再跑 `.venv/bin/python tools/dev.py check`。
- 文档与注释使用项目现有的中文风格；提交信息沿用 `type(scope): 中文摘要`。
- 不要为了让测试变绿而放宽断言、吞异常或删除数据校验；先确认是环境缺失还是真回归。
- 不执行 `git commit` / 建分支，除非用户明确要求。

## 相关技能

`.agents/skills/` 下有动作批次处理、实验记录、动作复核和长任务运行技能；
涉及这些领域时先读对应 `SKILL.md`。
