---
name: run-detached-jobs
description: 在 MotionWeaver 里启动、观察和停止训练/数据构建/评测等长任务，让它们跑成独立 systemd user unit 而不是 Agent 的子进程；排查任务中途消失、DSH 被 OOM 杀掉、GPU 闲置时使用。
---

# 长任务与 Agent 分离

本项目所有耗时长于几分钟的进程（refpose 训练、mmap/release 构建、批量评测、overnight 数据流水线）都必须跑在独立的 systemd user unit 里。任务的生命周期不能挂在 Agent 上，也不能挂在桌面会话上。

## 为什么（两次真实损失，2026-11-14）

1. **Agent 子进程会被连根拔掉。** Agent 每次 bash 调用都落在 `dsh-subprocess-<pid>-<hash>.scope`。调用结束或会话关闭，systemd 停掉整个 scope，里面的训练收到 SIGTERM。14:16 那轮 20-epoch 训练跑到 ep0 step1800（711s）无声消失，日志尾部只有 `multiprocessing.resource_tracker: 32 leaked semaphore objects`，没有任何异常栈。
2. **训练会带着 DSH 一起死。** Electron 渲染进程固定 `oom_score_adj=300`，是内核 OOM 时最先被挑的目标；systemd 对 `app-*.scope` 默认 `OOMPolicy=stop`，渲染进程一死整个 DSH scope 被停掉。当天 15:31、15:50、16:02 各发生一次，16:02 那次训练正跑到 ep15 step118000，进程表里能看到训练进程 454596 与 DSH 一起被扫掉。

结论：**任务要有自己的 unit、自己的 cgroup 配额、自己的日志文件**；DSH 只是观察者。

## 标准做法

用 `tools/run_detached.sh` 启动，不要用 `nohup`、`setsid`、`&`：

```bash
tools/run_detached.sh --name mw-train \
  --log /home/acear/MotionWeaverData/train_v2_20ep.log \
  --mem-high 12G --mem-max 18G --swap-max 8G --nice 5 \
  -- .venv/bin/python tools/train_reference_pose.py \
     --release /home/acear/MotionWeaverData/full_release_v2 \
     --mmap-root /home/acear/MotionWeaverData/full_mmap_v2 \
     --output /home/acear/MotionWeaverData/runs/refpose_v2_20ep \
     --epochs 20 --batch-size 256 --width 256 --lr 3e-4 --seed 42 \
     --log-every 100 --workers 8 --contact-weight 0.4 --chunk-size 16 \
     --resume /home/acear/MotionWeaverData/runs/refpose_v2_20ep/last.pt
```

观察与停止：

```bash
systemctl --user status mw-train --no-pager     # 状态 + MainPID + 内存
systemctl --user show mw-train -p MainPID -p MemoryCurrent -p OOMScoreAdjust
tail -f /home/acear/MotionWeaverData/train_v2_20ep.log
systemctl --user stop mw-train                 # 只停任务，绝不碰 DSH
```

脚本固化的四个属性，缺一不可：

| 属性 | 值 | 为什么 |
| --- | --- | --- |
| `--unit` | 任务名 | 父进程是 `systemd --user`，不在 Agent scope 里 |
| `--collect` | — | 退出后回收 unit，`failed` 状态不会挡住下次同名启动 |
| `MemoryHigh/MemoryMax` | 默认 8G/12G | 超限只在本 cgroup 内回收或杀掉，不推整机进全局 OOM |
| `OOMScoreAdjust` | 500 | 必须**高于** DSH 的 300；内核按该值从大到小挑，值高才先被杀 |

## 训练必须能续训

`tools/train_reference_pose.py` 支持 `--resume <last.pt>`：恢复权重、AdamW 状态、余弦退火调度器、`epoch`、`best` 与 `history`，然后只补跑剩下的 epoch。检查点里同时保存 `optimizer`/`scheduler`/`step`/`best`/`history`；老检查点缺这些字段时，按已跑步数把 LR 空转到同一位置。

只存权重不存优化器会让续训把 LR 与动量重新起步，之前的 epoch 等于白跑——这是必须留 `optimizer` 的原因。

## 环境变量必须用 --env 显式传

`systemd-run` 起的进程**不继承调用方的环境变量**——它不走 shell，由 systemd 按 unit 配置 fork。所以这种写法是错的：

```bash
# 错的：RUN_DIR 会被静默丢弃，脚本回落到默认值
RUN_DIR=/path/to/run tools/run_detached.sh --name mw-eval -- tools/run_full_evaluation.sh
```

必须写成：

```bash
tools/run_detached.sh --name mw-eval \
  --env RUN_DIR=/home/acear/MotionWeaverData/runs/refpose_v2_30ep \
  --env OUT_DIR=/home/acear/MotionWeaverData/reports/refpose_v2_30ep_test \
  -- tools/run_full_evaluation.sh
```

2026-11-14 就是这样踩的：延长训练后的评测用了默认 `RUN_DIR`，把旧的 20ep 检查点重算了一遍，报告覆盖在旧目录里，30ep 的 `best.pt` 一次都没被评测到——日志看着全程正常，只有 `model.json` 里的 `checkpoint` 字段能看出来。**评测跑完先核对 `model.json` 的 `checkpoint` 和 `epoch` 是不是你要的那一份。**

## 连评测 watcher 也要按 unit 等

`tools/run_full_evaluation.sh` 通过 `TRAIN_UNIT`（默认 `mw-train`）轮询 `systemctl --user is-active`，不要写死训练 PID：续训会换主 PID，写死 PID 的 watcher 会在训练还跑着的时候就去抢显存。注意 `TRAIN_UNIT` 也是环境变量，同样要用 `--env` 传。

watcher 一旦发现训练 unit 退出就会立刻开跑评测。所以**不要在训练已结束后才启动 watcher**——它会在第一轮轮询就直接开跑，此时如果你还没想清楚用哪个 `RUN_DIR`，就会评错权重。要事后补评，直接跑 `tools/run_full_evaluation.sh`（此时无需 watcher）。

## 排查清单

任务中途消失时按顺序确认：

1. `systemctl --user status <unit>` 的 `Result=`：`oom-kill` 指向内存，`signal` 指向被杀。
2. `journalctl -k --since '-30min' | grep 'Killed process'`：确认被杀的是谁、`oom_score_adj` 多少。
3. `journalctl --user --since '-30min' | grep -iE 'oom|kill'`：`systemd-oomd` 也会按 scope 杀进程组。
4. 训练日志的最后一行：停在 epoch 边界说明是外部信号，停在 step 中间且有 `leaked semaphore` 说明父 scope 被拆。
5. 日志里有没有 `续训：... epoch N 起`：确认重启确实接上了状态，而不是从 ep0 重跑。

## 红线

- 不要为了让任务活下来去 kill DSH、改 DSH 的 `oom_score_adj`、或关 `systemd-oomd`。任务是可重跑的计算，DSH 是交互现场。
- 不要在两个 unit 里同时跑同一份输出目录的训练。
- 不要在 Agent 的 bash 里用 `&` 起长任务后立刻返回——调用结束它就没了。
