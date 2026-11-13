#!/usr/bin/env python3
"""Render the HUD mid-task into assets/screenshot.png, without a monitor or a
running Claude Code session.

It feeds hud.py a scripted turn through a private state directory, steps the
simulation, and paints one frame into an image. The window is never shown, and no OpenGL
context is needed: hud.py's QPainter is pointed at a QImage instead.

Run: python3 tools/screenshot.py [WIDTHxHEIGHT] [OUT.png] [--waiting | --summary | --fault] [--half]

--waiting  the standby screen (Claude blocked on a permission prompt): the banner strip,
           or the whole screen with NEURAL_HUD_STANDBY=full
--summary  the end-of-task summary
--fault    the glitch a failed step causes
--half     save at half size (rendered full size, then scaled smoothly, so text
           keeps its proportions: the HUD's fonts have a minimum size)
The theme comes from NEURAL_HUD_THEME, as on the real HUD.
"""
import json
import os
import random
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MODE = next((a[2:] for a in sys.argv[1:] if a in ("--waiting", "--summary", "--fault")), "working")
WAITING = MODE == "waiting"
args = [a for a in sys.argv[1:] if not a.startswith("--")]
size = args[0] if args else "1920x1080"
out = args[1] if len(args) > 1 else os.path.join(ROOT, "assets", "screenshot.png")
W, H = map(int, size.lower().split("x"))

# a private state dir, so a real HUD or session on this machine is untouched
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="neural-hud-shot-")
sys.path.insert(0, ROOT)
import hud  # noqa: E402  (reads XDG_RUNTIME_DIR at import)

os.environ["QT_QPA_PLATFORM"] = "offscreen"  # hud.py asks for xcb; we never show a window
from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtGui import QImage, QPainter  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

# (session, kind, detail, tool, seconds it took or None = still running, lines added, removed)
STEPS = [
    ("a", "LINK", "directive received", None, 0, 0, 0),
    ("a", "PLAN", "Recording why Claude is waiting", "TodoWrite", 0.1, 0, 0),
    ("a", "READ", "hud.py", "Read", 0.1, 0, 0),
    ("a", "SEARCH", "def draw_status", "Grep", 0.2, 0, 0),
    ("b", "LINK", "directive received", None, 0, 0, 0),
    ("a", "WRITE", "hook.py", "Edit", 0.3, 64, 9),
    ("b", "READ", "limits.py", "Read", 0.1, 0, 0),
    ("a", "EXEC", "Compile the hook", "Bash", 1.2, 0, 0),
    ("a", "WRITE", "hud.py", "Edit", 0.4, 112, 21),
    ("b", "WRITE", "limits.py", "Edit", 0.3, 18, 4),
    ("a", "NET", "doc.qt.io", "WebFetch", 3.1, 0, 0),
    ("a", "EXEC", "Run the full test suite", "Bash", 9.6, 0, 0),
    ("a", "FAULT", "EXEC failed: Exit code 1: 2 tests failed", None, 0, 0, 0),
    ("a", "WRITE", "tests/test_hook.py", "Write", 0.2, 38, 0),
    ("b", "EXEC", "Run the rate limit tests", "Bash", 6.2, 0, 0),
    ("a", "AGENT", "Map the hook payloads", "Agent", None, 0, 0),
    ("a", "EXEC", "Run the full test suite", "Bash", None, 0, 0),
]
SESSIONS = {"a": ("neural-hud", "main", 128_400), "b": ("orbital-api", "feature/rate-limits", 41_000)}
PLAN = [
    ("Record why Claude is waiting", "Recording why Claude is waiting", "completed"),
    ("Pair each step with its END", "Pairing each step with its END", "completed"),
    ("Draw the plan checklist", "Drawing the plan checklist", "completed"),
    ("Run the full test suite", "Running the full test suite", "in_progress"),
    ("Render new screenshots", "Rendering new screenshots", "pending"),
    ("Update the README", "Updating the README", "pending"),
]
REASON = {"kind": "PERMISSION", "detail": "EXEC · Force-push rewritten history to GitHub"}


def stage(now, start):
    """Write the state files a real pair of sessions would have produced."""
    d = hud.STATE_DIR
    os.makedirs(d, exist_ok=True)
    tmp = os.path.dirname(d)
    with open(hud.TURN, "w") as f:
        f.write(str(start))
    for sid, (proj, branch, tokens) in SESSIONS.items():
        cwd = os.path.join(tmp, proj)
        os.makedirs(os.path.join(cwd, ".git"), exist_ok=True)
        with open(os.path.join(cwd, ".git", "HEAD"), "w") as f:
            f.write(f"ref: refs/heads/{branch}\n")
        transcript = os.path.join(tmp, f"{sid}.jsonl")
        with open(transcript, "w") as f:
            f.write(json.dumps({"type": "assistant", "message": {"model": "claude", "usage": {
                "input_tokens": 4, "cache_read_input_tokens": tokens, "output_tokens": 900}}}) + "\n")
        with open(os.path.join(d, f"m-{sid}.json"), "w") as f:
            json.dump({"cwd": cwd, "transcript": transcript}, f)
        with open(os.path.join(d, f"turn-{sid}"), "w") as f:
            f.write(str(start))
        state = "waiting" if WAITING and sid == "a" else "working"
        with open(os.path.join(d, f"s-{sid}"), "w") as f:
            f.write(f"{state} {now}")
    with open(os.path.join(d, "w-a"), "w") as f:
        json.dump(dict(REASON, ts=now - 17), f)
    with open(os.path.join(d, "plan-a.json"), "w") as f:
        json.dump({"ts": now - 30, "items": [{"id": str(i + 1), "text": t, "active": a, "status": s}
                                              for i, (t, a, s) in enumerate(PLAN)]}, f)
    with open(hud.EVENTS, "w") as f:
        for i, (sid, kind, detail, tool, took, add, rm) in enumerate(STEPS):
            ts = start + 2 + i * 160 / len(STEPS) + random.uniform(-2, 2)
            if took is None:  # still running: started a while before the shot
                ts = now - (61 if kind == "AGENT" else 43) - (17 if WAITING else 0)
            ev = {"ts": ts, "kind": kind, "detail": detail, "sid": sid}
            if tool:
                ev.update(id=f"t{i}", tool=tool)
            f.write(json.dumps(ev) + "\n")
            if tool and took is not None:
                end = {"ts": ts + took, "kind": "END", "id": f"t{i}", "tool": tool, "sid": sid}
                if add or rm:
                    end.update(add=add, rm=rm, file=detail)
                f.write(json.dumps(end) + "\n")


def main():
    random.seed(7)
    now = time.time()
    start = now - 204
    stage(now, start)

    app = QApplication(sys.argv)  # noqa: F841
    w = hud.HUD((0, 0, W, H))
    w.timer.stop()
    w.started = now - 10
    states = hud.read_states()
    w.poll_sessions(states)
    w.plan = hud.read_plan(states, w.turn_start)
    w.last_ctx = 0
    w.poll_context(now)
    w.on_event(w.events[-1])
    if WAITING:
        w.reason = hud.read_reason(states)
        w.begin_wait(now - 17)
        w.fit_window()
    for _ in range(int(4 * hud.FPS)):  # let signals spread through the net
        w.simulate(time.time(), 1 / hud.FPS)
    w.typers = {}

    img = QImage(W, w.banner_h() if w.banner else H, QImage.Format.Format_RGB32)

    class ImagePainter(QPainter):  # paintEvent does QPainter(self): draw into img instead
        def __init__(self, target):
            super().__init__(img if target is w else target)

    hud.QPainter = ImagePainter
    real_time = time.time
    t0 = real_time()
    w.paintEvent(None)  # the first paint starts the typing clocks at t0
    # look again once the lines are typed, at a moment the blinking cursor is lit
    t_shot = next(t0 + 1.7 + k * 0.05 for k in range(40) if int((t0 + 1.7 + k * 0.05) * 2.2) % 2)
    if MODE == "summary":
        for e in w.running:  # the turn ends: everything still running finishes now
            w.close_step(e, {"ts": t_shot - 1})
        w.running = []
        w.plan = [dict(i, status="completed") for i in w.plan]
        w.closing = t_shot - 2.2
        w.close_msg, w.close_color = "TASK COMPLETE", hud.GREEN
        w.summary = w.make_summary(t_shot - 2.2)
        w.crt_at = hud.SUMMARY_FOR
    if MODE == "fault":
        w.fault_at = t_shot - 0.12
        random.seed(3)
    hud.time.time = lambda: t_shot
    w.paintEvent(None)
    hud.time.time = real_time
    if "--half" in sys.argv:
        img = img.scaled(W // 2, img.height() // 2, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    if not img.save(out):
        sys.exit(f"could not write {out}")
    print(f"wrote {out} ({W}x{img.height()}, {MODE}, theme {hud.THEME})")


if __name__ == "__main__":
    main()
