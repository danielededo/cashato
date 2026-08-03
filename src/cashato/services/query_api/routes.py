"""The /api/v1 router: every read endpoint over the GOLD views.

Route logic only — response shapes live in models.py, shared state in deps.py,
app assembly (obs, probes, prefixing) in app.py.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query

from cashato.config import setting
from cashato.coverage import coverage_report
from cashato.parsers.categorize import CATEGORY_SOURCES
from cashato.parsers.registry import SOURCE_NAMES
from cashato.recurrence import detect_recurring

from .deps import CAT, LANG, fetch_rows
from .models import (
    AccountsResponse,
    CategoriesMonthlyResponse,
    CoverageResponse,
    InvestmentsResponse,
    MerchantsResponse,
    MetaResponse,
    MonthlyResponse,
    ReconciliationResponse,
    RecurringResponse,
    SummaryResponse,
    TransactionDetail,
    TransactionsResponse,
    TransfersResponse,
    WealthResponse,
)

api = APIRouter(prefix="/api/v1", tags=["analytics"])


@api.get("/summary", response_model=SummaryResponse, summary="Totals per category")
def summary(lang: str = LANG):
    """Income/expense/net per category, with localized labels."""
    rows = fetch_rows("SELECT * FROM gold.v_category_totals ORDER BY net")
    return {
        "lang": lang,
        "categories": [{**r, "category_label": CAT.label(r["category"], lang)} for r in rows],
    }


@api.get("/meta", response_model=MetaResponse, summary="Sources, categories, upload limits")
def meta():
    """What the client needs to build its selectors, from the single source of truth.

    Sources come from the adapter registry (dropping in a parser module adds one
    with no further wiring); categories and their labels from `categories.yaml`;
    upload limits from `settings.yaml`. All three are runtime config or code
    discovery, so a client that reads this can never be out of step with what
    the pipeline actually accepts.
    """
    # Name each source ONCE, here, rather than letting every client invent its
    # own rendering of the id.
    # A source is named after its accounts' bank only when they all agree;
    # otherwise the id, title-cased, which is honest about being derived.
    banks = {
        r["source"]: r["bank"]
        for r in fetch_rows(
            "SELECT source, MIN(bank_name) AS bank FROM gold.v_accounts "
            "WHERE bank_name IS NOT NULL GROUP BY source "
            "HAVING COUNT(DISTINCT bank_name) = 1"
        )
    }
    return {
        "sources": [
            {"id": s, "label": banks.get(s) or s.replace("_", " ").title()}
            for s in SOURCE_NAMES
        ],
        "categories": [
            {"code": code, "labels": labels} for code, labels in sorted(CAT.categories.items())
        ],
        "languages": CAT.languages,
        "default_category": CAT.default,
        "asset_categories": sorted(CAT.asset_categories),
        "category_sources": list(CATEGORY_SOURCES),
        "model_threshold": CAT.model_threshold,
        "allowed_extensions": setting("uploads.allowed_extensions", [".pdf", ".csv", ".xlsx"]),
        "max_file_bytes": int(setting("uploads.max_file_bytes", 10 * 1024 * 1024)),
        "max_files_per_batch": int(setting("uploads.max_files_per_batch", 50)),
    }


@api.get("/accounts", response_model=AccountsResponse, summary="Accounts and how they are held")
def accounts():
    """The accounts behind the ingested statements: bank, product, joint or not.

    The display name is composed in the view from the stored parts, so no bank
    name is ever spelled out in the client. Accounts with no movements are still
    listed — a statement can describe an account whose currency we skip.
    """
    return {
        "accounts": fetch_rows(
            "SELECT * FROM gold.v_accounts ORDER BY transactions DESC, account_id"
        )
    }


@api.get(
    "/transactions/{natural_key}",
    response_model=TransactionDetail,
    summary="One movement, in full",
)
def transaction_detail(natural_key: str, lang: str = LANG):
    """Everything known about a single movement.

    Beyond the list columns: how the category was assigned and how confident
    that was, the raw provider signals, which uploaded file it came from, the
    instrument if it was a trade, and the paired leg if it is one half of an
    internal transfer.
    """
    rows = fetch_rows(
        "SELECT * FROM gold.v_transaction_detail WHERE natural_key = :k", {"k": natural_key}
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"unknown transaction {natural_key!r}")
    row = rows[0]

    # The other leg of an internal transfer: same group, different movement.
    counterpart = None
    if row.get("transfer_group"):
        others = fetch_rows(
            "SELECT natural_key, value_date, account, amount, description "
            "FROM gold.v_transactions WHERE transfer_group = :g AND natural_key <> :k",
            {"g": row["transfer_group"], "k": natural_key},
        )
        if others:
            counterpart = others[0]

    return {
        **row,
        "category_label": CAT.label(row["category"], lang),
        "transfer_counterpart": counterpart,
    }


@api.get("/investments", response_model=InvestmentsResponse, summary="Investments")
def investments(lang: str = LANG):
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
    holdings = fetch_rows("SELECT * FROM gold.v_holdings ORDER BY invested DESC NULLS LAST")
    months = fetch_rows("SELECT * FROM gold.v_investment_month ORDER BY month, category")

    # Roll the months up per destination kind. Kinds with no movements simply do
    # not appear, so the UI renders a section only when there is something in it.
    kinds: dict[str, dict] = {}
    for m in months:
        k = kinds.setdefault(
            m["category"],
            {
                "category": m["category"],
                "category_label": CAT.label(m["category"], lang),
                "net_invested": Decimal(0),
                "contributed": Decimal(0),
                "returned": Decimal(0),
                "n_movements": 0,
                "has_instruments": False,
            },
        )
        # psycopg hands back Decimal for NUMERIC and we keep it that way: seed
        # the accumulators with Decimal(0) to stay in exact arithmetic.
        k["net_invested"] += m["net_invested"] or 0
        k["contributed"] += m["contributed"] or 0
        k["returned"] += m["returned"] or 0
        k["n_movements"] += m["n_movements"]
        k["has_instruments"] = k["has_instruments"] or bool(m["into_known"])

    known = sum((m["into_known"] or 0 for m in months), Decimal(0))
    unknown = sum((m["into_unknown"] or 0 for m in months), Decimal(0))
    return {
        "holdings": holdings,
        "months": months,
        "kinds": sorted(kinds.values(), key=lambda k: -k["net_invested"]),
        "total_contributed": known + unknown,
        "total_returned": sum((m["returned"] or 0 for m in months), Decimal(0)),
        "total_invested": sum((m["net_invested"] or 0 for m in months), Decimal(0)),
        "total_in_known_instruments": known,
        "total_in_unknown": unknown,
    }


@api.get("/coverage", response_model=CoverageResponse, summary="File-coverage report")
def coverage():
    """Which statement is missing, and how far behind each account is.

    Read off the data the statements left behind: staleness of the last
    covered day against today (tolerance scaled to the account's own anchor
    cadence) and holes in the anchor spacing. Uploading the missing file is
    the fix, which is why the UI shows this next to Upload/Reprocess.
    """
    today = date.today()
    movements = fetch_rows("SELECT account, source, value_date FROM gold.v_transactions")
    anchors = fetch_rows("SELECT account, source, balance_date FROM gold.v_balances")
    report = coverage_report(movements, anchors, today)
    return {
        "today": today,
        "n_stale": sum(1 for c in report if c.stale),
        "n_holes": sum(len(c.holes) for c in report),
        "sources": [asdict(c) for c in report],
    }


@api.get("/recurring", response_model=RecurringResponse, summary="Recurring movements")
def recurring(lang: str = LANG, active_only: bool = False):
    """Subscriptions, salary, rent, utilities — detected from the data's rhythm.

    Same merchant (numbers stripped from the normalized text) at a steady
    cadence; amounts may drift the way salaries and utility bills do. Detection
    runs on the fly over gold — a personal dataset is thousands of rows, and no
    derived table means nothing to go stale. Internal transfers are excluded:
    a monthly top-up of one's own account is a rhythm, not a subscription.
    """
    # Two exclusions for the same reason: linked transfer legs AND rows the
    # model categorized as transfers (a leg whose twin sits outside the window,
    # e.g. a monthly card top-up, is still own money moving).
    rows = fetch_rows(
        "SELECT value_date, description, amount, account, category "
        "FROM gold.v_transactions "
        "WHERE transfer_group IS NULL AND (category IS NULL OR category <> 'transfers')"
    )
    groups = detect_recurring(rows)
    if active_only:
        groups = [g for g in groups if g.active]
    # Asset-destined recurrences (an ETF savings plan, a pension contribution)
    # are listed but kept out of the spend/income totals — the same line gold's
    # spend views draw, read from the same declared list.
    consumption = [
        g for g in groups if g.active and g.category not in CAT.asset_categories
    ]
    return {
        "lang": lang,
        "horizon": max((r["value_date"] for r in rows), default=None),
        "n_active": sum(1 for g in groups if g.active),
        "monthly_expense": sum(
            (g.monthly_equivalent for g in consumption if g.amount < 0), Decimal(0)
        ),
        "monthly_income": sum(
            (g.monthly_equivalent for g in consumption if g.amount > 0), Decimal(0)
        ),
        "items": [
            {**asdict(g), "category_label": CAT.label(g.category, lang)} for g in groups
        ],
    }


@api.get("/wealth", response_model=WealthResponse, summary="Declared balances over time")
def wealth():
    """Month-end balance per account, carried forward between statement anchors.

    Dense sources (Revolut, Trade Republic) anchor almost every day; a quarterly
    statement anchors four times a year. Carrying the last anchor forward puts
    them on one monthly grid; ``as_of`` on every row says how old each figure
    really is, and ``oldest_as_of`` bounds the freshness of the total.
    """
    rows = fetch_rows("SELECT * FROM gold.v_balance_month ORDER BY month, account")
    # Rows arrive month-ascending, so the last write per account is its latest.
    latest: dict[str, dict] = {}
    for r in rows:
        latest[r["account"]] = r
    accounts = sorted(latest.values(), key=lambda r: -r["balance"])
    return {
        "months": rows,
        "accounts": accounts,
        "total_liquid": sum((r["balance"] for r in latest.values()), Decimal(0)),
        "oldest_as_of": min((r["as_of"] for r in latest.values()), default=None),
    }


@api.get("/monthly", response_model=MonthlyResponse, summary="Monthly income/expense")
def monthly():
    """Monthly income/expense/net. Asset-destined movements (investments,
    crypto, …) and internal transfers are excluded by the view itself."""
    return {"months": fetch_rows("SELECT * FROM gold.v_income_expense_month ORDER BY month")}


@api.get(
    "/categories/monthly",
    response_model=CategoriesMonthlyResponse,
    summary="Spend per category and month",
)
def categories_monthly(lang: str = LANG):
    """Spend per category and month, with localized labels."""
    rows = fetch_rows("SELECT * FROM gold.v_category_month ORDER BY month, category")
    return {
        "lang": lang,
        "rows": [{**r, "category_label": CAT.label(r["category"], lang)} for r in rows],
    }


@api.get("/merchants", response_model=MerchantsResponse, summary="Top merchants by spend")
def merchants(
    lang: str = LANG,
    date_from: date | None = Query(default=None, description="Value date >= (inclusive)"),
    date_to: date | None = Query(default=None, description="Value date <= (inclusive)"),
    limit: int = Query(default=20, ge=1, le=100),
):
    """Where the money actually goes, by counterparty instead of raw text.

    Groups case-insensitively (the quarterly writes ALL CAPS, the 13-month
    export Title-Cases the same merchant) and nets refunds against purchases.
    Internal transfers never carry a merchant's weight here, and rows whose
    description yields no merchant (wire transfers, ATM, securities) are out
    by construction — this reads as a shopping report, not a counterparty list.
    """
    conds = ["merchant IS NOT NULL", "transfer_group IS NULL"]
    params: dict = {"limit": limit}
    if date_from:
        conds.append("value_date >= :date_from")
        params["date_from"] = date_from
    if date_to:
        conds.append("value_date <= :date_to")
        params["date_to"] = date_to
    where = f"WHERE {' AND '.join(conds)}"
    # HAVING net < 0: a group that is pure inflow (a refunded purchase whose
    # expense leg fell outside the range, P2P money received via a PSP) is not
    # a place money went.
    grouped = (
        f"FROM gold.v_transactions {where} "
        "GROUP BY lower(merchant) HAVING sum(amount) < 0"
    )
    n_merchants = fetch_rows(f"SELECT count(*) AS n FROM (SELECT 1 {grouped}) g", params)[0]["n"]
    rows = fetch_rows(
        "SELECT mode() WITHIN GROUP (ORDER BY merchant) AS merchant, "
        "count(*) AS n_movements, "
        "round(-sum(amount), 2) AS total_spent, "
        "round(-sum(amount) / count(*), 2) AS avg_spent, "
        "max(value_date) AS last_date, "
        "mode() WITHIN GROUP (ORDER BY category) AS category "
        f"{grouped} ORDER BY total_spent DESC LIMIT :limit",
        params,
    )
    return {
        "lang": lang,
        "n_merchants": n_merchants,
        "merchants": [{**r, "category_label": CAT.label(r["category"], lang)} for r in rows],
    }


# Sortable columns of gold.v_transactions, keyed by the public param value.
# `abs_amount` ranks by magnitude regardless of sign: a review queue wants the
# rows that move the totals most, and ordering by the signed amount would put
# every inflow at one end instead.
SORT_COLS = {
    "date": "value_date",
    "amount": "amount",
    "abs_amount": "abs(amount)",
    "description": "description",
    "category": "category",
    "account": "account",
    "confidence": "category_confidence",
}


@api.get("/transactions", response_model=TransactionsResponse, summary="List transactions")
def transactions(
    lang: str = LANG,
    account: str | None = Query(default=None, description="Filter by account id"),
    source: str | None = Query(default=None, description="Filter by source"),
    category: str | None = Query(default=None, description="Filter by category code"),
    category_source: str | None = Query(
        default=None,
        description=f"Filter by how the category was assigned: one of {list(CATEGORY_SOURCES)}",
    ),
    sign: str | None = Query(default=None, description="'income' (amount>0) or 'expense' (amount<0)"),
    date_from: date | None = Query(default=None, description="Value date >= (inclusive)"),
    date_to: date | None = Query(default=None, description="Value date <= (inclusive)"),
    q: str | None = Query(default=None, description="Case-insensitive text search in the description"),
    merchant: str | None = Query(
        default=None, description="Filter by extracted merchant (exact, case-insensitive)"
    ),
    min_amount: float | None = Query(default=None, description="Amount >= (signed)"),
    max_amount: float | None = Query(default=None, description="Amount <= (signed)"),
    min_confidence: float | None = Query(default=None, description="Category confidence >= (0..1)"),
    max_confidence: float | None = Query(default=None, description="Category confidence <= (0..1)"),
    include_transfers: bool = Query(default=True, description="Include internal-transfer legs"),
    sort: str = Query(default="date", description=f"Sort column: one of {sorted(SORT_COLS)}"),
    order: str = Query(default="desc", description="'asc' or 'desc'"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """Filterable, paginated list of transactions (read-only gold projection).

    Sorting happens HERE, not in the client: a page is 50-500 rows of a much
    larger set, so re-sorting the loaded page under a column header would show
    "the biggest of the newest page", not the biggest overall.
    """
    if sign not in (None, "income", "expense"):
        raise HTTPException(status_code=422, detail="sign must be 'income' or 'expense'")
    if category_source is not None and category_source not in CATEGORY_SOURCES:
        raise HTTPException(
            status_code=422,
            detail=f"category_source must be one of {list(CATEGORY_SOURCES)}",
        )
    if sort not in SORT_COLS:
        raise HTTPException(status_code=422, detail=f"sort must be one of {sorted(SORT_COLS)}")
    if order not in ("asc", "desc"):
        raise HTTPException(status_code=422, detail="order must be 'asc' or 'desc'")
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
    if merchant:
        conds.append("lower(merchant) = lower(:merchant)")
        params["merchant"] = merchant
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

    # One aggregate pass over the SAME filtered set the page comes from: the
    # count the pager needs anyway, plus income/expense/net sums so a category
    # or date-range total never requires fetching every page.
    agg = fetch_rows(
        "SELECT count(*) AS n, "
        "sum(amount) FILTER (WHERE amount > 0) AS sum_income, "
        "sum(amount) FILTER (WHERE amount < 0) AS sum_expense, "
        "sum(amount) AS sum_net "
        f"FROM gold.v_transactions {where}",
        params,
    )[0]
    total = agg["n"]
    page_params = {**params, "limit": limit, "offset": offset}
    # Column and direction come from the whitelists above, never from raw input.
    order_by = f"{SORT_COLS[sort]} {order.upper()}, id DESC"
    rows = fetch_rows(
        f"SELECT * FROM gold.v_transactions {where} "
        f"ORDER BY {order_by} LIMIT :limit OFFSET :offset",
        page_params,
    )
    return {
        "lang": lang,
        "total": total,
        "sum_income": agg["sum_income"],
        "sum_expense": agg["sum_expense"],
        "sum_net": agg["sum_net"],
        "limit": limit,
        "offset": offset,
        "transactions": [{**r, "category_label": CAT.label(r["category"], lang)} for r in rows],
    }


@api.get("/transfers", response_model=TransfersResponse, summary="Detected internal transfers")
def transfers():
    """Internal-transfer pairs (own-account movements excluded from spending)."""
    rows = fetch_rows(
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
        "total_volume": sum((r["amount"] for r in rows), Decimal(0)),
        "transfers": rows,
    }


@api.get(
    "/reconciliation",
    response_model=ReconciliationResponse,
    summary="Parsed movements vs the statements' own balances",
)
def reconciliation(mismatched_only: bool = False):
    """Every interval between two consecutive statement-declared balances,
    with the balance delta the statement promises vs the sum of the movements
    actually parsed. A non-zero discrepancy localizes a data problem to one
    account and date range (a parser dropping rows, a missing file, or an
    Intesa value date crossing the quarter boundary)."""
    rows = fetch_rows("SELECT * FROM gold.v_reconciliation ORDER BY account, from_date")
    mismatched = [r for r in rows if r["discrepancy"] != 0]
    return {
        "n_intervals": len(rows),
        "n_mismatched": len(mismatched),
        "intervals": mismatched if mismatched_only else rows,
    }
