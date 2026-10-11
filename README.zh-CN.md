# pt35-buttonmap

[English](README.md) · **中文**

给 **Waveshare PocketTerm35**（树莓派 4B / Pi 5 掌机）的内置键盘加上鼠标能力。

**不刷固件。** 全部在 Linux 的 `evdev`/`uinput` 层完成。一条命令安装，一条命令卸载。

```
按住方向键          ->  移动鼠标指针（带加速度）
← 快速点两下        ->  鼠标左键
→ 快速点两下        ->  鼠标右键
连按两次 Backspace  ->  在「鼠标模式 / 按钮模式」之间切换
                        （切换时弹桌面提示，告诉你当前处于哪个模式）

L R Y X A B 及其余所有字母键  ->  完全不受影响，始终正常输入字母
```

---

## 为什么需要它

PocketTerm35 的内置键盘是一颗跑 **CircuitPython** 的 **RP2040（树莓派 Pico）**，
通过 USB OTG 接到树莓派。它的原厂固件只做「矩阵扫描 → USB HID 键码」，
**根本没有鼠标映射层**：

| 面板按键 | 原厂固件实际发出的键码 |
|---|---|
| 方向键 ↑↓←→ | `KEY_UP` / `KEY_DOWN` / `KEY_LEFT` / `KEY_RIGHT` |
| L / R | **`KEY_L` / `KEY_R`** —— 就是字母 `l` / `r` |
| Y / X / A / B | **`KEY_Y` / `KEY_X` / `KEY_A` / `KEY_B`** —— 就是字母 |

因此：方向键推不动指针；**L/R 无法当鼠标键，因为它们和字母 `l`/`r` 共用完全相同的
键码**，操作系统无法区分「按了面板 L 键」和「敲了字母 l」。

只有两处可以动手：

1. **重刷键盘固件**给按键独立键码。但原厂 `boot.py` 里调用了
   `storage.disable_usb_drive()`，关掉了 CIRCUITPY 大容量存储盘 —— 所以
   **必须拆机按 BOOTSEL**（Pico 封在机壳内，没有对外的 USB 数据口）。
   而且这块键盘是掌机**唯一的输入设备**，刷坏就等于失去操作能力。
2. **在 Linux 侧做用户态重映射** ← 本项目采用的方案。

选方案 2 的理由：零拆机风险、立刻可验证、`install.sh --uninstall` 全部还原、
完全不碰固件。代价是 shell **无法区分**「按了面板 A 键」和「打了字母 a」——
这个信息在到达 Linux 之前就已经丢失了。如果你需要那种区分度（例如给
[pt35-desktop](https://github.com/therezor/PocketTerm35-OS) 这类把 A 绑成确认、
B 绑成返回的桌面用），就必须走方案 1。见[相关项目](#相关项目)。

### 一条用代价换来的设计准则

本项目最初把 L/R 映射成鼠标键，后来又映射了 A/B/X/Y。这些全部是**字母键码**，
于是那些字母再也打不出来（`ls`、`rm`、`clear` 全部残废）。最终设计遵循一条准则：

> **绝不占用一个已经承载了你需要的语义的键码。**
> 点击改由**方向键轻点手势**承担 —— 因为方向键在鼠标模式下本来就是沉默的
> （不发任何方向键），所以它没有任何冲突。

---

## 环境要求

- Waveshare PocketTerm35（Pi 4B 或 Pi 5 版本）
- Raspberry Pi OS **Debian 12 (bookworm) 或 13 (trixie)，64 位**；Wayland 或 X11 均可
- Python 3.11+（用到 `tomllib`）
- `python3-evdev`（安装脚本会自动装）
- 键盘是 USB OTG 设备，`config.txt` 必须含
  `dtoverlay=dwc2,dr_mode=host`（官方要求；官方镜像已带）

> 本项目与合成器无关：它在 **evdev/uinput** 层工作，所以 Wayland
> （labwc / sway / KWin / Hyprland）和 X11 都能用。X11 的
> `xbindkeys`/`xmodmap` 与合成器自身的 keybind 都**无法生成指针移动**。

---

## 安装

```bash
git clone https://github.com/akmikolas/pt35-buttonmap
cd pt35-buttonmap
sudo ./install.sh
```

默认**降权运行**：以调用 `sudo` 的账号运行服务，并安装一条 udev 规则把
`/dev/uinput` 授权给 `input` 组。想以 root 运行：`sudo ./install.sh --user=root`。

安装后立即生效，无需重启。验证：

```bash
sudo pt35-buttonmap.py --status      # 当前模式
journalctl -u pt35-buttonmap -f      # 日志
```

### 卸载

```bash
sudo ./install.sh --uninstall
```

键盘立刻恢复原厂行为。`/etc/pt35-buttonmap.toml`（你的配置）会保留，需要就手动删。

---

## 使用

| 操作 | 命令 |
|---|---|
| 查看当前模式 | `sudo pt35-buttonmap.py --status` |
| 切换模式 | `sudo pt35-buttonmap.py --toggle` |
| 切到按钮模式 | `sudo pt35-buttonmap.py --set-mode buttons` |
| 切到鼠标模式 | `sudo pt35-buttonmap.py --set-mode mouse` |
| 前台调试 | `sudo pt35-buttonmap.py --debug --config /etc/pt35-buttonmap.toml` |

**手势切换**：连按两次 `Backspace`（0.35 秒内）。切换时会弹桌面通知告诉你当前模式
（需要 `org.freedesktop.Notifications`；没有该服务会优雅降级，见
[模式切换提示](#模式切换提示)）。

> **为什么默认用 `Backspace` 而不是 `Fn`？** `Fn` 在固件内部被拦截，
> **不产生任何键码** —— Linux 完全看不见它。任何"检测 Fn"的方案都只能靠副作用猜
> （比如"一段异常长的静默"），实测**误判严重**：打字时的正常停顿会被读成 Fn 轻点，
> 模式疯狂来回跳。所以默认选一个真实存在、且不与常用操作冲突的键。
> 该启发式仍保留为 `toggle.mode = "fn"`，风险自负。

---

## 点击手势：放在方向键上，不碰 L/R

面板 L/R 发出的就是 **`KEY_L`/`KEY_R`**，与字母 `l`/`r` 完全同码。
所以"L 当鼠标键"和"能打出 l"**互斥**。早期版本劫持了 L/R，后来又劫持了 A/B/X/Y；
那些字母再也打不出来 —— `ls`、`rm`、`clear` 全部残废。

**现在没有任何字母键被占用。** 点击来自方向键的快速轻点，而方向键在鼠标模式下
本来就是沉默的，所以**不存在键码冲突**：

| 手势 | 动作 |
|---|---|
| **←** 快速点两下 | 鼠标左键 |
| **→** 快速点两下 | 鼠标右键 |
| 按住方向键 | 推动指针（不走手势）|

单击刻意不接动作 —— 否则只是轻碰一下方向键就会触发点击。

### 判定规则（三条都在真机上调出来的）

1. 按住时间 ≤ `tap_max`（默认 **0.28 s**）算一次**轻点**；
2. **或者**按住期间指针一次都没移动过 —— 也算轻点。
   这条是为"连击天生难按快"加的：真按住推指针必然产生位移，所以它几乎不会误触发；
3. 连击累积窗口为 `tap_window × tap_hold_factor`（默认 **0.40 × 2 = 0.8 s**）。
   慢按的算术是"按住 0.35 s + 松手再按 0.08 s = 0.43 s"，已经超过 `tap_window`；
   不放宽的话，第 1 次轻点会在下一按到来之前被结算，连击永远接不上。

只有**刚好配了动作**的轻点次数才会触发。`←`/`→` 只配了 `double`，所以点一下什么都不做。

> **延迟取舍**：没配动作的轻点次数（例如只配 `triple` 时的第 1、2 下）要等宽窗口
> 结束才被丢弃，这段时间不会有点击。这是"按得慢也能连击"的必要代价，
> 可用 `gestures.tap_hold_factor` 调整（调小 = 点击更跟手，但慢按更易失效）。

---

## 配置

编辑 `/etc/pt35-buttonmap.toml`（首次安装时自动生成，含注释），
然后 `sudo systemctl restart pt35-buttonmap`。

动作语法：

| 写法 | 含义 |
|---|---|
| `btn:left` / `btn:right` / `btn:middle` / `btn:side` / `btn:extra` | 鼠标按键 |
| `scroll:up` / `scroll:down` / `scroll:left` / `scroll:right` | 滚轮 |
| `key:<键名>` | 普通按键；键名为 evdev `KEY_*` 去掉前缀，如 `key:l`、`key:enter` |

常见改法：

```toml
# 换绑手势（默认：← 双击 = 左击，→ 双击 = 右击）
[gestures]
tap_max         = 0.28   # 短于此时长算一次轻点
tap_window      = 0.40   # 两次轻点之间的最大间隔
tap_hold_factor = 2.0    # 连击累积窗口 = tap_window × 该系数
left  = { double = "btn:left" }
right = { double = "btn:right" }
# 想让单击也立即点击（更跟手，但轻碰一下方向键就会点击）？
# left = { single = "btn:left", double = "btn:left" }

# 或者把手势放到别的方向
# down = { double = "btn:left" }
# up   = { triple = "btn:right" }

# 指针速度
[pointer]
speed       = 6      # 每个事件的像素数
fast_speed  = 16     # 按住超过 accel_after 秒后的速度
accel_after = 0.45

# 切换手势
[toggle]
mode   = "backspace"   # backspace | fn | both | off
window = 0.35

# 模式切换提示（默认开启）
[feedback]
notify = true
notify_timeout_ms = 4000
# 提示音默认关闭。不要用 /usr/share/sounds/alsa/Noise.wav ——
# 那是白噪声测试音，听感是"沙沙响半秒"。
sound = ""
# sound = "/usr/share/sounds/alsa/Front_Center.wav"
```

### 让 L/R 或 YXAB 重新当鼠标键

默认关闭，因为代价是打不出那些字母：

```toml
[map]
l_r_scope   = "mouse"    # never（默认）| mouse | always
field_scope = "always"   # never（默认）| always
l = "btn:left"
r = "btn:right"
mouse_a = "btn:left"     # 仅当 field_scope = "always" 时生效
mouse_b = "btn:right"
mouse_x = "scroll:up"
mouse_y = "scroll:down"
```

`"never"` —— 永远是字母 · `"mouse"` —— 仅鼠标模式下当鼠标键 ·
`"always"` —— 两种模式下都是鼠标键（那就打不出这些字母了）。

### 模式切换提示

切换时会弹通知告诉你当前模式：

| 模式 | 提示 |
|---|---|
| 鼠标模式 | `PT35: MOUSE mode` / 方向键推指针 · ←← 左击 · →→ 右击（字母键全部不占用）|
| 按钮模式 | `PT35: BUTTONS mode` / 字母键正常打字 · 方向键恢复为方向键（连按两次 Backspace 切回）|

实现细节，全是踩坑换来的：

- 通知依赖通知守护进程（`org.freedesktop.Notifications`）。桌面面板自带该服务时
  开箱即用；本项目按 **GIO → `notify-send` → 仅写日志** 三级回退，
  所以没有该服务的桌面除了少一个弹窗之外没有任何损失。
- systemd 服务**没有会话环境**。缺 `DBUS_SESSION_BUS_ADDRESS` 时 GIO 报
  `Cannot autolaunch D-Bus without X11 $DISPLAY`。GIO 读的是**进程环境**，
  所以必须写进进程内，只给子进程设没用。
- **不要靠"总线 socket 文件在不在"挑总线**：服务环境里可能残留指向别处的
  `DBUS_SESSION_BUS_ADDRESS`，连上去是 `Permission denied` 而且永不回退。
  本项目**逐个候选总线连上去问 `NameHasOwner("org.freedesktop.Notifications")`**，
  第一个答"在"的才用。
- GIO 连接必须带 `MESSAGE_BUS_CONNECTION` 标志；只给 `AUTHENTICATION_CLIENT`
  时它不做 D-Bus `Hello` 握手，后续调用会被拒：
  `Client tried to send a message other than Hello without being registered`。

D-Bus 调用带 2 秒超时，所以通知服务卡住也不会阻塞按键处理
（它只在切换模式那一刻运行）。

---

## 安全：为什么默认降权运行

守护进程必须**读取全部键盘输入**（`/dev/input/event*`）并**写 `/dev/uinput`**。
最省事的做法是让它以 root 常驻 —— 但那意味着一个能读取所有按键（含密码）的进程
长期持有最高权限。

所以安装脚本默认：

- 安装 udev 规则 `70-pt35-uinput.rules`，把 `/dev/uinput` 设为 `root:input 0660`；
- 把运行账号加入 `input` 组；
- systemd 服务以该账号运行，并授予 `SupplementaryGroups=input`；
- 通过 `RuntimeDirectory=pt35-buttonmap` 给它一个专属的 `/run` 子目录 ——
  注意**降权后无法直接在 `/run` 下创建或删除文件**（删除需要父目录写权限，
  socket 自身的 `0660` 不够）。这是开发中真实踩到的 bug。

想改回 root：`sudo ./install.sh --user=root`（会移除 udev 规则）。

---

## 排查

**服务起不来** → `journalctl -u pt35-buttonmap -n 30 --no-pager`

| 日志 | 原因 | 处理 |
|---|---|---|
| `cannot be opened for writing` / `/dev/uinput` 权限 | udev 规则未生效，或账号不在 `input` 组 | 重新登录（组变更需新会话），或 `sudo ./install.sh --user=root` |
| 找不到键盘 | 键盘未被识别 | 检查 `config.txt` 是否有 `dtoverlay=dwc2,dr_mode=host`；检查内部 Type-C 转接线与 1.27H 4PIN 线 |
| `Address already in use` | 已有实例在运行 | `sudo systemctl restart pt35-buttonmap` |
| `grab/ungrab ... failed (non-fatal)` | 权限不足，或设备被占用 | 功能降级但**键盘始终可用**；按键可能被投递两次 |

**按键行为异常** → 用 `--debug` 前台运行，它会打印每个按键事件与当前模式：

```bash
sudo systemctl stop pt35-buttonmap
sudo pt35-buttonmap.py --debug --config /etc/pt35-buttonmap.toml
```

**确认设备身份**（不同批次固件的设备名可能不同）：

```bash
grep -B2 -A4 "1209:0001" /proc/bus/input/devices
```

守护进程按 **VID:PID `1209:0001`** 定位设备（**从不写死 `eventN`**，所以重启、
重枚举都不会错位），并优先按名字 `Pico Keyboard` / `Pico Mouse` 区分键盘与鼠标接口；
若某批次改了名，则回退为"第一个具备 `EV_KEY` 能力的设备"。
VID/PID 可在配置的 `[device]` 段覆盖。

### 一个输入源，只能有一条投递通路

两种模式下都对物理键盘持有**独占的 `EVIOCGRAB`**，所有按键只经虚拟设备投递。
这很关键：如果按钮模式释放了抓取，物理设备会把按键直接送给合成器，
而守护进程**同时又**转发一份 —— 每个字母都会输入两次（`aabbcc`）。
用 `libinput debug-events` 实测：

| | 物理 event0 | 虚拟 event7 |
|---|---|---|
| 有 bug 时 | 113 个事件 | 113 个事件（一一对应 = 重复）|
| 修复后 | **0 个事件** | 132 个事件（单一通路）|

---

## 测试

```bash
sudo python3 tests/test_smoke.py           # 27 项功能断言
sudo python3 tests/verify_live_gestures.py # 4 项可靠性断言
```

`test_smoke.py` 用 uinput **伪造一对与真机同 VID:PID 的键盘/鼠标**，
注入按键并断言虚拟设备的输出 —— 因此整套逻辑**不需要人按键就能确定性验证**。
覆盖：指针移动、方向键不泄漏、**字母键零占用**（L/R 与 A/B/X/Y 在两种模式下）、
轻点手势、按住与轻点的区分、模式双向切换、以及透传一致性。

`verify_live_gestures.py` 覆盖的是**稳定性**而非功能：连做 N 次手势并断言点击次数
**严格等于 N**（而不是"至少一次"），这样才能发现丢事件与状态残留。
它会启动自己专属的守护进程与配置，所以你正在运行的服务与配置不会干扰结果。

---

## 原厂键位表（供参考）

内置键盘是 **7 行 × 10 列**矩阵；行引脚 `GP16, GP10–GP15`，列引脚 `GP0–GP9`。
按住 `Fn` 切换到第二层，提供 `F1`–`F12`、`Esc`、音量、媒体键、锁屏（`Win+L`）、
屏幕开关（**Fn + C**）与亮度（**Fn + -** / **Fn + =**）。

---

## 相关项目

同一个问题的其他解决路径：

| 项目 | 说明 |
|---|---|
| [therezor/PocketTerm35-OS](https://github.com/therezor/PocketTerm35-OS) | 为 640×480 + 拇指键盘 + 方向键打造的**掌上桌面**（MIT）。自带 `pt35-kbd flash` 刷改良固件：**A B X Y → F18 F17 F15 F16**、**L R → F13 F14** |
| [black-ghost-off/pocketTerm35-keyboard-firmware](https://github.com/black-ghost-off/pocketTerm35-keyboard-firmware) | C / Pico SDK 固件，键盘 + 手柄双 HID 接口（手柄兼作鼠标）。**仓库里备份了原厂 `code.py`** —— 原厂键位表的公开来源 |
| [kay-lambdadelta/pocketterm35-keyboard-firmware](https://github.com/kay-lambdadelta/pocketterm35-keyboard-firmware) | Rust 固件实现 |
| [eltony81/pocketterm35-tinygo](https://github.com/eltony81/pocketterm35-tinygo) | TinyGo 固件 + 温度监控 |
| [Dewey1975/pocketterm35-mousemode](https://github.com/Dewey1975/pocketterm35-mousemode) | 更早的社区鼠标模式方案（方向键推指针）|
| [fry-guy/pocketterm35_touch_trackpad](https://github.com/fry-guy/pocketterm35_touch_trackpad) | 把触摸屏改造成触摸板 |

**本项目与上述项目无任何代码派生关系** —— 它是使用 Linux 标准
`evdev`/`uinput` 接口的独立实现。本项目以上述项目的公开文档与硬件实测结论
作为事实依据，特此致谢。

---

## 许可

[MIT](LICENSE)
