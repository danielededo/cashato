"""query-api — exposes spending aggregates from the GOLD views.

Categories in the DB are codes; here they are localized (``?lang=it|en``) via the
Categorizer (no ML model needed for labels only).

Path conventions: probes at root (``/healthz``, ``/readyz``); business API under
``/api/v1``; ``ROOT_PATH`` (env) for the gateway prefix. OpenAPI at ``/openapi.json``,
Swagger UI at ``/docs``, ReDoc at ``/redoc``.
"""

from __future__ import annotations

import os
from datetime import date, datetime

from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, Field
from sqlalchemy import text

from cashato.config import setting
from cashato.db.db import get_engine
from cashato.obs import (
    setup_logging,
    setup_tracing,
    start_metrics_server,
    tracing_enabled,
)
from cashato.parsers.categorize import Categorizer
from cashato.parsers.registry import SOURCE_NAMES

ROOT_PATH = os.environ.get("ROOT_PATH", "")
_log = setup_logging("query-api")

_TAGS = [
    {"name": "health", "description": "Liveness/readiness probes for Kubernetes."},
    {"name": "analytics", "description": "Spending aggregates from the GOLD layer."},
]

app = FastAPI(
    title="cashato query-api",
    version="0.1.0",
    description="Read API over the unified transactions: per-category and monthly aggregates.",
    root_path=ROOT_PATH,
    openapi_tags=_TAGS,
    license_info={"name": "MIT"},
)
_CAT = Categorizer.load()
_engine = get_engine()

# Prometheus metrics on a dedicated port (:9100), uniform across all services.
# The Instrumentator still records HTTP request metrics into the default registry;
# start_metrics_server serves that registry on :9100 instead of the business port.
Instrumentator().instrument(app)
start_metrics_server()

# Distributed tracing: auto-instrument HTTP handlers + the psycopg driver
# (driver-level, so the module-level engine created above is still traced).
setup_tracing("query-api")
if tracing_enabled():
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor

    FastAPIInstrumentor.instrument_app(app)
    PsycopgInstrumentor().instrument()


# --- response models (typed OpenAPI schema + examples) ---
class CategoryTotal(BaseModel):
    category: str = Field(examples=["groceries"])
    category_label: str = Field(examples=["Groceries"])
    n_movements: int = Field(examples=[618])
    income: float | None = Field(default=None, examples=[0.0])
    expense: float | None = Field(default=None, examples=[-19019.12])
    net: float | None = Field(examples=[-19019.12])


class SummaryResponse(BaseModel):
    lang: str = Field(examples=["en"])
    categories: list[CategoryTotal]


class MonthRow(BaseModel):
    month: date = Field(examples=["2025-01-01"])
    income: float | None = None
    expense: float | None = None
    net: float | None = None
    net_excl_investments: float | None = None


class MonthlyResponse(BaseModel):
    months: list[MonthRow]


class CategoryMonthRow(BaseModel):
    month: date
    category: str
    category_label: str
    n_movements: int
    total: float | None = None


class CategoriesMonthlyResponse(BaseModel):
    lang: str
    rows: list[CategoryMonthRow]


class TransactionRow(BaseModel):
    id: int
    value_date: date
    booking_date: date
    description: str
    amount: float
    currency: str
    account: str
    source: str
    category: str | None = None
    category_label: str
    category_source: str | None = None
    category_confidence: float | None = None
    transfer_group: str | None = None
    natural_key: str


class TransactionsResponse(BaseModel):
    lang: str
    total: int = Field(description="Total rows matching the filters (before paging)")
    limit: int
    offset: int
    transactions: list[TransactionRow]


class TransferPair(BaseModel):
    transfer_group: str
    value_date: date = Field(description="Value date of the transfer legs")
    amount: float = Field(description="Absolute transferred amount", examples=[400.0])
    from_account: str | None = Field(default=None, description="Debited account (negative leg)")
    to_account: str | None = Field(default=None, description="Credited account (positive leg)")


class TransfersResponse(BaseModel):
    n_pairs: int
    total_volume: float = Field(description="Sum of transferred amounts (absolute)")
    transfers: list[TransferPair]


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
    display_name: str = Field(examples=["Revolut Bank UAB · Joint Account (Joint)"])
    transactions: int
    first_movement: date | None = None
    last_movement: date | None = None


class AccountsResponse(BaseModel):
    accounts: list[Account]


class SourceMeta(BaseModel):
    """A source cashato can parse, straight from the adapter registry."""

    id: str = Field(examples=["trade_republic"])


class CategoryMeta(BaseModel):
    code: str = Field(examples=["groceries"])
    labels: dict[str, str] = Field(description="Localized labels, one key per supported language.")


class MetaResponse(BaseModel):
    """The vocabulary the UI needs, from the same place the pipeline reads it.

    Exists so no client has to restate the list of sources or categories. Those
    lists live in the adapter registry and in `categorie.yaml`; a copy in the
    frontend drifts the moment either changes — which it did, within a day of
    the categories growing.
    """

    sources: list[SourceMeta]
    categories: list[CategoryMeta]
    languages: list[str]
    allowed_extensions: list[str]
    max_file_bytes: int


class TransferLeg(BaseModel):
    """The other side of an internal transfer."""

    natural_key: str
    value_date: date
    account: str
    amount: float
    description: str


class TransactionDetail(BaseModel):
    """Everything known about one movement, for investigating it."""

    natural_key: str
    value_date: date
    booking_date: date
    description: str
    amount: float
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
    quantity: float | None = None
    unit_price: float | None = None
    side: str | None = None


class Holding(BaseModel):
    """A position, aggregated from the trades a source disclosed."""

    isin: str | None = None
    instrument: str | None = None
    asset_class: str | None = None
    units: float = Field(description="Net units held (buys minus sells).")
    invested: float = Field(description="Cash cost basis: what actually left the account.")
    n_trades: int
    first_trade: date | None = None
    last_trade: date | None = None
    last_price: float | None = Field(
        default=None,
        description="Last price seen ON A STATEMENT, not a market quote — it ages.",
    )
    value_at_last_price: float | None = None


class InvestmentMonth(BaseModel):
    month: date
    category: str = Field(description="Wealth destination kind: investments, pension_fund, …")
    contributed: float | None = Field(default=None, description="Money in (outflows).")
    returned: float | None = Field(default=None, description="Money back (sales, dividends).")
    net_invested: float | None = None
    into_known: float | None = Field(
        default=None, description="Contributions whose instrument the source disclosed."
    )
    into_unknown: float | None = Field(
        default=None,
        description="Contributions with no instrument detail — e.g. a transfer to an "
        "outside broker. Real money invested, contents not in our documents.",
    )
    n_movements: int


class WealthKind(BaseModel):
    """One destination kind, rolled up. Present only when it has movements."""

    category: str
    category_label: str
    net_invested: float
    contributed: float
    returned: float
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
    total_contributed: float
    total_returned: float = Field(description="Money back: sales, dividends, maturities.")
    total_invested: float = Field(
        description="NET of returns, i.e. total_contributed - total_returned. Reported "
        "separately because the gross figure is what the known/unknown split adds up to."
    )
    total_in_known_instruments: float
    total_in_unknown: float


_LANG = Query(default="it", description="Category label language", examples=["it", "en"])


def _rows(sql: str, params: dict | None = None) -> list[dict]:
    with _engine.connect() as conn:
        return [dict(r) for r in conn.execute(text(sql), params or {}).mappings().all()]


@app.get("/healthz", tags=["health"], summary="Liveness probe")
def healthz():
    return {"status": "ok"}


@app.get("/readyz", tags=["health"], summary="Readiness probe (checks the DB)")
def readyz():
    try:
        with _engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"ready": True}
    except Exception:
        return JSONResponse({"ready": False}, status_code=503)


api = APIRouter(prefix="/api/v1", tags=["analytics"])


@api.get("/summary", response_model=SummaryResponse, summary="Totals per category")
def summary(lang: str = _LANG):
    """Income/expense/net per category, with localized labels."""
    rows = _rows("SELECT * FROM gold.v_category_totals ORDER BY net")
    return {
        "lang": lang,
        "categories": [{**r, "category_label": _CAT.label(r["category"], lang)} for r in rows],
    }


@api.get("/meta", response_model=MetaResponse, summary="Sources, categories, upload limits")
def meta():
    """What the client needs to build its selectors, from the single source of truth.

    Sources come from the adapter registry (dropping in a parser module adds one
    with no further wiring); categories and their labels from `categorie.yaml`;
    upload limits from `settings.yaml`. All three are runtime config or code
    discovery, so a client that reads this can never be out of step with what
    the pipeline actually accepts.
    """
    return {
        "sources": [{"id": s} for s in SOURCE_NAMES],
        "categories": [
            {"code": code, "labels": labels} for code, labels in sorted(_CAT.categories.items())
        ],
        "languages": _CAT.languages,
        "allowed_extensions": setting("uploads.allowed_extensions", [".pdf", ".csv", ".xlsx"]),
        "max_file_bytes": int(setting("uploads.max_file_bytes", 10 * 1024 * 1024)),
    }


@api.get("/accounts", response_model=AccountsResponse, summary="Accounts and how they are held")
def accounts():
    """The accounts behind the ingested statements: bank, product, joint or not.

    The display name is composed in the view from the stored parts, so no bank
    name is ever spelled out in the client. Accounts with no movements are still
    listed — a statement can describe an account whose currency we skip.
    """
    return {
        "accounts": _rows(
            "SELECT * FROM gold.v_accounts ORDER BY transactions DESC, account_id"
        )
    }


@api.get(
    "/transactions/{natural_key}",
    response_model=TransactionDetail,
    summary="One movement, in full",
)
def transaction_detail(natural_key: str, lang: str = _LANG):
    """Everything known about a single movement.

    Beyond the list columns: how the category was assigned and how confident
    that was, the raw provider signals, which uploaded file it came from, the
    instrument if it was a trade, and the paired leg if it is one half of an
    internal transfer.
    """
    rows = _rows(
        "SELECT * FROM gold.v_transaction_detail WHERE natural_key = :k", {"k": natural_key}
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"unknown transaction {natural_key!r}")
    row = rows[0]

    # The other leg of an internal transfer: same group, different movement.
    counterpart = None
    if row.get("transfer_group"):
        others = _rows(
            "SELECT natural_key, value_date, account, amount, description "
            "FROM gold.v_transactions WHERE transfer_group = :g AND natural_key <> :k",
            {"g": row["transfer_group"], "k": natural_key},
        )
        if others:
            counterpart = others[0]

    return {
        **row,
        "category_label": _CAT.label(row["category"], lang),
        "transfer_counterpart": counterpart,
    }


@api.get("/investments", response_model=InvestmentsResponse, summary="Investments")
def investments(lang: str = _LANG):
    """Investments on the two levels the sources actually support.

    **Contributions** are always knowable — money leaving towards investing,
    including a plain transfer to an outside broker. **Positions** need the
    source to disclose the instrument, which a bank transfer never does. The
    split between the two is reported rather than hidden: ``into_unknown`` is
    money genuinely invested whose contents are not in our documents, and
    presenting only the instruments we happen to know would understate the
    total.

    No market prices are involved. ``last_price`` is the last price printed on a
    statement, so ``value_at_last_price`` is a cost-basis-era figure, not what
    the position is worth today.
    """
    holdings = _rows("SELECT * FROM gold.v_holdings ORDER BY invested DESC NULLS LAST")
    months = _rows("SELECT * FROM gold.v_investment_month ORDER BY month, category")

    # Roll the months up per destination kind. Kinds with no movements simply do
    # not appear, so the UI renders a section only when there is something in it.
    kinds: dict[str, dict] = {}
    for m in months:
        k = kinds.setdefault(
            m["category"],
            {
                "category": m["category"],
                "category_label": _CAT.label(m["category"], lang),
                "net_invested": 0.0,
                "contributed": 0.0,
                "returned": 0.0,
                "n_movements": 0,
                "has_instruments": False,
            },
        )
        # psycopg hands back Decimal for NUMERIC. Pydantic coerces on the way
        # out, but this roll-up happens first, and Decimal + float raises.
        k["net_invested"] += float(m["net_invested"] or 0)
        k["contributed"] += float(m["contributed"] or 0)
        k["returned"] += float(m["returned"] or 0)
        k["n_movements"] += m["n_movements"]
        k["has_instruments"] = k["has_instruments"] or bool(m["into_known"])

    known = sum(float(m["into_known"] or 0) for m in months)
    unknown = sum(float(m["into_unknown"] or 0) for m in months)
    return {
        "holdings": holdings,
        "months": months,
        "kinds": sorted(kinds.values(), key=lambda k: -k["net_invested"]),
        "total_contributed": known + unknown,
        "total_returned": sum(float(m["returned"] or 0) for m in months),
        "total_invested": sum(float(m["net_invested"] or 0) for m in months),
        "total_in_known_instruments": known,
        "total_in_unknown": unknown,
    }


@api.get("/monthly", response_model=MonthlyResponse, summary="Monthly income/expense")
def monthly():
    """Monthly income/expense/net (with and without investments/crypto)."""
    return {"months": _rows("SELECT * FROM gold.v_income_expense_month ORDER BY month")}


@api.get(
    "/categories/monthly",
    response_model=CategoriesMonthlyResponse,
    summary="Spend per category and month",
)
def categories_monthly(lang: str = _LANG):
    """Spend per category and month, with localized labels."""
    rows = _rows("SELECT * FROM gold.v_category_month ORDER BY month, category")
    return {
        "lang": lang,
        "rows": [{**r, "category_label": _CAT.label(r["category"], lang)} for r in rows],
    }


@api.get("/transactions", response_model=TransactionsResponse, summary="List transactions")
def transactions(
    lang: str = _LANG,
    account: str | None = Query(default=None, description="Filter by account id"),
    source: str | None = Query(default=None, description="Filter by source"),
    category: str | None = Query(default=None, description="Filter by category code"),
    category_source: str | None = Query(
        default=None,
        description="Filter by how the category was assigned (mcc|model|rule|manual|default)",
    ),
    sign: str | None = Query(default=None, description="'income' (amount>0) or 'expense' (amount<0)"),
    date_from: date | None = Query(default=None, description="Value date >= (inclusive)"),
    date_to: date | None = Query(default=None, description="Value date <= (inclusive)"),
    q: str | None = Query(default=None, description="Case-insensitive text search in the description"),
    min_amount: float | None = Query(default=None, description="Amount >= (signed)"),
    max_amount: float | None = Query(default=None, description="Amount <= (signed)"),
    min_confidence: float | None = Query(default=None, description="Category confidence >= (0..1)"),
    max_confidence: float | None = Query(default=None, description="Category confidence <= (0..1)"),
    include_transfers: bool = Query(default=True, description="Include internal-transfer legs"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """Filterable, paginated list of transactions (read-only gold projection)."""
    if sign not in (None, "income", "expense"):
        raise HTTPException(status_code=422, detail="sign must be 'income' or 'expense'")
    conds: list[str] = []
    params: dict = {}
    if account:
        conds.append("account = :account")
        params["account"] = account
    if source:
        conds.append("source = :source")
        params["source"] = source
    if category:
        conds.append("category = :category")
        params["category"] = category
    if category_source:
        conds.append("category_source = :category_source")
        params["category_source"] = category_source
    if sign == "income":
        conds.append("amount > 0")
    elif sign == "expense":
        conds.append("amount < 0")
    if date_from:
        conds.append("value_date >= :date_from")
        params["date_from"] = date_from
    if date_to:
        conds.append("value_date <= :date_to")
        params["date_to"] = date_to
    if q:
        conds.append("description ILIKE :q")
        params["q"] = f"%{q}%"
    if min_amount is not None:
        conds.append("amount >= :min_amount")
        params["min_amount"] = min_amount
    if max_amount is not None:
        conds.append("amount <= :max_amount")
        params["max_amount"] = max_amount
    if min_confidence is not None:
        conds.append("category_confidence >= :min_confidence")
        params["min_confidence"] = min_confidence
    if max_confidence is not None:
        conds.append("category_confidence <= :max_confidence")
        params["max_confidence"] = max_confidence
    if not include_transfers:
        conds.append("transfer_group IS NULL")
    where = f"WHERE {' AND '.join(conds)}" if conds else ""

    total = _rows(f"SELECT count(*) AS n FROM gold.v_transactions {where}", params)[0]["n"]
    page_params = {**params, "limit": limit, "offset": offset}
    rows = _rows(
        f"SELECT * FROM gold.v_transactions {where} "
        "ORDER BY value_date DESC, id DESC LIMIT :limit OFFSET :offset",
        page_params,
    )
    return {
        "lang": lang,
        "total": total,
        "limit": limit,
        "offset": offset,
        "transactions": [{**r, "category_label": _CAT.label(r["category"], lang)} for r in rows],
    }


@api.get("/transfers", response_model=TransfersResponse, summary="Detected internal transfers")
def transfers():
    """Internal-transfer pairs (own-account movements excluded from spending)."""
    rows = _rows(
        """
        SELECT transfer_group,
               min(value_date)                          AS value_date,
               max(abs(amount))                         AS amount,
               max(account) FILTER (WHERE amount < 0)   AS from_account,
               max(account) FILTER (WHERE amount > 0)   AS to_account
        FROM gold.v_internal_transfers
        GROUP BY transfer_group
        ORDER BY value_date DESC
        """
    )
    return {
        "n_pairs": len(rows),
        "total_volume": float(sum(r["amount"] for r in rows)),
        "transfers": rows,
    }


app.include_router(api)
