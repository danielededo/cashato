"""query-api — exposes spending aggregates from the GOLD views.

Categories in the DB are codes; here they are localized (``?lang=it|en``) via the
Categorizer (no ML model needed for labels only).

Path conventions: probes at root (``/healthz``, ``/readyz``); business API under
``/api/v1``; ``ROOT_PATH`` (env) for the gateway prefix. OpenAPI at ``/openapi.json``,
Swagger UI at ``/docs``, ReDoc at ``/redoc``.
"""

from __future__ import annotations

import os
from datetime import date

from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, Field
from sqlalchemy import text

from cashato.db.db import get_engine
from cashato.obs import (
    setup_logging,
    setup_tracing,
    start_metrics_server,
    tracing_enabled,
)
from cashato.parsers.categorize import Categorizer

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
    sign: str | None = Query(default=None, description="'income' (amount>0) or 'expense' (amount<0)"),
    date_from: date | None = Query(default=None, description="Value date >= (inclusive)"),
    date_to: date | None = Query(default=None, description="Value date <= (inclusive)"),
    q: str | None = Query(default=None, description="Case-insensitive text search in the description"),
    min_amount: float | None = Query(default=None, description="Amount >= (signed)"),
    max_amount: float | None = Query(default=None, description="Amount <= (signed)"),
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
