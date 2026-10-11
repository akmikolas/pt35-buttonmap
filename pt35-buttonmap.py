#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pt35-buttonmap — button-mapping daemon for the Waveshare PocketTerm35

Background
----
The PocketTerm35's built-in keyboard is an RP2040 running CircuitPython, wired to the Pi by OTG.
In the stock code.py 7x10 matrix: D-pad = the arrow keys, L/R = letters L/R, YXAB = letters Y/X/A/B.
In other words, **the firmware never binds the panel buttons to mouse functions at all**, so:
  - the D-pad cannot move the mouse pointer
  - L/R cannot be mouse buttons (same keycodes as letters l/r, the OS cannot tell them apart)
  - YXAB has no dedicated meaning

This daemon remaps on the Linux side (evdev/uinput layer): it never touches the
firmware, carries zero risk, and can be stopped at any time.

Features
----
  * mouse mode: hold a D-pad key -> the pointer moves (with acceleration)
  * mouse mode: double-tap ← -> left click, double-tap → -> right click (tap gestures)
  * toggle: press Backspace twice (default) or Fn twice (configurable)
  * a desktop notification on toggle, spelling out the current mode

**No letter key is ever hijacked**: L R Y X A B are the keycodes the stock firmware sends
(KEY_L/KEY_R/KEY_Y/KEY_X/KEY_A/KEY_B) — the letters themselves; hijacking them means those
letters cannot be typed. Early versions used L/R and even YXAB as mouse buttons, leaving
users unable to type "l/a"; clicks now use D-pad taps (l_r_scope/field_scope restore the old way).

Environment
--------
Raspberry Pi OS (Debian 13 trixie) / Wayland+labwc / Python 3.13 / python3-evdev

Usage
----
    sudo pt35-buttonmap.py --status        # show the current mode
    sudo pt35-buttonmap.py --set-mode mouse|buttons
    sudo pt35-buttonmap.py --foreground    # run in the foreground for debugging
"""

from __future__ import annotations

import argparse
import os
import select
import signal
import socket
import sys
import time
import tomllib
from pathlib import Path

try:
    import evdev
    from evdev import UInput, ecodes
except ImportError:  # pragma: no cover
    # Inert placeholder: --print-config / --help do not need evdev. The error is
    # raised only when the daemon truly runs (see require_evdev); otherwise, with the
    # dependency missing, the message would pollute --print-config's stdout output.
    evdev = None
    UInput = None
    ecodes = None


def require_evdev():
    global evdev, UInput, ecodes
    if ecodes is not None:
        return
    try:
        import evdev as _evdev
        from evdev import UInput as _UInput, ecodes as _ecodes
    except ImportError:
        sys.stderr.write("python3-evdev missing: sudo apt install python3-evdev\n")
        raise SystemExit(1)
    evdev, UInput, ecodes = _evdev, _UInput, _ecodes

# ---------------------------------------------------------------- device identification
# USB identity of the PocketTerm35's built-in RP2040; hardcoding VID:PID beats event numbers
VID = 0x1209
PID = 0x0001
KBD_NAME = "Pico Keyboard"
MOU_NAME = "Pico Mouse"

DEFAULT_CONFIG = "/etc/pt35-buttonmap.toml"
DEFAULT_SOCK = "/run/pt35-buttonmap/socket"

CONFIG_TEMPLATE = """\
# pt35-buttonmap configuration
# After editing: sudo systemctl restart pt35-buttonmap

[device]
# USB VID:PID of the built-in keyboard (1209:0001 = the PocketTerm35's RP2040)
vid = 0x1209
pid = 0x0001

[pointer]
# How many pixels to move per repeat event while a D-pad key is held down
speed       = 6      # normal speed
fast_speed  = 16     # speed once held longer than accel_after seconds
accel_after = 0.45

[scroll]
# Only takes effect when X/Y are wired to scrolling (field_scope = "always")
# Scroll steps per press
step = 1

[toggle]
# Gesture that toggles "mouse mode / buttons mode"
#   "backspace" = press Backspace twice (default; most reliable, purely event driven)
#   "fn"        = press Fn twice (heuristic, may misfire, can be enabled on its own)
#   "both"      = enable both
#   "off"       = no gesture; only pt35-buttonmap --toggle can switch
mode   = "backspace"
window = 0.35
# "Silence gap" in seconds for the Fn heuristic (only when mode includes fn)
fn_gap = 1.2
# Max interval between two silence gaps (seconds); beyond it: two pauses, not a "double Fn"
fn_pair_window = 5.0

[map]
# Panel buttons -> mouse. Key names are evdev KEY_* without the prefix, lowercase.
#
# ⚠️ Important: the panel L / R keys emit the letter keys KEY_L / KEY_R in the stock
#    firmware — **the very same keycodes, indistinguishable** from typed l / r.
#    So "L as a mouse button" inevitably breaks typing l in that mode. Use scope:
#      "never"  = always a plain letter; **L/R left alone** (default; clicks use D-pad taps)
#      "mouse"  = mouse button only in mouse mode (breaks typing l / r in mouse mode)
#      "always" = mouse button in both modes (l / r can never be typed)
l_r_scope = "never"
l = "btn:left"     # L key -> left mouse button
r = "btn:right"    # R key -> right mouse button

# YXAB are **letter keys** too (KEY_Y/X/A/B); hijacking them means yxab cannot be typed.
#   "never"  = never hijack them; letters still work in mouse mode (default)
#   "always" = mouse buttons in mouse mode (cost: those four letters stop working)
field_scope = "never"

# The four entries below only take effect when field_scope = "always"
mouse_a = "btn:left"      # A -> left button
mouse_b = "btn:right"     # B -> right button
mouse_x = "scroll:up"     # X -> scroll up
mouse_y = "scroll:down"   # Y -> scroll down

[gestures]
# Using quick taps on the D-pad as mouse clicks — the win is L/R need no changes at all,
# and typing is unaffected (the D-pad is silent in mouse mode anyway, no key conflicts).
#
# Rules:
#   * hold time <= tap_max seconds   =>  one "tap"
#   * taps within tap_window seconds accumulate; the count decides the action:
#       1 tap -> single   2 taps -> double   3 taps -> triple
#   * held longer than tap_max        =>  normal pointer movement (no gesture)
#   * Only wire up the counts that must fire; leaving one empty means no trigger.
#     Defaults use the **left/right D-pad keys**: ← double = left click, → double = right.
#     Leaving single taps unwired is deliberate — no accidental click from one tap.
tap_max    = 0.28
tap_window = 0.40
# Lenient window for accumulating taps = tap_window * tap_hold_factor.
# Pressing a bit slowly — "hold 0.35s + release, press again 0.08s later" — exceeds tap_window;
# this factor keeps a run of taps from being settled early in the middle.
tap_hold_factor = 2.0
left  = { double = "btn:left" }     # ←← = left mouse button
right = { double = "btn:right" }    # →→ = right mouse button
down  = {}
up    = {}

[feedback]
# Show a desktop notification on mode switch, telling you which mode you are in (default on).
# Depends on the org.freedesktop.Notifications notification service;
# here it is provided by wf-panel-pi; without that service it degrades to log-only.
notify = true
notify_timeout_ms = 4000

# Sound on mode switch. Empty "" = silent (default).
# On this device everything in /usr/share/sounds/alsa/ is a channel test tone;
# Noise.wav in particular is white noise — it sounds like hiss, not recommended.
# To enable a sound, fill in a .wav path, for example:
#   sound = "/usr/share/sounds/alsa/Front_Center.wav"
sound = ""
"""


def log(msg: str) -> None:
    print(f"[pt35-buttonmap] {msg}", flush=True)


# ---------------------------------------------------------------- config
def load_config(path: str) -> dict:
    cfg: dict = {
        "device": {"vid": VID, "pid": PID},
        "pointer": {"speed": 6, "fast_speed": 16, "accel_after": 0.45},
        "scroll": {"step": 1},
        "toggle": {"mode": "backspace", "window": 0.35, "fn_gap": 1.2, "fn_pair_window": 5.0},
        "map": {
            # L/R scope: never (default — L/R untouched, clicks use D-pad gestures)
            "l_r_scope": "never",
            "field_scope": "never",
            "l": "btn:left",
            "r": "btn:right",
            "mouse_a": "btn:left",
            "mouse_b": "btn:right",
            "mouse_x": "scroll:up",
            "mouse_y": "scroll:down",
        },
        "feedback": {
            # Desktop notification on mode switch (spells out the mode), on by default
            "notify": True,
            "notify_timeout_ms": 4000,
            # Sound on mode switch; empty = silent (default)
            "sound": "",
        },
        "gestures": {
            # Quick D-pad taps = mouse clicks. Empty means that count fires nothing.
            "tap_max": 0.28,
            "tap_window": 0.40,
            "left": {"double": "btn:left"},
            "right": {"double": "btn:right"},
            "down": {},
            "up": {},
        },
    }
    if path and os.path.exists(path):
        try:
            with open(path, "rb") as fh:
                user = tomllib.load(fh)
            for section, values in user.items():
                if isinstance(values, dict):
                    cfg.setdefault(section, {}).update(values)
                else:
                    cfg[section] = values
            log(f"loaded config {path}")
        except Exception as exc:  # must still start on a broken config; use defaults
            log(f"failed to parse config {path} ({exc}); using built-in defaults")
    return cfg


def parse_action(spec: str):
    """Parse 'btn:left' / 'scroll:up' / 'key:l' into (kind, code)."""
    kind, _, arg = spec.partition(":")
    kind, arg = kind.strip().lower(), arg.strip().lower()
    if kind == "btn":
        code = getattr(ecodes, f"BTN_{arg.upper()}", None)
        if code is None:
            raise ValueError(f"unknown mouse button {arg}")
        return ("btn", code)
    if kind == "scroll":
        if arg == "up":
            return ("scroll", 1)
        if arg == "down":
            return ("scroll", -1)
        if arg == "left":
            return ("hscroll", -1)
        if arg == "right":
            return ("hscroll", 1)
        raise ValueError(f"unknown scroll direction {arg}")
    if kind == "key":
        code = getattr(ecodes, f"KEY_{arg.upper()}", None)
        if code is None:
            raise ValueError(f"unknown key {arg}")
        return ("key", code)
    raise ValueError(f"cannot parse action {spec!r}")


# ---------------------------------------------------------------- device lookup
def find_devices(vid: int, pid: int, name_filter: str | None = None):
    """Find the built-in keyboard and mouse event devices by VID:PID, optionally also

    by a name substring. The keyboard prefers a name match; if a firmware batch renamed
    the device, it falls back to "the first device with EV_KEY capability". VID:PID has
    already narrowed the range, so the fallback is safe and fits any PocketTerm35 batch.
    """
    kbd = mou = None
    fallback_kbd = None
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except Exception:
            continue
        if dev.info.vendor != vid or dev.info.product != pid:
            dev.close()
            continue
        if name_filter and name_filter not in dev.name:
            dev.close()
            continue

        name = dev.name
        caps = dev.capabilities()
        is_named_mouse = MOU_NAME in name
        # "mouse in the name" keeps the mouse interface out of the keyboard fallback pool.
        # Without it, a device named "...Mouse" would be taken as the keyboard fallback
        # candidate, so mou would stay None forever (hit this on real hardware).
        is_mouse_name = is_named_mouse or "mouse" in name.lower()

        # Decide the mouse first so the keyboard branch cannot steal it
        if is_named_mouse and mou is None:
            mou = dev
            continue

        if KBD_NAME in name and ecodes.EV_KEY in caps:
            if kbd is None:
                kbd = dev
                continue
        elif ecodes.EV_KEY in caps and not is_mouse_name and fallback_kbd is None:
            fallback_kbd = dev
            continue
        dev.close()

    if kbd is None and fallback_kbd is not None:
        kbd, fallback_kbd = fallback_kbd, None
    if fallback_kbd is not None:
        fallback_kbd.close()
    return kbd, mou


# ---------------------------------------------------------------- main logic
class ButtonMap:
    def __init__(self, cfg: dict, start_mode: str, state_path: str,
                 debug: bool = False, name_filter: str | None = None,
                 ui_name: str = "PocketTerm35 ButtonMap",
                 sock_path: str = DEFAULT_SOCK):
        self.cfg = cfg
        self.debug = debug
        self.name_filter = name_filter
        self.ui_name = ui_name
        self.sock_path = sock_path
        self.state_path = state_path
        self.mode = start_mode
        self.pointer = cfg["pointer"]
        self.scroll_step = int(cfg["scroll"]["step"])
        self.toggle_mode = str(cfg["toggle"]["mode"]).lower()
        self.toggle_window = float(cfg["toggle"]["window"])
        self.fn_gap = float(cfg["toggle"].get("fn_gap", 1.2))
        self.fn_pair_window = float(cfg["toggle"].get("fn_pair_window", 5.0))
        _fb = cfg.get("feedback", {})
        # Sound on mode switch; off by default (see [feedback])
        self.feedback_sound = str(_fb.get("sound", "") or "")
        # Desktop notification: on by default, tells you which mode we switched to
        self.notify_enabled = bool(_fb.get("notify", True))
        self.notify_timeout_ms = int(_fb.get("notify_timeout_ms", 4000))
        # GIO finds the session bus by reading the **process environment**, and a systemd
        # service has none. Measured: without DBUS_SESSION_BUS_ADDRESS, GIO fails with
        # "Cannot autolaunch D-Bus without X11 $DISPLAY", so patch it into the process env.
        if self.notify_enabled:
            try:
                _env = self._session_env()
                os.environ["XDG_RUNTIME_DIR"] = _env["XDG_RUNTIME_DIR"]
                os.environ["DBUS_SESSION_BUS_ADDRESS"] = _env["DBUS_SESSION_BUS_ADDRESS"]
            except Exception:
                pass

        m = cfg["map"]
        self.l_r_scope = str(m.get("l_r_scope", "mouse")).lower()
        if self.l_r_scope not in ("mouse", "always", "never"):
            log(f"l_r_scope value {self.l_r_scope!r} is invalid; treating as mouse")
            self.l_r_scope = "mouse"
        self.act_l = parse_action(m["l"])
        self.act_r = parse_action(m["r"])
        # YXAB scope: never (default, never hijack) / always (mouse keys in mouse mode)
        self.field_scope = str(m.get("field_scope", "never")).lower()
        if self.field_scope not in ("never", "always"):
            log(f"field_scope value {self.field_scope!r} is invalid; treating as never")
            self.field_scope = "never"
        self.act_a = parse_action(m["mouse_a"])
        self.act_b = parse_action(m["mouse_b"])
        self.act_x = parse_action(m["mouse_x"])
        self.act_y = parse_action(m["mouse_y"])

        # ---- D-pad tap gestures ----
        # Map "tap a D-pad key N times quickly" to mouse actions, leaving L/R untouched.
        g = cfg.get("gestures", {})
        self.tap_max = float(g.get("tap_max", 0.28))
        self.tap_window = float(g.get("tap_window", 0.40))
        # Lenient window for accumulating taps: a slow press plus release-and-press gap
        # exceeds tap_window, so widen it or triple taps never connect (see _continue_pending).
        self.tap_hold_window = self.tap_window * float(g.get("tap_hold_factor", 2.0))
        self.tap_actions: dict[int, dict[int, tuple]] = {}
        for dirname, keycode in (("up", ecodes.KEY_UP), ("down", ecodes.KEY_DOWN),
                                 ("left", ecodes.KEY_LEFT), ("right", ecodes.KEY_RIGHT)):
            spec = g.get(dirname) or {}
            if not isinstance(spec, dict):
                continue
            per_count: dict[int, tuple] = {}
            for count_name, action in spec.items():
                n = {"single": 1, "double": 2, "triple": 3}.get(str(count_name).lower())
                if n is None:
                    log(f"gestures.{dirname}.{count_name}: unknown count (single/double/triple)")
                    continue
                if not action:
                    continue
                try:
                    per_count[n] = parse_action(str(action))
                except ValueError as exc:
                    log(f"gestures.{dirname}.{count_name} has an invalid action: {exc}")
            if per_count:
                self.tap_actions[keycode] = per_count

        # runtime gesture state
        self.dir_press: dict[int, float] = {}      # code -> press timestamp
        self.dir_moved: set[int] = set()           # did this press move the pointer yet
        self.pending_taps: dict[int, int] = {}     # code -> taps accumulated so far
        self.pending_since: float = 0.0
        self.pending_code: int | None = None

        self.kbd = None
        self.mou = None
        self.grabbed = False

        # D-pad state: code -> press time (used for acceleration)
        self.held: dict[int, float] = {}
        # gesture state
        self.tap_times: dict[str, float] = {}
        self.last_event_at = time.monotonic()
        self.fn_gap_started: float | None = None   # when a "suspected Fn silence" began
        self.fn_last_tap: float = 0.0

        # Read keyboard caps first, then create the virtual mouse, else pass-through is lost
        src_caps = None
        for _ in range(2):
            probe, _mou = find_devices(int(cfg["device"]["vid"]),
                                       int(cfg["device"]["pid"]), name_filter)
            if probe is not None:
                src_caps = probe.capabilities(absinfo=False)
                probe.close()
                if _mou is not None:
                    _mou.close()
                break
            time.sleep(1)

        self.ui = self._make_uinput(src_caps)
        self.server = self._make_server()

    # -------------------------------------------------- uinput
    def _make_uinput(self, src_caps: dict | None = None) -> UInput:
        """Create the virtual mouse.

        Critical: uinput only accepts event codes **declared in the device's capability
        set**; undeclared codes are dropped by the kernel (no Python-level error). So every
        keycode of the physical keyboard must be declared, or pass-through keys (D-pad,
        letters, modifiers, ...) vanish. Mouse buttons and the wheel are added on top.
        """
        keys: set[int] = set()
        rels: set[int] = set()
        if src_caps:
            keys.update(src_caps.get(ecodes.EV_KEY, []))
            rels.update(src_caps.get(ecodes.EV_REL, []))

        keys.update((ecodes.BTN_LEFT, ecodes.BTN_RIGHT, ecodes.BTN_MIDDLE,
                     ecodes.BTN_SIDE, ecodes.BTN_EXTRA))
        rels.update((ecodes.REL_X, ecodes.REL_Y, ecodes.REL_WHEEL, ecodes.REL_HWHEEL))

        return UInput({ecodes.EV_KEY: sorted(keys), ecodes.EV_REL: sorted(rels)},
                      name=self.ui_name, version=0x1)

    # -------------------------------------------------- control socket
    def _make_server(self) -> socket.socket:
        path = self.sock_path
        # Just unlink and bind: AF_UNIX never reclaims a leftover socket file.
        # Do not probe for "is somebody already listening" — on a systemd service restart
        # the old instance has just exited while its socket file remains, so the probe would
        # call it "in use" and refuse to start (manufacturing the failure). Normally Service=
        # guarantees one instance; a second one on the same path gets EADDRINUSE and fails.
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log(f"cannot create {Path(path).parent}: {exc}")
        try:
            Path(path).unlink(missing_ok=True)
        except OSError as exc:
            log(f"cannot remove old socket {path}: {exc}")

        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            srv.bind(path)
        except OSError as exc:
            raise SystemExit(
                f"cannot bind {path}: {exc}. "
                f"If another instance really is running, use --socket to pick another path")
        os.chmod(path, 0o660)
        srv.listen(4)
        srv.setblocking(False)
        return srv

    # -------------------------------------------------- mode
    def set_mode(self, mode: str, announce: bool = True) -> None:
        if mode not in ("mouse", "buttons"):
            return
        if mode == self.mode:
            return
        self.mode = mode
        self.held.clear()
        try:
            Path(self.state_path).write_text(mode + "\n")
        except Exception:
            pass
        self.apply_grab()          # Critical: a mode switch must re-apply the device grab
        if announce:
            log(f"mode switch -> {mode}")
            self._notify(mode)     # on-screen hint (on by default)
            self._beep()           # audible hint (off by default)

    def _beep(self) -> None:
        """Optional sound feedback on a mode switch.

        Off by default: early versions hardcoded /usr/share/sounds/alsa/Noise.wav, which is
        a **white-noise test tone** — half a second of hiss, quite irritating. To use a
        sound cue, enable it explicitly in the config:

            [feedback]
            sound = "/usr/share/sounds/alsa/Front_Center.wav"

        Only players that really exist are used, and stdout/stderr are discarded, so an
        unavailable audio service cannot spam the journal with errors.
        """
        wav = self.feedback_sound
        if not wav:
            return
        if not os.path.exists(wav):
            log(f"sound file does not exist, skipping: {wav}")
            return
        for player in ("/usr/bin/pw-play", "/usr/bin/paplay", "/usr/bin/aplay"):
            if not os.path.exists(player):
                continue
            try:
                pid = os.fork()
                if pid == 0:
                    devnull = os.open(os.devnull, os.O_RDWR)
                    os.dup2(devnull, 1)
                    os.dup2(devnull, 2)
                    os.execv(player, [player, wav])
                    os._exit(127)
                return
            except Exception:
                continue
        log("no usable audio player found, skipping sound")

    # -------------------------------------------------- desktop notification
    def _bus_candidates(self) -> list[str]:
        """List session bus addresses that may carry the notification service, best first."""
        cands: list[str] = []
        env_addr = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
        if env_addr:
            cands.append(env_addr)
        runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        cands.append(f"unix:path={runtime}/bus")
        try:
            for name in sorted(os.listdir("/run/user"), key=lambda x: int(x) if x.isdigit() else 0):
                cands.append(f"unix:path=/run/user/{name}/bus")
        except OSError:
            pass
        # dedupe, preserving order
        seen, out = set(), []
        for c in cands:
            if c not in seen:
                seen.add(c)
                out.append(c)
        return out

    def _session_env(self) -> dict:
        """Find a session bus that can **really send notifications**, and fill in the env.

        The daemon is started by systemd and has no login session environment; a systemd
        service may also still hold `DBUS_SESSION_BUS_ADDRESS=/run/user/0/bus` — a value
        that **points elsewhere** (measured: gives `Could not connect: Permission denied`).

        So this must not just check whether the file exists; it has to really connect and
        ask "is org.freedesktop.Notifications there?", and use the first one that says yes.
        """
        addr = self._find_notify_bus()
        env = dict(os.environ)
        if addr:
            env["DBUS_SESSION_BUS_ADDRESS"] = addr
            runtime = addr.split("unix:path=", 1)[-1].rsplit("/bus", 1)[0]
            env["XDG_RUNTIME_DIR"] = runtime
        elif not env.get("DBUS_SESSION_BUS_ADDRESS"):
            env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path=/run/user/{os.getuid()}/bus"
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        return env

    def _find_notify_bus(self) -> str | None:
        """Return the first session bus address with a notification service, else None."""
        if getattr(self, "_notify_bus", None):
            return self._notify_bus
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio, GLib
        except Exception:
            return None
        for addr in self._bus_candidates():
            try:
                conn = Gio.DBusConnection.new_for_address_sync(
                    addr, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                    | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
            except Exception:
                continue
            try:
                res = conn.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                                     "org.freedesktop.DBus", "NameHasOwner",
                                     GLib.Variant("(s)", ("org.freedesktop.Notifications",)),
                                     None, Gio.DBusCallFlags.NONE, 1500, None)
                if res.unpack()[0]:
                    self._notify_bus = addr
                    if self.debug:
                        log(f"  notify bus: {addr}")
                    return addr
            except Exception:
                continue
        return None

    def _notify_gio(self, title: str, body: str) -> bool:
        """Prefer GIO from PyGObject to send the D-Bus notification directly (most reliable)."""
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio, GLib
        except Exception:
            return False
        try:
            addr = self._find_notify_bus()
            if not addr:
                return False
            conn = Gio.DBusConnection.new_for_address_sync(
                addr, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                    | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
            bus = conn
            bus.call_sync(
                "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications", "Notify",
                GLib.Variant("(susssasa{sv}i)",
                             ("pt35-buttonmap", 0, "", title, body, [], {},
                              int(self.notify_timeout_ms))),
                None, Gio.DBusCallFlags.NONE, 2000, None)
            return True
        except Exception as exc:
            if self.debug:
                log(f"  GIO notification failed: {exc!r}")
            return False

    def _notify_send(self, title: str, body: str) -> bool:
        """Fallback: call libnotify's notify-send (only tried when it exists)."""
        exe = next((c for c in ("/usr/bin/notify-send", "/bin/notify-send")
                    if os.path.exists(c)), None)
        if exe is None:
            return False
        try:
            pid = os.fork()
            if pid == 0:
                devnull = os.open(os.devnull, os.O_RDWR)
                os.dup2(devnull, 1)
                os.dup2(devnull, 2)
                os.execve(exe, [exe, "-a", "pt35-buttonmap",
                                "-t", str(self.notify_timeout_ms), title, body],
                          self._session_env())
                os._exit(127)
            return True
        except Exception:
            return False

    def _notify(self, mode: str) -> None:
        """On a mode switch, pop a desktop notification naming the mode that is now active.

        Notifications depend on a **notification daemon** (org.freedesktop.Notifications).
        Measured to be provided by wf-panel-pi here; a desktop without that service falls
        back to notify-send and finally journal-only — the mapping itself is unaffected.
        """
        if not self.notify_enabled:
            return
        if mode == "mouse":
            title = "PT35 switched to Mouse mode"
            body = "D-pad moves pointer · ←← left click · →→ right click (no letters taken)"
        else:
            title = "PT35 switched to Buttons mode"
            body = "letters type normally · D-pad back to arrows (Backspace twice = mouse)"

        if self._notify_gio(title, body):
            if self.debug:
                log("  desktop notification sent (GIO)")
            return
        if self._notify_send(title, body):
            if self.debug:
                log("  desktop notification sent (notify-send)")
            return
        if self.debug:
            log("  no usable notification service (needs org.freedesktop.Notifications)")

    def toggle(self) -> None:
        self.set_mode("buttons" if self.mode == "mouse" else "mouse")

    # -------------------------------------------------- output
    def _btn(self, code: int, value: int) -> None:
        self.ui.write(ecodes.EV_KEY, code, value)
        self.ui.syn()

    def _rel(self, axis: int, value: int) -> None:
        if value:
            self.ui.write(ecodes.EV_REL, axis, value)
            self.ui.syn()

    def _move(self, axis: int, delta: float) -> None:
        """Accumulate fractional steps so no step is lost at low speed."""
        acc = getattr(self, f"_acc_{axis}", 0.0) + delta
        step = int(acc)
        setattr(self, f"_acc_{axis}", acc - step)
        if step:
            self._rel(axis, step)

    def _passthrough(self, ev) -> None:
        """Forward untouched raw events to the virtual device as-is.

        ui.write_event() is deliberately avoided: an explicit write()+syn() is more
        predictable and lets any exception be logged instead of silently dropped.
        """
        try:
            self.ui.write(ev.type, ev.code, ev.value)
            self.ui.syn()
        except Exception as exc:
            log(f"passthrough failed type={ev.type} code={ev.code} value={ev.value}: {exc!r}")

    def dispatch(self, action, value: int) -> None:
        kind, arg = action
        if kind == "btn":
            self._btn(arg, value)
        elif kind == "key":
            self._btn(arg, value)
        elif kind == "scroll":
            if value == 1:                     # one step on press only, no repeat spam
                self._rel(ecodes.REL_WHEEL, arg * self.scroll_step)
        elif kind == "hscroll":
            if value == 1:
                self._rel(ecodes.REL_HWHEEL, arg * self.scroll_step)

    # -------------------------------------------------- gestures
    def handle_toggle_key(self, name: str) -> bool:
        """Double-press detection. Returns True when the gesture consumed this press."""
        now = time.monotonic()
        last = self.tap_times.get(name, 0.0)
        if now - last <= self.toggle_window:
            self.tap_times[name] = 0.0
            log(f"gesture hit: {name} pressed twice, switching mode")
            self.toggle()
            return True
        if self.debug:
            log(f"gesture: first tap of {name}, waiting for the second")
        self.tap_times[name] = now
        return False

    def check_fn_silence(self) -> None:
        """Fn is swallowed by the firmware and emits no keycode. While it is held the
        firmware runs its breathing-LED branch: a stretch with no key events at all.

        The end of such a silence gap approximates one Fn press. This is a heuristic though:
        gaps count only while fully idle (nothing held), and two gap-ends must be close enough
        (within fn_pair_window) to count as Fn, else a typing pause is mistaken for Fn.
        """
        if self.toggle_mode not in ("fn", "both"):
            return
        if self.held:
            self.fn_gap_started = None
            return

        now = time.monotonic()
        gap = now - self.last_event_at

        if gap >= self.fn_gap:
            if self.fn_gap_started is None:
                self.fn_gap_started = self.last_event_at
            return

        # An event just arrived: if a long enough silence really happened, count one Fn tap
        if self.fn_gap_started is not None:
            self.fn_gap_started = None
            if self.fn_last_tap and (now - self.fn_last_tap) <= self.fn_pair_window:
                self.fn_last_tap = 0.0
                self.toggle()
            else:
                self.fn_last_tap = now

    # -------------------------------------------------- event handling
    def handle_kbd(self, ev) -> None:
        self.last_event_at = time.monotonic()

        if self.debug and ev.type == ecodes.EV_KEY:
            log(f"  <- {evdev.ecodes.KEY.get(ev.code, ev.code)} value={ev.value} mode={self.mode}")

        if ev.type != ecodes.EV_KEY:
            self._passthrough(ev)
            return

        code, value = ev.code, ev.value

        # Backspace pressed twice toggles
        if (self.toggle_mode in ("backspace", "both")
                and code == ecodes.KEY_BACKSPACE and value == 1
                and self.handle_toggle_key("backspace")):
            return

        # L / R: stock firmware emits them as the letter keys KEY_L / KEY_R, the same keycodes
        # as typed l / r, so "mouse button" and "typeable letter" cannot both hold; l_r_scope
        # sets the scope. Default is mouse-mode only, so l / r type normally in buttons mode.
        if self.l_r_scope != "never" and (
                self.l_r_scope == "always" or self.mode == "mouse"):
            if code == ecodes.KEY_L:
                self.dispatch(self.act_l, value)
                return
            if code == ecodes.KEY_R:
                self.dispatch(self.act_r, value)
                return

        if self.mode == "mouse":
            if code in (ecodes.KEY_UP, ecodes.KEY_DOWN, ecodes.KEY_LEFT, ecodes.KEY_RIGHT):
                self.handle_dpad(code, value)
                return
            # YXAB are **letter keys** too (KEY_Y/X/A/B); hijacking them means no yxab.
            # With the default field_scope="never" they still emit letters in mouse mode;
            # clicks all go through D-pad tap gestures, so no letter has to be sacrificed.
            if self.field_scope != "never":
                if code == ecodes.KEY_A:
                    self.dispatch(self.act_a, value)
                    return
                if code == ecodes.KEY_B:
                    self.dispatch(self.act_b, value)
                    return
                if code == ecodes.KEY_X:
                    self.dispatch(self.act_x, value)
                    return
                if code == ecodes.KEY_Y:
                    self.dispatch(self.act_y, value)
                    return

        self._passthrough(ev)

    # -------------------------------------------------- D-pad tap gestures
    def _is_tap_dir(self, code: int) -> bool:
        """Whether this D-pad key has tap gestures; without them it uses plain push logic."""
        return code in self.tap_actions

    def _tap_action(self, code: int, count: int) -> tuple | None:
        return (self.tap_actions.get(code) or {}).get(count)

    def _fire_taps(self) -> None:
        """Settle one round of taps: fire the action for the accumulated count.

        Only a count that has an action configured fires; other counts are dropped
        silently, so an unconfigured count cannot leak a stray click.
        """
        code, count = self.pending_code, self.pending_taps.get(self.pending_code or -1, 0)
        self.pending_code = None
        self.pending_taps.clear()
        if code is None or count <= 0:
            return
        action = self._tap_action(code, count)
        if action is None:
            if self.debug:
                log(f"  gesture: {evdev.ecodes.KEY.get(code, code)} tap x{count} unmapped, ignored")
            return
        log(f"gesture: {evdev.ecodes.KEY.get(code, code)} tap x{count} -> mouse action")
        # a mouse button needs a paired press+release to be one complete click
        kind, arg = action
        self.dispatch(action, 1)
        self.dispatch(action, 0)

    def check_tap_timeout(self) -> None:
        """Settle pending gestures on timeout; called every main-loop iteration.

        There are two wait times, depending on **whether another tap would still help**:

        * This direction already has an action at this count => settle at once, zero delay
          (e.g. down has double configured, so the 2nd tap clicks immediately).
        * One more tap could still reach a configured count => wait `tap_hold_window`
          (e.g. up only has triple, so after taps 1 and 2 it waits for the 3rd). The lenient
          window is mandatory here, or slow presses settle mid-way and triples never connect.

        The cost: counts with no action (up's 1st and 2nd taps, say) are dropped only when the
        wide window expires, and no click happens meanwhile — a necessary delay, paid for
        "slow taps can still chain", tunable via `gestures.tap_hold_factor`.
        """
        if self.pending_code is None:
            return
        code = self.pending_code
        count = self.pending_taps.get(code, 0)
        max_cfg = max(self.tap_actions.get(code, {0: None}).keys())
        wait = self.tap_window if count >= max_cfg else self.tap_hold_window
        if time.monotonic() - self.pending_since >= wait:
            self._fire_taps()

    def _continue_pending(self, code: int, now: float) -> None:
        """On a fresh press of the same direction, decide: keep chaining or start over.

        This uses the **wider** `tap_hold_window` rather than `tap_window`, because the

            press a bit slowly = hold 0.35s + 0.08s to press again = 0.43s

        which already exceeds tap_window(0.40s), so the 1st tap is settled by `check_tap_timeout()`
        as "only 1 tap" before the next press arrives, and a triple tap can never connect
        (instrumentation kept showing `pending_code=103 taps={103: 1}` — that is this line).

        Chained taps should be more forgiving than a single tap, so it gets a longer window:
        same direction inside it = keep chaining; other direction or timeout = settle.
        """
        if self.pending_code != code:
            self._fire_taps()
            return
        if now - self.pending_since >= self.tap_hold_window:
            self._fire_taps()

    def handle_dpad(self, code: int, value: int) -> None:
        """Handle D-pad keys in mouse mode: tell tap gestures from hold-to-move.

        A press counts as a "tap" under **either** of two rules:

        1. hold time <= tap_max (a quick press)
        2. or **the pointer never moved during the press**

        Rule 2 comes from real-hardware feedback: triple taps are inherently harder to press
        quickly than doubles, and raising tap_max past 0.4s would slow "hold to move" starts.
        But "did it move" alone reveals the intent — really holding to move always produces
        motion, so this rule almost never misfires, yet a slow multi-tap still works.
        """
        if not self._is_tap_dir(code):
            # D-pad key with no tap gesture: keep the old behaviour (move on press, repeat)
            if value == 1:
                self.held[code] = time.monotonic()
            elif value == 0:
                self.held.pop(code, None)
            if value in (1, 2):
                self._drive_dpad()
            return

        now = time.monotonic()
        if value == 1:                      # press: record the time only, do not move
            self._continue_pending(code, now)
            self.dir_press[code] = now
            self.dir_moved.discard(code)    # this press has not moved the pointer yet
            self.held.pop(code, None)
            return

        if value == 2:                      # auto-repeat: does this become a hold?
            t0 = self.dir_press.get(code)
            if t0 is not None and now - t0 > self.tap_max:
                self.held[code] = t0        # switch to moving; keep t0 for acceleration
                self.dir_press.pop(code, None)
                self._fire_taps()           # holding is new intent, settle pending taps
            if code in self.held:
                self.dir_moved.add(code)    # this press really did move the pointer
                self._drive_dpad()
            return

        # value == 0: release
        t0 = self.dir_press.pop(code, None)
        moved = code in self.dir_moved
        self.dir_moved.discard(code)
        if t0 is None:
            self.held.pop(code, None)
            return
        if now - t0 > self.tap_max and moved:
            # held long **and** really moved the pointer => a hold, not a tap
            self.held.pop(code, None)
            self._drive_dpad()
            return

        # one valid tap: add it to the current sequence
        if self.pending_code != code:
            self.pending_code = code
            self.pending_taps = {code: 0}
        self.pending_taps[code] = self.pending_taps.get(code, 0) + 1
        self.pending_since = now
        if self.debug:
            slow = "(slow but no movement, still a tap) " if now - t0 > self.tap_max else ""
            log(f"  gesture: {evdev.ecodes.KEY.get(code, code)} x{self.pending_taps[code]} {slow}")
        # once the direction's highest configured count is reached, settle at once
        max_configured = max(self.tap_actions.get(code, {0: None}).keys())
        if self.pending_taps[code] >= max_configured:
            self._fire_taps()

    def _drive_dpad(self) -> None:
        now = time.monotonic()
        speed = float(self.pointer["speed"])
        for code, t0 in self.held.items():
            if now - t0 >= float(self.pointer["accel_after"]):
                speed = float(self.pointer["fast_speed"])
                break
        dx = dy = 0.0
        if ecodes.KEY_LEFT in self.held:
            dx -= speed
        if ecodes.KEY_RIGHT in self.held:
            dx += speed
        if ecodes.KEY_UP in self.held:
            dy -= speed
        if ecodes.KEY_DOWN in self.held:
            dy += speed
        if dx:
            self._move(ecodes.REL_X, dx)
        if dy:
            self._move(ecodes.REL_Y, dy)

    # -------------------------------------------------- grab / release
    def open_devices(self) -> bool:
        kbd, mou = find_devices(int(self.cfg["device"]["vid"]),
                                int(self.cfg["device"]["pid"]),
                                self.name_filter)
        if kbd is None:
            return False
        self.kbd, self.mou = kbd, mou
        log(f"kbd {kbd.path} ({kbd.name})" + (f" / mouse {mou.path}" if mou else " / no mouse"))
        self.apply_grab()
        return True

    def apply_grab(self) -> None:
        """**Both modes must exclusively grab the physical keyboard.**

        This fixes "one letter in, two out": by design there must be only **one** delivery path:

        * If buttons mode released the grab, the physical device would send keys straight to
          the compositor while the daemon **also** forwards the same key to the virtual one,
          which sees both — showing up as `aabbcc` (event0/event7 measured 113:113 events).
        * So EVIOCGRAB stays on in every mode and all keys go through the virtual device. The
          grab is exclusive: once grabbed, the compositor cannot see that physical device.

        Safety design:
        * A failed grab is **not fatal** (dropped privileges, permissions, device busy).
          On failure it conservatively counts as "not grabbed": delivery may be doubled,
          but **the keyboard always works** — the handheld never turns into a brick.
        * self.grabbed becomes True only after every device succeeded, so the state cannot
          disagree with reality and make a later ungrab be skipped.
        """
        if self.grabbed:
            return
        ok = True
        for dev in (self.kbd, self.mou):
            if dev is None:
                continue
            try:
                dev.grab()
                if self.debug:
                    log(f"  exclusively grabbed {dev.path}")
            except Exception as exc:
                ok = False
                log(f"grab {dev.path} failed (not fatal, but keys may double up): {exc}")
        self.grabbed = ok

    # -------------------------------------------------- main loop
    def run(self) -> None:
        while not self.open_devices():
            log("PocketTerm35 built-in keyboard (VID:PID 1209:0001) not found, retry in 5s")
            time.sleep(5)

        self.apply_grab()
        log(f"startup complete, current mode: {self.mode}")

        while True:
            fds = [self.kbd.fileno(), self.server.fileno()]
            if self.mou is not None:
                fds.append(self.mou.fileno())
            try:
                ready, _, _ = select.select(fds, [], [], 0.1)
            except (OSError, ValueError):
                # device dropped: re-enumerate
                log("device handle went stale, looking for devices again")
                self._close_devices()
                while not self.open_devices():
                    time.sleep(3)
                continue

            for fd in ready:
                if fd == self.server.fileno():
                    self._serve_control()
                    continue
                dev = self.kbd if fd == self.kbd.fileno() else self.mou
                try:
                    for ev in dev.read():
                        if dev is self.kbd:
                            self.handle_kbd(ev)
                        elif self.mode == "mouse":
                            # physical mouse interface (touch/wheel) passes through in mouse mode
                            self._passthrough(ev)
                except OSError:
                    log("device read failed, re-enumerating")
                    self._close_devices()
                    while not self.open_devices():
                        time.sleep(3)

            self.check_fn_silence()
            self.check_tap_timeout()

    def _serve_control(self) -> None:
        try:
            conn, _ = self.server.accept()
        except BlockingIOError:
            return
        with conn:
            conn.settimeout(1.0)
            try:
                req = conn.recv(64).decode(errors="replace").strip().lower()
            except Exception:
                return
            if req.startswith("toggle"):
                self.toggle()
                conn.sendall(f"mode={self.mode}\n".encode())
            elif req.startswith("mouse"):
                self.set_mode("mouse")
                conn.sendall(f"mode={self.mode}\n".encode())
            elif req.startswith("buttons"):
                self.set_mode("buttons")
                conn.sendall(f"mode={self.mode}\n".encode())
            else:
                conn.sendall(f"mode={self.mode}\n".encode())

    def _close_devices(self) -> None:
        for dev in (self.kbd, self.mou):
            if dev is None:
                continue
            try:
                if self.grabbed:
                    dev.ungrab()
                dev.close()
            except Exception:
                pass
        self.kbd = self.mou = None
        self.grabbed = False
        self.held.clear()


# ---------------------------------------------------------------- CLI
def client_request(req: str, path: str = DEFAULT_SOCK) -> str:
    if not os.path.exists(path):
        return f"daemon not running ({path} missing). Start: systemctl start pt35-buttonmap"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(2.0)
            s.connect(path)
            s.sendall(req.encode())
            return s.recv(64).decode(errors="replace").strip()
    except ConnectionRefusedError:
        return (f"{path} exists but nobody is listening (probably a stale dead socket).\n"
                f"Fix: sudo systemctl restart pt35-buttonmap")
    except Exception as exc:
        return f"communication failed: {exc}"


def main() -> int:
    ap = argparse.ArgumentParser(description="PocketTerm35 button-mapping daemon")
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--status", action="store_true", help="query the current mode")
    ap.add_argument("--toggle", action="store_true", help="toggle the mode")
    ap.add_argument("--set-mode", choices=["mouse", "buttons"], help="set the mode directly")
    ap.add_argument("--start-mode", choices=["mouse", "buttons"], default=None,
                    help="mode to start the daemon in (default: read the state file)")
    ap.add_argument("--state", default="/run/pt35-buttonmap/mode")
    ap.add_argument("--socket", default=DEFAULT_SOCK,
                    help="control socket path (debug instances: use another to avoid clashes)")
    ap.add_argument("--print-config", action="store_true", help="print the default config template")
    ap.add_argument("--debug", action="store_true", help="log every key event, for troubleshooting")
    ap.add_argument("--device-name", default=None,
                    help="match only devices whose name contains this substring (testing)")
    ap.add_argument("--name", default="PocketTerm35 ButtonMap",
                    help="virtual device name (use a unique one when self-testing)")
    # Foreground is the default; this switch exists for readability in docs/services
    ap.add_argument("--foreground", action="store_true",
                    help="run in the foreground (the default; explicit helps debugging)")
    args = ap.parse_args()

    if args.print_config:
        print(CONFIG_TEMPLATE, end="")
        return 0
    if args.status:
        print(client_request("status", args.socket))
        return 0
    if args.toggle:
        print(client_request("toggle", args.socket))
        return 0
    if args.set_mode:
        print(client_request(args.set_mode, args.socket))
        return 0

    start = args.start_mode
    if start is None:
        try:
            start = Path(args.state).read_text().strip()
        except Exception:
            start = "mouse"
    if start not in ("mouse", "buttons"):
        start = "mouse"

    cfg = load_config(args.config)
    require_evdev()
    app = ButtonMap(cfg, start, args.state, debug=args.debug,
                    name_filter=args.device_name, ui_name=args.name,
                    sock_path=args.socket)

    def bye(signum, _frame):
        log(f"received signal {signum}, exiting (releasing devices)")
        app._close_devices()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)

    try:
        app.run()
    except SystemExit:
        raise
    except Exception as exc:
        log(f"exited with an error: {exc}")
        app._close_devices()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
