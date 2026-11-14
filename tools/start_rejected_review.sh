#!/usr/bin/env bash
# 在项目自有的 Motion Review 验收界面里查看被训练视图拒绝的动作。
#
# 四个片段（69/69/69/71 帧）都短于「窗口 48 + 历史 24 = 72」的门槛，因此进不了训练集。
# 这里把它们组织成一个合法的 --native-index 批次，用同一个服务播放，人工判断是否真的该拒。
#
# 用法：
#   tools/start_rejected_review.sh            # 默认端口 8766
#   tools/start_rejected_review.sh 8770       # 指定端口
set -euo pipefail

PORT="${1:-8766}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIBRARY="${MW_REVIEW_LIBRARY:-/home/acear/MotionWeaverData/review_library}"
INDEX="$LIBRARY/reports/soma_uniform/rejected-full-train-v1/export-index.csv"
PYTHON="$ROOT/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  echo "找不到 venv 解释器：$PYTHON" >&2
  exit 1
fi
if [[ ! -f "$INDEX" ]]; then
  echo "找不到验收批次索引：$INDEX" >&2
  echo "先跑：$PYTHON -m data.tools.review.build_rejected_review_library --help" >&2
  exit 1
fi

cd "$ROOT"
exec "$PYTHON" -m data.tools.review.motion_review_server \
  --library "$LIBRARY" --native-index "$INDEX" --port "$PORT"
