"""benchmark 包用到的路径约定：仓库根、数据根、以及可覆盖的数据路径。

为什么要有这个文件：benchmark 下的脚本原本各自硬编码
"D:/AILocomotonSystem/MotionWeaver" 与 "D:/MotionWeaverData/..."。
换机器、换盘符要改十几处，改漏一处不报错——它只是读不到数据，
然后以 FileNotFoundError 或"评估拿到空表"的形式在下游炸掉。

两套环境变量，与仓库既有约定保持一致：

    MW_ROOT        仓库根，默认按本文件位置向上推一级
    MW_DATA_ROOT   数据根，默认 D:/MotionWeaverData（Windows 上的历史位置）

数据根下的具体子目录还可以单独覆盖（换机器时最常用）：

    MW_VIEWS      训练视图目录，默认 <数据根>/soma_train_v1/full-v1-f32
    MW_RELEASE    契约发布目录，默认 <数据根>/full_release
    MW_MMAP       mmap 训练集，默认 <数据根>/full_mmap
    MW_RUNS       训练产物根，默认 <数据根>/training_runs
    MW_REPORTS    报告输出根，默认 <数据根>/reports

绝对路径不是设计，是这些脚本的一次性属性。真要产品化，第一步是把这里的
常量变成 CLI 参数，而不是继续加环境变量。
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _env_path(name, default, anchor=None):
    """读一个路径型环境变量，空串/纯空白一律回落到默认值。

    为什么要 strip：`os.environ.get(name) or default` 只能挡住空串，
    挡不住 "   "——那会变成相对当前工作目录的怪路径，而且不报错，
    只是所有数据都读不到，排查起来非常费劲。
    """
    value = os.environ.get(name, "").strip()
    if not value:
        return Path(default)
    path = Path(value).expanduser()
    if anchor is not None and not path.is_absolute():
        path = Path(anchor) / path
    return path


DATA = _env_path("MW_DATA_ROOT", "D:/MotionWeaverData", anchor=ROOT)
VIEWS = _env_path("MW_VIEWS", DATA / "soma_train_v1" / "full-v1-f32", anchor=ROOT)
RELEASE = _env_path("MW_RELEASE", DATA / "full_release", anchor=ROOT)
MMAP = _env_path("MW_MMAP", DATA / "full_mmap", anchor=ROOT)
RUNS = _env_path("MW_RUNS", DATA / "training_runs", anchor=ROOT)
REPORTS = _env_path("MW_REPORTS", DATA / "reports", anchor=ROOT)
WEIGHTS = DATA / "model_weight"


def data_path(*parts):
    """拼一个数据根下的路径。"""
    return DATA.joinpath(*parts)
