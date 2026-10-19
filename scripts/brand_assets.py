"""Draw the site's logo (three narrowing layers) and icons: web/favicon.svg, web/favicon.ico (16, 32, 48 px),
web/favicon-16.png, web/favicon-32.png, web/icon-512.png, web/apple-touch-icon.png (180 px) and web/og-card.png
(1200 x 630, for link previews). `--header` prints the logo's markup for the page headers in web/*.html.

A development tool, not a runtime dependency: `pip install pillow`, then `python scripts/brand_assets.py` from the
repository root after changing the logo or the colours. Fonts: Segoe UI on Windows, DejaVu Sans elsewhere."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WEB = Path(__file__).resolve().parent.parent / "web"
ACCENT = (4, 120, 87)  # --accent, light theme
INK = (15, 23, 42)
MUTED = (100, 116, 139)
BG = (248, 250, 252)
WHITE = (255, 255, 255)

# the logo: three layers seen from above, each narrower than the one below, in a near-black tile. The jobs are the
# wide teal layers at the bottom; filtering narrows them to the small orange layer on top, the jobs that fit
TILE = "#020617"  # near black
LAYERS = (  # half width (the half height is half of it) and centre height, in the logo's 32-unit square
    (9.7, "#0f766e"),  # all jobs: deep teal
    (7.0, "#14b8a6"),  # filtered: teal
    (4.3, "#fb923c"),  # the jobs that fit: orange
)
STEP = 4.6  # centre to centre, upwards
ROUND = 0.55  # corner rounding (half the stroke drawn in each layer's own colour)
GAP = 0.75  # the dark line that separates a layer from the one beneath it


def layers() -> list[tuple[list[tuple[float, float]], str]]:
    """(corners, colour) of each layer, bottom first, centred in the 32-unit square."""
    centres = [i * -STEP for i in range(len(LAYERS))]
    top = centres[-1] - LAYERS[-1][0] / 2
    bottom = centres[0] + LAYERS[0][0] / 2
    shift = 16 - (top + bottom) / 2
    out = []
    for (hw, colour), cy in zip(LAYERS, centres, strict=True):
        y = cy + shift
        out.append(([(16 - hw, y), (16, y - hw / 2), (16 + hw, y), (16, y + hw / 2)], colour))
    return out


def _d(corners: list[tuple[float, float]]) -> str:
    return "M" + "L".join(f"{x:.2f} {y:.2f}" for x, y in corners) + "Z"


def svg(tile: str = f'fill="{TILE}"') -> str:
    """The logo as SVG (web/favicon.svg and the header of every page, which passes its own tile attributes). Each
    layer is drawn twice: a wider tile-coloured outline that cuts the gap, then the layer with rounded corners."""
    shapes = "".join(
        f'<path d="{_d(c)}" fill="{TILE}" stroke="{TILE}" stroke-width="{2 * (ROUND + GAP):.2f}"/>'
        f'<path d="{_d(c)}" fill="{colour}" stroke="{colour}" stroke-width="{2 * ROUND:.2f}"/>'
        for c, colour in layers())
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect x=".5" y=".5" width="31" height="31" '
            f'rx="8" {tile}/><g stroke-linejoin="round">{shapes}</g></svg>')


def logo(size: int, square: bool = False) -> Image.Image:
    """The logo drawn at `size` pixels with 4x supersampling; `square` fills the corners (iOS rounds them)."""
    s = size * 4
    u = s / 32
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if square:
        d.rectangle((0, 0, s, s), fill=TILE)
    else:
        d.rounded_rectangle((0, 0, s - 1, s - 1), radius=8 * u, fill=TILE)

    def shape(corners, colour, half):  # a polygon whose outline is `half` wide on each side, with round corners
        pts = [(x * u, y * u) for x, y in corners]
        d.polygon(pts, fill=colour)
        d.line(pts + pts[:1], fill=colour, width=round(2 * half * u), joint="curve")
        for cx, cy in pts:
            d.ellipse((cx - half * u, cy - half * u, cx + half * u, cy + half * u), fill=colour)

    for corners, colour in layers():
        shape(corners, TILE, ROUND + GAP)
        shape(corners, colour, ROUND)
    return img.resize((size, size), Image.LANCZOS)


def font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    names = (["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"] if bold
             else ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"])
    for name in names:
        for folder in (Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/dejavu")):
            if (folder / name).exists():
                return ImageFont.truetype(str(folder / name), size)
    return ImageFont.load_default()


def card() -> Image.Image:
    img = Image.new("RGB", (1200, 630), BG)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 1200, 12), fill=TILE)
    img.paste(logo(150), (90, 150), logo(150))
    d.text((270, 160), "Tech Jobs Radar", font=font(True, 76), fill=INK)
    d.text((272, 258), "IT jobs in the Netherlands, filtered by what they ask", font=font(False, 36), fill=INK)
    d.text((92, 410), "Dutch or English, visa sponsorship, years of experience,", font=font(False, 34), fill=MUTED)
    d.text((92, 458), "degree, position, company size and city.", font=font(False, 34), fill=MUTED)
    d.text((92, 506), "Straight from employers' own career sites, updated daily.", font=font(False, 34), fill=MUTED)
    d.text((92, 568), "techjobsradar.nl", font=font(True, 30), fill=ACCENT)
    return img


def main() -> None:
    if "--header" in sys.argv:
        print(svg(f'fill="{TILE}" stroke="var(--logo-edge, {TILE})"').replace(
            '<svg xmlns="http://www.w3.org/2000/svg" ', '<svg class="logo" aria-hidden="true" '))
        return
    (WEB / "favicon.svg").write_text(svg() + chr(10), encoding="utf-8")
    # one hand-made image per size: Pillow would otherwise shrink the 48-pixel one for 16
    logo(48).save(WEB / "favicon.ico", format="ICO", sizes=[(48, 48), (32, 32), (16, 16)],
                  append_images=[logo(32), logo(16)])
    logo(32).save(WEB / "favicon-32.png")
    logo(16).save(WEB / "favicon-16.png")
    logo(512).save(WEB / "icon-512.png")
    logo(180, square=True).convert("RGB").save(WEB / "apple-touch-icon.png")  # iOS rounds the corners itself
    card().save(WEB / "og-card.png", optimize=True)
    for name in ("favicon.ico", "favicon-16.png", "favicon-32.png", "icon-512.png", "apple-touch-icon.png",
                 "og-card.png"):
        print(name, (WEB / name).stat().st_size, "bytes")


if __name__ == "__main__":
    sys.exit(main())
