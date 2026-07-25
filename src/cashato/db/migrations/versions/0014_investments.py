"""Investments: instrument-level trades, and contributions are not spending

Two changes that belong together.

1. ``silver.trades`` — what a movement actually bought or sold, when the source
   says so. Keyed by ``natural_key``, so it inherits the cash movement's dedup
   for free: the same purchase read from the PDF (no instrument detail) and from
   the CSV (full detail) is one movement with at most one trade row.

2. Money moved into investments stops counting as **spending**. Buying an ETF is
   not consumption, it is wealth changing shape, and counting it as expense
   understates the savings rate and makes the dashboard contradict the
   investments page. This mirrors what the spend views already do for internal
   transfer legs. Returns (sales, dividends) are excluded symmetrically — they
   are not income either.

Which categories count as assets is a row in ``silver.asset_categories``, not a
literal in each view, so adding one is an INSERT rather than a migration.

Revision ID: 0014_investments
Revises: 0013_account_display_override
"""

from __future__ import annotations

from alembic import op

revision = "0014_investments"
down_revision = "0013_account_display_override"
branch_labels = None
depends_on = None

_SPEND_VIEWS = ["v_category_month", "v_income_expense_month", "v_category_totals"]

# Rebuilt verbatim from 0006 except for the WHERE clause, which now also drops
# asset movements. Kept as one string so the three stay in step.
def _spend_views(where: str) -> list[str]:
    return [
        f"""
        CREATE VIEW gold.v_category_month AS
        SELECT date_trunc('month', value_date)::date AS month, category,
               count(*) AS n_movements, sum(amount) AS total
        FROM silver.transactions {where}
        GROUP BY 1, 2
        """,
        f"""
        CREATE VIEW gold.v_income_expense_month AS
        SELECT date_trunc('month', value_date)::date AS month,
               sum(amount) FILTER (WHERE amount > 0) AS income,
               sum(amount) FILTER (WHERE amount < 0) AS expense,
               sum(amount)                           AS net,
               sum(amount) FILTER (WHERE category NOT IN ('investments', 'crypto'))
                                                     AS net_excl_investments
        FROM silver.transactions {where}
        GROUP BY 1
        """,
        f"""
        CREATE VIEW gold.v_category_totals AS
        SELECT category, count(*) AS n_movements,
               sum(amount) FILTER (WHERE amount < 0) AS expense,
               sum(amount) FILTER (WHERE amount > 0) AS income,
               sum(amount) AS net
        FROM silver.transactions {where}
        GROUP BY 1
        """,
    ]


_OLD_WHERE = "WHERE transfer_group IS NULL"
_NEW_WHERE = """
WHERE transfer_group IS NULL
  AND category NOT IN (SELECT category FROM silver.asset_categories)
"""


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE silver.asset_categories (
            category TEXT PRIMARY KEY,
            note     TEXT
        )
        """
    )
    op.execute(
        """
        INSERT INTO silver.asset_categories (category, note) VALUES
            ('investments', 'Securities, funds, savings plans'),
            ('crypto',      'Crypto assets')
        """
    )

    op.execute(
        """
        CREATE TABLE silver.trades (
            natural_key TEXT PRIMARY KEY
                        REFERENCES silver.transactions(natural_key) ON DELETE CASCADE,
            -- Signed: positive when acquiring, negative when disposing, so a
            -- position is the running sum and needs no side-aware arithmetic.
            quantity    NUMERIC(28, 10) NOT NULL,
            side        TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
            isin        TEXT,
            instrument  TEXT,
            asset_class TEXT,
            unit_price  NUMERIC(20, 10)
        )
        """
    )
    op.execute("CREATE INDEX ix_trades_isin ON silver.trades(isin)")

    for v in _SPEND_VIEWS:
        op.execute(f"DROP VIEW IF EXISTS gold.{v}")
    for sql in _spend_views(_NEW_WHERE):
        op.execute(sql)

    # --- Investment-specific views ------------------------------------------
    # Positions, from the sources that disclose instruments. `invested` is the
    # cash cost basis (what actually left the account), not quantity*price:
    # fees are part of what an instrument cost you.
    op.execute(
        """
        CREATE VIEW gold.v_holdings AS
        SELECT
            tr.isin,
            min(tr.instrument)                                   AS instrument,
            min(tr.asset_class)                                  AS asset_class,
            sum(tr.quantity)                                     AS units,
            sum(-t.amount)                                       AS invested,
            count(*)                                             AS n_trades,
            min(t.value_date)                                    AS first_trade,
            max(t.value_date)                                    AS last_trade,
            -- Last price we ever saw quoted, with the date it refers to. NOT a
            -- market quote: it ages, and the UI must say so rather than imply
            -- this is today's value.
            (array_agg(tr.unit_price ORDER BY t.value_date DESC))[1]  AS last_price,
            sum(tr.quantity) * (array_agg(tr.unit_price ORDER BY t.value_date DESC))[1]
                                                                 AS value_at_last_price
        FROM silver.trades tr
        JOIN silver.transactions t ON t.natural_key = tr.natural_key
        GROUP BY tr.isin
        """
    )

    # Contributions over time. Split by whether we know what was bought, because
    # a transfer to an outside broker is money invested whose contents are not
    # in our documents — a real state, not missing data.
    op.execute(
        """
        CREATE VIEW gold.v_investment_month AS
        SELECT
            date_trunc('month', t.value_date)::date AS month,
            sum(-t.amount) FILTER (WHERE t.amount < 0)                   AS contributed,
            sum(t.amount)  FILTER (WHERE t.amount > 0)                   AS returned,
            sum(-t.amount)                                               AS net_invested,
            sum(-t.amount) FILTER (WHERE t.amount < 0 AND tr.natural_key IS NOT NULL)
                                                                         AS into_known,
            sum(-t.amount) FILTER (WHERE t.amount < 0 AND tr.natural_key IS NULL)
                                                                         AS into_unknown,
            count(*)                                                     AS n_movements
        FROM silver.transactions t
        LEFT JOIN silver.trades tr ON tr.natural_key = t.natural_key
        WHERE t.transfer_group IS NULL
          AND t.category IN (SELECT category FROM silver.asset_categories)
        GROUP BY 1
        """
    )

    for v in ("v_holdings", "v_investment_month", *_SPEND_VIEWS):
        op.execute(f"GRANT SELECT ON gold.{v} TO query_reader")


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS gold.v_investment_month")
    op.execute("DROP VIEW IF EXISTS gold.v_holdings")
    for v in _SPEND_VIEWS:
        op.execute(f"DROP VIEW IF EXISTS gold.{v}")
    for sql in _spend_views(_OLD_WHERE):
        op.execute(sql)
    op.execute("DROP TABLE IF EXISTS silver.trades")
    op.execute("DROP TABLE IF EXISTS silver.asset_categories")
