#!/usr/bin/env python3
"""Render the HUD mid-task into assets/screenshot.png, without a monitor or a
running Claude Code session.

It feeds hud.py a scripted turn through a private state directory, steps the
simulation, and paints one frame into an image. The window is never shown, and no OpenGL
context is needed: hud.py's QPainter is pointed at a QImage instead.

Run: python3 tools/screenshot.py [WIDTHxHEIGHT] [OUT.png]
"""
import os
import random
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
size = sys.argv[1] if len(sys.argv) > 1 else "1920x1080"
out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "assets", "screenshot.png")
W, H = map(int, size.lower().split("x"))

# a private state dir, so a real HUD or session on this machine is untouched
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="neural-hud-shot-")
sys.path.insert(0, ROOT)
import hud  # noqa: E402  (reads XDG_RUNTIME_DIR at import)

os.environ["QT_QPA_PLATFORM"] = "offscreen"  # hud.py asks for xcb; we never show a window
from PyQt6.QtGui import QImage, QPainter  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

STEPS = [
    ("LINK", "directive received"),
    ("READ", "README.md"),
    ("SEARCH", "draw_status"),
    ("READ", "hud.py"),
    ("EXEC", "Survey sibling repos and their layout"),
    ("AGENT", "Explore repo conventions"),
    ("READ", "tools/gen_art.py"),
    ("WRITE", "hud.py"),
    ("SEARCH", "def typed"),
    ("NET", "doc.qt.io"),
    ("WRITE", "tools/screenshot.py"),
    ("MCP", "ide › getDiagnostics"),
    ("READ", "install.sh"),
    ("WRITE", "Makefile"),
    ("EXEC", "Run the hook tests"),
    ("WRITE", "README.md"),
    ("EXEC", "Render the HUD offscreen into a screenshot"),
]


def main():
    random.seed(7)
    now = time.time()
    start = now - 204
    os.makedirs(hud.STATE_DIR, exist_ok=True)
    with open(hud.TURN, "w") as f:
        f.write(str(start))
    with open(os.path.join(hud.STATE_DIR, "s-demo"), "w") as f:
        f.write(f"working {now}")
    import json
    with open(hud.EVENTS, "w") as f:
        for i, (kind, detail) in enumerate(STEPS):
            ts = start + 2 + i * 198 / len(STEPS) + random.uniform(-3, 3)
            f.write(json.dumps({"ts": ts, "kind": kind, "detail": detail}) + "\n")

    app = QApplication(sys.argv)
    w = hud.HUD((0, 0, W, H))
    w.timer.stop()
    w.started = now - 10
    w.on_event(w.events[-1])
    for _ in range(int(4 * hud.FPS)):  # let signals spread through the net
        w.simulate(time.time(), 1 / hud.FPS)
    # catch the status line part-way through typing, cursor showing
    w.type_key = None

    img = QImage(W, H, QImage.Format.Format_RGB32)

    class ImagePainter(QPainter):  # paintEvent does QPainter(self): draw into img instead
        def __init__(self, target):
            super().__init__(img if target is w else target)

    hud.QPainter = ImagePainter
    real_time = time.time
    t_shot = real_time()
    w.paintEvent(None)  # first paint starts the typing clock at t_shot
    hud.time.time = lambda: t_shot + 0.75  # ...then look again 0.75 s in
    w.paintEvent(None)
    hud.time.time = real_time
    if not img.save(out):
        sys.exit(f"could not write {out}")
    print(f"wrote {out} ({W}x{H})")


if __name__ == "__main__":
    main()
