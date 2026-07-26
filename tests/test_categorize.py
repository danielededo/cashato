"""Categorizer unit tests (no ML model: MCC + rules + default)."""

from cashato.parsers.categorize import Categorizer, build_text


def _cat():
    return Categorizer.load()


class TestResolverChain:
    def test_mcc_wins(self):
        # 5411 = supermarkets -> groceries, with source 'mcc'
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

    def test_mcc_range_lookup(self):
        # 3246 (an airline: brand-specific block 3000-3299) is not an exact
        # entry — the "3000-3299" range in mcc.yaml must catch it. Seen in
        # real card data miscategorized as groceries by the model.
        r = _cat().resolve("qualcosa", mcc="3246")
        assert r.code == "travel" and r.source == "mcc"

    def test_mcc_exact_beats_range(self):
        c = Categorizer(
            {"categories": {}}, mcc_map={"3000-3999": "travel", "3246": "other"}
        )
        assert c.mcc_category("3246") == "other"
        assert c.mcc_category("3247") == "travel"
        assert c.mcc_category("4000") is None

    def test_every_mcc_category_has_labels(self):
        # Every code mcc.yaml can emit must be renderable: an unlabeled code
        # would surface raw in the UI (labels come from /meta).
        c = _cat()
        targets = set(c.mcc_map.values()) | {cat for _, _, cat in c.mcc_ranges}
        assert targets <= set(c.categories)


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


class TestWealthDestinations:
    """Wealth is not just securities. Destinations are not consumption —
    except protection policies, which are."""

    def test_pension_fund_contribution(self):
        assert _cat().resolve("Bonifico fondo pensione Cometa").code == "pension_fund"
        assert _cat().resolve("Piano individuale pensionistico").code == "pension_fund"

    def test_pension_received_is_still_income(self):
        # the salary rule contains "pension": without explicit precedence it
        # would swallow "fondo pensione" (the first matching rule wins)
        assert _cat().resolve("Pensione INPS accredito").code == "salary"

    def test_deposits_both_legs(self):
        assert _cat().resolve("Versamento conto deposito vincolato").code == "deposits"
        assert _cat().resolve("Svincolo deposito").code == "deposits"

    def test_insurance_defaults_to_expense_not_wealth(self):
        # a pure-risk policy IS consumption: filing it as wealth would erase
        # a real expense from the savings rate
        assert _cat().resolve("Premio polizza RC auto").code == "insurance"


class TestDetectionSpecificity:
    """Intesa's markers must identify Intesa, not Italian banking in general.

    Scoring now prevents the alphabetical-order accident, but a generic marker
    is still wrong on its own terms: it makes another bank's file *ambiguous*
    instead of unrecognised, which is a worse answer than not matching at all.
    """

    def test_generic_italian_banking_words_do_not_match_intesa(self):
        from cashato.parsers import intesa

        def matches(text: str) -> bool:
            t = text.lower()
            return any(all(m in t for m in g) for g in intesa.DETECTION)

        # An ING quarterly says exactly this; a Hype movements table has that
        # column. Both used to be routed to the Intesa parser, which then found
        # no table and returned 0 rows without an error.
        assert not matches("Estratto conto trimestrale al 30/06/2026")
        assert not matches("LISTA MOVIMENTI\nData Contabile  Descrizione  Importo")
        assert not matches("Data operazione  Importo (EUR)")

    def test_real_intesa_markers_still_match(self):
        from cashato.parsers import intesa

        def matches(text: str) -> bool:
            t = text.lower()
            return any(all(m in t for m in g) for g in intesa.DETECTION)

        # quarterly: the page-1 footer names the bank. NB it is the app name
        # that matches — the domain "intesasanpaolo.com" has no space, so it
        # does NOT satisfy the "intesa sanpaolo" marker on its own.
        assert matches("Cell: 333 App Intesa Sanpaolo Mobile Orari:")
        assert not matches("www.intesasanpaolo.com")
        # 13-month export (PDF and XLSX): filter-recap header of the web export
        assert matches("Conti e Carte: Conto 1000 / 00014132")


class TestDetectionIsNotOrderDependent:
    """The outcome must not depend on the modules' alphabetical order."""

    def _detect(self, monkeypatch, text, signatures):
        from cashato.parsers import detect

        monkeypatch.setattr(detect, "head_text", lambda _p: text)
        monkeypatch.setattr(detect, "detection_signatures", lambda: signatures)
        return detect.detect_source("x.pdf"), detect.detect_candidates("x.pdf")

    def test_more_specific_match_wins_regardless_of_order(self, monkeypatch):
        # generic "banca" vs a two-marker group: the second wins, even though
        # the generic source comes first alphabetically
        sigs = [("aaa_generic", [["banca"]]), ("zzz_specific", [["banca", "conto zzz"]])]
        src, _ = self._detect(monkeypatch, "banca … conto zzz", sigs)
        assert src == "zzz_specific"
        # and reversing the discovery order does not change the result
        src, _ = self._detect(monkeypatch, "banca … conto zzz", list(reversed(sigs)))
        assert src == "zzz_specific"

    def test_equally_specific_matches_are_ambiguous_not_a_coin_flip(self, monkeypatch):
        # whoever sorted first used to win, silently: the file went to the
        # wrong parser, which found no tables and returned 0 rows
        sigs = [("aaa", [["estratto conto"]]), ("bbb", [["estratto conto"]])]
        src, cands = self._detect(monkeypatch, "estratto conto trimestrale", sigs)
        assert src is None
        assert [c[0] for c in cands] == ["aaa", "bbb"]

    def test_single_match_still_resolves(self, monkeypatch):
        sigs = [("aaa", [["pippo"]]), ("bbb", [["estratto conto"]])]
        src, _ = self._detect(monkeypatch, "estratto conto trimestrale", sigs)
        assert src == "bbb"
