"""Render assets/architecture.png.

Pillow, drawn at 2x and downsampled. Dark card with light text so it reads on
both the GitHub light and dark themes.

Run:  python assets/make_architecture.py
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

S = 2
W, H = 980 * S, 620 * S
OUT = Path(__file__).with_name("architecture.png")

BG, FG, MUTED, LINE = (13, 17, 23), (201, 209, 217), (139, 148, 158), (110, 118, 129)
ACCENT, GREEN, AMBER = (188, 140, 255), (63, 185, 80), (210, 153, 34)
FONTS = r"C:\Windows\Fonts"


def font(n, s):
    return ImageFont.truetype(f"{FONTS}\\{n}", s * S)


f_title, f_head = font("seguisb.ttf", 15), font("seguisb.ttf", 12)
f_small, f_lbl = font("segoeui.ttf", 10), font("segoeuii.ttf", 9)

img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)


def box(x, y, w, h, c=LINE, width=2):
    d.rounded_rectangle([x * S, y * S, (x + w) * S, (y + h) * S],
                        radius=6 * S, outline=c, width=int(width * S))


def text(x, y, s, f=f_small, fill=MUTED, anchor="mm"):
    d.text((x * S, y * S), s, font=f, fill=fill, anchor=anchor)


def _head(p0, p1, c, size=6):
    (x0, y0), (x1, y1) = p0, p1
    dx, dy = x1 - x0, y1 - y0
    dist = max((dx * dx + dy * dy) ** .5, 1e-6)
    ux, uy = dx / dist, dy / dist
    px, py = -uy, ux
    s = size * S
    d.polygon([(x1, y1),
               (x1 - ux * s + px * s * .5, y1 - uy * s + py * s * .5),
               (x1 - ux * s - px * s * .5, y1 - uy * s - py * s * .5)], fill=c)


def arrow(pts, c=LINE, w=1.5):
    pts = [(x * S, y * S) for x, y in pts]
    for i in range(len(pts) - 1):
        d.line([pts[i], pts[i + 1]], fill=c, width=int(w * S))
    _head(pts[-2], pts[-1], c)


text(490, 26, "AI-Personal-Finance-Manager — receipts in, categorised spending and forecasts out",
     f_title, FG)

# ── inputs ──────────────────────────────────────────────────────────────────
box(30, 54, 280, 58)
text(170, 74, "Receipt upload", f_head, FG)
text(170, 93, "PDF or photo")

box(350, 54, 280, 58)
text(490, 74, "Transactions", f_head, FG)
text(490, 93, "synthetic / imported")

box(670, 54, 280, 58, GREEN)
text(810, 74, "JWT auth", f_head, FG)
text(810, 93, "every query scoped by user_id")

# ── extraction ──────────────────────────────────────────────────────────────
arrow([(170, 112), (170, 142)])
box(30, 144, 280, 76, ACCENT)
text(170, 165, "Extraction — rules_smart", f_head, FG)
text(170, 184, "keyword-anchored, 0.2 ms, $0")
text(170, 202, "amount 0.83 after GST fix")

# ── categorisation ──────────────────────────────────────────────────────────
arrow([(490, 112), (490, 142)])
box(350, 144, 280, 76, ACCENT)
text(490, 165, "Categoriser — TF-IDF + LinearSVC", f_head, FG)
text(490, 184, "10 classes, macro-F1 0.975 in-dist")
text(490, 202, "0.08 s to fit, $0 to run")

# ── LLM fallback ────────────────────────────────────────────────────────────
box(670, 144, 280, 76, AMBER)
text(810, 165, "LLM fallback — Claude", f_head, FG)
text(810, 184, "only on low confidence / novel merchants")
text(810, 202, "$0.14 per 1k · holds F1 1.00 on novel")

arrow([(630, 182), (666, 182)], AMBER)

# ── analytics ───────────────────────────────────────────────────────────────
arrow([(330, 220), (330, 252)])
box(30, 254, 920, 82)
text(52, 274, "Analytics over the categorised ledger", f_head, FG, anchor="lm")
cells = [
    (52, "Anomaly detection", "IsolationForest · AP 0.979 · P@20 0.95"),
    (285, "Recurring charges", "precision 0.988 · recall 1.00"),
    (518, "Cash-flow forecast", "Prophet · MAPE 15.8%"),
    (751, "Investment advice", "per-user risk profile"),
]
for x, t, s in cells:
    box(x, 292, 212, 34)
    text(x + 106, 302, t, f_small, FG)
    text(x + 106, 318, s, f_lbl)

# ── serving ─────────────────────────────────────────────────────────────────
arrow([(490, 336), (490, 366)])
box(30, 368, 450, 74, ACCENT)
text(255, 390, "FastAPI  :8000  — JWT-scoped", f_head, FG)
text(255, 409, "every endpoint filters by user_id")
text(255, 427, "cross-tenant reads 404 · p95 22.7 ms")

box(510, 368, 440, 74)
text(730, 390, "Flask UI  ·  Streamlit ops dashboard", f_head, FG)
text(730, 409, "MySQL ledger · Redis cache")
text(730, 427, "12/12 production checks passing")

# ── the finding ─────────────────────────────────────────────────────────────
box(30, 464, 920, 76, AMBER)
text(490, 486, "The finding that shaped the design", f_head, FG)
text(490, 505, "The $0 model ties the LLM on merchants it has seen (F1 1.00) but collapses to 0.24 on novel ones —")
text(490, 523, "below the 0.34 keyword floor. It fails silently and confidently, which is why the LLM fallback exists.")

text(30, 566, "All experiments use public SROIE receipts or synthetic transactions — no real financial data",
     f_lbl, GREEN, anchor="lm")
text(30, 586, "Every figure comes from a committed artifact (archived at commit b7ff703) · 122 tests",
     f_lbl, MUTED, anchor="lm")

img.resize((W // S, H // S), Image.LANCZOS).save(OUT, "PNG", optimize=True)
print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")
