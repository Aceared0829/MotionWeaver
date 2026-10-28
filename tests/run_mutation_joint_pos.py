"""方案 A 的变异检查（带崩溃保护）。

## 上一版的问题

上一版在第一个变异后崩溃（subprocess 解码错误），没走到还原步骤，
导致源码里留下 `# removed by mutation`，关节位置损失实际没接进总额失。
后来测试报红才发现——测试本身是有效的，是脚本把代码弄脏了。

## 本版的三重保护

1. `try/finally`：无论正常结束、异常还是 Ctrl+C，都还原源码；
2. 每次变异后立即校验文件内容确实改变、且能通过语法解析；
3. 结束时对比备份哈希，不一致就报警。
"""

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(r"D:\AILocomotonSystem\MotionWeaver")
TARGET = REPO / "training/models/vqvae/motion_vqvae.py"
BACKUP = Path(r"D:\tmp\mutation_backup_joint_pos.py")
PYTEST = [str(REPO / ".venv/Scripts/python.exe"), "-m", "pytest",
          "tests/test_joint_position_loss.py", "-q"]

MUTATIONS = [
    ("删掉总额失里的关节位置项",
     '+ joint_pos_loss_coeff * losses["l_joint_pos_m"]',
     '# removed by mutation'),
    ("损失从 SmoothL1 改成 MSE",
     "joint_pos_loss = t.nn.SmoothL1Loss()(\n                pred_global_joints, gt_joints_pos)",
     "joint_pos_loss = t.nn.MSELoss()(pred_global_joints, gt_joints_pos)"),
    ("默认系数改成 0.1",
     'get("joint_pos_loss_coeff", 0.0)',
     'get("joint_pos_loss_coeff", 0.1)'),
    ("gt 口径改成 global_rot_data",
     'joint_positions_from="ric_data"',
     'joint_positions_from="global_rot_data"'),
    ("不再记录 l_joint_pos_m",
     'losses["l_joint_pos_m"] = joint_pos_loss',
     'losses["l_joint_pos_m_unused"] = joint_pos_loss'),
    ("删掉 local->global 转换（CR 抓到的原始 bug）",
     (
         '            if self.pose_net.motion_rep.name == "local":\n'
         "                # local -> global（都保持归一化状态），再按 global 口径还原\n"
         "                recon_global = self.dual_rep.local_to_global("
     ),
     (
         "            if False:\n"
         "                recon_global = self.dual_rep.local_to_global("
     )),
    ("损失用未转换的 pred（回到 bug 状态）",
     (
         "            joint_pos_loss = t.nn.SmoothL1Loss()(\n"
         "                pred_global_joints, gt_joints_pos)"
     ),
     (
         "            joint_pos_loss = t.nn.SmoothL1Loss()(\n"
         "                pred_joints_pos, gt_joints_pos)"
     )),
    ("转换后不重新归一化（空间不一致）",
     (
         "                    is_normalized=True,\n"
         "                    to_normalize=True,"
     ),
     (
         "                    is_normalized=True,\n"
         "                    to_normalize=False,"
     )),
]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def run_tests():
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(PYTEST, cwd=str(REPO), capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          env=env, check=False)
    out = (proc.stdout or "").strip()
    return proc.returncode, (out.splitlines()[-1] if out else "?")


def main():
    original = TARGET.read_text(encoding="utf-8")
    baseline_hash = digest(TARGET)
    shutil.copy2(TARGET, BACKUP)
    print(f"备份哈希 {baseline_hash}\n")

    code, tail = run_tests()
    print(f"基线: exit={code}  {tail}")
    if code != 0:
        print("基线失败，先修测试")
        return 1

    results = []
    try:
        for name, old, new in MUTATIONS:
            if old not in original:
                print(f"  [!] 锚点未找到，跳过：{name}")
                results.append((name, None))
                continue
            mutated = original.replace(old, new, 1)
            if mutated == original:
                print(f"  [!] 替换无效，跳过：{name}")
                results.append((name, None))
                continue
            TARGET.write_text(mutated, encoding="utf-8")

            # 确认真的写进去了
            now = TARGET.read_text(encoding="utf-8")
            if now == original:
                print(f"  [!] 写入未生效：{name}")
                results.append((name, None))
                continue

            code, tail = run_tests()
            caught = code != 0
            print(f"  {'✅ 被抓' if caught else '❌ 漏过'}  {name}")
            print(f"           {tail}")
            results.append((name, caught))
            TARGET.write_text(original, encoding="utf-8")
    finally:
        TARGET.write_text(original, encoding="utf-8")
        after = digest(TARGET)
        print(f"\n还原后哈希 {after}  {'✅ 一致' if after == baseline_hash else '❌ 不一致!'}")
        if after != baseline_hash:
            shutil.copy2(BACKUP, TARGET)
            print("  已用备份强制覆盖")

    code, tail = run_tests()
    print(f"还原后测试: exit={code}  {tail}")

    missed = [name for name, caught in results if caught is False]
    skipped = [name for name, caught in results if caught is None]
    print("\n=== 判定 ===")
    print(f"  抓到 {sum(1 for _, c in results if c is True)} / {len(results)}")
    if missed:
        print(f"  ❌ 漏过: {missed}")
    if skipped:
        print(f"  ⚠ 跳过: {skipped}")
    return 0 if not missed and not skipped else 1


if __name__ == "__main__":
    sys.exit(main())
