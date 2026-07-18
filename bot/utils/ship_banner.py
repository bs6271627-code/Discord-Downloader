"""
ship_banner.py — Premium romantic banner generator for the /ship command.

Generates a 900×500 animated-style banner using Pillow with:
  • Randomised colour palettes (Rose Gold, Purple Dream, Crimson, Twilight,
    Cherry Blossom, Midnight Orchid)
  • Multi-layer bokeh / glow orbs
  • Mathematically drawn hearts and sparkle stars
  • Multi-pass Gaussian glow on all text
  • Progress bar and vignette
  • Pacifico + Nunito fonts
Returns a BytesIO PNG ready to attach to a Discord message.
"""

from __future__ import annotations

import io
import math
import os
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ── Canvas ────────────────────────────────────────────────────────────
W, H = 900, 500

# ── Font paths ────────────────────────────────────────────────────────
_DIR    = os.path.join(os.path.dirname(__file__), "..", "assets", "fonts")
_PACIFICO   = os.path.join(_DIR, "Pacifico-Regular.ttf")
_NUNITO_BD  = os.path.join(_DIR, "Nunito-Bold.ttf")
_NUNITO_SB  = os.path.join(_DIR, "Nunito-SemiBold.ttf")
_FALLBACK   = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        try:
            return ImageFont.truetype(_FALLBACK, size)
        except Exception:
            return ImageFont.load_default()


# ── Colour palettes ───────────────────────────────────────────────────
_PALETTES: list[dict] = [
    {   # Rose Gold
        "bg_top":    (28, 8,  20),
        "bg_bot":    (72, 22, 48),
        "orb_a":     (220, 90,  130, 55),
        "orb_b":     (160, 45,  90,  38),
        "orb_c":     (255, 140, 180, 30),
        "heart":     (255, 100, 145),
        "sparkle":   (255, 200, 220, 200),
        "name":      (255, 232, 242),
        "glow":      (255, 130, 175),
        "pct":       (255, 218, 232),
        "pct_glow":  (255, 100, 150),
        "bar_fill":  (255, 110, 155),
        "bar_bg":    (75, 25,  48),
        "msg":       (255, 200, 220),
        "label":     (220, 160, 185),
        "center_glow": (200, 60, 110, 35),
    },
    {   # Purple Dream
        "bg_top":    (18, 8,  42),
        "bg_bot":    (52, 18, 98),
        "orb_a":     (140, 70,  220, 55),
        "orb_b":     (90,  40,  170, 38),
        "orb_c":     (190, 130, 255, 30),
        "heart":     (170, 90,  255),
        "sparkle":   (210, 180, 255, 200),
        "name":      (238, 220, 255),
        "glow":      (170, 100, 255),
        "pct":       (228, 210, 255),
        "pct_glow":  (150, 80,  255),
        "bar_fill":  (155, 90,  255),
        "bar_bg":    (38, 15,  72),
        "msg":       (200, 170, 255),
        "label":     (175, 140, 230),
        "center_glow": (120, 60, 210, 35),
    },
    {   # Crimson Love
        "bg_top":    (38, 4,   8),
        "bg_bot":    (88, 12,  22),
        "orb_a":     (210, 40,  70,  55),
        "orb_b":     (170, 25,  45,  38),
        "orb_c":     (255, 100, 120, 30),
        "heart":     (255, 50,  75),
        "sparkle":   (255, 180, 190, 200),
        "name":      (255, 218, 222),
        "glow":      (255, 70,  95),
        "pct":       (255, 210, 215),
        "pct_glow":  (255, 50,  75),
        "bar_fill":  (255, 55,  80),
        "bar_bg":    (70, 12,  20),
        "msg":       (255, 185, 195),
        "label":     (220, 145, 158),
        "center_glow": (200, 30, 55, 35),
    },
    {   # Twilight
        "bg_top":    (8,  8,   32),
        "bg_bot":    (32, 18,  72),
        "orb_a":     (110, 70, 195, 55),
        "orb_b":     (185, 65, 145, 38),
        "orb_c":     (160, 120, 240, 30),
        "heart":     (190, 90,  215),
        "sparkle":   (200, 175, 255, 200),
        "name":      (228, 210, 255),
        "glow":      (175, 95,  255),
        "pct":       (220, 200, 255),
        "pct_glow":  (155, 80,  240),
        "bar_fill":  (150, 85,  235),
        "bar_bg":    (25, 12,  55),
        "msg":       (195, 165, 255),
        "label":     (165, 135, 225),
        "center_glow": (110, 55, 200, 35),
    },
    {   # Cherry Blossom
        "bg_top":    (40, 16,  32),
        "bg_bot":    (90, 36,  62),
        "orb_a":     (255, 150, 185, 55),
        "orb_b":     (210, 105, 150, 38),
        "orb_c":     (255, 195, 215, 30),
        "heart":     (255, 120, 165),
        "sparkle":   (255, 210, 225, 200),
        "name":      (255, 235, 245),
        "glow":      (255, 155, 195),
        "pct":       (255, 225, 238),
        "pct_glow":  (255, 130, 175),
        "bar_fill":  (255, 130, 172),
        "bar_bg":    (82, 33,  58),
        "msg":       (255, 195, 215),
        "label":     (222, 162, 185),
        "center_glow": (210, 80, 135, 35),
    },
    {   # Midnight Orchid
        "bg_top":    (12, 6,   28),
        "bg_bot":    (45, 18,  68),
        "orb_a":     (125, 55, 190, 55),
        "orb_b":     (185, 55, 165, 38),
        "orb_c":     (155, 105, 230, 30),
        "heart":     (200, 80,  210),
        "sparkle":   (210, 185, 255, 200),
        "name":      (232, 215, 255),
        "glow":      (185, 90,  230),
        "pct":       (222, 200, 250),
        "pct_glow":  (170, 75,  225),
        "bar_fill":  (165, 75,  220),
        "bar_bg":    (35, 12,  55),
        "msg":       (200, 165, 248),
        "label":     (168, 135, 215),
        "center_glow": (130, 45, 185, 35),
    },
]

# Romantic message per score range
_MSG: list[tuple[int, str]] = [
    (20,  "💔  Not quite written in the stars..."),
    (40,  "🤔  There's a spark, but it's faint."),
    (60,  "💛  A beautiful friendship blossoms."),
    (80,  "💜  The stars are clearly aligned!"),
    (101, "💕  A love story for the ages!"),
]


# ── Geometry helpers ──────────────────────────────────────────────────

def _heart_pts(cx: float, cy: float, size: float, n: int = 72) -> list[tuple[float, float]]:
    """Parametric heart: x=16sin³t  y=-(13cosT-5cos2t-2cos3t-cos4t)."""
    scale = size / 17.0
    pts   = []
    for i in range(n):
        t = 2 * math.pi * i / n
        x = scale * 16 * math.sin(t) ** 3
        y = -scale * (
            13 * math.cos(t)
            - 5  * math.cos(2 * t)
            - 2  * math.cos(3 * t)
            -      math.cos(4 * t)
        )
        pts.append((cx + x, cy + y))
    return pts


def _sparkle_pts(cx: float, cy: float, size: float, angle_off: float = 0.0) -> list[tuple[float, float]]:
    """4-pointed star sparkle."""
    pts = []
    for i in range(8):
        a = math.pi * i / 4 + angle_off
        r = size if i % 2 == 0 else size * 0.28
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


# ── Layer helpers ─────────────────────────────────────────────────────

def _gradient_bg(p: dict) -> Image.Image:
    """Vertical linear gradient from bg_top to bg_bot."""
    img = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)
    r0, g0, b0 = p["bg_top"]
    r1, g1, b1 = p["bg_bot"]
    for y in range(H):
        t = y / (H - 1)
        r = int(r0 + (r1 - r0) * t)
        g = int(g0 + (g1 - g0) * t)
        b = int(b0 + (b1 - b0) * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b))
    return img.convert("RGBA")


def _add_orbs(base: Image.Image, p: dict, rng: random.Random) -> Image.Image:
    """Large soft bokeh orbs for depth."""
    for orb_key, configs in [
        ("orb_a", [(rng.randint(50, 350),  rng.randint(50, 250),  rng.randint(280, 380))]),
        ("orb_b", [(rng.randint(500, 850), rng.randint(200, 400), rng.randint(240, 340))]),
        ("orb_c", [(rng.randint(200, 700), rng.randint(80,  350), rng.randint(180, 280))]),
    ]:
        col = p[orb_key]  # RGBA
        for (cx, cy, r) in configs:
            layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ld    = ImageDraw.Draw(layer)
            ld.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)
            layer = layer.filter(ImageFilter.GaussianBlur(radius=r // 2))
            base  = Image.alpha_composite(base, layer)
    return base


def _add_center_glow(base: Image.Image, p: dict) -> Image.Image:
    """Soft radial glow behind the text centre."""
    cx, cy = W // 2, H // 2 - 20
    r      = 260
    col    = p["center_glow"]
    layer  = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld     = ImageDraw.Draw(layer)
    ld.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)
    layer = layer.filter(ImageFilter.GaussianBlur(radius=90))
    return Image.alpha_composite(base, layer)


def _add_floating_hearts(base: Image.Image, p: dict, rng: random.Random) -> Image.Image:
    """Small hearts scattered across the canvas."""
    heart_col = (*p["heart"], 60)   # low alpha → subtle
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld    = ImageDraw.Draw(layer)
    positions = [
        (rng.randint(30,  200), rng.randint(30,  460), rng.randint(10, 22)),
        (rng.randint(700, 880), rng.randint(30,  460), rng.randint(10, 22)),
        (rng.randint(30,  200), rng.randint(30,  460), rng.randint(6,  14)),
        (rng.randint(700, 880), rng.randint(30,  460), rng.randint(6,  14)),
        (rng.randint(100, 800), rng.randint(380, 480), rng.randint(7,  16)),
        (rng.randint(100, 800), rng.randint(30,  100), rng.randint(7,  16)),
        (rng.randint(250, 650), rng.randint(30,  80),  rng.randint(5,  10)),
        (rng.randint(250, 650), rng.randint(410, 480), rng.randint(5,  10)),
    ]
    for (hx, hy, hs) in positions:
        pts = _heart_pts(hx, hy, hs)
        if len(pts) >= 3:
            ld.polygon(pts, fill=heart_col)
    layer = layer.filter(ImageFilter.GaussianBlur(radius=1))
    return Image.alpha_composite(base, layer)


def _add_sparkles(base: Image.Image, p: dict, rng: random.Random) -> Image.Image:
    """Small star sparkles scattered around the canvas."""
    col   = p["sparkle"]
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld    = ImageDraw.Draw(layer)
    spots = [
        (rng.randint(40,  180),  rng.randint(40,  460), rng.randint(4, 9)),
        (rng.randint(720, 860),  rng.randint(40,  460), rng.randint(4, 9)),
        (rng.randint(150, 750),  rng.randint(30,  80),  rng.randint(3, 7)),
        (rng.randint(150, 750),  rng.randint(400, 470), rng.randint(3, 7)),
        (rng.randint(40,  860),  rng.randint(40,  460), rng.randint(2, 5)),
        (rng.randint(40,  860),  rng.randint(40,  460), rng.randint(2, 5)),
        (rng.randint(40,  860),  rng.randint(40,  460), rng.randint(5, 11)),
        (rng.randint(40,  860),  rng.randint(40,  460), rng.randint(2, 5)),
    ]
    for (sx, sy, ss) in spots:
        pts = _sparkle_pts(sx, sy, ss, angle_off=rng.uniform(0, math.pi / 4))
        if len(pts) >= 3:
            ld.polygon(pts, fill=col)
    return Image.alpha_composite(base, layer)


def _glow_text(
    base: Image.Image,
    text: str,
    xy: tuple[int, int],
    font: ImageFont.FreeTypeFont,
    text_col: tuple[int, int, int],
    glow_col: tuple[int, int, int],
    glow_passes: list[tuple[int, int]],  # [(radius, alpha), ...]
    anchor: str = "mm",
) -> Image.Image:
    """
    Draw text with multi-pass Gaussian glow then crisp text on top.
    `glow_passes` = [(blur_radius, glow_alpha), ...] from largest to smallest.
    """
    for radius, alpha in glow_passes:
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ld    = ImageDraw.Draw(layer)
        ld.text(xy, text, font=font, fill=(*glow_col, alpha), anchor=anchor)
        layer = layer.filter(ImageFilter.GaussianBlur(radius=radius))
        base  = Image.alpha_composite(base, layer)

    # Crisp text on top
    ld = ImageDraw.Draw(base)
    ld.text(xy, text, font=font, fill=(*text_col, 255), anchor=anchor)
    return base


def _add_vignette(base: Image.Image) -> Image.Image:
    """Darken the edges to focus attention on the centre."""
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld    = ImageDraw.Draw(layer)
    steps = 14
    for i in range(steps):
        t  = i / steps
        a  = int(t * t * 155)        # quadratic falloff → gentle centre
        mx = int(t * W / 2)
        my = int(t * H / 2)
        ld.rectangle([mx, my, W - mx, H - my],
                     fill=(0, 0, 0, 0),
                     outline=(0, 0, 0, a),
                     width=4)
    layer = layer.filter(ImageFilter.GaussianBlur(radius=18))
    return Image.alpha_composite(base, layer)


def _fit_name_font(name1: str, name2: str, max_w: int) -> tuple[ImageFont.FreeTypeFont, int]:
    """Scale Pacifico down until 'Name1  ♥  Name2' fits within max_w."""
    combined = f"{name1}  ♥  {name2}"
    for size in range(60, 18, -2):
        f   = _font(_PACIFICO, size)
        tmp = Image.new("RGBA", (1, 1))
        td  = ImageDraw.Draw(tmp)
        bb  = td.textbbox((0, 0), combined, font=f, anchor="lt")
        if bb[2] - bb[0] <= max_w:
            return f, size
    return _font(_PACIFICO, 18), 18


def _bar_color_at(t: float, fill: tuple, bg: tuple) -> tuple:
    """Interpolate fill→bg across the bar for a gradient effect."""
    return tuple(int(fill[i] + (bg[i] - fill[i]) * (1 - t)) for i in range(3))


# ── Public API ────────────────────────────────────────────────────────

def generate(
    name1: str,
    name2: str,
    pct: int,
    seed: int | None = None,
) -> bytes:
    """
    Generate a 900×500 romantic ship banner and return it as PNG bytes.

    Parameters
    ----------
    name1 : display name of the first user
    name2 : display name of the second user
    pct   : compatibility percentage (0–100)
    seed  : optional RNG seed (for reproducibility in tests)
    """
    rng = random.Random(seed)
    p   = rng.choice(_PALETTES)

    # ── Layer 1: gradient background ─────────────────────────────────
    img = _gradient_bg(p)

    # ── Layer 2: bokeh orbs ───────────────────────────────────────────
    img = _add_orbs(img, p, rng)

    # ── Layer 3: centre glow ──────────────────────────────────────────
    img = _add_center_glow(img, p)

    # ── Layer 4: floating hearts ──────────────────────────────────────
    img = _add_floating_hearts(img, p, rng)

    # ── Layer 5: sparkles ─────────────────────────────────────────────
    img = _add_sparkles(img, p, rng)

    # ── Layer 6: heart divider decoration (above name line) ───────────
    deco_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dd         = ImageDraw.Draw(deco_layer)
    for hx, hy, hs, ha in [
        (W // 2 - 120, 115, 8,  120),
        (W // 2,       108, 11, 160),
        (W // 2 + 120, 115, 8,  120),
    ]:
        pts = _heart_pts(hx, hy, hs)
        if len(pts) >= 3:
            dd.polygon(pts, fill=(*p["heart"], ha))
    img = Image.alpha_composite(img, deco_layer)

    # ── Layer 7: name line — "Name1  ♥  Name2" ───────────────────────
    name_font, _name_sz = _fit_name_font(name1, name2, W - 80)
    name_cy = 178

    # Glowing heart symbol (separately so it can be in accent colour)
    heart_font = _font(_PACIFICO, _name_sz)
    heart_x    = W // 2
    heart_y    = name_cy

    # Glow for the entire name line together
    full_name = f"{name1}  ♥  {name2}"
    img = _glow_text(
        img, full_name, (W // 2, name_cy), name_font,
        text_col  = p["name"],
        glow_col  = p["glow"],
        glow_passes = [(24, 200), (14, 160), (7, 120), (3, 80)],
    )

    # ── Layer 8: thin decorative rule ─────────────────────────────────
    rule_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    rd = ImageDraw.Draw(rule_layer)
    ry = 220
    for rx, rw, ra in [(W // 2 - 200, 160, 120), (W // 2 + 40, 160, 120)]:
        rd.rectangle([rx, ry - 1, rx + rw, ry + 1], fill=(*p["heart"], ra))
    # Small diamond centre
    dm = [(W // 2, ry - 5), (W // 2 + 6, ry), (W // 2, ry + 5), (W // 2 - 6, ry)]
    rd.polygon(dm, fill=(*p["heart"], 200))
    rule_layer = rule_layer.filter(ImageFilter.GaussianBlur(radius=1))
    img = Image.alpha_composite(img, rule_layer)

    # ── Layer 9: big percentage number ────────────────────────────────
    pct_font = _font(_NUNITO_BD, 92)
    pct_text = f"{pct}%"
    img = _glow_text(
        img, pct_text, (W // 2, 298), pct_font,
        text_col    = p["pct"],
        glow_col    = p["pct_glow"],
        glow_passes = [(28, 220), (16, 170), (8, 120), (3, 70)],
    )

    # ── Layer 10: "Compatible" sub-label ──────────────────────────────
    lbl_font = _font(_NUNITO_SB, 22)
    img = _glow_text(
        img, "Compatible", (W // 2, 358), lbl_font,
        text_col    = p["label"],
        glow_col    = p["pct_glow"],
        glow_passes = [(10, 160), (5, 100)],
    )

    # ── Layer 11: progress bar ────────────────────────────────────────
    bar_w, bar_h = 480, 10
    bar_x = (W - bar_w) // 2
    bar_y = 388
    bar_r = bar_h // 2   # corner radius

    bar_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bar_layer)

    # Background track
    bd.rounded_rectangle(
        [bar_x, bar_y, bar_x + bar_w, bar_y + bar_h],
        radius=bar_r, fill=(*p["bar_bg"], 180),
    )
    # Filled portion
    fill_w = max(bar_r * 2, int(bar_w * pct / 100))
    for bx in range(fill_w):
        t   = bx / fill_w
        col = _bar_color_at(t, p["bar_fill"], tuple(c + 30 for c in p["bar_fill"]))
        bd.line(
            [(bar_x + bx, bar_y + 1), (bar_x + bx, bar_y + bar_h - 1)],
            fill=(*col, 220),
        )
    bd.rounded_rectangle(
        [bar_x, bar_y, bar_x + fill_w, bar_y + bar_h],
        radius=bar_r, fill=None, outline=(*p["bar_fill"], 120), width=1,
    )
    # Bar glow
    bar_layer = bar_layer.filter(ImageFilter.GaussianBlur(radius=2))
    img = Image.alpha_composite(img, bar_layer)

    # ── Layer 12: romantic message ────────────────────────────────────
    msg_text = next(m for (threshold, m) in _MSG if pct < threshold)
    msg_font = _font(_NUNITO_SB, 20)
    img = _glow_text(
        img, msg_text, (W // 2, 430), msg_font,
        text_col    = p["msg"],
        glow_col    = p["pct_glow"],
        glow_passes = [(8, 140), (4, 90)],
    )

    # ── Layer 13: large decorative hearts lower corners ───────────────
    corner_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cd = ImageDraw.Draw(corner_layer)
    for hx, hy, hs, ha in [
        (55,      460, 28, 55),
        (W - 55,  460, 28, 55),
        (55,      50,  18, 40),
        (W - 55,  50,  18, 40),
    ]:
        pts = _heart_pts(hx, hy, hs)
        if len(pts) >= 3:
            cd.polygon(pts, fill=(*p["heart"], ha))
    corner_layer = corner_layer.filter(ImageFilter.GaussianBlur(radius=2))
    img = Image.alpha_composite(img, corner_layer)

    # ── Layer 14: vignette ────────────────────────────────────────────
    img = _add_vignette(img)

    # ── Serialise to PNG bytes ────────────────────────────────────────
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=False)
    buf.seek(0)
    return buf.read()
