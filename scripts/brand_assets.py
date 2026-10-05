"""Draw the site's icons and share card from the logo (web/favicon.svg): web/favicon.ico (16, 32, 48 px),
web/icon-512.png, web/apple-touch-icon.png (180 px) and web/og-card.png (1200 x 630, for link previews).

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


def logo(size: int, small: bool = False) -> Image.Image:
    """The logo of web/favicon.svg (a 32-unit square), drawn at `size` pixels with 4x supersampling. `small` is the
    16-pixel cut: no faint outer ring, a brighter ring and thicker strokes, so it stays legible in a browser tab."""
    s = size * 4
    u = s / 32
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=(7 if small else 9) * u, fill=ACCENT)
    w = max(1, round((2.4 if small else 2) * u))
    for r, alpha in (((8.5, 0.85),) if small else ((10, 0.35), (5.5, 0.6))):
        ring = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        ImageDraw.Draw(ring).ellipse((16 * u - r * u, 16 * u - r * u, 16 * u + r * u, 16 * u + r * u),
                                     outline=WHITE + (round(255 * alpha),), width=w)
        img = Image.alpha_composite(img, ring)
    d = ImageDraw.Draw(img)
    if small:  # a thicker needle: at 16 px it is the shape that makes the ring read as a radar, not a target
        w = round(3.4 * u)
    d.line((16 * u, 16 * u, 23 * u, 9 * u), fill=WHITE, width=w)
    for cx, cy in ((23, 9), (16, 16)):
        d.ellipse((cx * u - w / 2, cy * u - w / 2, cx * u + w / 2, cy * u + w / 2), fill=WHITE)  # round line caps
    dot = 2.2 if small else 1.8
    for cx, cy in (((16, 16),) if small else ((16, 16), (10.5, 20))):  # no blip at 16 px: it merges with the ring
        d.ellipse((cx * u - dot * u, cy * u - dot * u, cx * u + dot * u, cy * u + dot * u), fill=WHITE)
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
    d.rectangle((0, 0, 1200, 12), fill=ACCENT)
    img.paste(logo(150), (90, 150), logo(150))
    d.text((270, 160), "Tech Jobs Radar", font=font(True, 76), fill=INK)
    d.text((272, 258), "Straight from Dutch employers", font=font(False, 40), fill=INK)
    d.text((92, 410), "Read from the employers' own career sites, updated daily.", font=font(False, 34), fill=MUTED)
    d.text((92, 460), "Software, data, AI, cloud and security. Filter on English-speaking,", font=font(False, 34),
           fill=MUTED)
    d.text((92, 506), "visa sponsorship, junior roles and city.", font=font(False, 34), fill=MUTED)
    d.text((92, 568), "techjobsradar.nl", font=font(True, 30), fill=ACCENT)
    return img


def main() -> None:
    # one hand-made image per size: Pillow would otherwise shrink the 48-pixel one for 16
    logo(48).save(WEB / "favicon.ico", format="ICO", sizes=[(48, 48), (32, 32), (16, 16)],
                  append_images=[logo(32), logo(16, small=True)])
    logo(32).save(WEB / "favicon-32.png")
    logo(16, small=True).save(WEB / "favicon-16.png")
    logo(512).save(WEB / "icon-512.png")
    apple = Image.new("RGB", (180, 180), ACCENT)  # iOS draws its own rounded corners on a full square
    apple.paste(logo(180), (0, 0), logo(180))
    apple.save(WEB / "apple-touch-icon.png")
    card().save(WEB / "og-card.png", optimize=True)
    for name in ("favicon.ico", "favicon-16.png", "favicon-32.png", "icon-512.png", "apple-touch-icon.png",
                 "og-card.png"):
        print(name, (WEB / name).stat().st_size, "bytes")


if __name__ == "__main__":
    sys.exit(main())
