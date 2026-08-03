"""Response models for the query-api (typed OpenAPI schema + examples).

Pure data shapes: no route logic, no DB access. Money fields are Decimal end
to end (the project rule): pydantic serializes them as JSON STRINGS, and the
frontend's `Money` type + num() convert deliberately — a JSON number is an
IEEE754 double, which is what the rule exists to avoid.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from pydantic import BaseModel, Field


class CategoryTotal(BaseModel):
    category: str = Field(examples=["groceries"])
    category_label: str = Field(examples=["Groceries"])
    n_movements: int = Field(examples=[618])
    income: Decimal | None = Field(default=None, examples=[0.0])
    expense: Decimal | None = Field(default=None, examples=[-19019.12])
    net: Decimal | None = Field(examples=[-19019.12])


class SummaryResponse(BaseModel):
    lang: str = Field(examples=["en"])
    categories: list[CategoryTotal]


class MonthRow(BaseModel):
    month: date = Field(examples=["2025-01-01"])
    income: Decimal | None = None
    expense: Decimal | None = None
    net: Decimal | None = None


class MonthlyResponse(BaseModel):
    months: list[MonthRow]


class CategoryMonthRow(BaseModel):
    month: date
    category: str
    category_label: str
    n_movements: int
    total: Decimal | None = None


class CategoriesMonthlyResponse(BaseModel):
    lang: str
    rows: list[CategoryMonthRow]


class TransactionRow(BaseModel):
    id: int
    value_date: date
    booking_date: date
    description: str
    amount: Decimal
    currency: str
    account: str
    source: str
    category: str | None = None
    category_label: str
    category_source: str | None = None
    category_confidence: float | None = None
    transfer_group: str | None = None
    natural_key: str
    merchant: str | None = Field(
        default=None, description="Counterparty extracted from the description, when one exists"
    )
    purchase_time: time | None = Field(
        default=None, description="Time of day the statement text carries (POS/ATM operations)"
    )


class TransactionsResponse(BaseModel):
    lang: str
    total: int = Field(description="Total rows matching the filters (before paging)")
    sum_income: Decimal | None = Field(
        description="Sum of positive amounts over ALL matching rows, not just the page"
    )
    sum_expense: Decimal | None = Field(
        description="Sum of negative amounts over ALL matching rows (signed, so <= 0)"
    )
    sum_net: Decimal | None = Field(description="Sum of every matching amount")
    limit: int
    offset: int
    transactions: list[TransactionRow]


class MerchantRow(BaseModel):
    merchant: str = Field(description="Display name (most frequent casing in the group)")
    n_movements: int
    total_spent: Decimal = Field(description="Net outflow at this merchant (refunds netted), > 0")
    avg_spent: Decimal = Field(description="total_spent / n_movements")
    last_date: date
    category: str | None = Field(default=None, description="Dominant category in the group")
    category_label: str


class MerchantsResponse(BaseModel):
    lang: str
    n_merchants: int = Field(description="Distinct merchants matching the filters (before limit)")
    merchants: list[MerchantRow]


class TransferPair(BaseModel):
    transfer_group: str
    value_date: date = Field(description="Value date of the transfer legs")
    amount: Decimal = Field(description="Absolute transferred amount", examples=[400.0])
    from_account: str | None = Field(default=None, description="Debited account (negative leg)")
    to_account: str | None = Field(default=None, description="Credited account (positive leg)")


class TransfersResponse(BaseModel):
    n_pairs: int
    total_volume: Decimal = Field(description="Sum of transferred amounts (absolute)")
    transfers: list[TransferPair]


class ReconciliationInterval(BaseModel):
    """One span between two consecutive statement-declared balances of an
    account. ``discrepancy`` = parsed movements minus the balance delta the
    statement promises: 0 means the parser accounted for every cent."""

    account: str
    from_date: date
    to_date: date
    from_balance: Decimal
    to_balance: Decimal
    expected_delta: Decimal = Field(description="to_balance - from_balance")
    actual_delta: Decimal = Field(description="Sum of parsed movements in (from_date, to_date]")
    discrepancy: Decimal = Field(description="actual_delta - expected_delta; 0 = reconciled")
    n_movements: int


class ReconciliationResponse(BaseModel):
    n_intervals: int
    n_mismatched: int
    intervals: list[ReconciliationInterval]


class BalanceMonthRow(BaseModel):
    """One account's balance at one month's end, carried forward from the last
    statement-declared anchor. ``as_of`` is that anchor's date — the figure's
    age, not the month it is shown under."""

    month: date
    account: str
    currency: str
    balance: Decimal
    as_of: date


class AccountBalance(BaseModel):
    """The latest balance a statement declared for one account."""

    account: str
    currency: str
    balance: Decimal
    as_of: date


class WealthResponse(BaseModel):
    """Liquid wealth over time, from the statements' own balances.

    Only what a statement declared, carried forward — no market prices, no
    reconstruction from movements. Invested wealth lives in ``/investments``;
    the two are complementary, not overlapping.
    """

    months: list[BalanceMonthRow]
    accounts: list[AccountBalance]
    total_liquid: Decimal = Field(description="Sum of each account's latest declared balance.")
    oldest_as_of: date | None = Field(
        description="Age of the stalest figure inside total_liquid: the total is only "
        "as fresh as its oldest account."
    )


class RecurringItem(BaseModel):
    """One recurring relationship with a counterparty: a subscription, the
    salary, rent, a utility. Detected from rhythm, never from a merchant list."""

    description: str = Field(description="Richest description observed in the group.")
    category: str | None
    category_label: str
    accounts: list[str]
    cadence: str = Field(
        description="weekly | monthly | bimonthly | quarterly | semiannual | yearly"
    )
    n_occurrences: int
    first_date: date
    last_date: date
    amount: Decimal = Field(description="Signed median amount per occurrence.")
    amount_min: Decimal
    amount_max: Decimal
    monthly_equivalent: Decimal = Field(
        description="Signed cost/income normalized to one month (a yearly fee shows as 1/12)."
    )
    regularity: float = Field(description="Share of gaps inside the cadence window (0..1).")
    active: bool = Field(
        description="Judged against the newest data, not today: data ends with the last "
        "statement, and silence after it is absence of evidence."
    )
    next_expected: date | None


class RecurringResponse(BaseModel):
    lang: str
    horizon: date | None = Field(description="Newest movement date; activity is judged here.")
    n_active: int
    monthly_expense: Decimal = Field(
        description="Sum of active recurring expenses per month (signed, <= 0)."
    )
    monthly_income: Decimal = Field(description="Sum of active recurring income per month.")
    items: list[RecurringItem]


class CoverageHole(BaseModel):
    """A window between two covered days that no data touches. A missing
    statement and a genuinely quiet period look identical from the data alone,
    so this is a hint to check, not a verdict."""

    from_date: date
    to_date: date
    days: int


class CoverageSource(BaseModel):
    """Coverage of one SOURCE — the unit a statement is uploaded for. Every
    account of the source testifies for the same file, so a dormant crypto
    pocket does not look 'behind' while the cash account is fresh."""

    source: str
    accounts: list[str]
    n_movements: int
    n_anchors: int
    covered_from: date | None
    covered_until: date | None = Field(description="Last day any of its data covers.")
    anchor_cadence_days: int | None = Field(
        description="Median days between balance anchors; null below 3 anchors."
    )
    stale_days: int
    stale: bool = Field(
        description="Behind schedule given the source's own anchor cadence — a "
        "quarterly source gets a quarter's grace, a daily export weeks."
    )
    holes: list[CoverageHole]


class CoverageResponse(BaseModel):
    today: date
    n_stale: int
    n_holes: int
    sources: list[CoverageSource] = Field(description="Worst first.")


class Account(BaseModel):
    """An account as the statements describe it. The id is opaque and stable (it
    is hashed into ``natural_key``); everything else is display metadata read off
    the documents, so most of it is nullable."""

    account_id: str = Field(examples=["revolut_joint_eur"])
    source: str
    bank_name: str | None = Field(default=None, examples=["Intesa Sanpaolo"])
    product: str | None = Field(default=None, examples=["XME Conto", "Joint Account"])
    is_joint: bool | None = Field(
        default=None,
        description="null = the document did not say, which is NOT the same as individual.",
    )
    currency: str | None = None
    iban: str | None = None
    #: The user's chosen name, when set. Must be declared: response_model strips
    #: anything absent here.
    display_name_override: str | None = None
    display_name: str = Field(examples=["Revolut Bank UAB · Joint Account (Joint)"])
    transactions: int
    first_movement: date | None = None
    last_movement: date | None = None


class AccountsResponse(BaseModel):
    accounts: list[Account]


class SourceMeta(BaseModel):
    """A source cashato can parse, straight from the adapter registry."""

    id: str = Field(examples=["trade_republic"])
    label: str = Field(
        description="Human name for the source. The bank read off its statements "
        "when they agree on one, otherwise the id title-cased — never invented.",
        examples=["Trade Republic Bank"],
    )


class CategoryMeta(BaseModel):
    code: str = Field(examples=["groceries"])
    labels: dict[str, str] = Field(description="Localized labels, one key per supported language.")


class MetaResponse(BaseModel):
    """The vocabulary the UI needs, from the same place the pipeline reads it.

    Exists so no client has to restate the list of sources or categories. Those
    lists live in the adapter registry and in `categories.yaml`; a copy in the
    frontend drifts the moment either changes.
    """

    sources: list[SourceMeta]
    categories: list[CategoryMeta]
    languages: list[str]
    # The default code, the wealth-not-consumption codes and the provenance
    # vocabulary are pipeline knowledge too: published here so no client
    # hardcodes 'other', an asset list, or a threshold that drifts the moment
    # the config is recalibrated.
    default_category: str
    asset_categories: list[str]
    category_sources: list[str]
    model_threshold: float
    allowed_extensions: list[str]
    max_file_bytes: int
    max_files_per_batch: int


class TransferLeg(BaseModel):
    """The other side of an internal transfer."""

    natural_key: str
    value_date: date
    account: str
    amount: Decimal
    description: str


class TransactionDetail(BaseModel):
    """Everything known about one movement, for investigating it."""

    natural_key: str
    value_date: date
    booking_date: date
    description: str
    amount: Decimal
    currency: str
    account: str
    source: str
    category: str | None = None
    category_label: str | None = None
    category_source: str | None = Field(
        default=None, description="How the category was assigned: mcc | model | rule | manual."
    )
    category_confidence: float | None = None
    mcc: str | None = Field(default=None, description="ISO 18245 merchant category code.")
    native_category: str | None = Field(
        default=None,
        description="The provider's own category. Kept for transparency; never used at runtime.",
    )
    transfer_group: str | None = None
    transfer_counterpart: TransferLeg | None = None
    file_name: str | None = None
    file_uploaded_at: datetime | None = None
    file_sha256: str | None = None
    # Instrument leg, when the movement was a trade and the source said what.
    isin: str | None = None
    instrument: str | None = None
    asset_class: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    side: str | None = None
    merchant: str | None = None
    purchase_time: time | None = None


class Holding(BaseModel):
    """A position, aggregated from the trades a source disclosed.

    Money stays ``Decimal`` all the way to the wire: the gold views compute it
    exactly, and the project rule is Decimal, never float.
    """

    isin: str | None = None
    instrument: str | None = None
    asset_class: str | None = None
    units: Decimal = Field(description="Net units held (buys minus sells).")
    invested: Decimal = Field(description="Cash cost basis: what actually left the account.")
    n_trades: int
    first_trade: date | None = None
    last_trade: date | None = None
    last_price: Decimal | None = Field(
        default=None,
        description="Last price seen ON A STATEMENT, not a market quote — it ages.",
    )
    value_at_last_price: Decimal | None = None


class InvestmentMonth(BaseModel):
    month: date
    category: str = Field(description="Wealth destination kind: investments, pension_fund, …")
    contributed: Decimal | None = Field(default=None, description="Money in (outflows).")
    returned: Decimal | None = Field(default=None, description="Money back (sales, dividends).")
    net_invested: Decimal | None = None
    into_known: Decimal | None = Field(
        default=None, description="Contributions whose instrument the source disclosed."
    )
    into_unknown: Decimal | None = Field(
        default=None,
        description="Contributions with no instrument detail — e.g. a transfer to an "
        "outside broker. Real money invested, contents not in our documents.",
    )
    n_movements: int


class WealthKind(BaseModel):
    """One destination kind, rolled up. Present only when it has movements."""

    category: str
    category_label: str
    net_invested: Decimal
    contributed: Decimal
    returned: Decimal
    n_movements: int
    #: Instruments are only knowable for kinds whose source discloses them; a
    #: pension fund reached by bank transfer never will.
    has_instruments: bool


class InvestmentsResponse(BaseModel):
    holdings: list[Holding]
    months: list[InvestmentMonth]
    kinds: list[WealthKind]
    #: Gross money in. `total_in_known_instruments + total_in_unknown` equals
    #: this by construction — they are the same sum split by available detail.
    total_contributed: Decimal
    total_returned: Decimal = Field(description="Money back: sales, dividends, maturities.")
    total_invested: Decimal = Field(
        description="NET of returns, i.e. total_contributed - total_returned. Reported "
        "separately because the gross figure is what the known/unknown split adds up to."
    )
    total_in_known_instruments: Decimal
    total_in_unknown: Decimal
