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
