# Tests

Two scripts, covering two different questions:

| Script | Question it answers |
|---|---|
| `test_smoke.py` | **Does it behave correctly?** — 27 functional assertions |
| `verify_live_gestures.py` | **Is it stable?** — 4 reliability assertions |

```bash
sudo python3 tests/test_smoke.py
sudo python3 tests/verify_live_gestures.py
```

Both need root (they read `/dev/input/*` and write `/dev/uinput`) plus
`python3-evdev`.

---

## Why the logic can be tested without anyone pressing buttons

The daemon locates its devices by **VID:PID (`1209:0001`) plus a device-name
substring**. The tests exploit that: they use uinput to **forge a keyboard/mouse
pair carrying the same VID:PID** (with `PT35TEST` in the name), and pass
`--device-name PT35TEST` so the daemon under test only accepts the forged pair.

The result is that keys can be injected precisely and the virtual device's output
asserted — **the whole logic is reproducible deterministically, with no real
hardware and no human input.**

---

## `test_smoke.py` — 27 functional assertions

| Group | Asserted |
|---|---|
| Devices | daemon alive; virtual device created |
| Pointer | → `REL_X > 0`; ↑ `REL_Y < 0`; **no `KEY_RIGHT` leaks in Mouse mode** |
| Letter keys | in **Buttons mode**: `L`/`R` emit `KEY_L`/`KEY_R` and do **not** fire mouse buttons |
| Letter keys | in **Mouse mode**: `L`/`R` emit letters; and `A`/`B`/`X`/`Y` emit letters and fire **no** mouse button or wheel |
| Mode switch | Backspace twice → `buttons`; Backspace twice again → `mouse` |
| Buttons mode | arrow keys revert to `KEY_UP` with no `REL` events |
| Gestures | ← double → one left click; → double → one right click; ← single → **nothing**; both with zero pointer drift |
| Hold vs tap | holding a direction moves the pointer and fires no click; holding **and having moved** fires nothing |
| Slow taps | a slow double-tap (0.35 s hold, no pointer motion) still fires exactly one right click |
| Unbound direction | ↓ has no gesture, so it still moves the pointer |

## `verify_live_gestures.py` — 4 reliability assertions

Performs **N gestures in a row** and asserts the click count is **exactly N**
(not "at least once"). That is what catches dropped events, state leakage, and
multi-tap chains being settled early:

```
tap ← twice, fast      x5  ->  [1,0] x5
tap → twice, fast      x5  ->  [1,0] x5
tap → twice, slow 0.35s x5 ->  [1,0] x5
hold ↓ 0.6 s           x3  ->  pointer motion, 0 clicks
```

It starts **its own private daemon with its own config and a unique device name**.
This matters: an earlier version of this script talked to the *running* service,
whose real configuration and live input made the verdict meaningless (it once
reported a false pass).

---

## Four traps hit while writing these tests

1. **Create the forged devices *before* starting the daemon.** Otherwise the
   daemon's first scan misses them and it waits out its 5-second retry period, so
   every assertion runs too early and fails.

2. **Use a unique device name plus `--name`.** Otherwise the test binds to a stale
   virtual device left over from a previous run (it lingers in `/dev/input/` for a
   moment), reads a dead device, and every assertion fails while the daemon is
   actually fine.

3. **Do not set `O_NONBLOCK` on a uinput client's fd** — events already delivered
   become unreadable. Use `select()` for the timeout and keep the fd blocking; see
   `drain()`.

4. **Drain the daemon's stdout** (`pump_daemon_log()`). It runs under
   `stdout=PIPE` with `--debug`, and a full pipe blocks it outright — which shows
   up as a mysterious "gesture failed" that is really just a stalled pipe.
