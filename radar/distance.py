"""Distance between job cities, for the distance filter: a straight line between city centres, in kilometres.

A visitor picks a city, never an address, and the choice stays in their browser.
"""

from __future__ import annotations

import math

# the centre of each job city (radar/normalize.py NL_CITIES): latitude, longitude
CENTRES: dict[str, tuple[float, float]] = {
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


def km(origin: str | None, city: str | None) -> int | None:
    """Kilometres in a straight line from `origin` to `city`, or None when either is unknown."""
    a, b = CENTRES.get(origin or ""), CENTRES.get(city or "")
    if a is None or b is None:
        return None
    (la1, lo1), (la2, lo2) = map(math.radians, a), map(math.radians, b)
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return round(6371 * 2 * math.asin(math.sqrt(h)))


def origins() -> list[str]:
    return sorted(CENTRES)


def from_city(origin: str) -> dict[str, int]:
    """Every job city with its distance from `origin` (empty for an unknown city)."""
    if origin not in CENTRES:
        return {}
    return {c: km(origin, c) for c in CENTRES}
