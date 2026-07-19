"""Test unitari del Categorizer (senza modello ML: MCC + regole + default)."""

from libs.parsers.categorize import Categorizer, build_text


def _cat():
    return Categorizer.load()


class TestResolverChain:
    def test_mcc_wins(self):
        # 5411 = supermercati -> groceries, con source 'mcc'
        r = _cat().resolve("qualsiasi cosa", mcc="5411")
        assert r.code == "groceries" and r.source == "mcc"

    def test_rule_match(self):
        r = _cat().resolve("Netflix abbonamento mensile")
        assert r.code == "subscriptions" and r.source == "rule"

    def test_rule_bilingual(self):
        assert _cat().resolve("Restaurant downtown").code == "dining"
        assert _cat().resolve("Ristorante da Mario").code == "dining"

    def test_default_when_unknown(self):
        r = _cat().resolve("xyzzy merchant sconosciuto 123")
        assert r.code == "other" and r.source == "default"


class TestI18nLabels:
    def test_labels_it_en(self):
        c = _cat()
        assert c.label("dining", "it") == "Ristorazione"
        assert c.label("dining", "en") == "Dining"

    def test_languages_available(self):
        assert {"it", "en"} <= set(_cat().languages)


class TestBuildText:
    def test_normalizes(self):
        assert build_text("Caffè BAR!") == "caffe bar"
