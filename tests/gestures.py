"""D-pad tap gesture self-test: imported by test_smoke.py, also runs standalone.

It is a separate file because gestures involve real timing (tap / hold / multi-tap
window); fixed sleeps jitter across machines, so timing lives here with margin.
"""
import time

from evdev import ecodes

# Keep in sync with pt35-buttonmap.py defaults; tests pass config explicitly to match
TAP_MAX = 0.28
TAP_WINDOW = 0.40


def tap(ui, code, hold=0.06):
    """Press briefly and release = one tap."""
    ui.write(ecodes.EV_KEY, code, 1)
    ui.syn()
    time.sleep(hold)
    ui.write(ecodes.EV_KEY, code, 0)
    ui.syn()


def multi_tap(ui, code, n, gap=0.08):
    """Tap n times, pausing gap between taps (far below tap_window)."""
    for i in range(n):
        tap(ui, code)
        if i != n - 1:
            time.sleep(gap)


def press_hold_release(ui, code, hold):
    """Hold for hold seconds, then release; auto-repeat events fire meanwhile (kernel)."""
    ui.write(ecodes.EV_KEY, code, 1)
    ui.syn()
    end = time.time() + hold
    while time.time() < end:
        time.sleep(0.025)
        ui.write(ecodes.EV_KEY, code, 2)
        ui.syn()
    ui.write(ecodes.EV_KEY, code, 0)
    ui.syn()


def summarize(events):
    """Compress the event list into assertion-friendly statistics."""
    keys = [(c, v) for t, c, v, _ in events if t == ecodes.EV_KEY]
    rel_x = sum(v for t, c, v, _ in events if t == ecodes.EV_REL and c == ecodes.REL_X)
    rel_y = sum(v for t, c, v, _ in events if t == ecodes.EV_REL and c == ecodes.REL_Y)
    clicks = {}
    for c, v in keys:
        if c in (ecodes.BTN_LEFT, ecodes.BTN_RIGHT) and v == 1:
            clicks[c] = clicks.get(c, 0) + 1
    return {
        "left_clicks": clicks.get(ecodes.BTN_LEFT, 0),
        "right_clicks": clicks.get(ecodes.BTN_RIGHT, 0),
        "rel_x": rel_x,
        "rel_y": rel_y,
        "key_events": keys,
    }


def slow_tap(ui, code, hold=0.35):
    """"Slightly slow" tap: held longer than tap_max but **emits no auto-repeat events**
    (as with a real quick tap -- released before the kernel repeat delay kicks in).
    """
    ui.write(ecodes.EV_KEY, code, 1)
    ui.syn()
    time.sleep(hold)
    ui.write(ecodes.EV_KEY, code, 0)
    ui.syn()
    time.sleep(0.05)


def multi_slow_tap(ui, code, n, hold=0.35, gap=0.08):
    for i in range(n):
        slow_tap(ui, code, hold)
        if i != n - 1:
            time.sleep(gap)
