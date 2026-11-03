
"""这批一次性仪器的路径约定，集中一处。

为什么要有这个文件：inference/visualization 下十几个脚本原本各自硬编码
"D:/AILocomotonSystem/MotionWeaver" 和 "D:/MotionWeaverData/..."。
换机器、换盘符就得改十几处，而且改漏一处不报错——它只是读不到数据。

这里的每一个值都可以用同名环境变量覆盖，默认值等于本机现状：

    MW_ROOT        仓库根，默认按本文件位置向上两级
    MW_DATA_ROOT   数据根，默认 D:/MotionWeaverData

绝对路径不是设计，是这些脚本的一次性属性（参见 docs/refpose-game-sim-removed.md）。
真要产品化，第一步是把这里的常量变成 CLI 参数，而不是继续加环境变量。
"""
import os
from pathlib import Path


def _env_path(name, default, anchor=None):
    """读一个路径型环境变量，空串/纯空白一律回落到默认值。

    为什么要 strip：`os.environ.get(name) or default` 只能挡住空串，
    挡不住 "   "——那会变成相对当前工作目录的怪路径，而且不报错，
    只是所有数据都读不到，排查起来非常费劲。

    给了 anchor 时，相对路径按 anchor 解析（而不是按 CWD），
    这样在仓库外任何目录跑脚本，MW_DATA_ROOT=data 都指向同一个地方。
    """
    value = os.environ.get(name, "").strip()
    if not value:
        return Path(default)
    path = Path(value).expanduser()
    if anchor is not None and not path.is_absolute():
        path = Path(anchor) / path
    return path


ROOT = _env_path("MW_ROOT", Path(__file__).resolve().parents[2])
DATA = _env_path("MW_DATA_ROOT", "D:/MotionWeaverData", anchor=ROOT)

# 自检：ROOT 是按 __file__ 推的，挪动本文件会让它静默指错。
# 这一行把"静默错误"变成"import 时就炸"。
if not (ROOT / "inference").is_dir():
    raise RuntimeError(
        f"paths.ROOT 指向的不是仓库根：{ROOT}\n"
        "通常是因为 paths.py 被挪了位置，或者 MW_ROOT 设错了。"
    )

MMAP = DATA / "full_mmap"
RELEASE = DATA / "full_release"
CONTRACT = RELEASE / "contract" / "contract.json"
STATS = RELEASE / "contract" / "stats"

POSE_WEIGHT = DATA / "model_weight" / "refpose_v3_71k" / "best.pt"

# Root 头权重有两个候选，脚本按「先 ROOT_WEIGHT、没有再用 ROOT_WEIGHT_ALT」取。
# 历史沿革：早期版本首选 refpose_root_ep_grouped，但那个目录在本机从未存在过
# （is_file() 恒为 False），所以实际一直加载的是 20ep_grouped——11-02 的
# closed_loop/report.json 记的正是它。这里把两个槽位都落在真实存在的目录上，
# 不再留一条指向空气的回退分支。
ROOT_WEIGHT = DATA / "training_runs" / "refpose_root_20ep_grouped" / "best.pt"
ROOT_WEIGHT_ALT = DATA / "training_runs" / "refpose_root_20ep_seq" / "best.pt"
REVIEWS = DATA / "reviews" / "refpose-closed-loop"

OUT_PREVIEW = ROOT / "output" / "refpose_preview"
