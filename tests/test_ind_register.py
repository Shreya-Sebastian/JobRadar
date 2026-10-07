from radar import sponsors
from radar.config import settings


def _register(tmp_path, monkeypatch, *names):
    (tmp_path / "seeds").mkdir()
    (tmp_path / "seeds" / "ind_sponsors.tsv").write_text(
        "# name\tkvk\n" + "".join(f"{n}\t1\n" for n in names), encoding="utf-8")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    sponsors._register.clear()
    sponsors._recognised.clear()


def test_employers_match_the_ind_register_by_name(tmp_path, monkeypatch):
    _register(tmp_path, monkeypatch, "Deloitte Consulting B.V.", "KPMG Advisory N.V.", "Adyen N.V.",
              "Open Universiteit", "ABN AMRO Bank N.V.")
    assert sponsors.recognised("Adyen")  # the same name without its legal form
    assert sponsors.recognised("Deloitte") and sponsors.recognised("KPMG") and sponsors.recognised("ABN AMRO")
    assert not sponsors.recognised("OPEN")  # an everyday word is not the leading word of someone else's name
    assert not sponsors.recognised("Acme") and not sponsors.recognised(None)
    sponsors._register.clear()
    sponsors._recognised.clear()


def test_the_visa_filter_keeps_only_listings_that_mention_sponsorship():
    from datetime import datetime

    from radar import stats

    now = datetime(2026, 10, 7)

    def row(n, company, visa):
        return stats.Row(id=n, title="Engineer", company=company, city="Amsterdam", remote=False, url=f"https://x/{n}",
                         posted_at=now, first_seen=now, closed_at=None, ats="lever",
                         ex={"visa_sponsorship": visa} if visa is not None else {})

    # Adyen is on the IND register, but this listing does not say it sponsors: silence is not sponsorship
    rows = [row(1, "Acme", True), row(2, "Adyen", None), row(3, "Acme", False)]
    assert [r.id for r in stats.Filters(sponsorship=True).apply(rows)] == [1]
