"""续训状态恢复的契约测试：权重、优化器/调度器、epoch、best、history 都要接上。

为什么值得单独测：续训时如果只 load_state_dict 权重，LR 会从初值重新开始，
AdamW 的动量也归零——训练曲线看起来还在下降，但已经花掉的 epoch 收益被打散，
而且不会报任何错。这种损失只有对状态本身做断言才能提前发现。
"""

import json
import sys
from pathlib import Path

import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.train_reference_pose import restore_training_state  # noqa: E402

WIDTH, BONES, STEPS_PER_EPOCH = 256, 78, 100


def build(lr=3e-4, t_max=100):
    model = torch.nn.Linear(4, 3)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=0.01)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=t_max)
    return model, opt, sched


def train_steps(model, opt, sched, n):
    for _ in range(n):
        opt.zero_grad(set_to_none=True)
        model(torch.ones(2, 4)).sum().backward()
        opt.step()
        sched.step()


def make_ckpt(model, opt, sched, *, epoch, step, best=None, history=None, **overrides):
    ckpt = {"model": model.state_dict(), "width": WIDTH, "bones": BONES,
            "epoch": epoch, "step": step,
            "optimizer": opt.state_dict(), "scheduler": sched.state_dict()}
    if best is not None:
        ckpt["best"] = best
    if history is not None:
        ckpt["history"] = history
    ckpt.update(overrides)
    return ckpt


def test_full_restore_matches_weights_optimizer_and_learning_rate(tmp_path):
    trained, opt, sched = build()
    train_steps(trained, opt, sched, 10)
    ckpt = make_ckpt(trained, opt, sched, epoch=2, step=10, best=4.5)

    fresh, fresh_opt, fresh_sched = build()
    start_epoch, best, history, step = restore_training_state(
        ckpt, fresh, fresh_opt, fresh_sched,
        width=WIDTH, bones=BONES, steps_per_epoch=STEPS_PER_EPOCH, out=tmp_path)

    assert start_epoch == 3, "应从检查点 epoch+1 接着跑"
    assert step == 10
    assert best == 4.5
    for a, b in zip(trained.parameters(), fresh.parameters()):
        assert torch.equal(a, b), "权重必须逐位一致"
    assert fresh_sched.get_last_lr() == pytest.approx(sched.get_last_lr()), \
        "LR 必须停在同一位置，否则余弦退火会重头再来"
    # AdamW 的一二阶动量：取任意参数对应的 exp_avg 比对，确保不是空状态。
    state_a = opt.state_dict()["state"]
    state_b = fresh_opt.state_dict()["state"]
    assert set(state_a) == set(state_b)
    for key in state_a:
        assert torch.allclose(state_a[key]["exp_avg"], state_b[key]["exp_avg"]), \
            "优化器动量必须恢复，否则续训等于重新热身"


def test_history_file_wins_over_checkpoint_copy(tmp_path):
    trained, opt, sched = build()
    train_steps(trained, opt, sched, 3)
    # 检查点里记的是旧最优 9.9，磁盘上的 history.json 更新（含 4.25）。
    ckpt = make_ckpt(trained, opt, sched, epoch=1, step=3,
                     best=9.9, history=[{"epoch": 0, "val_pose_rmse_cm": 9.9}])
    (tmp_path / "history.json").write_text(
        json.dumps([{"epoch": 0, "val_pose_rmse_cm": 9.9},
                    {"epoch": 1, "val_pose_rmse_cm": 4.25}]), encoding="utf-8")

    fresh, fresh_opt, fresh_sched = build()
    start_epoch, best, history, step = restore_training_state(
        ckpt, fresh, fresh_opt, fresh_sched,
        width=WIDTH, bones=BONES, steps_per_epoch=STEPS_PER_EPOCH, out=tmp_path)

    assert start_epoch == 2
    assert len(history) == 2
    assert best == pytest.approx(4.25), "best 要取历史最小值，不能让续训把 best.pt 覆盖差权重"


def test_legacy_checkpoint_fast_forwards_scheduler(tmp_path):
    """2026-11-14 之前的检查点没有 optimizer/scheduler，只能按已跑步数把 LR 追上。"""
    trained, opt, sched = build()
    # epoch=1 意味着前两个 epoch（0 和 1）已经跑完，即 2 * steps_per_epoch = 20 步。
    train_steps(trained, opt, sched, 20)
    legacy = {"model": trained.state_dict(), "width": WIDTH, "bones": BONES,
              "epoch": 1, "val_pose_rmse_cm": 5.0}  # 无 step / optimizer / scheduler

    fresh, fresh_opt, fresh_sched = build()
    start_epoch, best, history, step = restore_training_state(
        legacy, fresh, fresh_opt, fresh_sched,
        width=WIDTH, bones=BONES, steps_per_epoch=10, out=tmp_path)

    assert start_epoch == 2
    assert step == 20, "缺 step 时按 start_epoch * steps_per_epoch 推算"
    assert fresh_sched.get_last_lr() == pytest.approx(sched.get_last_lr()), \
        "空转到已跑步数后 LR 应与原训练的同一位置一致"


def test_width_or_bone_mismatch_is_rejected(tmp_path):
    trained, opt, sched = build()
    ckpt = make_ckpt(trained, opt, sched, epoch=0, step=1, width=192)

    fresh, fresh_opt, fresh_sched = build()
    with pytest.raises(ValueError, match="配置不匹配"):
        restore_training_state(ckpt, fresh, fresh_opt, fresh_sched,
                               width=WIDTH, bones=BONES,
                               steps_per_epoch=STEPS_PER_EPOCH, out=tmp_path)

def test_param_group_lr_matches_scheduler_after_restore(tmp_path):
    """续训第一步必须按恢复后的 LR 更新。

    sched.load_state_dict 只写回调度器自己的 _last_lr，不会碰 optimizer 的
    param_groups；而 opt.step() 读的正是参数组里的 lr。不同步的话，续训第一步
    会按命令行 --lr（满学习率）更新一次，日志打印的却是调度器的 lr，看不出异常。
    """
    trained, opt, sched = build()
    train_steps(trained, opt, sched, 60)          # T_max=100，走到 60% 处
    expected_lr = sched.get_last_lr()[0]
    assert expected_lr != pytest.approx(3e-4), "前置条件：此时 LR 应已明显低于初值"
    ckpt = make_ckpt(trained, opt, sched, epoch=2, step=60)

    fresh, fresh_opt, fresh_sched = build()       # 新建的调度器给的是满 lr
    restore_training_state(ckpt, fresh, fresh_opt, fresh_sched,
                           width=WIDTH, bones=BONES, steps_per_epoch=STEPS_PER_EPOCH, out=tmp_path)

    assert fresh_opt.param_groups[0]["lr"] == pytest.approx(expected_lr), \
        "参数组必须同步到调度器的 LR，否则续训第一步按满学习率更新"


def test_larger_t_max_rebuilds_schedule_instead_of_rebounding(tmp_path):
    """把 --epochs 调大后续训，不能靠 load_state_dict 搬调度器状态。

    CosineAnnealingLR 越过 T_max 后余弦从 -1 折返，LR 沿原路回升。而
    load_state_dict 会把构造时给的 T_max 覆盖回检查点的旧值，于是「30 epoch 续训」
    实际得到的是「20 epoch 走到 0 之后重新升温」——语言上看是接着训，数值上是重新热身。
    """
    trained, opt, sched = build(t_max=100)
    train_steps(trained, opt, sched, 100)         # 走满 T_max，LR 归零
    assert sched.get_last_lr()[0] == pytest.approx(0.0)
    ckpt = make_ckpt(trained, opt, sched, epoch=9, step=100)

    # 目标 20 epoch（T_max=200）续训
    fresh, fresh_opt, longer = build(t_max=200)
    restore_training_state(ckpt, fresh, fresh_opt, longer,
                           width=WIDTH, bones=BONES, steps_per_epoch=10, out=tmp_path)

    assert longer.T_max == 200, "调用方给的 T_max 不能被检查点的旧值覆盖"
    assert longer.last_epoch == 100, "进度要停在已跑步数上"
    # 新表走到一半，LR 应处于中段（1.5e-4），而不是 0（折返起点）
    assert longer.get_last_lr()[0] == pytest.approx(1.5e-4), \
        "T_max 变大后应在新的余弦上取同一进度位置，而不是从 0 折返回升"
    assert fresh_opt.param_groups[0]["lr"] == pytest.approx(1.5e-4)


def test_epochs_smaller_than_progress_is_rejected(tmp_path):
    """--epochs 是总 epoch 数；给成比已跑还小的话，新调度表一开始就走到终点。"""
    trained, opt, sched = build(t_max=100)
    train_steps(trained, opt, sched, 50)
    ckpt = make_ckpt(trained, opt, sched, epoch=4, step=50)

    # 目标 T_max=40，小于检查点的 100，也小于已跑的 50 步
    fresh, fresh_opt, shorter = build(t_max=40)
    with pytest.raises(ValueError, match="--epochs 太小"):
        restore_training_state(ckpt, fresh, fresh_opt, shorter,
                               width=WIDTH, bones=BONES, steps_per_epoch=10, out=tmp_path)
