#!/usr/bin/env python3
"""Generate Neural HUD branding art (icon, logo, banner, social preview) with pycairo.

The art reuses the HUD's own visual language: a node network inside a ring of
instrument arcs, the per-action colours from hud.py's KIND_COLORS, and the
typed status line with its block cursor, so the branding is a picture of the
thing rather than generic sci-fi clip-art.

Outputs into ../assets/.  Run: python3 tools/gen_art.py
"""
import math
import os
import random

import cairo

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(os.path.dirname(HERE), "assets")
os.makedirs(ASSETS, exist_ok=True)
MONO = "DejaVu Sans Mono"


def hexc(s):
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) / 255 for i in (0, 2, 4))


# --- Palette: hud.py's background and KIND_COLORS ---
BG = hexc("#05080f")
BG_TOP = hexc("#0b1424")
WHITE = hexc("#e8f4ff")
DIM = hexc("#6b84ad")
CYAN = hexc("#4fd8ff")
KINDS = [("READ", "#4fd8ff"), ("WRITE", "#ff5fd8"), ("EXEC", "#ffb347"),
         ("SEARCH", "#6dff9e"), ("AGENT", "#b18cff"), ("MCP", "#ffe66d")]


def rgba(cr, c, a=1.0):
    cr.set_source_rgba(c[0], c[1], c[2], a)


def background(cr, w, h):
    g = cairo.RadialGradient(w * 0.5, h * 0.45, 0, w * 0.5, h * 0.45, max(w, h) * 0.75)
    g.add_color_stop_rgb(0, *BG_TOP)
    g.add_color_stop_rgb(1, *BG)
    cr.set_source(g)
    cr.paint()
    # faint scanlines, as on the HUD
    rgba(cr, WHITE, 0.018)
    for y in range(0, h, 4):
        cr.rectangle(0, y, w, 1)
    cr.fill()


def glow_dot(cr, x, y, r, c, a=1.0):
    g = cairo.RadialGradient(x, y, 0, x, y, r * 4)
    g.add_color_stop_rgba(0, *c, 0.55 * a)
    g.add_color_stop_rgba(1, *c, 0)
    cr.set_source(g)
    cr.arc(x, y, r * 4, 0, 2 * math.pi)
    cr.fill()
    rgba(cr, c, a)
    cr.arc(x, y, r, 0, 2 * math.pi)
    cr.fill()


def emblem(cr, cx, cy, R, seed=3, nodes=46):
    """Instrument rings around a projected sphere of linked nodes."""
    # rings and arcs
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    rgba(cr, CYAN, 0.18)
    cr.set_line_width(max(1, R * 0.012))
    cr.arc(cx, cy, R, 0, 2 * math.pi)
    cr.stroke()
    for start, span, col, r, wd in [(-2.2, 1.9, CYAN, 0.86, 0.035), (0.6, 1.6, CYAN, 0.86, 0.035),
                                    (2.75, 0.75, hexc("#ffb347"), 0.95, 0.03),
                                    (-0.25, 0.55, hexc("#ffb347"), 0.95, 0.03)]:
        rgba(cr, col, 0.85)
        cr.set_line_width(R * wd)
        cr.arc(cx, cy, R * r, start, start + span)
        cr.stroke()
    rgba(cr, CYAN, 0.35)
    cr.set_line_width(max(1, R * 0.008))
    for i in range(48):
        a = i * 2 * math.pi / 48
        l = 0.07 if i % 6 == 0 else 0.035
        cr.move_to(cx + math.cos(a) * R * 0.74, cy + math.sin(a) * R * 0.74)
        cr.line_to(cx + math.cos(a) * R * (0.74 - l), cy + math.sin(a) * R * (0.74 - l))
    cr.stroke()

    # nodes on a sphere (golden spiral), tilted and projected like hud.py's project()
    rng = random.Random(seed)
    pts = []
    for i in range(nodes):
        y = 1 - 2 * (i + 0.5) / nodes
        r = math.sqrt(1 - y * y)
        t = i * math.pi * (3 - math.sqrt(5)) + rng.uniform(-0.2, 0.2)
        x, z = math.cos(t) * r, math.sin(t) * r
        cb, sb = math.cos(0.38), math.sin(0.38)
        y2, z2 = y * cb - z * sb, y * sb + z * cb
        p = 3.2 / (3.2 + z2)
        pts.append((cx + x * R * 0.58 * p, cy + y2 * R * 0.58 * p, 0.3 + 0.7 * (1 - (z2 + 1) / 2)))
    cr.set_line_width(max(1, R * 0.01))
    for i, a in enumerate(pts):
        near = sorted(range(nodes), key=lambda j: (pts[j][0] - a[0]) ** 2 + (pts[j][1] - a[1]) ** 2)[1:4]
        for j in near:
            if j > i:
                rgba(cr, CYAN, 0.28 * (a[2] + pts[j][2]) / 2)
                cr.move_to(a[0], a[1])
                cr.line_to(pts[j][0], pts[j][1])
                cr.stroke()
    lit = {2: KINDS[2][1], 9: KINDS[0][1], 15: KINDS[1][1], 22: KINDS[3][1], 28: KINDS[4][1]}
    for i, (x, y, d) in enumerate(pts):
        if i in lit:
            glow_dot(cr, x, y, R * 0.03, hexc(lit[i]))
        else:
            rgba(cr, hexc("#9fd0ff"), 0.4 + 0.5 * d)
            cr.arc(x, y, R * 0.014 * (0.6 + d), 0, 2 * math.pi)
            cr.fill()


def wordmark(cr, x, y, size):
    """"NEURAL" in white, "HUD" in the accent: the same split as the sibling projects."""
    cr.select_font_face(MONO, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(size)
    rgba(cr, WHITE)
    cr.move_to(x, y)
    cr.show_text("NEURAL ")
    wa = cr.text_extents("NEURAL ").x_advance
    rgba(cr, CYAN)
    cr.show_text("HUD")
    return wa + cr.text_extents("HUD").x_advance


def status_line(cr, x, y, size, kind, color, text):
    """The typed status line from the bottom of the HUD, cursor included."""
    cr.select_font_face(MONO, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(size)
    c = hexc(color)
    line = f"› {kind}  {text}"
    rgba(cr, c)
    cr.move_to(x, y)
    cr.show_text(line)
    cx = x + cr.text_extents(line).x_advance + size * 0.1
    rgba(cr, c, 0.9)
    cr.rectangle(cx, y - size * 0.78, size * 0.34, size * 0.92)
    cr.fill()


def chips(cr, x, y, size, gap):
    cr.select_font_face(MONO, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(size)
    for name, col in KINDS:
        c = hexc(col)
        rgba(cr, c, 0.9)
        cr.rectangle(x, y - size * 0.75, size * 0.3, size * 0.8)
        cr.fill()
        cr.move_to(x + size * 0.6, y)
        cr.show_text(name)
        x += size * 0.6 + cr.text_extents(name).x_advance + gap


def brackets(cr, x0, y0, x1, y1, l):
    rgba(cr, CYAN, 0.45)
    cr.set_line_width(2)
    for (x, y, dx, dy) in [(x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)]:
        cr.move_to(x + dx * l, y)
        cr.line_to(x, y)
        cr.line_to(x, y + dy * l)
    cr.stroke()


def rounded(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def make_icon(path, size=512):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surf)
    # rounded dark tile so the icon stays legible on any backdrop
    rounded(cr, 0, 0, size, size, size * 0.18)
    cr.clip()
    background(cr, size, size)
    emblem(cr, size / 2, size / 2, size * 0.42)
    surf.write_to_png(path)


def make_logo(path, w=1280, h=380):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surf)
    # self-contained tile: the logo has to survive GitHub's light theme too
    rounded(cr, 0, 0, w, h, 36)
    cr.clip()
    background(cr, w, h)
    emblem(cr, 200, h / 2, 140)
    wordmark(cr, 390, h / 2 + 10, 104)
    cr.select_font_face(MONO, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(30)
    rgba(cr, DIM)
    cr.move_to(394, h / 2 + 70)
    cr.show_text("// LINK FOR CLAUDE CODE")
    surf.write_to_png(path)


def make_banner(path, w=1280, h=640, tagline="A neural link display for Claude Code."):
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
    cr = cairo.Context(surf)
    background(cr, w, h)
    brackets(cr, 40, 40, w - 40, h - 40, 34)
    emblem(cr, 260, 300, 190)
    wordmark(cr, 500, 270, 92)
    cr.select_font_face("Cantarell", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(34)
    rgba(cr, DIM)
    cr.move_to(502, 325)
    cr.show_text(tagline)
    chips(cr, 504, 400, 20, 30)
    status_line(cr, 120, 570, 30, "EXEC", "#ffb347", "Run the hook tests")
    surf.write_to_png(path)


def main():
    make_icon(os.path.join(ASSETS, "icon.png"))
    make_logo(os.path.join(ASSETS, "logo.png"))
    make_banner(os.path.join(ASSETS, "banner.png"))
    make_banner(os.path.join(ASSETS, "social-preview.png"),
                tagline="Watch Claude Code think, on your spare monitor.")
    print("wrote icon, logo, banner and social-preview to", ASSETS)


if __name__ == "__main__":
    main()
