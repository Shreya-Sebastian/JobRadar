"""Write data/commute.tsv: estimated train travel times between the job cities, for the commute filter.

A development tool, run again when a city is added (radar/normalize.py NL_CITIES). The estimate is a straight line
between the main stations, turned into minutes with a fit on real NS intercity times (KNOWN below, door-free:
station to station, no waiting); where a real time is known, it is used as it is. Within one city the time is 15
minutes (local transport). Good to about a quarter of an hour, which is what a "within 45 minutes" filter needs;
NS timetable data (its API needs a key) can replace the estimate later without changing the file's format.

    python scripts/commute_matrix.py
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from radar.normalize import NL_CITIES  # noqa: E402

# the main railway station of each job city (latitude, longitude)
STATIONS = {
    "Amsterdam": (52.379, 4.900), "Rotterdam": (51.925, 4.469), "Utrecht": (52.089, 5.110),
    "The Hague": (52.080, 4.325),
    "Eindhoven": (51.443, 5.481), "Groningen": (53.211, 6.564), "Tilburg": (51.561, 5.083), "Almere": (52.375, 5.218),
    "Breda": (51.596, 4.780), "Nijmegen": (51.843, 5.853), "Apeldoorn": (52.209, 5.970), "Arnhem": (51.985, 5.899),
    "Haarlem": (52.388, 4.638), "Enschede": (52.222, 6.890), "Amersfoort": (52.153, 5.374), "Zwolle": (52.505, 6.091),
    "Leiden": (52.166, 4.482), "Maastricht": (50.850, 5.705), "Dordrecht": (51.808, 4.668), "Delft": (52.007, 4.357),
    "Alkmaar": (52.638, 4.740), "Leeuwarden": (53.196, 5.793), "'s-Hertogenbosch": (51.690, 5.294),
    "Hilversum": (52.226, 5.181), "Deventer": (52.257, 6.161), "Ede": (52.028, 5.668), "Lelystad": (52.512, 5.475),
    "Gouda": (52.017, 4.705), "Venlo": (51.364, 6.171), "Heerlen": (50.891, 5.975), "Sittard": (50.998, 5.860),
    "Roermond": (51.193, 5.993), "Oss": (51.766, 5.528), "Emmen": (52.787, 6.894), "Assen": (52.994, 6.565),
    "Middelburg": (51.495, 3.617), "Weert": (51.249, 5.705), "Veenendaal": (52.029, 5.556),
    "Wageningen": (51.970, 5.665), "Barneveld": (52.140, 5.585), "Woerden": (52.085, 4.891),
    "Culemborg": (51.955, 5.227), "Bussum": (52.283, 5.150), "Naarden": (52.283, 5.150),
    "Nieuw-Vennep": (52.264, 4.634),
}
# no station of its own: add the bus or bike from the nearest one
NO_STATION = {"Wageningen": 15}
# real NS intercity times, station to station (minutes)
KNOWN = {
    ("Amsterdam", "Utrecht"): 27, ("Amsterdam", "Rotterdam"): 41, ("Amsterdam", "The Hague"): 50,
    ("Amsterdam", "Eindhoven"): 80, ("Amsterdam", "Groningen"): 120, ("Amsterdam", "Zwolle"): 70,
    ("Amsterdam", "Arnhem"): 65, ("Amsterdam", "Leiden"): 35, ("Amsterdam", "Haarlem"): 15,
    ("Amsterdam", "Almere"): 22, ("Amsterdam", "Amersfoort"): 35, ("Amsterdam", "Maastricht"): 150,
    ("Utrecht", "Rotterdam"): 37, ("Utrecht", "Eindhoven"): 50, ("Utrecht", "The Hague"): 40,
    ("Utrecht", "Arnhem"): 37, ("Utrecht", "Zwolle"): 50, ("Utrecht", "Amersfoort"): 15,
    ("Rotterdam", "The Hague"): 25, ("Rotterdam", "Delft"): 12, ("Rotterdam", "Breda"): 25,
    ("Rotterdam", "Dordrecht"): 15, ("Eindhoven", "Tilburg"): 20, ("Eindhoven", "'s-Hertogenbosch"): 20,
    ("Eindhoven", "Maastricht"): 65, ("The Hague", "Leiden"): 12, ("The Hague", "Delft"): 10,
    # with a change, or on slower lines
    ("Amsterdam", "Enschede"): 125, ("Rotterdam", "Maastricht"): 140, ("Leiden", "Utrecht"): 45,
    ("Delft", "Amsterdam"): 55, ("Utrecht", "Groningen"): 115, ("Amsterdam", "Nijmegen"): 75,
    ("Utrecht", "Nijmegen"): 60, ("Rotterdam", "Eindhoven"): 70, ("The Hague", "Eindhoven"): 90,
    ("Amsterdam", "Alkmaar"): 35, ("Amsterdam", "Hilversum"): 25, ("Utrecht", "'s-Hertogenbosch"): 30,
    ("Amsterdam", "Apeldoorn"): 65, ("Groningen", "Leeuwarden"): 35, ("Zwolle", "Groningen"): 55,
}
BOARD = 8  # getting on: the fixed part of every trip
FLOOR = 10  # two places never closer than this
LOCAL = 15  # within one city


def km(a: tuple[float, float], b: tuple[float, float]) -> float:
    (la1, lo1), (la2, lo2) = (map(math.radians, a)), (map(math.radians, b))
    return 6371 * 2 * math.asin(math.sqrt(math.sin((la2 - la1) / 2) ** 2
                                          + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2))


def fit() -> tuple[float, float]:
    """Least squares of minutes = BOARD + b * kilometres over the known times."""
    xs = [km(STATIONS[x], STATIONS[y]) for x, y in KNOWN]
    ys = [t - BOARD for t in KNOWN.values()]
    return BOARD, sum(x * y for x, y in zip(xs, ys, strict=True)) / sum(x * x for x in xs)


def minutes(x: str, y: str, a: float, b: float) -> int:
    if x == y:
        return LOCAL
    known = KNOWN.get((x, y)) or KNOWN.get((y, x))
    base = known if known is not None else max(FLOOR, a + b * km(STATIONS[x], STATIONS[y]))
    return round(base + NO_STATION.get(x, 0) + NO_STATION.get(y, 0))


def main() -> None:
    missing = sorted(set(NL_CITIES) - set(STATIONS))
    if missing:
        sys.exit(f"no station for: {', '.join(missing)}")
    a, b = fit()
    errors = [abs(a + b * km(STATIONS[x], STATIONS[y]) - t) for (x, y), t in KNOWN.items()]
    cities = sorted(STATIONS)
    lines = [f"# estimated train minutes between job cities (scripts/commute_matrix.py): {a:.1f} + {b:.3f} x km,",
             f"# real NS times where known; mean error of the estimate on those: {sum(errors) / len(errors):.0f} min",
             "from\tto\tminutes"]
    lines += [f"{x}\t{y}\t{minutes(x, y, a, b)}" for x in cities for y in cities]
    (ROOT / "data" / "commute.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"fit {a:.1f} + {b:.3f}/km, mean error {sum(errors) / len(errors):.1f} min, max {max(errors):.0f} min; "
          f"{len(cities)} cities")


if __name__ == "__main__":
    main()
