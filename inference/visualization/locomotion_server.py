"""实时 Locomotion 模拟服务。

Root 由算法生成（对标 CMC），姿态网只补身体。坐标：
  模型内部是训练坐标系（前 +Z、上 +Y、右 -X）；
  发给页面的是 UE 坐标（前 +X、右 +Y、上 +Z）。

协议（WebSocket JSON）：
  <- {"type":"step","x":摇杆右,"y":摇杆前,"run":false}
  <- {"type":"reset"}
  -> {"type":"hello", ...}
  -> {"type":"step","joints":[...],"roots":[...],"trail":[...],"yaw","ms","speed"}
"""
import asyncio
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "inference" / "visualization"))
import paths as P
import render_refpose_gifs as g
from ue_axis import vec_to_ue

from inference.runtime.conditioned_pose import rotation_6d_to_matrix
from training.pretrain.train_conditioned_pose import ReferenceGuidedPose

POSE_W = P.POSE_WEIGHT
HOST, PORT = "127.0.0.1", 8822
WINDOW, PLAY = 24, 8
FPS = 120.0
DT = 1.0 / FPS
WALK, RUN = 1.5, 4.0
TURN = np.deg2rad(360.0)
# 超过这个速度就换成慢跑周期。走路周期对应 1.0-1.5 m/s，跑是 3.1 m/s。
RUN_GAIT_MIN = 2.0


def localize(track, origin):
    reference = Rotation.from_quat(origin[3:])
    positions = reference.inv().apply(track[:, :3] - origin[:3])
    quats = (reference.inv() * Rotation.from_quat(track[:, 3:])).as_quat()
    return np.concatenate((positions, quats), axis=-1).astype(np.float32)


def place(local, root_abs):
    rotation = Rotation.from_quat(root_abs[:, 3:])
    flat = local.reshape(-1, 3)
    repeated = np.repeat(rotation.as_quat(), local.shape[1], axis=0)
    world = Rotation.from_quat(repeated).apply(flat).reshape(local.shape)
    return world + root_abs[:, None, :3]


def wrap(angle):
    return (angle + np.pi) % (2 * np.pi) - np.pi


def align_quats(quats):
    """相邻四元数点积为负就是绕了一圈后的符号翻转，矩阵其实没变，但网络会把它当成突变。"""
    out = np.array(quats, dtype=np.float64, copy=True)
    for i in range(1, len(out)):
        if float(np.dot(out[i], out[i - 1])) < 0:
            out[i] *= -1
    return out.astype(np.float32)


def yaw_quat(yaw):
    """模型系：正 yaw 朝右（-X）转，yaw=0 朝 +Z。"""
    return Rotation.from_euler("y", -float(yaw)).as_quat().astype(np.float32)


def forward_model(yaw):
    return np.array([-np.sin(yaw), 0.0, np.cos(yaw)], dtype=np.float64)


def align_window_forward(positions, rotations):
    """把身体前方旋到模型 +Z，和 Root 朝向一致。

    肩线的左右方向叉乘向上，得到身体前方。数据里这一帧往往并不朝 +Z，
    直接套到角色上，人就和 Entity 的朝向岔开。
    """
    positions = np.array(positions, dtype=np.float64, copy=True)
    rotations = np.array(rotations, dtype=np.float64, copy=True)
    left, right, hips = 12, 40, 1
    for i in range(len(positions)):
        across = positions[i, right] - positions[i, left]
        across[1] = 0.0
        norm = float(np.linalg.norm(across))
        if norm < 1e-6:
            continue
        across /= norm
        fwd = np.cross(np.array([0.0, 1.0, 0.0]), across)
        yaw = float(np.arctan2(-fwd[0], fwd[2]))
        turn = Rotation.from_euler("y", yaw)
        positions[i] = turn.apply(positions[i])
        mats = rotation_6d_to_matrix(rotations[i])
        mats[hips] = (turn.as_matrix() @ mats[hips]).astype(np.float64)
        col0, col1 = mats[hips][:, 0], mats[hips][:, 1]
        rotations[i, hips] = [col0[0], col1[0], col0[1], col1[1], col0[2], col1[2]]
        positions[i, 0] = 0.0
    return positions.astype(np.float32), rotations.astype(np.float32)


def normalize_pose(positions, mean, std):
    out = np.zeros_like(positions, dtype=np.float32)
    np.divide(positions - mean, std, out=out, where=std > 1e-8)
    return out


def load_assets():
    torch.set_num_threads(4)
    mean = np.load(g.STATS / "pose_m.npy").astype(np.float32)
    std = np.load(g.STATS / "pose_s.npy").astype(np.float32)
    ckpt = torch.load(POSE_W, map_location="cpu", weights_only=False)
    net = ReferenceGuidedPose(78, ckpt["width"]).eval()
    net.load_state_dict(ckpt["model"])
    skeleton = json.loads((g.MMAP / "skeleton.json").read_text(encoding="utf-8"))
    names = [bone["name"] for bone in skeleton["bones"]]
    parents = [bone["parent"] for bone in skeleton["bones"]]

    positions = np.load(g.MMAP / "027106" / "p.npy").astype(np.float32)
    rotations = np.load(g.MMAP / "027106" / "r.npy").astype(np.float32)
    root = np.load(g.MMAP / "027106" / "k.npy").astype(np.float32)
    speed = np.linalg.norm(np.diff(root[:, [0, 2]], axis=0), axis=1) * FPS
    window = np.convolve(speed, np.ones(WINDOW) / WINDOW, mode="valid")
    # 159 帧最慢，但是双手横平，是个 T 字。改用手臂放下的站姿。
    start = 238
    stand_pos, stand_rot = align_window_forward(positions[start:start + WINDOW], rotations[start:start + WINDOW])
    seed_pose = normalize_pose(stand_pos, mean, std)
    seed_rot = stand_rot
    # 走路周期：片段 019534，143 帧（1.19 秒），步长 1.35 米，速度 1.14 m/s，自相关 1.000。
    # 原来用 027106 的第 759 帧起 159 帧，那段躯干前倾 11°，站着只有 4.5°，
    # 一走路人就前栽。这里按"整段躯干最直"重新挑，平均前倾 1.7°、最差 2.9°。
    walk_src = np.load(g.MMAP / "019534" / "p.npy").astype(np.float32)
    walk_src_rot = np.load(g.MMAP / "019534" / "r.npy").astype(np.float32)
    cycle_at, cycle_n, stride = 159, 143, 1.35
    walk_pos, walk_rot = align_window_forward(
        walk_src[cycle_at:cycle_at + cycle_n], walk_src_rot[cycle_at:cycle_at + cycle_n]
    )
    walk_pose = normalize_pose(walk_pos, mean, std)
    # 慢跑：片段 019857，周期 88 帧（0.73 秒），步长 2.00 米，速度 2.73 m/s，自相关 0.998。
    # 027106 里没有跑（最高 1.39 m/s）。挑片段的判据不只是躯干直，还要关节角度合理：
    # 017732 躯干最直，但肘部只有 76-93°（正常摆臂约 140°），摆臂是僵的；
    # 003018 肘 102-114°、躯干前倾 25.5°。019857 肘 147/147、膝 141/141、躯干 10.3°。
    run_positions = np.load(g.MMAP / "019857" / "p.npy").astype(np.float32)
    run_rotations = np.load(g.MMAP / "019857" / "r.npy").astype(np.float32)
    run_at, run_n, run_stride = 32, 88, 2.00
    run_pos, run_rot = align_window_forward(
        run_positions[run_at:run_at + run_n], run_rotations[run_at:run_at + run_n]
    )
    run_pose = normalize_pose(run_pos, mean, std)
    print(
        f"stand seed {start} speed {window[start]:.3f} m/s  "
        f"walk {cycle_at}+{cycle_n} stride {stride:.3f} m  "
        f"run {run_at}+{run_n} stride {run_stride:.3f} m",
        flush=True,
    )
    return (net, mean, std, names, parents, seed_pose, seed_rot,
            walk_pose, walk_rot, stride, run_pose, run_rot, run_stride)


class LocomotionSim:
    def __init__(self, net, mean, std, seed_pose, seed_rot, walk_pose, walk_rot, stride,
                 run_pose, run_rot, run_stride):
        self.net = net
        self.mean = mean
        self.std = std
        self.seed_pose = seed_pose
        self.seed_rot = seed_rot
        mid = WINDOW // 2
        self.base = np.concatenate((seed_pose[mid], seed_rot[mid]), axis=-1).reshape(-1).astype(np.float32)
        # 两套周期：走路与慢跑。按速度选，相位各自独立推进。
        self.gaits = {
            "walk": (np.concatenate((walk_pose, walk_rot), axis=-1).reshape(len(walk_pose), -1).astype(np.float32),
                     float(stride)),
            "run": (np.concatenate((run_pose, run_rot), axis=-1).reshape(len(run_pose), -1).astype(np.float32),
                    float(run_stride)),
        }
        self.gait = "walk"
        self.cycle, self.stride = self.gaits["walk"]
        self.cycle_n = len(self.cycle)
        self.phase = 0.0
        self.walk = WALK
        self.run = RUN
        self.turn = TURN
        self.reset()

    def reset(self):
        self.pose_hist = self.seed_pose.copy()
        self.rot_hist = self.seed_rot.copy()
        quat = yaw_quat(0.0)
        roots = np.zeros((WINDOW, 7), dtype=np.float32)
        roots[:, 3:] = quat
        self.root_hist = roots
        self.pos = np.zeros(3, dtype=np.float64)
        self.yaw = 0.0
        self.vel = np.zeros(3, dtype=np.float64)
        self.phase = 0.0

    def plan_roots(self, stick_x, stick_y, run, cam_yaw):
        """PoseSearchGenerateTrajectory 的简化版。

        不把速度瞬间拧到输入方向，而是像 CharacterMovement::CalcVelocity：
        朝期望速度加速、松手刹车，朝向再按转向速率追上速度方向。
        预测 1.2 秒，前 24 帧交给姿态网。
        """
        mag = float(np.hypot(stick_x, stick_y))
        max_speed = (self.run if run else self.walk) * min(1.0, mag)
        if mag >= 0.08:
            wish = wrap(float(cam_yaw) + np.arctan2(stick_x, stick_y))
            desired = forward_model(wish) * max_speed
        else:
            desired = np.zeros(3, dtype=np.float64)
        # 走到最高速大约 0.3 秒，和虚幻 600/2048 的比例同一量级
        accel = 8.0 if max_speed > 0 else 0.0
        brake = 12.0
        horizon = int(1.2 * FPS)
        pos = self.pos.copy()
        yaw = float(self.yaw)
        vel = self.vel.copy()
        roots = np.empty((horizon, 7), dtype=np.float32)
        yaw_at = np.empty(horizon, dtype=np.float64)
        for i in range(horizon):
            if accel > 0:
                delta_v = desired - vel
                cap = accel * DT
                norm = float(np.linalg.norm(delta_v))
                vel = vel + (delta_v if norm <= cap or norm < 1e-8 else delta_v * (cap / norm))
                speed_now = float(np.linalg.norm(vel))
                if speed_now > max_speed > 0:
                    vel *= max_speed / speed_now
            else:
                speed_now = float(np.linalg.norm(vel))
                if speed_now < 1e-4:
                    vel[:] = 0
                else:
                    vel *= max(0.0, speed_now - brake * DT) / speed_now
            pos = pos + vel * DT
            speed_now = float(np.linalg.norm(vel))
            if speed_now > 0.2:
                face = float(np.arctan2(-vel[0], vel[2]))
                delta = wrap(face - yaw)
                yaw = yaw + float(np.clip(delta, -self.turn * DT, self.turn * DT))
            roots[i, :3] = pos
            roots[i, 3:] = yaw_quat(yaw)
            yaw_at[i] = yaw
        roots[:, 3:] = align_quats(roots[:, 3:])
        return roots, yaw_at, vel, float(np.linalg.norm(vel))

    def step(self, stick_x, stick_y, run, cam_yaw=0.0):
        stick_x = float(np.clip(stick_x, -1, 1))
        stick_y = float(np.clip(stick_y, -1, 1))
        plan_abs, yaw_at, vel_end, speed = self.plan_roots(stick_x, stick_y, run, cam_yaw)
        # 接下来 8 帧会走多远，用来把参考帧的相位往前推
        dist = float(np.hypot(plan_abs[PLAY - 1, 0] - self.pos[0], plan_abs[PLAY - 1, 2] - self.pos[2]))
        # 跑步按键或速度超过慢跑阈值时用跑的姿态周期，否则用走的
        gait = "run" if (run or speed >= RUN_GAIT_MIN) else "walk"
        if gait != self.gait:
            # 换步态时相位归零，避免从走的中点直接跳到跑的中点
            self.gait = gait
            self.phase = 0.0
            self.cycle, self.stride = self.gaits[gait]
            self.cycle_n = len(self.cycle)
        self.root_hist[-1]
        combined = np.concatenate((self.pose_hist, self.rot_hist), axis=-1).reshape(WINDOW, -1)
        hist_root = self.root_hist.copy()
        hist_root[:, 3:] = align_quats(hist_root[:, 3:])
        history = np.concatenate((combined, localize(hist_root, hist_root[-1])), axis=-1).astype(np.float32)
        # 姿态网只要接下来 0.2 秒。1.2 秒整段是给画面上的预测线。
        plan = localize(plan_abs[:WINDOW], hist_root[-1])
        reference = np.zeros((WINDOW, 702), dtype=np.float32)
        mask = np.zeros((WINDOW, 1), dtype=np.float32)
        if speed < 0.2:
            # 站着也不能 24 帧都盖成同一帧，否则人是冻住的。
            # 和走路一样：只钉还没播到的帧，0..7 留给模型。
            for k in (12, 16, 20, 23):
                reference[k] = self.base
                mask[k] = 1
        else:
            # 参考帧必须是"当前之后第 k 帧"的真值姿态，位置和训练一致（target[12]、target[23]）。
            # 之前用 phase + k 取，等于把未来的姿势当成已经过去的，模型只能瞎猜，
            # 膝盖角度能差 30°。这里按走路距离推进的相位往后取。
            for k in (12, 16, 20, 23):
                ahead = (dist / max(self.stride, 1e-6)) * self.cycle_n if speed > 0.2 else 0.0
                reference[k] = self.cycle[int(self.phase + ahead * k / WINDOW) % self.cycle_n]
                mask[k] = 1
        started = time.perf_counter()
        with torch.no_grad():
            out = self.net(
                torch.from_numpy(history[None]),
                torch.from_numpy(plan[None]),
                torch.from_numpy(reference[None]),
                torch.from_numpy(mask[None]),
            ).numpy()[0].reshape(WINDOW, 78, 9)
        elapsed = (time.perf_counter() - started) * 1000

        new_pose = out[:PLAY, :, :3].astype(np.float32)
        new_rot = out[:PLAY, :, 3:].astype(np.float32)
        new_root = plan_abs[:PLAY]
        if speed >= 0.2 and dist > 1e-4:
            self.phase = (self.phase + dist / self.stride * self.cycle_n) % self.cycle_n
        self.pose_hist = np.concatenate((self.pose_hist[PLAY:], new_pose), axis=0)
        self.rot_hist = np.concatenate((self.rot_hist[PLAY:], new_rot), axis=0)
        self.root_hist = np.concatenate((self.root_hist[PLAY:], new_root), axis=0)
        self.pos = new_root[-1, :3].astype(np.float64).copy()
        self.yaw = float(yaw_at[PLAY - 1])
        # 速度取提交点。不能用整段末端，否则每步都会把 1.2 秒后的速度提前用上。
        # plan 的第 PLAY 帧速度没有单独存，用位移差还原。
        if PLAY >= 2:
            self.vel = (plan_abs[PLAY - 1, :3] - plan_abs[PLAY - 2, :3]).astype(np.float64) / DT
        else:
            self.vel = vel_end

        local = new_pose * self.std + self.mean
        world = place(local, new_root)
        joints_ue = vec_to_ue(world).astype(np.float32)
        roots_ue = vec_to_ue(new_root[:, :3]).astype(np.float32)
        # 显示用未来 1.2 秒，每 4 帧一个点，避免和已经走过的线混在一起
        show = plan_abs[::4]
        trail_ue = vec_to_ue(show[:, :3]).astype(np.float32)
        facing = yaw_at[::4].astype(np.float64)
        return {
            "type": "step",
            "fps": FPS,
            "play": PLAY,
            "joints": np.round(joints_ue.reshape(-1), 4).tolist(),
            "roots": np.round(roots_ue.reshape(-1), 4).tolist(),
            "trail": np.round(trail_ue.reshape(-1), 4).tolist(),
            "facing": np.round(facing, 4).tolist(),
            "yaw": round(float(self.yaw), 5),
            "ms": round(elapsed, 2),
            "speed": round(float(speed), 3),
        }


def check(sim):
    idle = sim.step(0, 0, False)
    joints = np.array(idle["joints"], dtype=np.float64).reshape(PLAY, 78, 3)
    names = json.loads((g.MMAP / "skeleton.json").read_text(encoding="utf-8"))
    index = {bone["name"]: i for i, bone in enumerate(names["bones"])}
    hips, head = joints[0, index["Hips"]], joints[0, index["Head"]]
    left, right = joints[0, index["LeftFoot"]], joints[0, index["RightFoot"]]
    sim.reset()
    roots = []
    milliseconds = []
    for _ in range(15):
        packet = sim.step(0.0, 1.0, False)
        roots.append(np.array(packet["roots"]).reshape(PLAY, 3))
        milliseconds.append(packet["ms"])
    roots = np.concatenate(roots, axis=0)
    delta = roots[-1] - roots[0]
    print(json.dumps({
        "idle_up_z": round(float(head[2] - hips[2]), 3),
        "idle_foot_z": round(float((left[2] + right[2]) * 0.5), 3),
        "idle_left_minus_right_y": round(float(left[1] - right[1]), 3),
        "walk_1s_delta_xyz": [round(float(v), 3) for v in delta],
        "infer_ms_mean": round(float(np.mean(milliseconds)), 2),
    }, ensure_ascii=False), flush=True)


async def serve(sim_factory):
    import websockets
    from websockets.exceptions import ConnectionClosed, ConnectionClosedError

    async def handler(websocket):
        sim = sim_factory()
        hello = {
            "type": "hello",
            "fps": FPS,
            "play": PLAY,
            "walk": WALK,
            "run": RUN,
            "turn_deg": 360,
        }
        await websocket.send(json.dumps(hello))
        # 浏览器关标签或断网会直接掐 socket。这不是服务端的错误，
        # 以前会让整个服务跟着退，页面就再也连不上了。
        try:
            await serve_steps(websocket, sim)
        except (ConnectionClosedError, ConnectionResetError, ConnectionClosed):
            pass

    async def serve_steps(websocket, sim):
        async for raw in websocket:
            message = json.loads(raw)
            kind = message.get("type")
            if kind == "reset":
                sim.reset()
                await websocket.send(json.dumps({"type": "reset"}))
            elif kind == "step":
                if message.get("walk") is not None:
                    sim.walk = float(np.clip(message["walk"], 0.1, 8))
                if message.get("run_speed") is not None:
                    sim.run = float(np.clip(message["run_speed"], 0.1, 12))
                if message.get("turn") is not None:
                    sim.turn = float(np.deg2rad(np.clip(message["turn"], 30, 1080)))
                packet = sim.step(message.get("x", 0), message.get("y", 0), bool(message.get("run")), float(message.get("cam_yaw", 0)))
                await websocket.send(json.dumps(packet, separators=(",", ":")))

    async with websockets.serve(handler, HOST, PORT, max_size=2 * 1024 * 1024):
        print(f"locomotion ws://{HOST}:{PORT}", flush=True)
        await asyncio.Future()


def main():
    (net, mean, std, _names, _parents, seed_pose, seed_rot,
     walk_pose, walk_rot, stride, run_pose, run_rot, run_stride) = load_assets()
    def factory():
        return LocomotionSim(net, mean, std, seed_pose, seed_rot, walk_pose, walk_rot, stride,
                             run_pose, run_rot, run_stride)

    if "--check" in sys.argv:
        check(factory())
        return
    asyncio.run(serve(factory))


if __name__ == "__main__":
    main()
