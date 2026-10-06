"""Travel time between job cities by train, for the commute filter: data/commute.tsv (scripts/commute_matrix.py).

Times are station to station between city centres, an estimate good to about a quarter of an hour; a visitor
picks a city, never an address, and the choice stays in their browser.
"""

from __future__ import annotations

import threading
from pathlib import Path

from radar.config import settings

_matrix: dict[tuple[str, str], int] = {}
_lock = threading.Lock()


def matrix() -> dict[tuple[str, str], int]:
    with _lock:
        if not _matrix:
            path = Path(settings.data_dir) / "commute.tsv"
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines():
                    parts = line.split("\t")
                    if len(parts) == 3 and parts[2].isdigit():
                        _matrix[(parts[0], parts[1])] = int(parts[2])
        return _matrix


def minutes(origin: str | None, city: str | None) -> int | None:
    """Minutes by train from `origin` to `city`, or None when either is unknown."""
    if not origin or not city:
        return None
    return matrix().get((origin, city))


def origins() -> list[str]:
    return sorted({a for a, _ in matrix()})


def from_city(origin: str) -> dict[str, int]:
    """Every job city with its travel time from `origin`."""
    return {b: t for (a, b), t in matrix().items() if a == origin}
