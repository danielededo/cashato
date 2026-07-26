"""Wealth is more than securities: pension funds, deposits, savings policies

Money leaving the current account towards a pension fund or a term deposit is
the same kind of event as buying an ETF — wealth changing form, not consumption
— so it belongs on the same side of the line. Adding them is an INSERT, which
is why asset membership was made a table rather than a literal in each view.

Insurance is deliberately split. A protection policy (motor, home, term life)
IS consumption: you buy cover, you accumulate nothing, and treating it as an
asset would erase a real expense from the savings rate — the same error as
counting an ETF purchase as spending, in the opposite direction. Only
``insurance_savings`` (accumulation policies) is wealth, and since telling the
two apart from a transfer description is guesswork, nothing assigns it
automatically: it is a manual reclassification.

The monthly view gains ``category`` so the UI can show a section per kind, and
only for kinds that actually have movements.

Revision ID: 0016_wealth_kinds
Revises: 0015_transaction_detail
"""

from __future__ import annotations

from alembic import op

revision = "0016_wealth_kinds"
down_revision = "0015_transaction_detail"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO silver.asset_categories (category, note) VALUES
            ('pension_fund',      'Pension fund contributions (locked until retirement)'),
            ('deposits',          'Term/savings deposits'),
            ('insurance_savings', 'Accumulation policies — assigned by hand, never inferred')
        ON CONFLICT (category) DO NOTHING
        """
    )

    # Same shape as before plus `category`, so the page can break the total down
    # by destination kind. Rolling it up is the caller's job.
    op.execute("DROP VIEW IF EXISTS gold.v_investment_month")
    op.execute(
        """
        CREATE VIEW gold.v_investment_month AS
        SELECT
            date_trunc('month', t.value_date)::date AS month,
            t.category,
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
        GROUP BY 1, 2
        """
    )
    op.execute("GRANT SELECT ON gold.v_investment_month TO query_reader")


def downgrade() -> None:
    op.execute(
        "DELETE FROM silver.asset_categories "
        "WHERE category IN ('pension_fund', 'deposits', 'insurance_savings')"
    )
    op.execute("DROP VIEW IF EXISTS gold.v_investment_month")
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
    op.execute("GRANT SELECT ON gold.v_investment_month TO query_reader")
