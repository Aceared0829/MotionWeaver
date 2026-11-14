#!/usr/bin/env bash
# 把长任务（训练 / 数据构建 / 批量评测）跑成独立的 systemd user unit，而不是 Agent 的子进程。
#
# 为什么必须这样：Agent 的每次 bash 调用都跑在 dsh-subprocess-<pid>-<hash>.scope 里，
# 调用结束或会话关闭，systemd 会把整个 scope 连根拔掉。结果有两种，都很贵：
#   1) 直接 SIGTERM 掉训练（2026-11-14 14:16 那轮 20-epoch，跑到 ep0 step1800 就没了）；
#   2) 训练侥幸留在别的 scope，桌面/DSH 一 OOM，内核把 deepseek-harness 杀了
#      （当天 15:31、15:50、16:02 各一次）——DSH 一死，跟着它的训练也断，日志停在 ep15。
#
# 本脚本做四件事，缺一不可：
#   systemd-run --user --unit=NAME   独立 unit，父进程是 systemd --user，不是 Agent
#   --collect                        退出后自动回收 unit，不留 failed 状态挡住下次同名启动
#   MemoryHigh / MemoryMax / Swap    给任务自己的内存天花板，超了只在本 cgroup 内回收/杀掉，
#                                    不会把整机推到全局 OOM（那才会挑 DSH 下手）
#   StandardOutput=append:LOG        输出直接进文件，状态用 systemctl 查，不需要守着管道
#
# 用法：
#   tools/run_detached.sh --name mw-train --log /path/train.log -- \
#       .venv/bin/python tools/train_reference_pose.py --epochs 20 ...
#
#   systemctl --user status mw-train        # 看状态与主 PID
#   journalctl --user -u mw-train -f        # 跟日志（文件里也有一份）
#   systemctl --user stop mw-train          # 只停这个 unit，绝不碰 DSH
set -euo pipefail

NAME=""
LOG=""
MEM_HIGH="8G"
MEM_MAX="12G"
SWAP_MAX="6G"
CPU_QUOTA=""
NICE="5"
WORKDIR="$PWD"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name)      NAME="$2"; shift 2 ;;
    --log)       LOG="$2"; shift 2 ;;
    --mem-high)  MEM_HIGH="$2"; shift 2 ;;
    --mem-max)   MEM_MAX="$2"; shift 2 ;;
    --swap-max)  SWAP_MAX="$2"; shift 2 ;;
    --cpu-quota) CPU_QUOTA="$2"; shift 2 ;;
    --nice)      NICE="$2"; shift 2 ;;
    --workdir)   WORKDIR="$2"; shift 2 ;;
    --) shift; break ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

if [[ -z "$NAME" ]]; then
  echo "缺少 --name（unit 名，如 mw-train）" >&2; exit 2
fi
if [[ $# -eq 0 ]]; then
  echo "缺少要执行的命令（放在 -- 之后）" >&2; exit 2
fi

# 同名 unit 还在跑就直接拒绝，避免两个训练抢同一份显存和输出目录。
if systemctl --user is-active --quiet "$NAME.service"; then
  echo "unit $NAME 已在运行；先 systemctl --user stop $NAME" >&2
  exit 3
fi

# 相对路径的可执行文件（.venv/bin/python）在 systemd 里没有 shell 帮忙补全，
# 先按 WORKDIR 解析成绝对路径；PATH 里的命令（如 python3）保持原样。
CMD="$1"
if [[ "$CMD" == */* && "$CMD" != /* ]]; then
  CMD="$WORKDIR/$CMD"
fi

PROPS=(
  --property=Type=simple
  --property=WorkingDirectory="$WORKDIR"
  --property=MemoryHigh="$MEM_HIGH"
  --property=MemoryMax="$MEM_MAX"
  --property=MemorySwapMax="$SWAP_MAX"
  --property=Nice="$NICE"
  # 任务 OOM 时优先牺牲它自己，不要牵连桌面和 DSH。
  # 必须比 DSH 的 300 更高：内核按 oom_score_adj 从大到小挑，值低的反而不容易被杀。
  --property=OOMScoreAdjust=500
  --property=StandardInput=null
)

if [[ -n "$CPU_QUOTA" ]]; then
  PROPS+=(--property=CPUQuota="$CPU_QUOTA")
fi

if [[ -n "$LOG" ]]; then
  mkdir -p "$(dirname "$LOG")"
  # append: 让续训也能接着写同一个日志文件，而不是每次清空。
  PROPS+=(--property=StandardOutput=append:"$LOG" --property=StandardError=append:"$LOG")
fi

systemd-run --user --unit="$NAME" --collect "${PROPS[@]}" -- "$CMD" "${@:2}"

echo "已提交 unit: $NAME"
echo "  状态：systemctl --user status $NAME --no-pager"
echo "  日志：${LOG:-journalctl --user -u $NAME -f}"
echo "  停止：systemctl --user stop $NAME   （只停这个任务，不影响 DSH）"
