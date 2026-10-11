# pt35-buttonmap

**English** · [中文](README.zh-CN.md)

Add mouse capability to the built-in keyboard of the **Waveshare PocketTerm35**
(Raspberry Pi 4B / Pi 5 handheld).

**No firmware flashing.** Everything happens at the Linux `evdev`/`uinput` layer.
One command to install, one command to remove.

```
Hold a D-pad direction  ->  move the mouse pointer (with acceleration)
Tap ← twice quickly     ->  left click
Tap → twice quickly     ->  right click
Tap Backspace twice     ->  toggle between Mouse mode and Buttons mode
                            (a desktop notification tells you which mode you are in)

L R Y X A B and every other letter key  ->  untouched, always type normally
```

---

## Why this exists

The PocketTerm35's built-in keyboard is an **RP2040 (Raspberry Pi Pico) running
CircuitPython**, wired to the Pi over USB OTG. Its stock firmware only does
"matrix scan → USB HID keycodes" — **there is no mouse mapping layer at all**:

| Panel key | What the stock firmware actually sends |
|---|---|
| D-pad ↑↓←→ | `KEY_UP` / `KEY_DOWN` / `KEY_LEFT` / `KEY_RIGHT` |
| L / R | **`KEY_L` / `KEY_R`** — i.e. the letters `l` / `r` |
| Y / X / A / B | **`KEY_Y` / `KEY_X` / `KEY_A` / `KEY_B`** — i.e. the letters |

Consequences:

- the D-pad cannot move a pointer;
- **L/R cannot be mouse buttons, because they share the exact same keycode as the
  letters `l`/`r`** — the OS cannot tell "panel L pressed" from "letter l typed".

There are only two places to fix this:

1. **Reflash the keyboard firmware** to give the buttons their own keycodes.
   But the stock `boot.py` calls `storage.disable_usb_drive()`, which disables the
   CIRCUITPY mass-storage drive — so you **must open the case and press BOOTSEL**
   (the Pico is sealed inside; there is no external USB data port).
   And the keyboard is this handheld's **only input device**: brick it and the
   device becomes unusable.
2. **Remap in userspace on the Linux side** ← what this project does.

Why option 2: zero disassembly risk, verifiable immediately,
`install.sh --uninstall` reverts everything, and the firmware is never touched.
The trade-off is that the shell **cannot distinguish** "panel A pressed" from
"letter a typed" — that information is already lost before it reaches Linux.
If you need that distinction (e.g. for a desktop like
[pt35-desktop](https://github.com/therezor/PocketTerm35-OS) that binds A = confirm,
B = back), you must take route 1. See [Related projects](#related-projects).

### A design rule learned the hard way

This project originally mapped L/R — and then A/B/X/Y — to mouse buttons.
Every one of those keys is a **letter keycode**, so hijacking them made those
letters impossible to type (`ls`, `rm`, `clear` all broke). The final design
follows one rule:

> **Never occupy a keycode that already carries a meaning you need.**
> Clicks are handled by *tap gestures on the D-pad* instead, because the D-pad is
> already silent in Mouse mode (it emits no arrow keys) — so it has no conflict.

---

## Requirements

- Waveshare PocketTerm35 (Pi 4B or Pi 5 variant)
- Raspberry Pi OS **Debian 12 (bookworm) or 13 (trixie), 64-bit**; Wayland or X11
- Python 3.11+ (uses `tomllib`)
- `python3-evdev` (installed automatically by the script)
- The keyboard is a USB OTG device, so `config.txt` must contain
  `dtoverlay=dwc2,dr_mode=host` (official requirement; already present on stock images)

> This project is compositor-agnostic: it works at the **evdev/uinput** layer, so
> Wayland (labwc / sway / KWin / Hyprland) and X11 both work. Neither X11's
> `xbindkeys`/`xmodmap` nor compositor keybinds can generate pointer motion.

---

## Install

```bash
git clone https://github.com/akmikolas/pt35-buttonmap
cd pt35-buttonmap
sudo ./install.sh
```

By default the service runs **unprivileged**: as the account that invoked `sudo`,
plus a udev rule granting `/dev/uinput` to the `input` group.
To run as root instead: `sudo ./install.sh --user=root`.

Takes effect immediately, no reboot needed. Verify:

```bash
sudo pt35-buttonmap.py --status      # current mode
journalctl -u pt35-buttonmap -f      # logs
```

### Uninstall

```bash
sudo ./install.sh --uninstall
```

The keyboard reverts to stock behaviour immediately. `/etc/pt35-buttonmap.toml`
(your configuration) is kept — remove it by hand if you want.

---

## Usage

| Action | Command |
|---|---|
| Show current mode | `sudo pt35-buttonmap.py --status` |
| Toggle mode | `sudo pt35-buttonmap.py --toggle` |
| Switch to Buttons mode | `sudo pt35-buttonmap.py --set-mode buttons` |
| Switch to Mouse mode | `sudo pt35-buttonmap.py --set-mode mouse` |
| Debug in the foreground | `sudo pt35-buttonmap.py --debug --config /etc/pt35-buttonmap.toml` |

**Gesture toggle:** tap `Backspace` twice (within 0.35 s). A desktop notification
appears telling you which mode you are in (requires
`org.freedesktop.Notifications`; degrades gracefully if absent — see
[Mode-switch notification](#mode-switch-notification)).

> **Why `Backspace` and not `Fn`?** `Fn` is intercepted inside the firmware and
> emits **no keycode at all** — Linux cannot see it. Any "detect Fn" scheme has to
> guess from side effects (e.g. an unusually long silence), and in practice that
> mis-fires badly: a normal pause while typing gets read as an Fn tap and the mode
> flaps back and forth. So the default uses a key that really exists and does not
> collide with common actions. The heuristic is still available as
> `toggle.mode = "fn"`, at your own risk.

---

## Click gestures: on the D-pad, not on L/R

Panel L/R emit **`KEY_L`/`KEY_R`**, the very same keycodes as the letters `l`/`r`.
So "L as a mouse button" and "able to type `l`" are **mutually exclusive**.
Early versions hijacked L/R and then A/B/X/Y; those letters could no longer be typed
— `ls`, `rm`, `clear` all broke.

**Now no letter key is occupied at all.** Clicks come from quick taps on the D-pad,
which is silent in Mouse mode anyway, so there is **no keycode conflict**:

| Gesture | Action |
|---|---|
| Tap **←** twice quickly | Left click |
| Tap **→** twice quickly | Right click |
| Hold a direction | Move the pointer (no gesture) |

Tapping once deliberately does nothing — otherwise merely nudging a direction key
would fire a click.

### Timing rules (all three were tuned on real hardware)

1. A hold of at most `tap_max` (default **0.28 s**) counts as one *tap*;
2. **or** the pointer never moved during the hold — that also counts as a tap.
   This exists because multi-taps are hard to press fast enough, while a genuine
   pointer-move hold always produces motion — so this rule almost never mis-fires;
3. The multi-tap accumulation window is
   `tap_window × tap_hold_factor` (default **0.40 × 2 = 0.8 s**).
   A slow tap is "hold 0.35 s + 0.08 s before the next press = 0.43 s", which
   already exceeds `tap_window`; without widening it, the first tap is settled
   before the next press arrives and a multi-tap can never complete.

Only a tap count that **has an action configured** fires. `←`/`→` only define
`double`, so a single tap does nothing.

> **Latency trade-off:** tap counts with no action (e.g. the 1st and 2nd tap when
> only `triple` is configured) are discarded only after the wide window closes;
> no click happens during that time. That is the price of "slow taps still work".
> Tune `gestures.tap_hold_factor` (smaller = snappier clicks, but slow taps fail
> more easily).

---

## Configuration

Edit `/etc/pt35-buttonmap.toml` (generated on first install, with comments),
then `sudo systemctl restart pt35-buttonmap`.

Action syntax:

| Value | Meaning |
|---|---|
| `btn:left` / `btn:right` / `btn:middle` / `btn:side` / `btn:extra` | Mouse button |
| `scroll:up` / `scroll:down` / `scroll:left` / `scroll:right` | Wheel |
| `key:<name>` | Plain key; name is the evdev `KEY_*` without the prefix, e.g. `key:l`, `key:enter` |

Common changes:

```toml
# Rebind the gestures (default: ← double = left, → double = right)
[gestures]
tap_max         = 0.28   # holds shorter than this count as a tap
tap_window      = 0.40   # max gap between taps
tap_hold_factor = 2.0    # multi-tap window = tap_window * this
left  = { double = "btn:left" }
right = { double = "btn:right" }
# Want one tap to click immediately (snappier, but a nudge will click)?
# left = { single = "btn:left", double = "btn:left" }

# Point the gestures at other directions instead
# down = { double = "btn:left" }
# up   = { triple = "btn:right" }

# Pointer speed
[pointer]
speed       = 6      # pixels per event
fast_speed  = 16     # speed after holding longer than accel_after
accel_after = 0.45

# Toggle gesture
[toggle]
mode   = "backspace"   # backspace | fn | both | off
window = 0.35

# Mode-switch notification (on by default)
[feedback]
notify = true
notify_timeout_ms = 4000
# Sound is off by default. Do NOT use /usr/share/sounds/alsa/Noise.wav —
# that is a white-noise test tone; it sounds like half a second of static.
sound = ""
# sound = "/usr/share/sounds/alsa/Front_Center.wav"
```

### Letting L/R or YXAB act as mouse buttons again

Off by default, because it costs you those letters:

```toml
[map]
l_r_scope   = "mouse"    # never (default) | mouse | always
field_scope = "always"   # never (default) | always
l = "btn:left"
r = "btn:right"
mouse_a = "btn:left"     # only used when field_scope = "always"
mouse_b = "btn:right"
mouse_x = "scroll:up"
mouse_y = "scroll:down"
```

`"never"` — always letters · `"mouse"` — mouse buttons only in Mouse mode ·
`"always"` — mouse buttons in both modes (then you cannot type those letters).

### Mode-switch notification

A notification tells you which mode you are in:

| Mode | Notification |
|---|---|
| Mouse | `PT35: MOUSE mode` / D-pad moves the pointer · ←← left click · →→ right click (no letter key occupied) |
| Buttons | `PT35: BUTTONS mode` / letter keys type normally · D-pad is an arrow key again (tap Backspace twice to go back) |

Implementation notes, all learned the hard way:

- Notifications require a notification daemon (`org.freedesktop.Notifications`).
  It works out of the box when a desktop panel provides one, and this project falls
  back **GIO → `notify-send` → log only**, so a desktop without one loses nothing
  but the popup.
- A systemd service has **no session environment**. Without
  `DBUS_SESSION_BUS_ADDRESS`, GIO fails with
  `Cannot autolaunch D-Bus without X11 $DISPLAY`. GIO reads the **process**
  environment, so the variable must be set in-process, not merely for a child.
- Do not pick the bus by "does the socket file exist": a service environment may
  still carry a stale `DBUS_SESSION_BUS_ADDRESS` pointing elsewhere, which yields
  `Permission denied` and never falls back. This project **connects to each
  candidate bus and asks `NameHasOwner("org.freedesktop.Notifications")`**,
  using the first that answers yes.
- The GIO connection needs the `MESSAGE_BUS_CONNECTION` flag; with only
  `AUTHENTICATION_CLIENT` it skips the D-Bus `Hello` handshake and later calls are
  rejected with `Client tried to send a message other than Hello without being registered`.

D-Bus calls carry a 2 s timeout, so a hung notification service cannot block key
handling (it only runs at the moment of a mode switch).

---

## Security: why it runs unprivileged by default

The daemon must **read all keyboard input** (`/dev/input/event*`) and
**write `/dev/uinput`**. The lazy approach is to run it as root forever — but that
means a process able to read every keystroke (passwords included) permanently holds
the highest privilege.

So by default the installer:

- installs a udev rule `70-pt35-uinput.rules` setting `/dev/uinput` to `root:input 0660`;
- adds the run account to the `input` group;
- runs the systemd service as that account with `SupplementaryGroups=input`;
- gives it a private `/run` subdirectory via `RuntimeDirectory=pt35-buttonmap` —
  note that **an unprivileged process cannot create or delete files directly in
  `/run`** (deleting needs write permission on the parent directory; the socket's
  own `0660` is not enough). This was a real bug during development.

To go back to root: `sudo ./install.sh --user=root` (removes the udev rule).

---

## Troubleshooting

**Service will not start** → `journalctl -u pt35-buttonmap -n 30 --no-pager`

| Log | Cause | Fix |
|---|---|---|
| `cannot be opened for writing` / `/dev/uinput` permissions | udev rule not applied, or account not in the `input` group | log in again (group changes need a new session), or `sudo ./install.sh --user=root` |
| keyboard not found | keyboard not enumerated | check `config.txt` for `dtoverlay=dwc2,dr_mode=host`; check the internal Type-C adapter and the 1.27H 4PIN cable |
| `Address already in use` | an instance is already running | `sudo systemctl restart pt35-buttonmap` |
| `grab/ungrab ... failed (non-fatal)` | insufficient permission, or the device is in use | functionality degrades but **the keyboard stays usable**; keys may be delivered twice |

**Keys behaving oddly** → run in the foreground with `--debug`, which prints every
key event and the current mode:

```bash
sudo systemctl stop pt35-buttonmap
sudo pt35-buttonmap.py --debug --config /etc/pt35-buttonmap.toml
```

**Identify the devices** (device names can differ between firmware batches):

```bash
grep -B2 -A4 "1209:0001" /proc/bus/input/devices
```

The daemon locates devices by **VID:PID `1209:0001`** (never by `eventN`, so
restarts and re-enumeration cannot mismatch), and prefers the names
`Pico Keyboard` / `Pico Mouse` to tell the keyboard and mouse interfaces apart;
if a batch renames them it falls back to "the first device with `EV_KEY`".
VID/PID can be overridden in the `[device]` section of the config.

### One source, exactly one delivery path

Both modes hold an **exclusive `EVIOCGRAB`** on the physical keyboard; every key is
delivered through the virtual device only. This matters: if Buttons mode released
the grab, the physical device would deliver to the compositor *and* the daemon would
forward a second copy — every letter typed twice (`aabbcc`). Measured with
`libinput debug-events`:

| | physical event0 | virtual event7 |
|---|---|---|
| with the bug | 113 events | 113 events (one-to-one = duplicated) |
| fixed | **0 events** | 132 events (single path) |

---

## Tests

```bash
sudo python3 tests/test_smoke.py           # 27 functional assertions
sudo python3 tests/verify_live_gestures.py # 4 reliability assertions
```

`test_smoke.py` uses uinput to **forge a keyboard/mouse pair with the same VID:PID
as the real hardware**, injects keys and asserts the virtual device's output — so
the whole logic is verified deterministically without anyone pressing buttons.
Coverage: pointer motion, arrow keys not leaking, **zero letter-key occupation**
(L/R and A/B/X/Y across both modes), tap gestures, hold-vs-tap, mode switching both
ways, and pass-through consistency.

`verify_live_gestures.py` covers *stability* rather than *behaviour*: it performs N
gestures in a row and asserts the click count is **exactly N** (not "at least once"),
which is what catches dropped events and state leakage. It starts its own private
daemon and config, so your real service and configuration cannot skew the result.

---

## Stock keymap (reference)

The built-in keyboard is a **7-row × 10-column** matrix; row pins
`GP16, GP10–GP15`, column pins `GP0–GP9`. Holding `Fn` selects a second layer
providing `F1`–`F12`, `Esc`, volume, media keys, lock screen (`Win+L`),
screen off/on (**Fn + C**) and brightness (**Fn + -** / **Fn + =**).

---

## Related projects

Other approaches to the same problem:

| Project | Notes |
|---|---|
| [therezor/PocketTerm35-OS](https://github.com/therezor/PocketTerm35-OS) | A **pocket desktop** built for 640×480 + thumb keyboard + D-pad (MIT). Ships `pt35-kbd flash` with a patched firmware: **A B X Y → F18 F17 F15 F16**, **L R → F13 F14** |
| [black-ghost-off/pocketTerm35-keyboard-firmware](https://github.com/black-ghost-off/pocketTerm35-keyboard-firmware) | C / Pico SDK firmware with dual HID interfaces (keyboard + gamepad that doubles as a mouse). **Its repo contains a backup of the stock `code.py`** — the public source for the stock keymap |
| [kay-lambdadelta/pocketterm35-keyboard-firmware](https://github.com/kay-lambdadelta/pocketterm35-keyboard-firmware) | Rust firmware implementation |
| [eltony81/pocketterm35-tinygo](https://github.com/eltony81/pocketterm35-tinygo) | TinyGo firmware with temperature monitoring |
| [Dewey1975/pocketterm35-mousemode](https://github.com/Dewey1975/pocketterm35-mousemode) | An earlier community mouse-mode approach (D-pad moves the pointer) |
| [fry-guy/pocketterm35_touch_trackpad](https://github.com/fry-guy/pocketterm35_touch_trackpad) | Turns the touchscreen into a trackpad |

**This project shares no derived code with any of them** — it is an independent
implementation using the standard Linux `evdev`/`uinput` interfaces. It does rely on
those projects' public documentation and hardware findings as factual references,
and is grateful for them.

---

## License

[MIT](LICENSE)
