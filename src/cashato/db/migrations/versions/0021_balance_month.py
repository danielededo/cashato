"""gold.v_balance_month — month-end declared balance per account, carried forward

The statements' own balance anchors (``silver.balances``) are point-in-time:
dense for Revolut/Trade Republic (per day with movements), sparse for Intesa
(quarter ends). Wealth over time needs one figure per account per month, so
this view lays a monthly grid over the anchors and carries each account's
last declared balance forward — a balance stays what it was until a statement
says otherwise.

``as_of`` keeps the anchor date the figure was carried from: a June balance
shown in September is not a lie, but the reader deserves to know its age.
Months before an account's first anchor have no row — the account's history
simply has not started, which is different from a zero balance.

Revision ID: 0021_balance_month
Revises: 0020_balance_basis
"""

from __future__ import annotations

from alembic import op

revision = "0021_balance_month"
down_revision = "0020_balance_basis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE VIEW gold.v_balance_month AS
        WITH bounds AS (
            SELECT date_trunc('month', min(balance_date))::date AS first_month,
                   date_trunc('month', max(balance_date))::date AS last_month
            FROM silver.balances
        ),
        months AS (
            SELECT generate_series(first_month, last_month, interval '1 month')::date AS month
            FROM bounds
        ),
        accts AS (
            SELECT DISTINCT account, currency FROM silver.balances
        )
        SELECT m.month,
               a.account,
               a.currency,
               lb.balance,
               lb.balance_date AS as_of
        FROM months m
        CROSS JOIN accts a
        CROSS JOIN LATERAL (
            SELECT b.balance, b.balance_date
            FROM silver.balances b
            WHERE b.account = a.account
              AND b.balance_date < (m.month + interval '1 month')::date
            ORDER BY b.balance_date DESC
            LIMIT 1
        ) lb
        """
    )

    # Cluster role when present; a plain local Postgres has none of them.
    op.execute(
        """DO $$ BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'query_reader') THEN
        GRANT SELECT ON gold.v_balance_month TO query_reader;
    END IF;
END $$"""
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS gold.v_balance_month")
