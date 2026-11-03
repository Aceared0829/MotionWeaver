# 游戏模拟层：已从 viewer3d 移除，链路存档

- 日期：2026-11-03
- 动作：把 `output/refpose_preview/viewer3d/index.html` 的「游戏模拟」整层删除
- 文件：1069 行 → 605 行（按换行计；`split` 口径为 1070 → 606），删 **464 行**
- 存档：`.build/archive/viewer3d/index.game-sim.html`（删前完整副本，逐字节未改）
- 该 html 不在 git 里（`/output/` 被 gitignore），所以这份存档是唯一的回滚路径

---

## 一、为什么删

这一层是"能跑但签不了字"的典型：它没有测试、没有契约文件、坐标转换和 6D 解码在
Python 与 JS 各写一份，而 UE 接入调研（`docs/refpose-ue-runtime-integration-research.md`）
已经给出正确的承载位置——新 AnimNode + PoseSearch 轨迹 + LegIK/FootPlacement。
预览层继续长功能，等于在错误的容器里堆代码。

按第一阶段结论：**预览层降级为一次性仪器，不往里加功能。**
本次删除是这条决定的第一步——先砍掉最"像产品"的那块。

---

## 二、被删掉的链路（完整存档）

### 2.1 端到端

```
键盘 WASD / Shift
   │  keys{w,a,s,d,shift}
   ▼
stickNow()                     摇杆向量 (-1..1)，归一化到单位圆
   │  {x, y, run}
   ▼
pump()                         缓冲已播帧数 < 10 才发下一包（背压）
   │  JSON over WebSocket
   ▼
ws://127.0.0.1:8822            locomotion_server.py
   │  逐连接一个 LocomotionSim 实例
   ▼
plan_roots(stick_x, stick_y, run, cam_yaw)
   │  按 CMC 语义外推 1.2 秒：加速→巡航→刹车，朝向按转向速率收敛
   │  产出 horizon = 1.2*120 = 144 帧 root (pos + yaw_quat)
   ▼
取前 WINDOW=24 帧做 root_plan ，前 PLAY=8 帧提交播放
   │
   ▼
LocomotionSim.step()
   │  history  = 24 帧姿态(702) + 24 帧 root_local(7)   → [24,709]
   │  root_plan= localize(plan_abs[:24])                → [24,7]
   │  reference= 相位前推取周期帧（站立用 seed 中点）   → [24,702]
   │  mask     = 只钉 12/16/20/23 四帧                  → [24,1]
   ▼
ReferenceGuidedPose.forward()   3.09 M 参数，refpose_v3_71k/best.pt
   │  → [24, 78, 9]（3 位置 + 6D 旋转）
   ▼
new_pose[:8] 回填历史；place() 用 root 拼世界坐标；vec_to_ue() 转 UE 轴
   │
   ▼
WebSocket 回包 {joints, roots, trail, facing, yaw, ms, speed}
   │
   ▼
gameBuf.push()                 每帧一条 {joints: Float32Array, root}
   │
   ▼
gameTick()                     每 1/120 s 消费一帧 → gamePast（走过的轨迹）
   ▼
drawGame()                     2D canvas 手画：网格/骨架/Root 预测线/坐标轴
```

### 2.2 服务端常量（`inference/visualization/locomotion_server.py`）

| 常量 | 值 | 含义 |
|---|---|---|
| `HOST, PORT` | `127.0.0.1:8822` | 目前仍在这个端口监听 |
| `WINDOW` | 24 | 历史帧数 = 未来帧数（训练契约） |
| `PLAY` | 8 | 每代实际播出帧数 |
| `FPS` / `DT` | 120 / 1/120 | 训练数据是 120 Hz，不是 30 Hz |
| `WALK` / `RUN` | 1.5 / 4.0 m/s | 默认行走/奔跑速度 |
| `TURN` | 2π/s（360°/s） | 朝向收敛速率 |
| `RUN_GAIT_MIN` | 2.0 m/s | 超过此速度切慢跑周期 |
| 权重 | `D:\MotionWeaverData\model_weight\refpose_v3_71k\best.pt` | 封盘权重 |
| 走路周期 | clip `019534`，159 帧起 143 帧，步长 1.35 m | 相位按行走距离推进 |
| 跑步周期 | clip `019857`，32 帧起 88 帧，步长 2.00 m | 肘 147°、膝 141°、躯干 10.3° |
| 站立种子 | clip `027106` 第 238 帧 | 双臂放下，非 T-pose |

### 2.3 协议

**客户端 → 服务端**

```json
{"type":"reset"}
{"type":"step","x":0.0,"y":1.0,"run":false,"cam_yaw":0.0,
 "walk":1.5,"run_speed":4.0,"turn":360}
```

`cam_yaw` 让移动方向相对镜头，符合第三人称手感；`walk/run_speed/turn` 是右侧
「移动规则」面板实时改的，服务端每包都 clamp 一次（walk 0.1–8、run 0.1–12、turn 30–1080）。

**服务端 → 客户端**

```json
{"type":"hello","fps":120,"play":8,"walk":1.5,"run":4.0,"turn_deg":360}
{"type":"step","play":8,"joints":[...8*78*3],"roots":[...8*3],
 "trail":[...],"facing":[...],"yaw":0.0,"ms":3.6,"speed":1.5}
```

`joints` 已经是 **UE 坐标**（X 前、Y 右、Z 上），转换在 `ue_axis.py::vec_to_ue`。
`trail` 是未来 1.2 秒的 Root 预测线（每 4 帧取一点），画面上的青色虚线。

### 2.4 前端结构（已删部分）

| 块 | 行号（原） | 内容 |
|---|---|---|
| CSS `.ue-*` / `body.game-on` | 107–141 | 35 行，整套 UE 风格工具栏/视口/侧栏/内容栏 |
| HTML `<div id="game">` | 147–185 | 39 行，工具栏 + 视口 + 右侧「细节」面板 + 底部「内容·Poses」 |
| 工具栏按钮 | 209–211 | 「游戏模拟」入口 |
| 主循环守卫 | 469 | 游戏模式下挂起对比渲染 |
| 滚轮分支 | 662 | 游戏模式滚轮改 `gameDist`（2–12 m） |
| 游戏 JS | 681–1065 | 385 行，见下 |

**385 行游戏 JS 的构成：**

| 函数/块 | 作用 |
|---|---|
| `connectGame` / `pump` | WebSocket 生命周期 + 背压（缓冲 ≥10 帧就停发） |
| `gameBasis(root)` | 第三人称相机：直接由 yaw/pitch 组基，不从注视方向反推（反推过 ±180° 会翻 up） |
| `drawGame` | 手写 2D 投影：近平面裁剪、世界锚定网格、骨架、Root 预测线、坐标轴 Gizmo |
| `gameTick` | 每帧消费累积鼠标位移、每 1/120 s 推进一帧、写 `camLog` 供调试 |
| 指针锁定 + 鼠标累加 | `rotInputX/Y` 只累加，`gameTick` 每帧消费一次清零（对齐 UE `AddYawInput`） |
| 尖峰丢弃 | `SPIKE_PX=200` 整条丢；`FRAME_BUDGET_PX=(35°/0.003)` 限一帧总量，超出丢弃不跨帧欠账 |
| `window.__dbg` | `tilt()` 算躯干前倾角、`snap()` 导出镜头/缓冲状态，最多存 20000 条 `camLog` |
| `autowalk` | `?autowalk` 查询参数自动前进，用于截图回归 |

### 2.5 删掉之后仍然存在的东西

| 存在物 | 状态 |
|---|---|
| `inference/visualization/locomotion_server.py` | **文件保留，进程仍在 8822 监听**。页面已不再连它 |
| `inference/visualization/ue_axis.py` | 保留，是 UE 坐标转换的 Python 实现 |
| `web3d/`、`closed_loop/`、`matrix/` 等实验目录 | 保留，是历史证据 |
| `manny_gl.js` + `SKM_Manny.glb` | **曾计划接入、从未入库**，也未进版本库。2026-11-03 确认全盘已无 *manny* / *.glb 文件 |

---

## 三、验证记录（删后）

- 语法：抽出内联 `<script type="module">` 462 行 → `node --check` **EXIT 0**
- 结构：`<div>` 10 开 10 合、`<script>`/`<style>` 各 1 对
- 残留标识符扫描：46 个游戏层标识符（`gameOn` `gameBuf` `gameTick` `gameBasis` `drawGame`
  `connectGame` `sizeGame` `pump` `stickNow` `stickId` `ruleNum` `WS_URL` `uePlay` `ueStop`
  `ueBack` `gameBtn` `gameCanvas` `gameCtx` `gameDist` `rotInputX` `rotInputY` `__dbg` 等）→ **0 命中**
- 无头浏览器（Playwright chromium，1600×900）：
  - `window.__ready === true`，`console error`/`pageerror` **均为 0**
  - 6 个宫格、6 个 canvas 均 409×401 正常出图
  - `#gameBtn`、`#game` **均不存在**
  - 2 列/4 列切换、暂停、真值叠影、面板开合、A/B 模式切换 → 全部正常
  - 截图：`viewer3d/after_game_removed.png`

---

## 四、回滚方式

```powershell
Copy-Item .build\archive\viewer3d\index.game-sim.html \
          output\refpose_preview\viewer3d\index.html -Force
```

服务端一直没动过，所以回滚 html 就能恢复整条链路——**前提是 8822 那个进程还活着**。
如果进程已退出，重启用：

```powershell
.venv\Scripts\python.exe inference\visualization\locomotion_server.py
```
