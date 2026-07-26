"""internal transfers: transfer_group column + GOLD views exclude them

Revision ID: 0006_internal_transfers
Revises: 0005_english_schema
Create Date: 2026-07-18

Adds ``silver.transactions.transfer_group`` (shared id of the two legs of an
internal transfer between own accounts) and rebuilds the GOLD spend views to
exclude those legs from income/expense (they net to zero, not spending). A
dedicated ``v_internal_transfers`` view keeps them visible.
"""

from alembic import op

revision = "0006_internal_transfers"
down_revision = "0005_english_schema"
branch_labels = None
depends_on = None

_SPEND_VIEWS = ["v_category_month", "v_income_expense_month", "v_category_totals"]


def _create_views(where: str) -> None:
    op.execute(
        f"""
        CREATE VIEW gold.v_category_month AS
        SELECT date_trunc('month', value_date)::date AS month, category,
               count(*) AS n_movements, sum(amount) AS total
        FROM silver.transactions {where}
        GROUP BY 1, 2
        """
    )
    op.execute(
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
        """
    )
    op.execute(
        f"""
        CREATE VIEW gold.v_category_totals AS
        SELECT category, count(*) AS n_movements,
               sum(amount) FILTER (WHERE amount < 0) AS expense,
               sum(amount) FILTER (WHERE amount > 0) AS income,
               sum(amount) AS net
        FROM silver.transactions {where}
        GROUP BY 1
        """
    )


def upgrade() -> None:
    op.execute("ALTER TABLE silver.transactions ADD COLUMN transfer_group TEXT")
    op.execute("CREATE INDEX ix_tx_transfer_group ON silver.transactions(transfer_group)")
    for v in _SPEND_VIEWS:
        op.execute(f"DROP VIEW IF EXISTS gold.{v}")
    # spend views exclude internal-transfer legs
    _create_views("WHERE transfer_group IS NULL")
    # dedicated view to inspect the detected internal transfers
    op.execute(
        """
        CREATE VIEW gold.v_internal_transfers AS
        SELECT transfer_group, value_date, account, amount, description
        FROM silver.transactions
        WHERE transfer_group IS NOT NULL
        ORDER BY transfer_group, amount
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS gold.v_internal_transfers")
    for v in _SPEND_VIEWS:
        op.execute(f"DROP VIEW IF EXISTS gold.{v}")
    _create_views("")  # no filter
    op.execute("DROP INDEX IF EXISTS silver.ix_tx_transfer_group")
    op.execute("ALTER TABLE silver.transactions DROP COLUMN IF EXISTS transfer_group")
