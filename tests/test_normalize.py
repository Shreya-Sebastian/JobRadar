from datetime import datetime

from radar.adapters.base import RawPosting, html_to_text, parse_dt
from radar.normalize import dedup_key, detect_city, detect_country, is_remote, norm_title, normalize


def test_detect_city_variants():
    assert detect_city("Amsterdam, Netherlands") == "Amsterdam"
    assert detect_city("Veldhoven") == "Eindhoven"
    assert detect_city("Den Haag") == "The Hague"
    assert detect_city("'s-Hertogenbosch, NL") == "'s-Hertogenbosch"
    assert detect_city("Berlin, Germany") is None


def test_detect_country():
    assert detect_country("Utrecht, the Netherlands", None) == "NL"
    assert detect_country("Remote - Netherlands", None) == "NL"
    assert detect_country("Berlin", None) == "DE"
    assert detect_country("Antwerpen, België", None) == "BE"
    assert detect_country("Somewhere", None) is None
    assert detect_country("Berlin", "nl") == "NL"  # in Europe the board's own country field counts
    # outside Europe the place wins over a wrong country field (vidaXL's Hyderabad office says "Nederland")
    assert detect_country("Hyderabad, Nederland, 1st Floor, A-Wing, Purva Summit, Kondapur", "NL") == "IN"
    assert detect_country("Amsterdam, Netherlands; Hyderabad, India", "NL") == "NL"
    assert detect_country("Germany; Portugal; France; The Netherlands", None) == "NL"
    # lists of countries that include the Netherlands stay; a Dutch province is Dutch
    assert detect_country("United States; Canada; United Kingdom; Germany; The Netherlands", "NL") == "NL"
    assert detect_country("Remote: United States | Canada | The Netherlands | Denmark", "NL") == "NL"
    assert detect_country("Japan, The Netherlands", "NL") == "NL"
    assert detect_country("Zuid-Holland, United States", "NL") == "NL"
    # one address abroad is abroad, whatever the country field says
    assert detect_country("Monterrey, NL 64640, Mexico", "NL") == "MX"
    assert detect_country("Salisbury, Nederland, 2085 Harrison Road 28147 Salisbury USA", "NL") == "US"
    assert detect_country("Lima, NL", "NL") == "PE"


def test_regions_are_not_cities():
    from radar.normalize import plausible_place

    assert plausible_place("EMEA") is None and plausible_place("DACH") is None and plausible_place("Veghel") == "Veghel"
    # a list of countries is not a city: the card showed "United Kingdom" for a role open in five countries incl. NL
    assert plausible_place("United Kingdom; Canada; Germany; United States; The Netherlands") is None
    assert plausible_place("Hauts-de-France") is None and plausible_place("Harderwijk") == "Harderwijk"
    # Dutch regions are not towns either
    assert plausible_place("Randstad") is None and plausible_place("Noord-Brabant, Nederland") is None
    assert plausible_place("Zuid Holland") is None and plausible_place("Zuid-Nederland") is None
    assert plausible_place("Oosterhout") == "Oosterhout" and plausible_place("Westland") == "Westland"


def test_a_place_abroad_given_only_as_the_city_is_abroad():
    # vidaXL lists some jobs with an empty location, the city "Hyderabad" and the country "Nederland"
    raw = RawPosting(external_id="1", title="QA Engineer", url="https://x/1", location=None, city="Hyderabad",
                     country="NL", description_text="Test our web shop.")
    n = normalize(raw, "vidaXL")
    assert n["country"] == "IN" and n["city"] == "Hyderabad"  # abroad, the board's own place (foreign_city)
    region = RawPosting(external_id="2", title="Data Engineer", url="https://x/2", location="Randstad", country="NL",
                        description_text="Build pipelines.")
    assert normalize(region, "Xebia")["city"] is None


def test_cities_abroad_as_the_board_names_them():
    from radar.normalize import foreign_city

    assert foreign_city("Berlin, Germany") == "Berlin"
    assert foreign_city("CZE - Brno") == "Brno" and foreign_city("MYS - Kuala Lumpur") == "Kuala Lumpur"
    assert foreign_city("US") is None and foreign_city("Germany") is None and foreign_city("Remote") is None


def test_remote_and_title_normalisation():
    assert is_remote("Remote (NL)", None)
    assert not is_remote("Amsterdam", None)
    assert norm_title("Senior Backend Engineer (m/f/d) - Python") == "senior backend engineer python"
    assert dedup_key("Adyen B.V.", "Backend Engineer", "Amsterdam") == dedup_key("Adyen", "backend engineer",
                                                                                 "amsterdam")


def test_html_to_text_handles_escaped_greenhouse_content():
    escaped = "&lt;p&gt;We use &lt;strong&gt;Python&lt;/strong&gt; and Kubernetes.&lt;/p&gt;&lt;ul&gt;&lt;li&gt;AWS&lt;/li&gt;&lt;/ul&gt;"
    text = html_to_text(escaped)
    assert "Python and Kubernetes." in text
    assert "AWS" in text
    assert "<" not in text


def test_parse_dt_epoch_and_iso():
    assert parse_dt(1700000000000) == datetime(2023, 11, 14, 22, 13, 20)
    assert parse_dt("2026-09-01T10:00:00Z").year == 2026
    assert parse_dt(None) is None
    assert parse_dt("not a date") is None


def test_city_from_text_fallback():
    from radar.normalize import city_from_text

    assert city_from_text("Data Engineer Utrecht", "") == "Utrecht"
    assert city_from_text("Data Engineer", "Standplaats: Eindhoven. Je werkt aan ...") == "Eindhoven"
    assert city_from_text("Data Engineer", "You will be based in our Amsterdam office.") == "Amsterdam"
    assert city_from_text("Data Engineer", "We are a company in Delft building robots.") == "Delft"
    assert city_from_text("Data Engineer", "No place named here.") is None
    raw = RawPosting(external_id="1", title="Developer", url="https://x/1", location="Netherlands",
                     description_text="Werklocatie: Groningen")
    assert normalize(raw, "Acme")["city"] == "Groningen"
    remote = RawPosting(external_id="2", title="Developer", url="https://x/2", location="Remote, Netherlands",
                        description_text="Our HQ is in Amsterdam")
    assert normalize(remote, "Acme")["city"] is None  # remote postings stay remote
    unknown_town = RawPosting(external_id="3", title="Developer", url="https://x/3",
                              location="Heerhugowaard, Noord-Holland, Nederland",
                              description_text="Our other office in Eindhoven is nice too.")
    assert normalize(unknown_town, "Acme")["city"] == "Heerhugowaard"  # the board's own town wins over text
    from radar.normalize import plausible_place

    assert plausible_place("Alphen aan den Rijn, Zuid-Holland, Nederland") == "Alphen aan den Rijn"
    assert plausible_place("2 Locations") is None and plausible_place("Netherlands") is None
    assert plausible_place("Remote - NL") is None and plausible_place("harderwijk") == "Harderwijk"


def test_normalize_builds_fields():
    raw = RawPosting(external_id="1", title=" Data Engineer ", url="https://x/y",
                     location="Rotterdam, NL", description_html="<p>SQL and Spark</p>")
    f = normalize(raw, "Acme BV")
    assert f["company"] == "Acme"
    assert f["city"] == "Rotterdam"
    assert f["country"] == "NL"
    assert f["title"] == "Data Engineer"
    assert "SQL and Spark" in f["description"]
    assert len(f["content_hash"]) == 64


def test_scrub_contact_removes_emails_and_dutch_phones_but_keeps_numbers_we_need():
    from radar.normalize import scrub_contact

    text = ("Questions? Mail jan.de.vries+jobs@example-corp.nl or call +31 (0)6 12 34 56 78, 06-12345678 or "
            "020 123 4567. Salary EUR 4.500 - 6.000 per month, 3-5 years experience, start 01-02-2027, "
            "founded in 2010, 40 hours, postcode 1012 AB, KvK 12345678.")
    out = scrub_contact(text)
    assert "@" not in out and "12 34 56 78" not in out and "06-12345678" not in out and "020 123 4567" not in out
    for keep in ("4.500 - 6.000", "3-5 years", "01-02-2027", "2010", "40 hours", "1012 AB", "KvK 12345678"):
        assert keep in out, keep
    assert out.count("[e-mail]") == 1 and out.count("[telefoon]") == 3



def test_an_empty_board_date_is_no_date():
    assert parse_dt(0) is None and parse_dt("1970-01-01T00:00:00Z") is None and parse_dt("0001-01-01") is None
    assert parse_dt(1760000000000).year == 2025 and parse_dt("2026-10-01").day == 1
