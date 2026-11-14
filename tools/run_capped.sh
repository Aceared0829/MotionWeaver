#!/usr/bin/env bash
# 在「限核 + 限配额 + 低优先级」的 cgroup 里跑一个重 CPU 命令，避免把桌面拖死。
#
# 为什么需要它：数据构建（build_soma_training_set / build_refpose_release）是纯 CPU 的
# numpy 解压 + btrfs zstd 压缩写入，24 worker 能吃掉全部 32 个线程，
# load average 能冲到 180–315，鼠标和窗口都会卡。GPU 在这些阶段基本空闲（<10 W）。
#
# 做法：systemd-run --user --scope 建一个临时 scope，三件事一起上——
#   CPUQuota=<百分比>  cgroup v2 硬配额，超出就被 throttle（最可靠，先起作用的是它）
#   --nice=<值>        抢不到配额时也排在桌面进程后面
#   taskset -c <核>    把进程钉在指定的核上，剩下的核留给桌面
#
# 用法：
#   tools/run_capped.sh --quota 800% --cores 0-15 --nice 19 -- .venv/bin/python -m ...
#   tools/run_capped.sh -- .venv/bin/python -m ...      # 默认 50% × 16 核、nice 19
#
# 注意：quota 的百分比是相对「一个核」的，800% = 8 个核的算力。
set -euo pipefail

QUOTA="800%"
CORES="0-15"
NICE="19"
LOG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --quota) QUOTA="$2"; shift 2 ;;
    --cores) CORES="$2"; shift 2 ;;
    --nice)  NICE="$2"; shift 2 ;;
    --log)   LOG="$2"; shift 2 ;;
    --) shift; break ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

if [[ $# -eq 0 ]]; then
  echo "用法：$0 [--quota 800%] [--cores 0-15] [--nice 19] [--log 文件] -- <命令...>" >&2
  exit 2
fi

# 单核百分比 → systemd CPUQuota 接受 "800%" 这种写法，直接透传。
if [[ -n "$LOG" ]]; then
  exec systemd-run --user --scope -q --nice="$NICE" -p CPUQuota="$QUOTA" \
    -- taskset -c "$CORES" "$@" >"$LOG" 2>&1
else
  exec systemd-run --user --scope -q --nice="$NICE" -p CPUQuota="$QUOTA" \
    -- taskset -c "$CORES" "$@"
fi
