#!/bin/bash
# 训练完成后自动跑两级评测：模型四档 + 非学习基线四档，并合并成一份对照报告。
#
# 为什么串成一个脚本而不是手动跑：训练结束时刻不确定（约 16:15），
# 而 GPU 评测必须等训练释放显存才有意义；交给 watcher 等进程退出最稳。
#
# 用法（走独立 unit，不要用 setsid nohup，否则会被会话 scope 连根拔掉）：
#   tools/run_detached.sh --name mw-eval --mem-high 6G --mem-max 10G \
#       --log /home/acear/MotionWeaverData/eval_v2.log -- tools/run_full_evaluation.sh
set -u
cd /home/acear/项目/MotionWeaver || exit 1

PY=.venv/bin/python
RELEASE=/home/acear/MotionWeaverData/full_release_v2
MMAP=/home/acear/MotionWeaverData/full_mmap_v2
# 运行目录与报告目录可覆盖：延长训练（warm restart）会写到新的运行目录，
# 评测必须跟着换成同一次训练产出的 best.pt，不能沿用上一轮的。
RUN="${RUN_DIR:-/home/acear/MotionWeaverData/runs/refpose_v2_20ep}"
OUT="${OUT_DIR:-/home/acear/MotionWeaverData/reports/refpose_v2_test}"
# 默认按 systemd unit 名等，而不是写死 PID：续训会换主 PID，写死 PID 的 watcher
# 会在训练还跑着的时候就去抢显存（2026-11-14 那次就踩了这个）。
TRAIN_UNIT=${TRAIN_UNIT:-mw-train}

stamp() { date "+[%H:%M:%S] $*"; }

stamp "等待训练 unit $TRAIN_UNIT 退出"
while systemctl --user is-active --quiet "$TRAIN_UNIT.service"; do sleep 20; done
stamp "训练 unit 已退出，开始评测"

if [ ! -f "$RUN/best.pt" ]; then
  stamp "缺少 $RUN/best.pt，中止"; exit 1
fi
mkdir -p "$OUT"

stamp "1/3 模型四档硬参考（test split，每条 clip 取中心窗口）"
$PY tools/evaluate_reference_pose.py \
  --release "$RELEASE" --mmap-root "$MMAP" \
  --checkpoint "$RUN/best.pt" \
  --output "$OUT/model.json" --split test \
  || { stamp "模型评测失败"; exit 2; }

stamp "2/3 非学习基线（hold / linear_reference，同选窗口径）"
# 并行版：串行版单核跑 37 分钟，16 进程降到几分钟；workers 保守取 12，
# 免得和用户其他工作抢核。
#
# 基线只依赖契约、split 和选窗口径，跟模型权重无关。评测入口对已存在的输出目录
# 直接抛 FileExistsError（防覆盖），所以重复评测（续训后换 best.pt 再跑一次）必须
# 复用旧基线，不能每次都删——上一版脚本就是因为这个在第 2 步整段失败。
if [ -f "$OUT/baselines/report.json" ] && [ "${FORCE_BASELINES:-0}" != "1" ]; then
  stamp "  复用已有基线：$OUT/baselines/report.json（要重算设 FORCE_BASELINES=1）"
else
  $PY -m training.evaluation.evaluate_conditioned_baselines_parallel \
    --contract "$RELEASE/contract" \
    --output "$OUT/baselines" --split test --per-clip 1 --workers 12 \
    || { stamp "基线评测失败"; exit 3; }
fi

stamp "3/3 合并对照报告"
$PY tools/merge_evaluation_report.py \
  --model "$OUT/model.json" \
  --baselines "$OUT/baselines/report.json" \
  --output "$OUT/comparison.json" --markdown "$OUT/comparison.md" \
  || { stamp "合并失败"; exit 4; }

stamp "完成：$OUT/comparison.md"
