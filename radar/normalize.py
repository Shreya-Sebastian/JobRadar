"""Turn RawPostings into canonical fields: city, country, remote flag, dedup key, content hash."""

from __future__ import annotations

import hashlib
import re

from radar.adapters.base import RawPosting

NL_CITIES: dict[str, str] = {
    # canonical name -> regex alternatives (lowercase). Order matters for "den haag".
    "Amsterdam": r"amsterdam|amstelveen|schiphol|hoofddorp|haarlemmermeer",
    "Rotterdam": r"rotterdam|schiedam|capelle",
    "The Hague": r"den haag|the hague|'s-gravenhage|s-gravenhage|rijswijk|zoetermeer|voorburg",
    "Utrecht": r"utrecht|nieuwegein|houten|zeist|de meern",
    "Eindhoven": r"eindhoven|veldhoven|best\b|son en breugel|waalre|helmond|nuenen|brainport",
    "Delft": r"\bdelft\b",
    "Leiden": r"\bleiden\b|oegstgeest",
    "Groningen": r"groningen",
    "Nijmegen": r"nijmegen",
    "Tilburg": r"tilburg",
    "Breda": r"\bbreda\b",
    "Arnhem": r"arnhem",
    "Haarlem": r"\bhaarlem\b",
    "Hilversum": r"hilversum",
    "Amersfoort": r"amersfoort",
    "Apeldoorn": r"apeldoorn",
    "Enschede": r"enschede|hengelo",
    "Maastricht": r"maastricht",
    "Zwolle": r"zwolle",
    "'s-Hertogenbosch": r"hertogenbosch|den bosch",
    "Almere": r"almere",
    "Deventer": r"deventer",
    "Wageningen": r"wageningen",
    "Alkmaar": r"alkmaar",
    "Leeuwarden": r"leeuwarden",
    "Venlo": r"\bvenlo\b",
    "Dordrecht": r"dordrecht",
    "Gouda": r"\bgouda\b",
    "Ede": r"\bede\b",
    "Veenendaal": r"veenendaal",
    "Emmen": r"\bemmen\b",
    "Roermond": r"roermond",
    "Heerlen": r"heerlen",
    "Sittard": r"sittard|geleen",
    "Assen": r"\bassen\b",
    "Lelystad": r"lelystad",
    "Middelburg": r"middelburg",
    "Weert": r"\bweert\b",
    "Oss": r"\boss\b",
    "Nieuw-Vennep": r"nieuw-vennep",
    "Woerden": r"woerden",
    "Barneveld": r"barneveld",
    "Culemborg": r"culemborg",
    "Naarden": r"naarden",
    "Bussum": r"bussum",
}
_CITY_RX = {city: re.compile(rx, re.I) for city, rx in NL_CITIES.items()}
_NL_RX = re.compile(r"netherlands|nederland|\bnl\b|holland", re.I)
_REMOTE_RX = re.compile(r"\bremote\b|work from home|thuiswerken|anywhere", re.I)
# places abroad in a location text -> ISO country code; the cities are the ones boards actually list
_PLACES = {
    "DE": r"germany|deutschland|berlin|munich|münchen|hamburg|frankfurt|cologne|köln|düsseldorf|dusseldorf|"
          r"stuttgart|münster|aachen|eschborn|hannover|hanover|dresden|leipzig|bremen|essen|dortmund|nuremberg|nürnberg",
    "BE": r"belgium|belgi[eë]|belgique|brussels|bruxelles|brussel|antwerp|antwerpen|ghent|gent|leuven|li[eè]ge|"
          r"mechelen|hasselt",
    "FR": r"france|paris|lyon|toulouse|marseille|grenoble|lille|nantes|sophia antipolis",
    "GB": r"united kingdom|\buk\b|england|scotland|london|manchester|cambridge|oxford|edinburgh|bristol|belfast",
    "IE": r"ireland|dublin|cork",
    "LU": r"luxembourg",
    "ES": r"spain|españa|madrid|barcelona|valencia|malaga|málaga",
    "PT": r"portugal|lisbon|lisboa|porto",
    "IT": r"italy|italia|milan|milano|rome|roma|turin|torino",
    "CH": r"switzerland|zurich|z[üu]rich|geneva|gen[eè]ve|basel|lausanne",
    "AT": r"austria|vienna|wien|graz",
    "PL": r"poland|warsaw|warszawa|krak[oó]w|wroc[lł]aw|gda[nń]sk|pozna[nń]",
    "CZ": r"czech|czechia|prague|praha|brno",
    "SK": r"slovakia|bratislava",
    "HU": r"hungary|budapest",
    "RO": r"romania|bucharest|cluj",
    "BG": r"bulgaria|sofia",
    "GR": r"greece|athens",
    "HR": r"croatia|zagreb",
    "SI": r"slovenia|ljubljana",
    "RS": r"serbia|belgrade|novi sad",
    "BA": r"bosnia|sarajevo",
    "UA": r"ukraine|kyiv|kiev|lviv",
    "LT": r"lithuania|vilnius",
    "LV": r"latvia|riga",
    "EE": r"estonia|tallinn",
    "DK": r"denmark|copenhagen|aarhus",
    "SE": r"sweden|stockholm|gothenburg|g[öo]teborg|malm[öo]",
    "NO": r"norway|oslo",
    "FI": r"finland|helsinki",
    "MT": r"malta",
    "CY": r"cyprus|limassol|nicosia",
    "TR": r"turkey|türkiye|istanbul|ankara",
    "IL": r"israel|tel aviv",
    "AE": r"united arab emirates|dubai|abu dhabi",
    "US": r"\busa\b|united states|new york|san francisco|seattle|austin|boston|chicago|los angeles|denver|atlanta",
    "CA": r"canada|toronto|vancouver|montreal|montréal",
    "MX": r"mexico|m[ée]xico",
    "BR": r"brazil|brasil|s[ãa]o paulo",
    "AR": r"argentina|buenos aires",
    "CO": r"colombia|bogot[áa]",
    "CL": r"chile|santiago",
    "PE": r"peru|lima",
    "IN": r"india|bangalore|bengaluru|hyderabad|pune|chennai|mumbai|delhi|noida|gurgaon|gurugram|kolkata|kondapur",
    "SG": r"singapore",
    "MY": r"malaysia|kuala lumpur",
    "TH": r"thailand|bangkok",
    "VN": r"vietnam|ho chi minh|hanoi",
    "ID": r"indonesia|jakarta",
    "PH": r"philippines|manila|cebu",
    "CN": r"china|shanghai|beijing|shenzhen",
    "HK": r"hong kong",
    "JP": r"japan|tokyo",
    "KR": r"korea|seoul",
    "AU": r"australia|sydney|melbourne",
    "NZ": r"new zealand|auckland",
    "ZA": r"south africa|cape town|johannesburg",
    "NG": r"nigeria|lagos",
    "KE": r"kenya|nairobi",
    "EG": r"egypt|cairo",
}
_PLACE_RX = [(code, re.compile(r"\b(?:" + rx + r")\b", re.I)) for code, rx in _PLACES.items()]
# Europe (EU, EEA, UK, Switzerland and the Western Balkans): a board's own "Netherlands" is not second-guessed here
EUROPE = {"NL", "DE", "BE", "FR", "GB", "IE", "LU", "ES", "PT", "IT", "CH", "AT", "PL", "CZ", "SK", "HU", "RO", "BG",
          "GR", "HR", "SI", "RS", "BA", "UA", "LT", "LV", "EE", "DK", "SE", "NO", "FI", "MT", "CY"}


def place_country(text: str | None) -> str | None:
    """The country of the first place abroad named in a location text, as an ISO code, or None."""
    if not text:
        return None
    hits = [(m.start(), code) for code, rx in _PLACE_RX if (m := rx.search(text))]
    return min(hits)[1] if hits else None


def detect_city(location: str | None) -> str | None:
    if not location:
        return None
    for city, rx in _CITY_RX.items():
        if rx.search(location):
            return city
    return None


_CITY_HINT = re.compile(
    r"(?:standplaats|werklocatie|locatie|location|based in|office in|kantoor in|gevestigd in|located in|"
    r"vestiging|onze locatie|our office|werken in|working in|work in)\W{0,25}([A-Z][\w' -]{2,30})",
    re.I,
)


_GENERIC_LOCATION = re.compile(
    r"^[\s,;()-]*(the\s+)?(netherlands|nederland|holland|nl|remote|hybrid|thuis\w*)?[\s,;()-]*$", re.I
)


def is_generic_location(location: str | None) -> bool:
    """True when the board names no specific place ("Netherlands", "NL", empty). A town we do not recognise
    (Heerhugowaard) is still a specific place: guessing a city from the text would be wrong."""
    return not location or bool(_GENERIC_LOCATION.match(location.strip()))


_NOT_A_PLACE = re.compile(
    r"\d|locations?$|remote|hybrid|netherlands|nederland|holland|europe|benelux|^nl$|office|\bemea\b|\bdach\b|"
    r"\beu\b|worldwide|global|"
    r"kantoor|thuis|home|multiple|various|diverse|flexible|anywhere",
    re.I,
)


def plausible_place(value: str | None) -> str | None:
    """The board's own city text when it looks like a place name we simply do not have in the canonical list
    (Harderwijk, Veghel, Zaandam). Returned title-cased, or None."""
    if not value:
        return None
    v = re.split(r"[,;(/|]", value)[0].strip(" -")
    if not 2 <= len(v) <= 40 or _NOT_A_PLACE.search(v) or not re.fullmatch(r"[A-Za-zÀ-ÿ' .-]+", v):
        return None
    words = v.split()
    return " ".join(
        w if w.lower() in ("aan", "de", "den", "der", "het", "op", "ter", "van", "en") else w[:1].upper() + w[1:]
        for w in words
    )


def city_from_text(title: str | None, description: str | None) -> str | None:
    """Fallback when the board gives no city: the title ("Data Engineer Utrecht"), then an explicit location
    phrase in the text ("standplaats Eindhoven", "based in Amsterdam"), then any Dutch city in the opening."""
    if title:
        found = detect_city(title)
        if found:
            return found
    text = description or ""
    for m in _CITY_HINT.finditer(text[:6000]):
        found = detect_city(m.group(1))
        if found:
            return found
    return detect_city(text[:600])


def detect_country(location: str | None, country: str | None) -> str | None:
    """The posting's country as an ISO code ('NL', 'DE', 'IN'), None if unknown. The board's own country field
    counts, except when the place it lists first is outside Europe and no Dutch city is named: vidaXL gives its
    Hyderabad office the country "Nederland"."""
    first = re.split(r"[;|]", location)[0] if location else ""
    abroad = place_country(first)
    if country:
        if abroad and abroad not in EUROPE and not detect_city(location):
            return abroad
        return country.upper()
    if not location:
        return None
    if detect_city(location) or _NL_RX.search(location):
        return "NL"
    return abroad or place_country(location)


def is_remote(location: str | None, remote_flag: bool | None) -> bool:
    if remote_flag:
        return True
    return bool(location and _REMOTE_RX.search(location))


_EMAIL_RX = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Dutch numbers: +31 6 12 34 56 78, +31 (0)20 123 4567, 0031..., 06-12345678, 020 1234567. A leading 0 or +31 and
# 9-10 digits in total with optional spaces, dashes, dots or a "(0)"; dates like 01-02-2024 have too few digits.
_PHONE_RX = re.compile(r"(?:\+31|0031|\b0)[\s\-.]?(?:\(0\)[\s\-.]?)?\d(?:[\s\-.]?\d){7,9}\b")


def scrub_contact(text: str) -> str:
    """Remove recruiters' e-mail addresses and phone numbers from posting text before it is stored.
    They are personal data we never display or use, so we do not keep them (GDPR data minimisation)."""
    if not text:
        return text
    text = _EMAIL_RX.sub("[e-mail]", text)
    return _PHONE_RX.sub("[telefoon]", text)


def norm_title(title: str) -> str:
    t = title.lower()
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", t)  # drop parentheticals such as "(m/f/d)" or "(Remote)"
    t = re.sub(r"\b(m/f/d|m/v/x|f/m/x|w/m/d|m/w/d|h/f|m/f|m/v)\b", " ", t)
    t = re.sub(r"[^a-z0-9+#.]+", " ", t)
    return " ".join(t.split())


_NAMES: dict[str, str] | None = None


def company_names() -> dict[str, str]:
    """Curated employer names (data/company_names.tsv): the name a board or careers site gave -> the organisation's
    own name, one per organisation."""
    global _NAMES
    if _NAMES is None:
        from pathlib import Path

        from radar.config import settings

        _NAMES = {}
        try:
            for line in (Path(settings.data_dir) / "company_names.tsv").read_text(encoding="utf-8").splitlines():
                if line.strip() and not line.startswith("#"):
                    parts = line.split("	")
                    if len(parts) >= 2 and parts[1].strip():
                        _NAMES[parts[0].strip()] = parts[1].strip()
        except FileNotFoundError:
            pass
    return _NAMES


def norm_company(company: str) -> str:
    c = company.strip()
    c = re.sub(r"\s+(b\.?v\.?|n\.?v\.?|inc\.?|ltd\.?|gmbh|holding)$", "", c, flags=re.I).strip()
    return company_names().get(c, c)


def dedup_key(company: str, title: str, city: str | None) -> str:
    key = f"{norm_company(company).lower()}|{norm_title(title)}|{(city or '').lower()}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def content_hash(title: str, text: str) -> str:
    return hashlib.sha256((title.strip() + "\n" + text.strip()).encode("utf-8")).hexdigest()


def _valid_through(raw: RawPosting):
    """schema.org validThrough as a naive UTC datetime, when the source gives one."""
    value = (raw.raw or {}).get("valid_through")
    if not value:
        return None
    from radar.adapters.base import parse_dt

    try:
        dt = parse_dt(value)
    except Exception:
        return None
    if dt is not None and dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt


def normalize(raw: RawPosting, company: str) -> dict:
    from radar.htmlclean import clean_html

    text = scrub_contact(raw.text())
    city = raw.city if raw.city in NL_CITIES else detect_city(raw.location) or detect_city(raw.city)
    country = detect_country(raw.location, raw.country)
    if city and not country:
        country = "NL"
    remote = is_remote(raw.location, raw.remote)
    if city is None and country == "NL" and not remote:
        # a town outside the canonical list: trust the board's city field, then the first part of the location
        city = plausible_place(raw.city) or plausible_place(raw.location)
    if city is None and country == "NL" and not remote and is_generic_location(raw.location):
        city = city_from_text(raw.title, text)
    if remote and country is None and raw.location and _NL_RX.search(raw.location):
        country = "NL"
    return {
        "external_id": raw.external_id,
        "title": raw.title.strip()[:500],
        "company": norm_company(company)[:200],
        "location_raw": (raw.location or "")[:500] or None,
        "city": city,
        "country": country,
        "remote": remote,
        "url": raw.url[:1000],
        "description": text,
        "description_html": clean_html(raw.description_html) if raw.description_html else "",
        "posted_at": _plausible_posted(raw.posted_at),
        "valid_through": _valid_through(raw),
        "content_hash": content_hash(raw.title, text),
        "dedup_key": dedup_key(company, raw.title, city),
    }


def _plausible_posted(posted):
    """A posting date more than two days in the future is a parsing error (an id read as a year), not a date."""
    from datetime import datetime, timedelta

    if posted is None:
        return None
    naive = posted.replace(tzinfo=None) if getattr(posted, "tzinfo", None) else posted
    return None if naive > datetime.utcnow() + timedelta(days=2) or naive.year < 2000 else posted
