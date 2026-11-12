# Made by Claude
"""Sci-fi "neural link" HUD on a side monitor while Claude Code is working.

Opens when a session starts working and closes with a "TASK COMPLETE" outro when
no session is working any more. While Claude waits for an approval or an answer
it stays up on an amber "AWAITING OPERATOR" standby screen that says why. The
window never takes focus and lets every click through, so the other displays
keep working normally.

Fed by hook.py through files in $XDG_RUNTIME_DIR/claude-neural/:
  s-<session>          "working|waiting|done <ts>"
  w-<session>          why it is waiting: {"ts", "kind", "detail"}
  plan-<session>.json  the session's plan: {"ts", "items": [{"id", "text", "active", "status"}]}
  events.jsonl         one line per tool call: {"ts", "kind", "detail", "id", "tool"},
                       and an "END" line with the same id when the call finishes
  turn_start           timestamp of the last prompt
"""
import fcntl
import json
import math
import os
import random
import re
import subprocess
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "xcb"          # Wayland clients can't place windows
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"  # work in real X pixels

from PyQt6.QtCore import QLineF, QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QPainter, QPen,
                         QPixmap, QRadialGradient, QPolygonF)
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtWidgets import QApplication

STATE_DIR = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "claude-neural")
LOCK = os.path.join(STATE_DIR, "daemon.lock")
EVENTS = os.path.join(STATE_DIR, "events.jsonl")
TURN = os.path.join(STATE_DIR, "turn_start")
STALE_AFTER = 20 * 60
FPS = 24

KIND_COLORS = {
    "READ": "#4fd8ff", "WRITE": "#ff5fd8", "EXEC": "#ffb347", "SEARCH": "#6dff9e",
    "AGENT": "#b18cff", "NET": "#e8f4ff", "MCP": "#ffe66d", "SKILL": "#ff8f6d",
    "TOOL": "#9fb4d8", "LINK": "#4fd8ff", "FAULT": "#ff4f5e", "DONE": "#7dffb0",
    "PLAN": "#7dffb0", "QUESTION": "#ffb347", "PERMISSION": "#ffb347",
}
SPINNER = "◐◓◑◒"
BAR_KINDS = ["READ", "WRITE", "EXEC", "SEARCH", "AGENT", "MCP", "NET", "SKILL"]
CYAN = QColor("#4fd8ff")
GREEN = QColor("#7dffb0")
AMBER = QColor("#ffb347")


def xrandr_monitors():
    """Connected monitors: [{"name", "primary", "geo": (x, y, w, h)}]."""
    out = subprocess.run(["xrandr"], capture_output=True, text=True).stdout
    mons = []
    for line in out.splitlines():
        m = re.match(r"(\S+) connected( primary)?.*?(\d+)x(\d+)\+(\d+)\+(\d+)", line)
        if m:
            w, h, x, y = map(int, m.groups()[2:])
            mons.append({"name": m.group(1), "primary": bool(m.group(2)), "geo": (x, y, w, h)})
    return mons


def edids(mons):
    """{xrandr name: raw EDID bytes}, mapping each DRM connector (HDMI-A-1) to its
    xrandr output (HDMI-1)."""
    found = {}
    for name in os.listdir("/sys/class/drm"):
        try:
            with open(f"/sys/class/drm/{name}/edid", "rb") as f:
                data = f.read()
        except OSError:
            continue
        m = re.match(r"([A-Za-z]+).*?(\d+)$", name.split("-", 1)[-1])
        if not data or not m:
            continue
        kind, num = m.groups()
        for mon in mons:
            if re.fullmatch(rf"{kind}\S*?-{num}", mon["name"]):
                found[mon["name"]] = data
    return found


def edid_label(data):
    """The model name an EDID advertises (e.g. "ARZOPA"), else its 3-letter maker code."""
    for o in (54, 72, 90, 108):
        if data[o:o + 3] == b"\0\0\0" and data[o + 3:o + 4] == b"\xfc":
            name = data[o + 5:o + 18].split(b"\n")[0].decode("ascii", "replace").strip()
            if name:
                return name
    if len(data) > 9:
        m = (data[8] << 8) | data[9]
        return "".join(chr(64 + ((m >> s) & 31)) for s in (10, 5, 0))
    return ""


def pick_monitor(mons, want):
    """The monitor NEURAL_HUD_MONITOR=want selects, or None."""
    if not want:
        side = [m for m in mons if not m["primary"]]
        return min(side, key=lambda m: m["geo"][2] * m["geo"][3]) if side else None
    for m in mons:
        if m["name"] == want:
            return m
    for name, data in edids(mons).items():  # else: text found in the monitor's EDID
        if want.upper().encode() in data.upper():
            return next(m for m in mons if m["name"] == name)
    return None


def find_monitor():
    """Return (x, y, w, h) of the monitor to draw on, or None.

    NEURAL_HUD_MONITOR picks it: an xrandr output name (e.g. HDMI-1) or text found
    in the monitor's EDID (e.g. its brand). Unset: the smallest non-primary monitor.
    """
    m = pick_monitor(xrandr_monitors(), os.environ.get("NEURAL_HUD_MONITOR", "").strip())
    return m["geo"] if m else None


def list_monitors(want=""):
    """Print the connected monitors and the MONITOR= values that select each one."""
    mons = xrandr_monitors()
    if not mons:
        print("no monitors found (is xrandr working? on Wayland it needs XWayland)")
        return
    labels = {n: edid_label(d) for n, d in edids(mons).items()}
    # only offer a model name that really selects its monitor (and no other one first)
    labels = {n: l for n, l in labels.items() if l and (pick_monitor(mons, l) or {}).get("name") == n}
    default = pick_monitor(mons, "")
    chosen = pick_monitor(mons, want) if want else None
    rows = []
    for m in mons:
        x, y, w, h = m["geo"]
        notes = (["primary"] if m["primary"] else []) + (["default"] if m is default else [])
        if m is chosen:
            notes.append(f"MONITOR={want}")
        label = labels.get(m["name"], "")
        rows.append((m["name"], label or "-", f"{w}x{h}+{x}+{y}", ", ".join(notes)))
    widths = [max(len(r[i]) for r in rows + [("OUTPUT", "MODEL", "GEOMETRY", "")]) for i in range(3)]
    print(f"{'OUTPUT':<{widths[0]}}  {'MODEL':<{widths[1]}}  GEOMETRY")
    for r in rows:
        print(f"{r[0]:<{widths[0]}}  {r[1]:<{widths[1]}}  {r[2]:<{widths[2]}}  {r[3]}".rstrip())
    print()
    print("Use the OUTPUT or the MODEL, e.g.:  make install MONITOR=" + (
        next((labels[m["name"]] for m in mons if not m["primary"] and " " not in labels.get(m["name"], " ")), "")
        or mons[-1]["name"]))
    print("Without MONITOR, the HUD goes to the one marked default"
          + (" (none: every monitor is primary)." if not default else "."))
    if want and not chosen:
        print(f"MONITOR={want} matches no connected monitor: the HUD would not open.")


def read_states():
    """{session id: "working" | "waiting" | "done"}. A session silent for STALE_AFTER is
    dropped, together with its waiting reason and plan."""
    now, states = time.time(), {}
    try:
        names = os.listdir(STATE_DIR)
    except OSError:
        return states
    for f in names:
        if not f.startswith("s-"):
            continue
        p, sid = os.path.join(STATE_DIR, f), f[2:]
        try:
            if now - os.path.getmtime(p) > STALE_AFTER:
                for leftover in (p, os.path.join(STATE_DIR, "w-" + sid), os.path.join(STATE_DIR, f"plan-{sid}.json")):
                    try:
                        os.remove(leftover)
                    except OSError:
                        pass
                continue
            with open(p) as fh:
                parts = fh.read().split()
            if parts:
                states[sid] = parts[0]
        except OSError:
            pass
    return states


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def read_reason(states):
    """(kind, detail) of the latest reason a waiting session gave, or None."""
    reasons = [read_json(os.path.join(STATE_DIR, "w-" + sid)) for sid, st in states.items() if st == "waiting"]
    reasons = [r for r in reasons if isinstance(r, dict) and r.get("detail")]
    if not reasons:
        return None
    r = max(reasons, key=lambda r: r.get("ts", 0))
    return r.get("kind", "PERMISSION"), r["detail"]


def read_plan(states, turn_start):
    """Items of the plan to show: the most recently updated one that is still open, or
    that changed during this turn. A plan finished in an earlier turn is not shown."""
    best = None
    for sid in states:
        plan = read_json(os.path.join(STATE_DIR, f"plan-{sid}.json"))
        if not isinstance(plan, dict) or not plan.get("items"):
            continue
        items = [i for i in plan["items"] if isinstance(i, dict) and (i.get("text") or i.get("active"))]
        live = any(i.get("status") != "completed" for i in items) or plan.get("ts", 0) >= turn_start - 1
        if items and live and (best is None or plan.get("ts", 0) > best[0]):
            best = (plan.get("ts", 0), items)
    return best[1] if best else None


def read_turn_start():
    try:
        with open(TURN) as f:
            return float(f.read().strip())
    except (OSError, ValueError):
        return time.time()


DISMISSED = os.path.join(STATE_DIR, "dismissed")


def dismissed_since(ts):
    """True when the close shortcut was pressed after the prompt that started this turn."""
    try:
        return os.path.getmtime(DISMISSED) >= ts
    except OSError:
        return False


def ease(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def fmt_dur(sec, live=False):
    """4.2s / 2m05s for a finished step; 00:42 for one still running."""
    sec = max(0.0, sec)
    if live:
        return f"{int(sec) // 60:02d}:{int(sec) % 60:02d}"
    return f"{sec:.1f}s" if sec < 60 else f"{int(sec) // 60}m{int(sec) % 60:02d}s"


def with_alpha(c, a):
    c = QColor(c)
    c.setAlpha(max(0, min(255, int(a))))
    return c


class HUD(QOpenGLWidget):
    def __init__(self, geo):
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.X11BypassWindowManagerHint | Qt.WindowType.WindowTransparentForInput
                 | Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.Tool)
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        x, y, w, h = geo
        self.W, self.H = w, h
        self.setGeometry(x, y, w, h)
        self.u = h / 1728
        self.cx, self.cy = w * 0.5, h * 0.50
        self.R = h * 0.27

        self.t0 = self.prev = self.last_poll = self.last_working = time.time()
        self.started = None
        self.closing = None
        self.close_msg, self.close_color = "", GREEN
        self.rot = 0.0
        self.accent = QColor(CYAN)
        self.shocks = []
        self.signals = []
        self.events = []
        self.offset = 0
        self.turn_start = read_turn_start()
        self.sprites = {}
        self.running = []      # steps started but not finished, oldest first
        self.ended = {}        # id -> END ts, for an END that arrived before its start
        self.waiting_since = None
        self.reason = None     # (kind, detail) shown on the standby screen
        self.plan = None       # [{"text", "active", "status"}] or None
        self.last_beat = 0.0

        self.build_net()
        self.build_bg()
        self.read_new_events(initial=True)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000 // FPS)

    # ---------- setup ----------
    def font(self, px, bold=False):
        f = QFont()
        f.setFamilies(["JetBrains Mono", "DejaVu Sans Mono", "Monospace"])
        f.setPixelSize(max(8, int(px * self.u)))
        f.setBold(bold)
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 108)
        return f

    def build_net(self):
        n, pts = 170, []
        golden = math.pi * (3 - math.sqrt(5))
        for i in range(n):
            yy = 1 - (i / (n - 1)) * 2
            r = math.sqrt(1 - yy * yy)
            th = golden * i
            rad = random.uniform(0.82, 1.0) if random.random() > 0.22 else random.uniform(0.35, 0.75)
            pts.append((math.cos(th) * r * rad, yy * rad, math.sin(th) * r * rad))
        self.pts = pts
        self.adj = [set() for _ in range(n)]
        for a, p in enumerate(pts):
            near = sorted(range(n), key=lambda b: sum((p[k] - pts[b][k]) ** 2 for k in range(3)))
            for b in near[1:4]:
                self.adj[a].add(b)
                self.adj[b].add(a)
        self.edges = [(a, b) for a in range(n) for b in self.adj[a] if a < b]
        self.heat = [0.0] * n
        self.ncol = [CYAN] * n

    def build_bg(self):
        W, H, u = self.W, self.H, self.u
        pm = QPixmap(W, H)
        pm.fill(QColor("#02040a"))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        g = QRadialGradient(self.cx, self.cy, H * 0.75)
        g.setColorAt(0, QColor(12, 34, 72, 150))
        g.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(0, 0, W, H, QBrush(g))
        # dot grid
        p.setPen(QPen(QColor(30, 60, 110, 70), max(1, 2 * u)))
        step = int(44 * u)
        p.drawPoints(QPolygonF([QPointF(i, j) for i in range(step, W, step) for j in range(step, H, step)]))
        # top rule with ticks
        p.setPen(QPen(QColor(79, 216, 255, 60), max(1, u)))
        top = 118 * u
        p.drawLine(QLineF(60 * u, top, W - 60 * u, top))
        p.drawLines([QLineF(i, top, i, top + (14 if k % 10 == 0 else 6) * u)
                     for k, i in enumerate(range(int(60 * u), int(W - 60 * u), int(24 * u)))])
        # panel brackets
        for (x0, y0, x1, y1) in self.panel_rects():
            self.brackets(p, x0, y0, x1, y1)
        # scanlines baked in
        p.setPen(QPen(QColor(0, 0, 0, 55), 1))
        p.drawLines([QLineF(0, yy, W, yy) for yy in range(0, H, 3)])
        # vignette
        v = QRadialGradient(W / 2, H / 2, W * 0.62)
        v.setColorAt(0.55, QColor(0, 0, 0, 0))
        v.setColorAt(1, QColor(0, 0, 0, 210))
        p.fillRect(0, 0, W, H, QBrush(v))
        p.end()
        self.bg = pm

    def panel_rects(self):
        u, W, H = self.u, self.W, self.H
        return [(60 * u, 170 * u, 960 * u, H - 90 * u), (W - 900 * u, 170 * u, W - 60 * u, H - 90 * u)]

    def brackets(self, p, x0, y0, x1, y1):
        u = self.u
        L = 40 * u
        p.setPen(QPen(QColor(79, 216, 255, 120), max(1, 2.5 * u)))
        for (x, y, dx, dy) in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
            p.drawLine(QLineF(x, y, x + dx * L, y))
            p.drawLine(QLineF(x, y, x, y + dy * L))

    def sprite(self, color):
        key = QColor(color).name()
        if key not in self.sprites:
            s = 64
            pm = QPixmap(s, s)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            g = QRadialGradient(s / 2, s / 2, s / 2)
            c = QColor(color)
            g.setColorAt(0, QColor(255, 255, 255, 255))
            g.setColorAt(0.12, with_alpha(c, 240))
            g.setColorAt(0.4, with_alpha(c, 90))
            g.setColorAt(1, with_alpha(c, 0))
            p.fillRect(0, 0, s, s, QBrush(g))
            p.end()
            self.sprites[key] = pm
        return self.sprites[key]

    # ---------- data ----------
    def read_new_events(self, initial=False):
        try:
            size = os.path.getsize(EVENTS)
        except OSError:
            return
        if size < self.offset:
            self.offset = 0
        if size == self.offset:
            return
        with open(EVENTS, "rb") as f:
            f.seek(self.offset)
            data = f.read()
        cut = data.rfind(b"\n") + 1
        self.offset += cut
        now = time.time()
        for line in data[:cut].decode("utf-8", "replace").splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("ts", 0) < self.turn_start - 1:
                continue
            if ev.get("kind") == "END":
                self.finish(ev)
                continue
            ev["born"] = 0 if initial else now
            if ev.get("tool"):
                if ev.get("id") in self.ended:
                    ev["dur"] = max(0.0, self.ended.pop(ev["id"]) - ev["ts"])
                else:
                    self.running.append(ev)
            self.events.append(ev)
            if not initial:
                self.on_event(ev)
        self.events = self.events[-400:]
        self.running = [e for e in self.running if "dur" not in e][-50:]

    def finish(self, end):
        """Pair an END with its step (by tool_use_id, else the oldest running call of the
        same tool) and record how long it ran, not counting time spent waiting on you."""
        key = end.get("id")
        match = next((e for e in self.running if key and e.get("id") == key), None)
        if match is None and not key:
            match = next((e for e in self.running if e.get("tool") == end.get("tool")), None)
        if match is None:
            if key:
                self.ended[key] = end.get("ts", 0)
            return
        self.running.remove(match)
        match["dur"] = max(0.0, end.get("ts", 0) - match.get("ts", 0) - match.get("held", 0))

    def elapsed(self, ev, now):
        """How long a step has run so far (or took), minus any time held on a prompt."""
        if "dur" in ev:
            return ev["dur"]
        held = ev.get("held", 0) + (now - self.waiting_since if self.waiting_since else 0)
        return max(0.0, now - ev.get("ts", now) - held)

    def on_event(self, ev):
        if self.waiting_since is not None:
            return  # keep the standby palette until work resumes
        c = QColor(KIND_COLORS.get(ev.get("kind"), "#9fb4d8"))
        self.accent = c
        self.shocks.append((time.time(), c))
        self.fire(random.randrange(len(self.pts)), c, fanout=6)

    def fire(self, a, color, fanout=None):
        self.heat[a] = 1.0
        self.ncol[a] = color
        nbrs = list(self.adj[a])
        random.shuffle(nbrs)
        for b in nbrs[: fanout or random.randint(1, 2)]:
            if len(self.signals) < 420:
                self.signals.append([a, b, 0.0, random.uniform(0.9, 2.0), color])

    # ---------- loop ----------
    def tick(self):
        now = time.time()
        dt = min(0.1, now - self.prev)
        self.prev = now

        if now - self.last_poll > 0.2:
            self.last_poll = now
            states = read_states()
            turn = read_turn_start()
            if turn != self.turn_start:  # a new prompt: steps left running were interrupted
                self.turn_start, self.running, self.ended = turn, [], {}
            if dismissed_since(self.turn_start):
                QApplication.quit()
                return
            vals = set(states.values())
            self.plan = read_plan(states, self.turn_start)
            if "working" in vals:
                self.last_working = now
                self.end_wait(now)
                if self.started is None:
                    self.started = now
                    self.show()
                    self.raise_()
                if self.closing and now - self.closing < 1.0:
                    self.closing = None
            elif self.started is None:
                if now - self.t0 > 1.5:
                    QApplication.quit()
                    return
            elif "waiting" in vals:
                if self.closing and now - self.closing < 1.0:
                    self.closing = None
                if not self.closing and now - self.last_working > 0.6:
                    self.reason = read_reason(states) or self.reason or ("PERMISSION", "Claude needs your input")
                    if self.waiting_since is None:
                        self.begin_wait(now)
            elif not self.closing and now - self.last_working > 0.6:
                self.end_wait(now)
                self.closing = now
                self.close_msg, self.close_color = "TASK COMPLETE", GREEN
                for i in range(0, len(self.pts), 3):
                    self.fire(i, self.close_color, fanout=1)
                self.shocks.append((now, self.close_color))

        self.read_new_events()
        self.simulate(now, dt)
        if self.closing and now - self.closing > 2.0:
            QApplication.quit()
            return
        if self.started:
            self.update()

    def begin_wait(self, now):
        self.waiting_since = now
        self.accent = QColor(AMBER)
        for i in range(0, len(self.pts), 4):
            self.fire(i, AMBER, fanout=1)
        self.shocks.append((now, AMBER))

    def end_wait(self, now):
        """Leave standby. Steps that were held on the prompt don't count the hold."""
        if self.waiting_since is None:
            return
        for e in self.running:
            e["held"] = e.get("held", 0) + now - self.waiting_since
        self.waiting_since = None
        self.reason = None
        self.accent = QColor(CYAN)

    def simulate(self, now, dt):
        waiting = self.waiting_since is not None
        self.rot += dt * (0.04 if waiting else 0.11)
        recent = sum(1 for e in self.events[-30:] if now - e.get("born", 0) < 6)
        rate = 2.5 if waiting else 6 + 4 * recent
        if waiting and now - self.last_beat > 2.2:  # a slow amber heartbeat while on hold
            self.last_beat = now
            self.shocks.append((now, AMBER))
        for _ in range(int(rate * dt) + (1 if random.random() < (rate * dt) % 1 else 0)):
            self.fire(random.randrange(len(self.pts)), self.accent if random.random() < 0.5 else CYAN)
        alive = []
        for s in self.signals:
            s[2] += s[3] * dt
            if s[2] >= 1:
                if random.random() < 0.42:
                    self.fire(s[1], s[4])
                else:
                    self.heat[s[1]] = max(self.heat[s[1]], 0.5)
                    self.ncol[s[1]] = s[4]
                continue
            alive.append(s)
        self.signals = alive
        k = math.exp(-dt * 3.2)
        self.heat = [h * k for h in self.heat]
        self.shocks = [s for s in self.shocks if now - s[0] < 1.4][-4:]

    # ---------- drawing ----------
    def project(self):
        ca, sa = math.cos(self.rot), math.sin(self.rot)
        cb, sb = math.cos(0.38), math.sin(0.38)
        out = []
        for (x, y, z) in self.pts:
            x1, z1 = x * ca + z * sa, -x * sa + z * ca
            y2, z2 = y * cb - z1 * sb, y * sb + z1 * cb
            persp = 3.2 / (3.2 + z2)
            out.append((self.cx + x1 * self.R * persp, self.cy + y2 * self.R * persp, 0.25 + 0.75 * (1 - (z2 + 1) / 2)))
        return out

    def paintEvent(self, _):
        now = time.time()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.closing and now - self.closing > 1.15:
            self.draw_crt_off(p, (now - self.closing - 1.15) / 0.85)
            p.end()
            return
        boot = ease((now - self.started) / 1.1) if self.started else 0
        p.drawPixmap(0, 0, self.bg)
        p.setOpacity(boot)
        self.draw_core(p, now, boot)
        self.draw_net(p, boot)
        p.setOpacity(ease((now - self.started - 0.3) / 0.8) if self.started else 0)
        self.draw_header(p, now)
        self.draw_stream(p, now)
        self.draw_telemetry(p, now)
        self.draw_status(p, now)
        p.setOpacity(1)
        if self.started and now - self.started < 1.6:
            self.draw_boot_text(p, now - self.started)
        if self.waiting_since is not None and not self.closing:
            self.draw_standby(p, now)
        if self.closing:
            self.draw_outro(p, now - self.closing)
        p.end()

    def draw_core(self, p, now, boot):
        u, R = self.u, self.R * (0.55 + 0.45 * boot)
        p.save()
        p.translate(self.cx, self.cy)
        g = QRadialGradient(0, 0, R * 1.1)
        g.setColorAt(0, with_alpha(self.accent, 40))
        g.setColorAt(1, with_alpha(self.accent, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(0, 0), R * 1.1, R * 1.1)
        p.setBrush(Qt.BrushStyle.NoBrush)

        def arc(r, start, span, color, width):
            p.setPen(QPen(color, width * u, cap=Qt.PenCapStyle.FlatCap))
            p.drawArc(QRectF(-r, -r, 2 * r, 2 * r), int(start * 16), int(span * 16))

        t = now - self.t0
        r1 = R * 1.16
        for i in range(3):
            arc(r1, t * 22 + i * 120, 78, with_alpha(CYAN, 170), 4)
        r2 = R * 1.23
        p.save()
        p.rotate(-t * 6)
        p.setPen(QPen(with_alpha(CYAN, 110), max(1, 1.6 * u)))
        p.drawLines([QLineF(math.cos(math.radians(a)) * r2, math.sin(math.radians(a)) * r2,
                            math.cos(math.radians(a)) * (r2 + (22 if a % 30 == 0 else 9) * u),
                            math.sin(math.radians(a)) * (r2 + (22 if a % 30 == 0 else 9) * u))
                     for a in range(0, 360, 5)])
        p.setFont(self.font(15))
        p.setPen(with_alpha(CYAN, 120))
        for a in range(0, 360, 90):
            rr = r2 + 44 * u
            p.drawText(QPointF(math.cos(math.radians(a)) * rr - 24 * u, math.sin(math.radians(a)) * rr + 6 * u), f"{a:03d}°")
        p.restore()
        r3 = R * 1.36
        pen = QPen(with_alpha(CYAN, 60), max(1, 1.2 * u))
        pen.setDashPattern([2, 6])
        p.setPen(pen)
        p.drawEllipse(QPointF(0, 0), r3, r3)
        for i in range(2):
            arc(r3, -t * 55 + i * 180, 24, with_alpha(self.accent, 230), 6)
        for (t0, c) in self.shocks:
            age = (now - t0) / 1.4
            rr = R * (0.9 + 0.7 * ease(age))
            p.setPen(QPen(with_alpha(c, 200 * (1 - age)), (5 - 4 * age) * u))
            p.drawEllipse(QPointF(0, 0), rr, rr)
        p.restore()

    def draw_net(self, p, boot):
        u = self.u
        pr = self.project()
        buckets = ([], [], [])
        for a, b in self.edges:
            d = (pr[a][2] + pr[b][2]) / 2
            buckets[0 if d < 0.45 else 1 if d < 0.7 else 2].append(QLineF(pr[a][0], pr[a][1], pr[b][0], pr[b][1]))
        for i, lines in enumerate(buckets):
            p.setPen(QPen(QColor(60, 140, 230, (35, 70, 120)[i]), max(1, (1, 1.4, 1.8)[i] * u)))
            p.drawLines(lines)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        for a, b, t, _, c in self.signals:
            x = pr[a][0] + (pr[b][0] - pr[a][0]) * t
            y = pr[a][1] + (pr[b][1] - pr[a][1]) * t
            d = pr[a][2] + (pr[b][2] - pr[a][2]) * t
            s = (14 + 16 * d) * u
            p.drawPixmap(QRectF(x - s, y - s, 2 * s, 2 * s), self.sprite(c), QRectF(0, 0, 64, 64))
        for i, (x, y, d) in enumerate(pr):
            h = self.heat[i]
            if h > 0.04:
                s = (16 + 50 * h) * u * (0.6 + 0.4 * d)
                p.setOpacity(min(1, h * 1.3) * boot)
                p.drawPixmap(QRectF(x - s, y - s, 2 * s, 2 * s), self.sprite(self.ncol[i]), QRectF(0, 0, 64, 64))
        p.setOpacity(boot)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.setPen(Qt.PenStyle.NoPen)
        for (x, y, d) in pr:
            p.setBrush(QColor(140, 200, 255, int(70 + 180 * d)))
            r = (2 + 3 * d) * u
            p.drawEllipse(QPointF(x, y), r, r)

    def draw_header(self, p, now):
        u = self.u
        blink = (now * 2) % 2 < 1.3
        p.setFont(self.font(30, True))
        p.setPen(CYAN)
        p.drawText(QPointF(64 * u, 92 * u), "NEURAL LINK")
        fm = QFontMetrics(self.font(30, True))
        x = 64 * u + fm.horizontalAdvance("NEURAL LINK") + 26 * u
        state, sc = ("STANDBY", AMBER) if self.waiting_since is not None else ("ACTIVE", GREEN)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(with_alpha(sc, 255 if blink else 60))
        p.drawEllipse(QPointF(x, 81 * u), 9 * u, 9 * u)
        p.setFont(self.font(22))
        p.setPen(with_alpha(sc, 230))
        p.drawText(QPointF(x + 22 * u, 90 * u), state)
        p.setPen(with_alpha(CYAN, 150))
        p.drawText(QPointF(x + 160 * u, 90 * u), "//  CLAUDE CODE")
        p.setFont(self.font(30, True))
        p.setPen(CYAN)
        clock = time.strftime("%H:%M:%S")
        p.drawText(QPointF(self.W - 64 * u - QFontMetrics(self.font(30, True)).horizontalAdvance(clock), 92 * u), clock)

    def draw_plan(self, p, now, x0, y0, x1):
        """The session's plan as a checklist. Returns the y where it ends."""
        u, items = self.u, self.plan
        done = sum(1 for i in items if i.get("status") == "completed")
        p.setFont(self.font(22, True))
        p.setPen(CYAN)
        p.drawText(QPointF(x0 + 30 * u, y0 + 52 * u), "◢ MISSION PLAN")
        p.setFont(self.font(20, True))
        count = f"{done}/{len(items)}"
        cw = QFontMetrics(self.font(20, True)).horizontalAdvance(count)
        p.setPen(with_alpha(GREEN, 230))
        p.drawText(QPointF(x1 - 30 * u - cw, y0 + 52 * u), count)
        bx0, bx1, n = x0 + 330 * u, x1 - 50 * u - cw, len(items)
        for k in range(n):  # one segment per step
            sw = (bx1 - bx0) / n
            st = items[k].get("status")
            col = with_alpha(GREEN, 200) if st == "completed" else \
                with_alpha(AMBER, 150 + 100 * (int(now * 2) % 2)) if st == "in_progress" else QColor(40, 70, 120, 90)
            p.fillRect(QRectF(bx0 + k * sw, y0 + 38 * u, max(1, sw - 3 * u), 12 * u), col)

        lh, maxrows = 42 * u, 8
        first_open = next((k for k, i in enumerate(items) if i.get("status") != "completed"), len(items))
        start = max(0, min(first_open - 2, len(items) - maxrows))
        f = self.font(21)
        fm = QFontMetrics(f)
        p.setFont(f)
        y = y0 + 104 * u
        if start:
            p.setPen(with_alpha(GREEN, 120))
            p.drawText(QPointF(x0 + 70 * u, y), f"▲ {start} done")
            y += lh
        shown = items[start:start + maxrows - (1 if start else 0)]
        for i in shown:
            st = i.get("status")
            box = QRectF(x0 + 32 * u, y - 19 * u, 20 * u, 20 * u)
            if st == "completed":
                p.fillRect(box, with_alpha(GREEN, 170))
                col, text = with_alpha(QColor("#9fb4d8"), 130), i.get("text") or i.get("active")
            elif st == "in_progress":
                pulse = 0.5 + 0.5 * math.sin(now * 5)
                p.setPen(QPen(AMBER, 2 * u))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(box)
                p.fillRect(box.adjusted(5 * u, 5 * u, -5 * u, -5 * u), with_alpha(AMBER, 120 + 135 * pulse))
                p.fillRect(QRectF(x0 + 14 * u, y - 24 * u, 5 * u, 30 * u), AMBER)
                col, text = QColor("#ffffff"), i.get("active") or i.get("text")
            else:
                p.setPen(QPen(QColor(110, 140, 190, 150), 1.6 * u))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(box)
                col, text = with_alpha(QColor("#dfe9ff"), 170), i.get("text") or i.get("active")
            ff = QFont(f)
            ff.setStrikeOut(st == "completed")
            p.setFont(ff)
            p.setPen(col)
            p.drawText(QPointF(x0 + 72 * u, y), fm.elidedText(text or "", Qt.TextElideMode.ElideRight, int(x1 - x0 - 110 * u)))
            y += lh
        rest = len(items) - start - len(shown)
        if rest > 0:
            p.setFont(f)
            p.setPen(with_alpha(QColor("#9fb4d8"), 130))
            p.drawText(QPointF(x0 + 70 * u, y), f"▼ {rest} more")
            y += lh
        p.setPen(QPen(QColor(79, 216, 255, 50), max(1, u)))
        p.drawLine(QLineF(x0 + 30 * u, y - 6 * u, x1 - 30 * u, y - 6 * u))
        return y

    def draw_stream(self, p, now):
        u = self.u
        x0, y0, x1, y1 = self.panel_rects()[0]
        if self.plan:
            y0 = self.draw_plan(p, now, x0, y0, x1)
        p.setFont(self.font(22, True))
        p.setPen(CYAN)
        p.drawText(QPointF(x0 + 30 * u, y0 + 52 * u), "◢ ACTIVITY STREAM")
        lh = 44 * u
        rows = max(1, int((y1 - y0 - 120 * u) / lh))
        evs = [e for e in self.events if e.get("kind") != "DONE"][-rows:]
        f = self.font(21)
        fm = QFontMetrics(f)
        p.setFont(f)
        y = y0 + 110 * u
        for i, e in enumerate(evs):
            age_rank = len(evs) - 1 - i
            alpha = 255 if age_rank == 0 else max(60, 210 - age_rank * 9)
            c = QColor(KIND_COLORS.get(e.get("kind"), "#9fb4d8"))
            ts = time.strftime("%H:%M:%S", time.localtime(e.get("ts", now)))
            p.setPen(with_alpha(QColor("#6b84ad"), alpha))
            p.drawText(QPointF(x0 + 30 * u, y), ts)
            p.setPen(with_alpha(c, alpha))
            p.drawText(QPointF(x0 + 170 * u, y), e.get("kind", "")[:6])
            mode = Qt.TextElideMode.ElideMiddle if e.get("kind") in ("READ", "WRITE") else Qt.TextElideMode.ElideRight
            detail = fm.elidedText(e.get("detail", ""), mode, int(x1 - x0 - 440 * u))
            born = e.get("born", 0)
            if born and now - born < 0.7:
                detail = detail[: int(len(detail) * (now - born) / 0.7)] + ("█" if int(now * 12) % 2 else "")
            p.setPen(with_alpha(QColor("#dfe9ff"), alpha))
            p.drawText(QPointF(x0 + 290 * u, y), detail)
            if age_rank == 0:
                p.fillRect(QRectF(x0 + 14 * u, y - 24 * u, 5 * u, 30 * u), c)
            if e.get("tool"):  # how long it took, or a live timer while it runs
                running = "dur" not in e
                sec = self.elapsed(e, now)
                if running or sec >= 1:
                    txt = f"{SPINNER[int(now * 6) % 4]} {fmt_dur(sec, True)}" if running else fmt_dur(sec)
                    p.setPen(with_alpha(c if running else QColor("#6b84ad"), alpha if not running else 255))
                    p.drawText(QPointF(x1 - 30 * u - fm.horizontalAdvance(txt), y), txt)
            y += lh

    def draw_telemetry(self, p, now):
        u = self.u
        x0, y0, x1, y1 = self.panel_rects()[1]
        p.setFont(self.font(22, True))
        p.setPen(CYAN)
        p.drawText(QPointF(x0 + 30 * u, y0 + 52 * u), "◢ TELEMETRY")
        evs = [e for e in self.events if e.get("kind") not in ("LINK", "DONE")]
        el = max(0, int(now - self.turn_start))
        p.setFont(self.font(18))
        p.setPen(with_alpha(CYAN, 150))
        p.drawText(QPointF(x0 + 30 * u, y0 + 120 * u), "ELAPSED")
        p.drawText(QPointF(x0 + 470 * u, y0 + 120 * u), "OPERATIONS")
        p.setFont(self.font(60, True))
        p.setPen(QColor("#e8f4ff"))
        p.drawText(QPointF(x0 + 30 * u, y0 + 190 * u), f"T+{el // 3600:02d}:{el // 60 % 60:02d}:{el % 60:02d}")
        p.drawText(QPointF(x0 + 470 * u, y0 + 190 * u), f"{len(evs):04d}")

        counts = {k: 0 for k in BAR_KINDS}
        for e in evs:
            if e.get("kind") in counts:
                counts[e["kind"]] += 1
        mx = max(1, max(counts.values()))
        y = y0 + 270 * u
        p.setFont(self.font(19))
        for k in BAR_KINDS:
            c = QColor(KIND_COLORS[k])
            p.setPen(with_alpha(c, 220))
            p.drawText(QPointF(x0 + 30 * u, y), k)
            bx, bw = x0 + 180 * u, (x1 - x0) - 320 * u
            p.fillRect(QRectF(bx, y - 16 * u, bw, 14 * u), QColor(40, 70, 120, 70))
            segs = int(40 * counts[k] / mx)
            for s in range(segs):
                p.fillRect(QRectF(bx + s * bw / 40, y - 16 * u, bw / 40 - 3 * u, 14 * u), with_alpha(c, 160 + 90 * (s == segs - 1)))
            p.setPen(QColor("#dfe9ff"))
            p.drawText(QPointF(x1 - 110 * u, y), f"{counts[k]:>3}")
            y += 46 * u

        # the slowest steps this turn, running ones included
        timed = sorted(((self.elapsed(e, now), e) for e in evs if e.get("tool")), key=lambda t: -t[0])
        timed = [t for t in timed if t[0] >= 1][:3]
        y += 26 * u
        p.setFont(self.font(18))
        p.setPen(with_alpha(CYAN, 150))
        p.drawText(QPointF(x0 + 30 * u, y), "LONGEST STEPS")
        f = self.font(19)
        fm = QFontMetrics(f)
        p.setFont(f)
        y += 44 * u
        if not timed:
            p.setPen(with_alpha(QColor("#6b84ad"), 160))
            p.drawText(QPointF(x0 + 30 * u, y), "—  every step under a second")
            y += 40 * u
        for sec, e in timed:
            running = "dur" not in e
            c = QColor(KIND_COLORS.get(e.get("kind"), "#9fb4d8"))
            p.setPen(QColor("#e8f4ff"))
            p.drawText(QPointF(x0 + 30 * u, y), (SPINNER[int(now * 6) % 4] + " " + fmt_dur(sec, True)) if running else fmt_dur(sec))
            p.setPen(with_alpha(c, 220))
            p.drawText(QPointF(x0 + 180 * u, y), e.get("kind", "")[:6])
            p.setPen(with_alpha(QColor("#dfe9ff"), 190))
            p.drawText(QPointF(x0 + 300 * u, y), fm.elidedText(e.get("detail", ""), Qt.TextElideMode.ElideRight, int(x1 - x0 - 340 * u)))
            y += 40 * u

        # activity oscilloscope: tool calls per second over the last 60 s
        gx, gy, gw, gh = x0 + 30 * u, y + 30 * u, (x1 - x0) - 60 * u, y1 - y - 90 * u
        p.setFont(self.font(18))
        p.setPen(with_alpha(CYAN, 150))
        p.drawText(QPointF(gx, gy - 4 * u), "SYNAPTIC ACTIVITY  /  60s")
        gy += 14 * u
        p.setPen(QPen(QColor(40, 80, 140, 90), max(1, u)))
        for i in range(7):
            p.drawLine(QLineF(gx + gw * i / 6, gy, gx + gw * i / 6, gy + gh))
        for i in range(5):
            p.drawLine(QLineF(gx, gy + gh * i / 4, gx + gw, gy + gh * i / 4))
        buckets = [0.0] * 120
        for e in evs:
            age = now - e.get("ts", 0)
            if 0 <= age < 60:
                buckets[119 - int(age * 2)] += 1
        sm = [sum(buckets[max(0, i - 2): i + 3]) / 5 for i in range(120)]
        top = max(1.0, max(sm))
        poly = QPolygonF([QPointF(gx + gw * i / 119,
                                  gy + gh - 8 * u - (gh - 24 * u) * v / top - 4 * u * math.sin(now * 5 + i * 0.4))
                          for i, v in enumerate(sm)])
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        p.setPen(QPen(with_alpha(self.accent, 60), 10 * u))
        p.drawPolyline(poly)
        p.setPen(QPen(with_alpha(self.accent, 230), 2.5 * u))
        p.drawPolyline(poly)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

    def draw_status(self, p, now):
        u = self.u
        y = self.cy + self.R * 1.36 + 110 * u
        cur = next((e for e in reversed(self.events) if e.get("kind") not in ("DONE",)), None)
        p.setFont(self.font(22, True))
        if self.waiting_since is not None:
            blocks = "".join("▰" if int(now * 1.5) % 2 else "▱" for i in range(12))
            txt, lc = f"STANDBY  {blocks}", AMBER
        else:
            blocks = "".join("▰" if (int(now * 10) - i) % 12 < 4 else "▱" for i in range(12))
            txt, lc = f"PROCESSING  {blocks}", CYAN
        p.setPen(with_alpha(lc, 200))
        p.drawText(QPointF(self.cx - QFontMetrics(self.font(22, True)).horizontalAdvance(txt) / 2, y), txt)
        if cur:
            c = QColor(KIND_COLORS.get(cur.get("kind"), "#9fb4d8"))
            f = self.font(32)
            fm = QFontMetrics(f)
            prompt = f"› {cur.get('kind', '')}  "
            detail = fm.elidedText(cur.get("detail", ""), Qt.TextElideMode.ElideMiddle, int(1100 * self.u))
            shown, typing = self.typed(cur, detail, now)
            # centre the finished line (plus cursor) so the text grows rightwards in place
            x = self.cx - fm.horizontalAdvance(prompt + detail + "█") / 2
            p.setFont(f)
            p.setPen(c)
            p.drawText(QPointF(x, y + 62 * u), prompt + shown)
            cx = x + fm.horizontalAdvance(prompt + shown)
            if typing or int(now * 2.2) % 2:  # solid while typing, then a slow blink
                p.fillRect(QRectF(cx + 2 * u, y + 62 * u - fm.ascent() * 0.8, fm.horizontalAdvance("█") * 0.55,
                                  fm.ascent() * 0.95), with_alpha(c, 230))
            if cur.get("tool") and not typing:  # after the cursor, so the line never shifts
                running, sec = "dur" not in cur, self.elapsed(cur, now)
                if sec >= 1 or not running:
                    t = f"{SPINNER[int(now * 6) % 4]} {fmt_dur(sec, True)}" if running else f"✓ {fmt_dur(sec)}"
                    p.setFont(self.font(26))
                    p.setPen(with_alpha(QColor("#e8f4ff") if running else QColor("#6b84ad"), 230))
                    p.drawText(QPointF(cx + fm.horizontalAdvance("█") + 22 * u, y + 62 * u), t)

    def typed(self, ev, text, now, slot="status"):
        """How much of a line has been typed for this event: (text, still typing).
        Each event gets its own keystroke rhythm, with short pauses at word breaks, so it
        reads like someone typing rather than a fixed-rate reveal. Each slot (the status
        line, the standby reason) types independently."""
        if not hasattr(self, "typers"):
            self.typers = {}
        key = (ev.get("ts"), ev.get("kind"), text)
        st = self.typers.get(slot)
        if st is None or st[0] != key:
            rng = random.Random(hash(key))
            t, times = 0.0, []
            for ch in text:
                t += rng.uniform(0.018, 0.055) + (rng.uniform(0.04, 0.12) if ch in " /._-" else 0)
                times.append(t)
            scale = min(1.0, 1.6 / t) if t else 1.0  # long lines speed up: never more than 1.6 s
            st = self.typers[slot] = (key, now, [x * scale for x in times])
        n = sum(1 for x in st[2] if x <= now - st[1])
        return text[:n], n < len(text)

    def big_text(self, p, text, color, alpha, size=96):
        f = self.font(size, True)
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 125)
        fm = QFontMetrics(f)
        p.setFont(f)
        x = self.cx - fm.horizontalAdvance(text) / 2
        y = self.cy + fm.ascent() / 2 - 10 * self.u
        band = QRectF(0, y - fm.ascent() - 40 * self.u, self.W, fm.height() + 70 * self.u)
        p.fillRect(band, QColor(2, 4, 10, int(alpha * 0.8)))
        p.setPen(QPen(with_alpha(color, alpha * 0.7), 2 * self.u))
        p.drawLine(QLineF(0, band.top(), self.W, band.top()))
        p.drawLine(QLineF(0, band.bottom(), self.W, band.bottom()))
        p.setPen(with_alpha(color, alpha))
        p.drawText(QPointF(x, y), text)

    def draw_standby(self, p, now):
        """Claude is blocked on you: say so, say why, and for how long."""
        u = self.u
        t = now - self.waiting_since
        a = 255 * (int(t * 20) % 2) if t < 0.3 else 165 + 90 * (0.5 + 0.5 * math.sin(t * 2.6))
        f = self.font(84, True)
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 125)
        fm = QFontMetrics(f)
        y = self.cy - 30 * u
        band = QRectF(0, y - fm.ascent() - 46 * u, self.W, fm.ascent() + 220 * u)
        p.fillRect(band, QColor(6, 4, 0, 215))
        p.setPen(QPen(with_alpha(AMBER, 170), 2 * u))
        p.drawLine(QLineF(0, band.top(), self.W, band.top()))
        p.drawLine(QLineF(0, band.bottom(), self.W, band.bottom()))
        p.setFont(f)
        p.setPen(with_alpha(AMBER, a))
        title = "AWAITING OPERATOR"
        p.drawText(QPointF(self.cx - fm.horizontalAdvance(title) / 2, y), title)

        kind, detail = self.reason or ("PERMISSION", "Claude needs your input")
        rf = self.font(30)
        rm = QFontMetrics(rf)
        prompt = f"{kind} › "
        detail = rm.elidedText(detail, Qt.TextElideMode.ElideRight, int(min(self.W * 0.8, 1500 * u)))
        shown, typing = self.typed({"ts": self.waiting_since, "kind": kind}, detail, now, slot="reason")
        x = self.cx - rm.horizontalAdvance(prompt + detail + "█") / 2
        p.setFont(rf)
        p.setPen(AMBER)
        p.drawText(QPointF(x, y + 76 * u), prompt)
        p.setPen(QColor("#fff3e0"))
        p.drawText(QPointF(x + rm.horizontalAdvance(prompt), y + 76 * u), shown)
        if typing or int(now * 2.2) % 2:
            cx = x + rm.horizontalAdvance(prompt + shown)
            p.fillRect(QRectF(cx + 2 * u, y + 76 * u - rm.ascent() * 0.8, rm.horizontalAdvance("█") * 0.55,
                              rm.ascent() * 0.95), with_alpha(AMBER, 230))
        hold = f"ON HOLD {fmt_dur(t, True)}   //   RESPOND IN CLAUDE CODE"
        p.setFont(self.font(20))
        p.setPen(with_alpha(AMBER, 150))
        p.drawText(QPointF(self.cx - QFontMetrics(self.font(20)).horizontalAdvance(hold) / 2, y + 130 * u), hold)

    def draw_boot_text(self, p, t):
        a = 255 * (1 - ease((t - 0.9) / 0.7)) if t > 0.9 else 255 * ((int(t * 14) % 2) or t > 0.4)
        self.big_text(p, "LINK ESTABLISHED", CYAN, a, 72)

    def draw_outro(self, p, t):
        a = 255 if t > 0.25 else 255 * (int(t * 20) % 2)
        self.big_text(p, self.close_msg, self.close_color, a)

    def draw_crt_off(self, p, f):
        p.fillRect(0, 0, self.W, self.H, QColor("#000000"))
        f = max(0.0, min(1.0, f))
        if f < 0.55:
            hh = self.H * 0.25 * (1 - ease(f / 0.55)) + 3 * self.u
            ww = self.W
        else:
            hh = 3 * self.u
            ww = self.W * (1 - ease((f - 0.55) / 0.45))
        r = QRectF(self.cx - ww / 2, self.cy - hh / 2, ww, hh)
        p.fillRect(r.adjusted(0, -10 * self.u, 0, 10 * self.u), with_alpha(self.close_color, 70))
        p.fillRect(r, QColor("#ffffff"))


def main():
    os.makedirs(STATE_DIR, exist_ok=True)
    if "--close" in sys.argv:  # bound to the keyboard shortcut
        open(DISMISSED, "w").close()
        return
    if "--list-monitors" in sys.argv:  # `make monitors`
        list_monitors(os.environ.get("NEURAL_HUD_MONITOR", "").strip())
        return
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit(0)
    geo = find_monitor()
    if not geo:
        sys.exit(0)
    app = QApplication(sys.argv)
    hud = HUD(geo)  # noqa: F841 — kept alive by the event loop
    app.exec()


if __name__ == "__main__":
    main()
