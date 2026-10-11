#!/usr/bin/env python3
"""D-pad tap gesture reliability check: run the gesture N times, assert exactly N clicks.

Why it is needed
----------------
`test_smoke.py` covers "does it work"; this script covers "**is it reliable**".
It repeats gestures many times and compares **exact counts** (not just "fired at
least once"), which catches dropped events, leftover state, and premature settlement.

Design notes (each one learned the hard way)
--------------------------------------------
1. **Spawns its own dedicated daemon**, matching only this script's fake device with
   `--device-name`. Don't attach to the real service: its gesture bindings (e.g. down
   bound only to double) would distort the assertions and steal real keyboard input.
2. **`--config` points at a config this script generates**, so gesture bindings match
   the assertions one to one, unaffected by the user's actual configuration.
3. **Do not enable `--debug`**: a log line per key fills the Popen pipe, so logs read
   a full step behind and a probing problem is misread as a gesture failure (real bug).
4. Leave enough `tap_window` between rounds for the daemon to settle the previous one.

    sudo python3 tests/verify_live_gestures.py
"""
import os
import select
import subprocess
import sys
import tempfile
import time

from evdev import UInput, InputDevice, ecodes, list_devices

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gestures  # noqa: E402

VID, PID = 0x1209, 0x0001
UI_NAME = "PT35VERIFY ButtonMap"
DEV_FILTER = "PT35VERIFY"
N = 5

DAEMON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "pt35-buttonmap.py")

# Gesture bindings mirror the assertions (factory default): double-tap ← = left, → = right.
VERIFY_CONFIG = """\
[device]
vid = 0x1209
pid = 0x0001

[pointer]
speed = 6
fast_speed = 16
accel_after = 0.45

[gestures]
tap_max    = 0.28
tap_window = 0.40
left  = { double = "btn:left" }
right = { double = "btn:right" }
down  = {}
up    = {}

[toggle]
mode = "off"
"""


def find_virtual():
    for p in sorted(list_devices()):
        try:
            d = InputDevice(p)
        except Exception:
            continue
        if d.name == UI_NAME:
            return d
        d.close()
    return None


def drain(dev, seconds):
    out = []
    end = time.time() + seconds
    while True:
        remain = end - time.time()
        if remain <= 0:
            break
        r, _, _ = select.select([dev.fileno()], [], [], remain)
        if not r:
            break
        try:
            for ev in dev.read():
                out.append((ev.type, ev.code, ev.value))
        except OSError:
            break
    return out


def main():
    if os.geteuid() != 0:
        print("root required (read /dev/input, write /dev/uinput)")
        return 2

    tmp = tempfile.mkdtemp(prefix="pt35-verify-")
    conf = os.path.join(tmp, "config.toml")
    with open(conf, "w", encoding="utf-8") as fh:
        fh.write(VERIFY_CONFIG)

    fake = UInput(
        {ecodes.EV_KEY: [ecodes.KEY_UP, ecodes.KEY_DOWN, ecodes.KEY_LEFT, ecodes.KEY_RIGHT]},
        name=f"{DEV_FILTER} Pico Keyboard", vendor=VID, product=PID, version=0x111)
    time.sleep(1.2)

    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        [sys.executable, DAEMON, "--start-mode", "mouse", "--config", conf,
         "--device-name", DEV_FILTER, "--name", UI_NAME,
         "--socket", os.path.join(tmp, "socket"), "--state", os.path.join(tmp, "mode")],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)

    def flush_daemon():
        """Drain without keeping anything: stops a full pipe from blocking the daemon."""
        while True:
            r, _, _ = select.select([proc.stdout], [], [], 0)
            if not r:
                return
            if not proc.stdout.readline():
                return

    time.sleep(3.0)
    flush_daemon()
    if proc.poll() is not None:
        print("daemon exited on startup, output:")
        print(proc.stdout.read())
        fake.close()
        return 1

    virt = find_virtual()
    if virt is None:
        print(f"❌ virtual device {UI_NAME!r} not found")
        proc.terminate()
        fake.close()
        return 1
    print(f"dedicated daemon pid={proc.pid}, virtual device {virt.path}")

    results = []

    def burst(label, click_code, action):
        print(f"\n--- {label}: {N} in a row (expect exactly {N}) ---")
        drain(virt, 1.2)                     # let the previous gesture window settle
        time.sleep(0.4)
        for _ in range(N):
            action()
            time.sleep(0.6)
        evs = drain(virt, 2.0)
        seq = [v for t, c, v in evs if t == ecodes.EV_KEY and c == click_code]
        print(f"    sequence = {seq}")
        results.append((f"{label}: {N} runs => exactly {N} clicks", seq == [1, 0] * N))
        flush_daemon()

    burst("double-tap ← (fast)", ecodes.BTN_LEFT,
          lambda: gestures.multi_tap(fake, ecodes.KEY_LEFT, 2))
    burst("double-tap → (fast)", ecodes.BTN_RIGHT,
          lambda: gestures.multi_tap(fake, ecodes.KEY_RIGHT, 2))
    # Lenient rule: a slow multi-tap that **never moved the pointer** still counts as taps
    burst("double-tap → (slow 0.35s)", ecodes.BTN_RIGHT,
          lambda: gestures.multi_slow_tap(fake, ecodes.KEY_RIGHT, 2, hold=0.35))

    # Converse guarantee: a hold that moved the pointer is never a tap
    print("\n--- hold ↓ 0.6s x 3 (expect: movement, zero clicks; ↓ has no gesture) ---")
    drain(virt, 1.2)
    time.sleep(0.4)
    total_clicks = 0
    total_rel = 0
    for _ in range(3):
        gestures.press_hold_release(fake, ecodes.KEY_DOWN, 0.6)
        evs = drain(virt, 0.8)
        total_rel += sum(1 for t, c, v in evs if t == ecodes.EV_REL)
        total_clicks += sum(1 for t, c, v in evs
                            if t == ecodes.EV_KEY and c in (ecodes.BTN_LEFT, ecodes.BTN_RIGHT))
    print(f"    motion events={total_rel} click events={total_clicks}")
    results.append(("hold moved the pointer => zero clicks", total_clicks == 0 and total_rel > 0))
    flush_daemon()

    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
    fake.close()
    virt.close()

    print("\n==== results ====")
    ok = 0
    for name, passed in results:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        ok += bool(passed)
    print(f"  {ok}/{len(results)} passed")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
