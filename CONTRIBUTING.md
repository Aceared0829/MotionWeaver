# 贡献指南

完整的开发环境、依赖分组、测试与数据路径说明见
[docs/development.md](docs/development.md)。

## 快速检查

```bash
.venv/bin/python tools/dev.py doctor
.venv/bin/python tools/dev.py lint
.venv/bin/python -m pytest -q
```

Windows 将 `.venv/bin/python` 换成 `.venv\Scripts\python.exe`。

## 提交前

- 改动聚焦，不顺手格式化全仓；当前仓库尚未统一历史代码格式。
- 新增数据、训练或导出行为时补对应回归测试。
- 不提交权重、训练视图、UE `Content/`、`Binaries/`、`Intermediate/` 或本机绝对路径。
- 提交信息沿用现有风格，例如 `fix(train): 续训后学习率按进度恢复`。

## 数据与权重

仓库只包含代码、契约和小型夹具。缺数据时相关测试会 skip，属于预期状态。
数据根用 `MW_DATA_ROOT`，仓库资源根用 `AIANIMATION_ROOT` 覆盖，不要写死盘符。

## CI 覆盖范围

`.github/workflows/checks.yml` 只跑无 GPU、无大数据的静态检查与仓库守卫测试。
训练、ONNX、MuJoCo 与 UE 相关验证仍需本机环境，命令见开发文档。
