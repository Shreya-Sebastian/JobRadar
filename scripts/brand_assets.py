"""Draw the site's logo and icons: web/favicon.svg, web/favicon.ico (16, 32, 48 px), web/favicon-16.png,
web/favicon-32.png, web/icon-512.png, web/apple-touch-icon.png (180 px) and web/og-card.png (1200 x 630, for link
previews). `--header` prints the logo's markup for the page headers in web/*.html.

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

# the logo: a pyramid of diamonds (4, 3, 2 and 1) in a night-blue tile. The other jobs are dim, one path of bright
# diamonds (cyan to green) climbs to the match, an orange diamond lifted off the top
TILE = "#0f172a"
DIM = ("#164e63", "#134e4a", "#064e3b")  # rows from the bottom
PATH = ("#22d3ee", "#2dd4bf", "#34d399")
MATCH = "#fb923c"
ON_PATH = (2, 1, 1)  # which diamond in each row lies on the path
HALF = 1.95  # half the diagonal of a diamond, before its corners are rounded by ROUND
ROUND = 0.65


def diamonds() -> list[tuple[float, float, str]]:
    """(x, y, colour) of each diamond's centre, in the logo's 32-unit square."""
    a = HALF + ROUND
    step = 2 * a + 0.9 * 2 ** 0.5  # centre to centre in a row; rows sit half a step apart
    rise, lift = step / 2, 1.8
    out = []
    for row in range(4):
        n = 4 - row
        y = 32 - (16 - 1.5 * rise + row * rise - lift / 2 + (lift if row == 3 else 0) + 0.6)
        for i in range(n):
            x = 16 - (n - 1) * step / 2 + i * step
            colour = MATCH if row == 3 else PATH[row] if ON_PATH[row] == i else DIM[row]
            out.append((round(x, 2), round(y, 2), colour))
    return out


def svg(tile: str = f'fill="{TILE}"') -> str:
    """The logo as SVG (web/favicon.svg and the header of every page, which passes its own tile attributes)."""
    shapes = "".join(
        f'<path d="M{x} {y - HALF:.2f}L{x + HALF:.2f} {y}L{x} {y + HALF:.2f}L{x - HALF:.2f} {y}Z" fill="{c}" '
        f'stroke="{c}"/>' for x, y, c in diamonds())
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect x=".5" y=".5" width="31" height="31" '
            f'rx="8" {tile}/><g stroke-width="{2 * ROUND}" stroke-linejoin="round">{shapes}</g></svg>')


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
    for x, y, c in diamonds():
        corners = [((x + dx) * u, (y + dy) * u) for dx, dy in ((0, -HALF), (HALF, 0), (0, HALF), (-HALF, 0))]
        d.polygon(corners, fill=c)
        d.line(corners + corners[:1], fill=c, width=round(2 * ROUND * u), joint="curve")
        for cx, cy in corners:  # round corners: the stroke's joins, drawn as discs
            d.ellipse((cx - ROUND * u, cy - ROUND * u, cx + ROUND * u, cy + ROUND * u), fill=c)
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
    d.text((270, 160), "Tech Jobs", font=font(True, 76), fill=INK)
    d.text((272, 258), "IT jobs in the Netherlands, filtered by what they ask", font=font(False, 36), fill=INK)
    d.text((92, 410), "Dutch or English, visa sponsorship, years of experience,", font=font(False, 34), fill=MUTED)
    d.text((92, 458), "degree, position, company size and city.", font=font(False, 34), fill=MUTED)
    d.text((92, 506), "Straight from employers' own career sites, updated daily.", font=font(False, 34), fill=MUTED)
    d.text((92, 568), "techjobsradar.nl", font=font(True, 30), fill=ACCENT)
    return img


def main() -> None:
    if "--header" in sys.argv:
        print(svg('fill="#0f172a" stroke="var(--logo-edge, #0f172a)"').replace(
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
