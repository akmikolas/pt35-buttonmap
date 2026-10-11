#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pt35-buttonmap self-test: fake a device via uinput with the same VID:PID and name
as the PocketTerm35 keyboard, inject keys, assert the daemon's virtual mouse output.

No real hardware required, safe to re-run:  sudo python3 tests/test_smoke.py
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from evdev import UInput, ecodes, InputDevice  # noqa: E402
import gestures  # noqa: E402  (gesture test helper in the same directory)

DAEMON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "pt35-buttonmap.py")
VID, PID = 0x1209, 0x0001
TIMEOUT = 5.0


def make_fake_devices():
    """Fake keyboard/mouse; VID:PID and name must match the real device for the daemon."""
    kbd_caps = {
        ecodes.EV_KEY: [
            ecodes.KEY_UP, ecodes.KEY_DOWN, ecodes.KEY_LEFT, ecodes.KEY_RIGHT,
            ecodes.KEY_A, ecodes.KEY_B, ecodes.KEY_X, ecodes.KEY_Y,
            ecodes.KEY_L, ecodes.KEY_R, ecodes.KEY_BACKSPACE, ecodes.KEY_ENTER,
        ],
    }
    kbd = UInput(kbd_caps, name="PT35TEST Fake Pico Keyboard",
                 vendor=VID, product=PID, version=0x0111,
                 input_props=[])
    mou = UInput({ecodes.EV_REL: [ecodes.REL_X, ecodes.REL_Y, ecodes.REL_WHEEL]},
                 name="PT35TEST Fake Pico Mouse",
                 vendor=VID, product=PID, version=0x0111,
                 input_props=[])
    return kbd, mou


UI_NAME = "PT35TEST ButtonMap"


def find_virtual():
    """Only accept the virtual device created by this test run, ignoring stale leftovers."""
    for path in sorted(evdev_list()):
        try:
            d = InputDevice(path)
        except Exception:
            continue
        if d.name == UI_NAME:
            return d
        d.close()
    return None


def evdev_list():
    from evdev import list_devices
    return list_devices()


def drain(dev, seconds):
    """Collect events over a period of time.

    Use select for the timeout and keep the fd blocking: setting O_NONBLOCK on a
    uinput client fd trips read()'s return path and hides events already arrived.
    """
    import select as _select
    out = []
    end = time.time() + seconds
    while True:
        remain = end - time.time()
        if remain <= 0:
            break
        r, _, _ = _select.select([dev.fileno()], [], [], remain)
        if not r:
            break
        try:
            for ev in dev.read():
                out.append((ev.type, ev.code, ev.value, time.time()))
        except OSError:
            break
    return out


def tap(ui, code, hold=0.06):
    ui.write(ecodes.EV_KEY, code, 1)
    ui.syn()
    time.sleep(hold)
    ui.write(ecodes.EV_KEY, code, 0)
    ui.syn()
    time.sleep(0.05)


def hold(ui, code, seconds):
    ui.write(ecodes.EV_KEY, code, 1)
    ui.syn()
    end = time.time() + seconds
    while time.time() < end:          # emulate kernel auto-repeat
        time.sleep(0.025)
        ui.write(ecodes.EV_KEY, code, 2)
        ui.syn()
    ui.write(ecodes.EV_KEY, code, 0)
    ui.syn()


RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def main():
    if os.geteuid() != 0:
        print("root required (uinput/evdev)")
        return 2

    # Fakes first, daemon second: the startup scan finds them, no retry cycle needed.
    print("== creating fake PocketTerm35 keyboard/mouse (uinput) ==")
    kbd, mou = make_fake_devices()
    time.sleep(1.5)

    print("== starting daemon (mouse mode, matching only the fake device) ==")
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        [sys.executable, DAEMON, "--start-mode", "mouse",
         "--config", "/nonexistent",
         "--device-name", "PT35TEST",
         "--name", UI_NAME,
         # Use a private socket; never touch the real service's /run/pt35-buttonmap.sock
         "--socket", "/tmp/pt35-test/socket",
         "--state", "/tmp/pt35-test/mode"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, text=True)
    daemon_log: list[str] = []

    def pump_daemon_log():
        """Drain the daemon's stdout into a buffer so a full pipe cannot block it."""
        import select as _select
        while True:
            r, _, _ = _select.select([proc.stdout], [], [], 0)
            if not r:
                return
            line = proc.stdout.readline()
            if not line:
                return
            daemon_log.append(line.rstrip())

    time.sleep(3.0)
    pump_daemon_log()

    if proc.poll() is not None:
        print("daemon exited, output:")
        print(proc.stdout.read())
        return 1

    virt = find_virtual()
    if virt is None:
        check("virtual mouse device exists", False, "not found: " + UI_NAME)
        proc.terminate()
        return 1
    pump_daemon_log()
    alive = proc.poll() is None
    check("daemon still running", alive, f"exit={proc.poll()}")
    check("virtual mouse device exists", True, virt.path)
    for line in daemon_log[-6:]:
        print("      [daemon]", line)

    print("== injecting key presses ==")

    # 1) D-pad -> pointer movement
    before = drain(virt, 0.2)
    pump_daemon_log()
    hold(kbd, ecodes.KEY_RIGHT, 0.35)
    evs = drain(virt, 0.4)
    pump_daemon_log()
    relx = sum(v for t, c, v, _ in evs if t == ecodes.EV_REL and c == ecodes.REL_X)
    check("D-pad right -> positive REL_X movement", relx > 0, f"REL_X total = {relx}")
    keyup = [c for t, c, v, _ in evs if t == ecodes.EV_KEY and c == ecodes.KEY_RIGHT]
    check("mouse mode: D-pad emits no KEY_RIGHT", not keyup, f"KEY_RIGHT events = {len(keyup)}")

    drain(virt, 0.2)
    hold(kbd, ecodes.KEY_UP, 0.35)
    evs = drain(virt, 0.4)
    pump_daemon_log()
    rely = sum(v for t, c, v, _ in evs if t == ecodes.EV_REL and c == ecodes.REL_Y)
    check("D-pad up -> negative REL_Y movement", rely < 0, f"REL_Y total = {rely}")

    # 2) L / R: default l_r_scope="never", so **always letters**, never mouse keys.
    #    Settled after user feedback: panel keys share l/r codes; capturing breaks typing.
    for name, code in (("L", ecodes.KEY_L), ("R", ecodes.KEY_R)):
        drain(virt, 0.2)
        tap(kbd, code)
        evs = drain(virt, 0.4)
        pump_daemon_log()
        keys = [(c, v) for t, c, v, _ in evs if t == ecodes.EV_KEY]
        has_letter = any(c == code for c, v in keys)
        clicks = [c for c, v in keys if c in (ecodes.BTN_LEFT, ecodes.BTN_RIGHT)]
        check(f"{name} key -> letter {name} (not hijacked as a mouse button)",
              has_letter and not clicks,
              f"letter={has_letter} mouse_buttons={clicks}")

    # 3) Key regression: YXAB are **letter keys** too, default field_scope="never",
    #    so in mouse mode they must still emit letters and trigger no mouse action.
    #    (They used to be hijacked to left/right button and wheel, so a b x y were untypable.)
    for name, code in (("A", ecodes.KEY_A), ("B", ecodes.KEY_B),
                       ("X", ecodes.KEY_X), ("Y", ecodes.KEY_Y)):
        drain(virt, 0.2)
        tap(kbd, code)
        evs = drain(virt, 0.4)
        pump_daemon_log()
        keys = [(c, v) for t, c, v, _ in evs if t == ecodes.EV_KEY]
        clicks = [c for c, v in keys if c in (ecodes.BTN_LEFT, ecodes.BTN_RIGHT)]
        wheels = [v for t, c, v, _ in evs if t == ecodes.EV_REL and c == ecodes.REL_WHEEL]
        check(f"mouse mode: {name} emits a letter and no mouse action",
              any(c == code for c, v in keys) and not clicks and not wheels,
              f"letter={any(c == code for c, v in keys)} buttons={clicks} wheel={wheels}")

    # 4) Tap Backspace twice -> switch to buttons mode
    drain(virt, 0.2)
    tap(kbd, ecodes.KEY_BACKSPACE, 0.05)
    tap(kbd, ecodes.KEY_BACKSPACE, 0.05)
    time.sleep(1.5)
    pump_daemon_log()
    mode = open("/tmp/pt35-test/mode").read().strip()
    check("Backspace tapped twice -> switches to buttons", mode == "buttons", f"mode = {mode}")

    # 5) In buttons mode the D-pad should revert to ordinary arrow keys
    drain(virt, 0.5)
    pump_daemon_log()
    time.sleep(0.3)
    tap(kbd, ecodes.KEY_UP, hold=0.15)
    evs = drain(virt, 0.8)
    pump_daemon_log()
    print("      [debug] virtual device received after KEY_UP in buttons mode:",
          [(t, c, v) for t, c, v, _ in evs])
    has_up = any(t == ecodes.EV_KEY and c == ecodes.KEY_UP for t, c, v, _ in evs)
    rel = sum(1 for t, c, v, _ in evs if t == ecodes.EV_REL)
    check("buttons mode: D-pad restored to KEY_UP", has_up and rel == 0,
          f"KEY_UP={has_up} REL_events={rel}")

    # 6) Regression: in buttons mode L/R must stay plain letters, not mouse buttons.
    #    This is a bug a real user hit -- the panel L key shares the l keycode, so
    #    without mode awareness, typing could never produce l / r.
    drain(virt, 0.2)
    tap(kbd, ecodes.KEY_L)
    evs = drain(virt, 0.4)
    pump_daemon_log()
    leaked_click = any(t == ecodes.EV_KEY and c == ecodes.BTN_LEFT for t, c, v, _ in evs)
    has_l = any(t == ecodes.EV_KEY and c == ecodes.KEY_L for t, c, v, _ in evs)
    check("buttons mode: L emits letter KEY_L, not the left mouse button",
          has_l and not leaked_click,
          f"KEY_L={has_l} leaked_BTN_LEFT={leaked_click}")

    drain(virt, 0.2)
    tap(kbd, ecodes.KEY_R)
    evs = drain(virt, 0.4)
    pump_daemon_log()
    leaked_click = any(t == ecodes.EV_KEY and c == ecodes.BTN_RIGHT for t, c, v, _ in evs)
    has_r = any(t == ecodes.EV_KEY and c == ecodes.KEY_R for t, c, v, _ in evs)
    check("buttons mode: R emits letter KEY_R, not the right mouse button",
          has_r and not leaked_click,
          f"KEY_R={has_r} leaked_BTN_RIGHT={leaked_click}")

    # 6.5) Key regression: with l_r_scope="never", L/R must emit letters in **Buttons mode** too.
    #      Users reported exactly this bug: clicking a letter key hijacked l/r so they could not be typed.
    for name, code in (("L", ecodes.KEY_L), ("R", ecodes.KEY_R)):
        drain(virt, 0.3)
        pump_daemon_log()
        gestures.tap(kbd, code, hold=0.08)
        evs = drain(virt, 0.5)
        pump_daemon_log()
        keys = [(c, v) for t, c, v, _ in evs if t == ecodes.EV_KEY]
        has_letter = any(c == code for c, v in keys)
        clicks = [c for c, v in keys if c in (ecodes.BTN_LEFT, ecodes.BTN_RIGHT)]
        check(f"buttons mode: {name} also emits no mouse button", not clicks,
              f"letter={has_letter} leaked_mouse_buttons={clicks}")

    # 7) Back to mouse mode: confirm L/R are **still letters** there (the l_r_scope=never promise).
    tap(kbd, ecodes.KEY_BACKSPACE, 0.05)
    tap(kbd, ecodes.KEY_BACKSPACE, 0.05)
    time.sleep(1.5)
    pump_daemon_log()
    mode = open("/tmp/pt35-test/mode").read().strip()
    check("Backspace tapped twice again -> back to mouse", mode == "mouse", f"mode = {mode}")

    for name, code in (("L", ecodes.KEY_L), ("R", ecodes.KEY_R)):
        drain(virt, 0.4)
        pump_daemon_log()
        gestures.tap(kbd, code, hold=0.08)
        evs = drain(virt, 0.5)
        pump_daemon_log()
        keys = [(c, v) for t, c, v, _ in evs if t == ecodes.EV_KEY]
        has_letter = any(c == code for c, v in keys)
        clicks = [c for c, v in keys if c in (ecodes.BTN_LEFT, ecodes.BTN_RIGHT)]
        check(f"mouse mode: {name} emits a letter and no mouse button",
              has_letter and not clicks,
              f"letter={has_letter} leaked_mouse_buttons={clicks}")

    # 8) D-pad tap gestures: double-tap <- = left click, double-tap -> = right click
    #    The waits here must cover tap_window (0.40s), so each drain is a bit longer.
    drain(virt, 0.5)
    pump_daemon_log()
    gestures.multi_tap(kbd, ecodes.KEY_LEFT, 2)
    evs = drain(virt, 1.2)
    pump_daemon_log()
    s = gestures.summarize(evs)
    check("double-tap ← -> one left click",
          s["left_clicks"] == 1 and s["right_clicks"] == 0,
          f"left={s['left_clicks']} right={s['right_clicks']} rel=({s['rel_x']},{s['rel_y']})")

    drain(virt, 0.5)
    pump_daemon_log()
    gestures.multi_tap(kbd, ecodes.KEY_RIGHT, 2)
    evs = drain(virt, 1.2)
    pump_daemon_log()
    s = gestures.summarize(evs)
    check("double-tap → -> one right click",
          s["right_clicks"] == 1 and s["left_clicks"] == 0,
          f"left={s['left_clicks']} right={s['right_clicks']}")

    # 9) Single tap of <-: only double is configured for it, so no click should fire
    drain(virt, 0.5)
    pump_daemon_log()
    gestures.tap(kbd, ecodes.KEY_LEFT)
    evs = drain(virt, 1.2)
    pump_daemon_log()
    s = gestures.summarize(evs)
    check("single-tap ← -> no click (only double is configured)",
          s["left_clicks"] == 0 and s["right_clicks"] == 0,
          f"left={s['left_clicks']} right={s['right_clicks']}")

    # 10) Holding the down key: should move the pointer rather than fire a gesture
    drain(virt, 0.5)
    pump_daemon_log()
    gestures.press_hold_release(kbd, ecodes.KEY_DOWN, 0.6)
    evs = drain(virt, 0.8)
    pump_daemon_log()
    s = gestures.summarize(evs)
    check("hold ↓ -> moves the pointer, no clicks",
          s["rel_y"] > 0 and s["left_clicks"] == 0,
          f"REL_Y={s['rel_y']} left={s['left_clicks']}")

    # 10.6) Converse guarantee: a hold that **moved the pointer** is never a tap
    drain(virt, 0.5)
    pump_daemon_log()
    gestures.press_hold_release(kbd, ecodes.KEY_UP, 0.6)
    evs = drain(virt, 0.8)
    pump_daemon_log()
    s = gestures.summarize(evs)
    check("hold that moved the pointer -> no clicks at all",
          s["right_clicks"] == 0 and s["left_clicks"] == 0 and s["rel_y"] < 0,
          f"right={s['right_clicks']} left={s['left_clicks']} REL_Y={s['rel_y']}")

    # 11) The key with no gesture (↓) should still move the pointer as usual
    drain(virt, 0.5)
    pump_daemon_log()
    gestures.tap(kbd, ecodes.KEY_DOWN, hold=0.30)   # ↓ has no gesture, so it moves
    evs = drain(virt, 0.6)
    pump_daemon_log()
    s = gestures.summarize(evs)
    check("↓ with no gesture still moves the pointer", s["rel_y"] > 0,
          f"REL_Y={s['rel_y']}")

    # 12) Lenient rule (kept last, to avoid auto-repeat residue from the "hold" steps):
    #     a multi-tap that is slow but **never moved the pointer** still counts as taps.
    #     This improves on "triple taps are naturally hard to press fast": instead of
    #     raising tap_max (which would delay pointer movement), intent comes from motion.
    drain(virt, 1.0)
    pump_daemon_log()
    # The daemon must fully settle the previous round (including the hold test's
    # gesture window), or leftover pending sequences would corrupt this multi-tap state.
    time.sleep(1.5)
    drain(virt, 0.5)
    pump_daemon_log()
    gestures.multi_slow_tap(kbd, ecodes.KEY_RIGHT, 2, hold=0.35)
    evs = drain(virt, 2.0)
    pump_daemon_log()
    s = gestures.summarize(evs)
    check("slow double-tap (pointer never moved) still fires a right click",
          s["right_clicks"] == 1 and s["rel_x"] == 0 and s["rel_y"] == 0,
          f"right={s['right_clicks']} rel=({s['rel_x']},{s['rel_y']})")

    print("== daemon log (tail) ==")
    pump_daemon_log()
    for line in daemon_log[-25:]:
        print("   ", line)

    print("== cleanup ==")
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
    kbd.close()
    mou.close()

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\nResult: {passed}/{len(RESULTS)} passed")
    fails = [n for n, ok, _ in RESULTS if not ok]
    if fails:
        print("failed: " + ", ".join(fails))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
