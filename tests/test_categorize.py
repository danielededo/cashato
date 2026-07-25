"""Test unitari del Categorizer (senza modello ML: MCC + regole + default)."""

from cashato.parsers.categorize import Categorizer, build_text


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


class TestWealthDestinations:
    """Patrimonio: non solo titoli. Le destinazioni non sono consumo, tranne
    le polizze di protezione, che lo sono."""

    def test_pension_fund_contribution(self):
        assert _cat().resolve("Bonifico fondo pensione Cometa").code == "pension_fund"
        assert _cat().resolve("Piano individuale pensionistico").code == "pension_fund"

    def test_pension_received_is_still_income(self):
        # la regola salary contiene "pension": senza precedenza esplicita
        # inghiottirebbe "fondo pensione" (vince la prima regola che matcha)
        assert _cat().resolve("Pensione INPS accredito").code == "salary"

    def test_deposits_both_legs(self):
        assert _cat().resolve("Versamento conto deposito vincolato").code == "deposits"
        assert _cat().resolve("Svincolo deposito").code == "deposits"

    def test_insurance_defaults_to_expense_not_wealth(self):
        # una polizza di puro rischio E' consumo: classificarla come patrimonio
        # cancellerebbe una spesa reale dal tasso di risparmio
        assert _cat().resolve("Premio polizza RC auto").code == "insurance"


class TestDetectionSpecificity:
    """Intesa's markers must identify Intesa, not Italian banking in general.

    Detection is first-match-wins in alphabetical registry order, so `intesa`
    is probed first and a generic marker silently steals other banks' files.
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
