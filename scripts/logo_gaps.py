"""Find logos for the biggest employers that still have none, for review before they go in data/logos.

A development tool. Input: a JSON list from production of the top employers, each {"company", "jobs", "status",
"pages"} (the employer's own careers pages and board addresses), made with:

    kubectl -n radar exec deploy/radar-radar-api -- python -m radar.cli logo-gaps --top 500 > top.json

For each employer without a usable logo it finds the official website (data/company_domains.tsv first, then a
domain built from the name that is accepted only when its homepage names the employer, radar/sponsors.py), reads
the sharpest logo there with the site's own lookup (radar/logos.py), and falls back to the site icon Google serves
for search results. Each find is saved as data/logos/<slug>.png (or .svg), noted in data/logos/SOURCES.tsv, and
drawn on a contact sheet (a PNG) to check by eye: delete a wrong file, or put a better one in its place.

    python scripts/logo_gaps.py top.json --sheet sheet.png
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar import logos, sponsors  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "data" / "logos"


def google_icon(domain: str, client) -> tuple[str, bytes, bool] | None:
    """The site icon Google shows next to the site in its results, at up to 256 px."""
    got = logos._read(f"https://www.google.com/s2/favicons?domain={domain}&sz=256", client, logos.MAX_BYTES + 1)
    if not got or got[0] != 200:
        return None
    return logos.tidy(logos.sniff(got[2]) or got[1], got[2])


def site_of(entry: dict) -> str | None:
    """An own website among the employer's pages (not a tracking system's)."""
    for page in entry.get("pages") or []:
        host = urlparse(page).netloc
        if host and not logos.ATS_HOSTS.search(host):
            return host
    return None


def find(entry: dict, domains: dict[str, str], client) -> tuple[str, str, tuple] | None:
    """(domain, where the image came from, tidied image) or None."""
    company = entry["company"]
    domain = domains.get(company) or site_of(entry) or sponsors.resolve(company, client)
    if not domain:
        return None
    got = logos.find_logo(f"https://{domain}/", client)
    if got and logos.quality(got[1:]) >= 100:
        return domain, got[0], got[1:]
    icon = google_icon(domain, client)
    if icon and (not got or logos.quality(icon) > logos.quality(got[1:])):
        return domain, f"google-s2:{domain}", icon
    return (domain, got[0], got[1:]) if got else None


def sheet(rows: list[tuple[str, str, bytes, bool]], path: Path) -> None:
    """A contact sheet: each logo as the card shows it (on white, or on dark when it needs it), with its name."""
    from PIL import Image, ImageDraw

    cols, cell, label = 6, 150, 34
    lines = (len(rows) + cols - 1) // cols
    canvas = Image.new("RGB", (cols * cell, max(1, lines) * (cell + label)), (240, 242, 245))
    draw = ImageDraw.Draw(canvas)
    for i, (company, ctype, data, dark) in enumerate(rows):
        x, y = (i % cols) * cell, (i // cols) * (cell + label)
        draw.rounded_rectangle((x + 15, y + 10, x + cell - 15, y + cell - 10), 14,
                               fill=(15, 23, 42) if dark else (255, 255, 255), outline=(200, 205, 215))
        if ctype != "image/svg+xml":
            im = Image.open(io.BytesIO(data)).convert("RGBA")
            im.thumbnail((cell - 46, cell - 46))
            canvas.paste(im, (x + (cell - im.width) // 2, y + (cell - im.height) // 2), im)
        else:
            draw.text((x + 50, y + 65), "SVG", fill=(120, 120, 120))
        draw.text((x + 6, y + cell + 2), company[:24], fill=(20, 20, 20))
    canvas.save(path)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("top", type=Path)
    ap.add_argument("--sheet", type=Path, default=Path("logo_sheet.png"))
    args = ap.parse_args()
    entries = json.loads(args.top.read_text(encoding="utf-8"))
    OUT.mkdir(exist_ok=True)
    have = logos.manual_files()
    domains = logos.known_domains()
    client = logos._client()
    found, missing, shown = [], [], []
    sources = OUT / "SOURCES.tsv"
    lines = sources.read_text(encoding="utf-8").splitlines() if sources.exists() else ["# slug\tcompany\tdomain\tfrom"]
    for e in entries:
        slug = logos.slug_of(e["company"])
        if e.get("status") == "ok" or slug in have:
            continue
        try:
            hit = find(e, domains, client)
        except Exception as exc:  # one odd site must not stop the run
            print("error", e["company"], exc, file=sys.stderr)
            hit = None
        if not hit:
            missing.append(e["company"])
            print("none ", e["jobs"], e["company"])
            continue
        domain, origin, (ctype, data, dark) = hit
        ext = ".svg" if ctype == "image/svg+xml" else ".png"
        (OUT / f"{slug}{ext}").write_bytes(data)
        lines.append(f"{slug}\t{e['company']}\t{domain}\t{origin}")
        found.append(e["company"])
        shown.append((e["company"], ctype, data, dark))
        print("found", e["jobs"], e["company"], domain, origin[:80])
    sources.write_text("\n".join(lines) + "\n", encoding="utf-8")
    sheet(shown, args.sheet)
    print(f"found {len(found)}, still missing {len(missing)}; contact sheet: {args.sheet}")
    if missing:
        print("missing:", "; ".join(missing))


if __name__ == "__main__":
    main()
