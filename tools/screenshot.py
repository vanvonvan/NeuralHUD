#!/usr/bin/env python3
"""Render the HUD mid-task into assets/screenshot.png, without a monitor or a
running Claude Code session.

It feeds hud.py a scripted turn through a private state directory, steps the
simulation, and paints one frame into an image. The window is never shown, and no OpenGL
context is needed: hud.py's QPainter is pointed at a QImage instead.

Run: python3 tools/screenshot.py [WIDTHxHEIGHT] [OUT.png] [--waiting]

--waiting renders the standby screen (Claude blocked on a permission prompt)
instead of the HUD mid-task.
"""
import json
import os
import random
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WAITING = "--waiting" in sys.argv
args = [a for a in sys.argv[1:] if a != "--waiting"]
size = args[0] if args else "1920x1080"
out = args[1] if len(args) > 1 else os.path.join(ROOT, "assets", "screenshot.png")
W, H = map(int, size.lower().split("x"))

# a private state dir, so a real HUD or session on this machine is untouched
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="neural-hud-shot-")
sys.path.insert(0, ROOT)
import hud  # noqa: E402  (reads XDG_RUNTIME_DIR at import)

os.environ["QT_QPA_PLATFORM"] = "offscreen"  # hud.py asks for xcb; we never show a window
from PyQt6.QtGui import QImage, QPainter  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

# (kind, detail, tool, seconds it took; None = still running)
STEPS = [
    ("LINK", "directive received", None, 0),
    ("PLAN", "plan updated", "TodoWrite", 0.1),
    ("READ", "hud.py", "Read", 0.1),
    ("SEARCH", "def draw_status", "Grep", 0.2),
    ("READ", "hook.py", "Read", 0.1),
    ("AGENT", "Map the hook payloads", "Agent", 38.4),
    ("WRITE", "hook.py", "Edit", 0.3),
    ("EXEC", "Compile the hook", "Bash", 1.2),
    ("WRITE", "hud.py", "Edit", 0.4),
    ("NET", "doc.qt.io", "WebFetch", 3.1),
    ("WRITE", "hud.py", "Edit", 0.3),
    ("MCP", "ide › getDiagnostics", "mcp__ide__getDiagnostics", 2.4),
    ("WRITE", "tests/test_hook.py", "Write", 0.2),
    ("EXEC", "Build the release bundle", "Bash", 12.7),
    ("READ", "README.md", "Read", 0.1),
    ("EXEC", "Run the full test suite", "Bash", None),
]
PLAN = [
    ("Record why Claude is waiting", "Recording why Claude is waiting", "completed"),
    ("Pair each step with its END", "Pairing each step with its END", "completed"),
    ("Draw the plan checklist", "Drawing the plan checklist", "completed"),
    ("Run the full test suite", "Running the full test suite", "in_progress"),
    ("Render new screenshots", "Rendering new screenshots", "pending"),
    ("Update the README", "Updating the README", "pending"),
]
REASON = {"kind": "PERMISSION", "detail": "EXEC · Force-push rewritten history to GitHub"}


def main():
    random.seed(7)
    now = time.time()
    start = now - 204
    d = hud.STATE_DIR
    os.makedirs(d, exist_ok=True)
    with open(hud.TURN, "w") as f:
        f.write(str(start))
    with open(os.path.join(d, "s-demo"), "w") as f:
        f.write(f"{'waiting' if WAITING else 'working'} {now}")
    with open(os.path.join(d, "w-demo"), "w") as f:
        json.dump(dict(REASON, ts=now - 17), f)
    with open(os.path.join(d, "plan-demo.json"), "w") as f:
        json.dump({"ts": now - 30, "items": [{"id": str(i + 1), "text": t, "active": a, "status": s}
                                              for i, (t, a, s) in enumerate(PLAN)]}, f)
    with open(hud.EVENTS, "w") as f:
        for i, (kind, detail, tool, took) in enumerate(STEPS):
            ts = start + 2 + i * 160 / len(STEPS) + random.uniform(-2, 2)
            if took is None:  # the running step: started 43 s before the shot
                ts = now - 43 - (17 if WAITING else 0)
            ev = {"ts": ts, "kind": kind, "detail": detail}
            if tool:
                ev.update(id=f"t{i}", tool=tool)
            f.write(json.dumps(ev) + "\n")
            if tool and took is not None:
                f.write(json.dumps({"ts": ts + took, "kind": "END", "id": f"t{i}", "tool": tool}) + "\n")

    app = QApplication(sys.argv)  # noqa: F841
    w = hud.HUD((0, 0, W, H))
    w.timer.stop()
    w.started = now - 10
    states = hud.read_states()
    w.plan = hud.read_plan(states, w.turn_start)
    w.on_event(w.events[-1])
    if WAITING:
        w.reason = hud.read_reason(states)
        w.begin_wait(now - 17)
    for _ in range(int(4 * hud.FPS)):  # let signals spread through the net
        w.simulate(time.time(), 1 / hud.FPS)
    w.typers = {}

    img = QImage(W, H, QImage.Format.Format_RGB32)

    class ImagePainter(QPainter):  # paintEvent does QPainter(self): draw into img instead
        def __init__(self, target):
            super().__init__(img if target is w else target)

    hud.QPainter = ImagePainter
    real_time = time.time
    t0 = real_time()
    w.paintEvent(None)  # the first paint starts the typing clocks at t0
    # look again once the lines are typed, at a moment the blinking cursor is lit
    t_shot = next(t0 + 1.7 + k * 0.05 for k in range(40) if int((t0 + 1.7 + k * 0.05) * 2.2) % 2)
    hud.time.time = lambda: t_shot
    w.paintEvent(None)
    hud.time.time = real_time
    if not img.save(out):
        sys.exit(f"could not write {out}")
    print(f"wrote {out} ({W}x{H})")


if __name__ == "__main__":
    main()
