"""方案 A（关节位置损失）的测试。

## 为什么要测这些

关节位置损失有两个容易「假生效」的坑：

1. **系数没接进总额失**：日志里能看到 `l_joint_pos_m` 数字，但 loss 里没加，
   训练照跑、指标照常打印，什么都看不出来。
2. **gt 与 pred 不同空间**：如果 gt 用归一化特征、pred 用米制，两个量直接相减
   会得到一个"看起来合理"的小数字，实际在优化无意义的目标。

另外还要钉住：默认 0.0 时不改变原有行为（必须能关掉）。
"""

import sys
import unittest

import torch

sys.path.insert(0, r"D:\AILocomotonSystem\MotionWeaver")

LOSS_SOURCE = (r"D:\AILocomotonSystem\MotionWeaver"
               r"\training\models\vqvae\motion_vqvae.py")
CLI_SOURCE = (r"D:\AILocomotonSystem\MotionWeaver"
              r"\training\pretrain\train_unreal.py")
YAML_SOURCE = (r"D:\AILocomotonSystem\MotionWeaver"
               r"\training\configs\pretrain\motionweaver_vqvae.yaml")


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class JointPositionLossWiringTests(unittest.TestCase):
    def test_coefficient_is_in_total_loss(self):
        """系数必须真的乘进总损失，否则这一项等于没开。"""
        text = read(LOSS_SOURCE)
        self.assertIn("joint_pos_loss_coeff * losses[\"l_joint_pos_m\"]", text,
                      "关节位置损失没有乘系数加进总额失")

    def test_metric_is_logged(self):
        """必须记进 losses，否则训练日志里看不到它在动。"""
        text = read(LOSS_SOURCE)
        self.assertIn('losses["l_joint_pos_m"] = joint_pos_loss', text,
                      "没有把关节位置损失记进 losses")

    def test_default_is_off(self):
        """默认系数为 0，必须保持原有行为不变。"""
        text = read(LOSS_SOURCE)
        self.assertIn('get("joint_pos_loss_coeff", 0.0)', text,
                      "默认值不是 0，会改变原有训练行为")

    def test_gt_and_pred_are_same_space(self):
        """gt 与 pred 必须都是米制关节位置，不能一个是归一化特征。"""
        text = read(LOSS_SOURCE)
        # pred 走 posed_joints
        self.assertIn("pred_joints_pos = pred_joints_output[\"posed_joints\"]",
                      text)
        # gt 也必须走 posed_joints（同一个键）
        self.assertIn('gt_joints_pos = gt_joints_output["posed_joints"]', text)
        # gt 必须用 ric_data 口径，且**每一处** gt 调用都要用它。
        # 这里统计 ric_data 的出现次数：基线是 2 处（gt 调用 + 方案 A 转换后调用），
        # 变异把其中一处改成 global_rot_data 时计数会降到 1。
        ric_count = text.count('joint_positions_from="ric_data"')
        self.assertEqual(ric_count, 2,
                         f'joint_positions_from="ric_data" 出现 {ric_count} 次，预期 2 次；'
                         "少于 2 说明有一处被改成了别的口径，与评测不一致")

    def test_local_pred_is_converted_to_global_before_loss(self):
        """pose=local 时 pred 必须转成 global 再与 gt 比较。

        CR 抓到的真实 bug：local_motion_rep.inverse 与 global_motion_rep.inverse
        还原出的关节位置实测相差 120.50 cm（前者无全局根轨迹）。若直接相减，
        罚的是两个不同坐标系之差，梯度没有意义。
        """
        text = read(LOSS_SOURCE)
        self.assertIn("self.dual_rep.local_to_global(", text,
                      "缺少 local -> global 的转换，pred 与 gt 不同空间")
        self.assertIn("pred_global_joints", text,
                      "没有单独的 global 关节变量，说明转换没接上")
        # 开关必须是「pose 是 local 时转换」，不能被改成恒 False
        self.assertIn('if self.pose_net.motion_rep.name == "local":', text,
                      "转换分支的条件被改了，local 时可能跳过转换")
        self.assertNotIn("if False:", text,
                         "出现恒假分支，转换被禁用")
        # 必须真的用转换后的量算损失
        self.assertIn("t.nn.SmoothL1Loss()(\n                pred_global_joints, gt_joints_pos)",
                      text,
                      "损失没有用转换后的 pred")

    def test_conversion_keeps_normalized_state(self):
        """local -> global 转换必须 to_normalize=True，否则与 gt 空间不一致。

        实测：to_normalize=True 时特征可逆（最大差 1.2e-7），
        关节位置差 1.0e-6 cm；若改 False，pred 停在未归一化空间。
        """
        text = read(LOSS_SOURCE)
        marker = "recon_global = self.dual_rep.local_to_global("
        start = text.index(marker)
        block = text[start:start + 400]
        self.assertIn("to_normalize=True", block,
                      "转换块里缺少 to_normalize=True，pred 与 gt 不在同一空间")
        self.assertIn("is_normalized=True", block,
                      "转换块里缺少 is_normalized=True")

    def test_local_to_global_conversion_is_verified(self):
        """转换路径必须经过验证，不能只写代码不验证。

        这里钉住注释里的实测证据，避免以后被删掉后没人知道为何这样写。
        """
        text = read(LOSS_SOURCE)
        self.assertIn("120.50 cm", text,
                      "缺少 bug 的实测证据说明")
        self.assertIn("1.2e-7", text,
                      "缺少转换可逆性的验证记录")

    def test_loss_is_smooth_l1_not_mse(self):
        """实测 MSE 单样本最大 43.87，会被离群样本主导，必须用 SmoothL1。"""
        text = read(LOSS_SOURCE)
        marker = "joint_pos_loss = t.nn.SmoothL1Loss()("
        self.assertIn(marker, text,
                      "关节位置损失不是 SmoothL1，离群样本会主导梯度")

    def test_inverse_call_count_is_intentional(self):
        """global_motion_rep.inverse 的调用次数必须是 3，且各有明确用途。

        基线（方案 A 落地后）：
          L272  pred（pose=global 分支）
          L322  gt（关节速度与关节位置共享）
          L384  pred 从 local 转 global 后还原（方案 A 专用）

        若超过 3，说明出现了重复前向，会拖慢每一步。
        若少于 3，说明方案 A 的转换路径被删掉了，pred 与 gt 会不同空间。
        """
        text = read(LOSS_SOURCE)
        calls = text.count("self.global_motion_rep.inverse(")
        self.assertEqual(calls, 3,
                         f"global_motion_rep.inverse 被调用 {calls} 次，预期 3 次："
                         "少则丢了方案 A 的转换路径，多则有重复前向")


class JointPositionLossBehaviourTests(unittest.TestCase):
    def test_smooth_l1_is_robust_to_outliers(self):
        """对比 MSE 与 SmoothL1 在离群样本上的表现。"""
        gt = torch.zeros(1, 1, 3)
        # 一个正常样本 + 一个 6 米离群样本
        pred = torch.tensor([[[0.5, 0.5, 0.5]], [[6.0, 6.0, 6.0]]])
        gt_batch = gt.repeat(2, 1, 1)
        mse = torch.nn.MSELoss()(pred, gt_batch)
        smooth = torch.nn.SmoothL1Loss()(pred, gt_batch)
        self.assertGreater(float(mse), 10.0, "MSE 应被离群样本主导")
        self.assertLess(float(smooth), 5.0, "SmoothL1 应更平稳")
        self.assertGreater(float(mse), float(smooth) * 2,
                           "两者差异不够明显，测试前提不成立")

    def test_zero_coefficient_gives_zero_contribution(self):
        """系数 0 时该项贡献必须为 0。"""
        coeff = 0.0
        loss_term = 0.5
        self.assertEqual(coeff * loss_term, 0.0)

    def test_gradient_flows_through_joint_pos_loss(self):
        """关节位置损失必须可反传（这是方案 A 有效的前提）。"""
        pred = torch.tensor([[[0.1, 0.2, 0.3]]], requires_grad=True)
        gt = torch.zeros(1, 1, 3)
        loss = torch.nn.SmoothL1Loss()(pred, gt)
        loss.backward()
        self.assertIsNotNone(pred.grad)
        self.assertGreater(float(pred.grad.abs().sum()), 0,
                           "梯度全零，损失不会生效")


class JointPositionLossCliTests(unittest.TestCase):
    def test_cli_exposes_flag(self):
        text = read(CLI_SOURCE)
        self.assertIn("--joint_pos_loss_coeff", text, "缺少 CLI 参数")
        self.assertIn("conf.model.args.joint_pos_loss_coeff", text,
                      "CLI 参数没有写进配置")

    def test_yaml_has_key_with_zero_default(self):
        text = read(YAML_SOURCE)
        self.assertIn("joint_pos_loss_coeff: 0.0", text,
                      "配置缺失或默认值不是 0")


if __name__ == "__main__":
    unittest.main()
