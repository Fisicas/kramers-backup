"""
make_identity.py -- Kramers icon and splash artwork (original, no third-party marks).

    python -X utf8 tools/make_identity.py --sheet            # mock-up sheet of all concepts
    python -X utf8 tools/make_identity.py --concept A        # write the app assets for concept A

Assets written by --concept:
    kramers/resources/kramers.ico         16-256 px
    kramers/resources/kramers-256.png     window icon / README
    kramers/resources/splash.png          splash background (steps are drawn live by the app)
    docs/images/social-preview.png        GitHub social preview (1280x640)

Needs Pillow (tools only; the app itself does not).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from kramers import __version__  # noqa: E402

RES = REPO / "kramers" / "resources"
DOCS = REPO / "docs"
S = 1024                                     # master canvas
NAVY, TEAL = (23, 37, 84), (13, 148, 136)
WHITE, CYAN = (255, 255, 255), (125, 211, 252)
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]


def tile() -> Image.Image:
    """Rounded square with a navy -> teal diagonal gradient."""
    grad = Image.new("RGB", (S, S))
    px = grad.load()
    for y in range(S):
        for x in range(S):
            t = (x + y) / (2 * S)
            px[x, y] = tuple(int(NAVY[i] + (TEAL[i] - NAVY[i]) * t) for i in range(3))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((40, 40, S - 40, S - 40), radius=210, fill=255)
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(grad, (0, 0), mask)
    return out


def arrow(d: ImageDraw.ImageDraw, x: int, y0: int, y1: int, w: int, color, head: int) -> None:
    """Vertical arrow from y0 to y1 (head at y1)."""
    sgn = 1 if y1 > y0 else -1
    d.rectangle((x - w // 2, min(y0, y1 - sgn * head), x + w // 2, max(y0, y1 - sgn * head)), fill=color)
    d.polygon([(x - head * 0.75, y1 - sgn * head), (x + head * 0.75, y1 - sgn * head), (x, y1)], fill=color)


def folder(d: ImageDraw.ImageDraw, box, color) -> None:
    x0, y0, x1, y1 = box
    tab_w, tab_h = (x1 - x0) * 0.38, (y1 - y0) * 0.16
    d.rounded_rectangle((x0, y0, x0 + tab_w, y0 + tab_h * 2), radius=28, fill=color)
    d.rounded_rectangle((x0, y0 + tab_h, x1, y1), radius=40, fill=color)


# --- concepts -------------------------------------------------------------- #

def concept_a() -> Image.Image:
    """Kramers doublet: one energy level carrying a spin-up / spin-down pair."""
    im = tile()
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((190, 492, 834, 548), radius=28, fill=WHITE)
    arrow(d, 400, 800, 230, 70, WHITE, 150)
    arrow(d, 624, 230, 800, 70, CYAN, 150)
    return im


def concept_b() -> Image.Image:
    """Symmetric double-well potential with a particle resting in each well."""
    im = tile()
    d = ImageDraw.Draw(im)
    pts = []
    for i in range(201):
        u = -1.45 + 2.9 * i / 200
        v = (u * u - 1) ** 2
        pts.append((512 + u * 290, 760 - min(v, 1.35) * 400))
    d.line(pts, fill=WHITE, width=56, joint="curve")
    for x, y in pts:
        d.ellipse((x - 28, y - 28, x + 28, y + 28), fill=WHITE)
    for x, c in ((222, WHITE), (802, CYAN)):
        d.ellipse((x - 78, 760 - 78 - 120, x + 78, 760 + 78 - 120), fill=c)
    return im


def concept_c() -> Image.Image:
    """A folder and its mirror image across a dashed mirror plane."""
    im = tile()
    d = ImageDraw.Draw(im)
    folder(d, (230, 170, 794, 470), WHITE)
    reflection = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    folder(ImageDraw.Draw(reflection), (230, 170, 794, 470), CYAN + (170,))
    reflection = reflection.transpose(Image.FLIP_TOP_BOTTOM).crop((0, 0, S, S))
    im.alpha_composite(reflection, (0, 0))
    for x in range(170, 860, 90):
        d.rounded_rectangle((x, 498, x + 52, 526), radius=12, fill=WHITE)
    return im


def concept_d() -> Image.Image:
    """Backup folder carrying the Kramers pair."""
    im = tile()
    d = ImageDraw.Draw(im)
    folder(d, (180, 230, 844, 800), WHITE)
    arrow(d, 420, 730, 380, 64, NAVY, 130)
    arrow(d, 604, 380, 730, 64, TEAL, 130)
    return im


CONCEPTS = {"A": ("Kramers doublet", concept_a), "B": ("Double well", concept_b),
            "C": ("Mirror plane", concept_c), "D": ("Folder + pair", concept_d)}


def sized(master: Image.Image, px: int) -> Image.Image:
    return master.resize((px, px), Image.LANCZOS)


def font(size: int, bold: bool = False):
    for name in (("segoeuib.ttf" if bold else "segoeui.ttf"), "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


# --- outputs --------------------------------------------------------------- #

def sheet(path: Path) -> None:
    W, H = 1400, 330 * len(CONCEPTS) + 60
    out = Image.new("RGB", (W, H), (245, 245, 245))
    d = ImageDraw.Draw(out)
    d.rectangle((700, 0, W, H), fill=(32, 32, 32))
    d.text((20, 15), "Kramers icon concepts — 256 / 32 / 16 px on light and dark", fill=(40, 40, 40),
           font=font(24, True))
    for i, (key, (title, fn)) in enumerate(CONCEPTS.items()):
        m = fn()
        y = 60 + i * 330
        for side, x0, fg in ((0, 20, (30, 30, 30)), (1, 720, (230, 230, 230))):
            d.text((x0, y), f"{key}  {title}", fill=fg, font=font(26, True))
            out.paste(sized(m, 256), (x0, y + 40), sized(m, 256))
            for j, px in enumerate((32, 16)):
                ic = sized(m, px)
                out.paste(ic, (x0 + 300 + j * 80, y + 160), ic)
                big = ic.resize((px * 4, px * 4), Image.NEAREST)        # zoomed pixels
                out.paste(big, (x0 + 470 + j * 0, y + 40 + j * 140), big)
    path.parent.mkdir(parents=True, exist_ok=True)
    out.save(path)
    print("wrote", path)


def splash(master: Image.Image, path: Path) -> None:
    W, H = 640, 360
    bg = Image.new("RGBA", (W, H), (17, 24, 39, 255))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((-120, -60, 360, 420), fill=TEAL + (90,))
    bg.alpha_composite(glow.filter(ImageFilter.GaussianBlur(70)))
    # faint degenerate-level pairs on the right edge
    ladder = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(ladder)
    for k, y in enumerate((40, 78, 116)):
        ld.line((520 + 12 * k, y, 610, y), fill=(255, 255, 255, 40), width=2)
        ld.line((520 + 12 * k, y + 8, 610, y + 8), fill=CYAN + (40,), width=2)
    bg.alpha_composite(ladder)
    d = ImageDraw.Draw(bg)
    d.line((240, 240, 600, 240), fill=TEAL + (255,), width=2)
    bg.alpha_composite(sized(master, 160), (48, 70))
    d.text((236, 92), "Kramers", fill=WHITE, font=font(64, True))
    d.text((240, 172), "Every file gets its degenerate partner.", fill=CYAN, font=font(20))
    d.text((240, 202), f"Mirror backup  ·  v{__version__}", fill=(170, 180, 195), font=font(16))
    path.parent.mkdir(parents=True, exist_ok=True)
    bg.save(path)
    print("wrote", path)


def social(master: Image.Image, path: Path) -> None:
    W, H = 1280, 640
    bg = Image.new("RGBA", (W, H), (17, 24, 39, 255))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((-200, -100, 700, 760), fill=TEAL + (90,))
    bg.alpha_composite(glow.filter(ImageFilter.GaussianBlur(120)))
    bg.alpha_composite(sized(master, 320), (110, 160))
    d = ImageDraw.Draw(bg)
    d.text((500, 210), "Kramers", fill=WHITE, font=font(120, True))
    d.text((506, 360), "Every file gets its degenerate partner.", fill=CYAN, font=font(38))
    d.text((506, 420), "Safe one-way mirror backups for Windows", fill=(170, 180, 195), font=font(30))
    path.parent.mkdir(parents=True, exist_ok=True)
    bg.save(path)
    print("wrote", path)


def assets(key: str) -> None:
    master = CONCEPTS[key][1]()
    RES.mkdir(parents=True, exist_ok=True)
    sized(master, 256).save(RES / "kramers-256.png")
    sized(master, 64).save(RES / "kramers-64.png")
    master.save(RES / "kramers.ico", sizes=[(s, s) for s in ICO_SIZES])
    print("wrote", RES / "kramers.ico")
    splash(master, RES / "splash.png")
    social(master, DOCS / "images" / "social-preview.png")
    (RES / "CONCEPT").write_text(key + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--sheet", action="store_true")
    ap.add_argument("--concept", choices=sorted(CONCEPTS))
    a = ap.parse_args()
    if a.sheet:
        sheet(DOCS / "identity" / "icon-concepts.png")
    if a.concept:
        assets(a.concept)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
