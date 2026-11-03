
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

ROOT = Path(os.environ.get("MW_ROOT") or Path(__file__).resolve().parents[2])
DATA = Path(os.environ.get("MW_DATA_ROOT") or "D:/MotionWeaverData")

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
