"""gold.v_transactions: read-only projection of silver for the query API

The frontend (Transactions / Category drill-down) needs the individual rows, but
``query-api`` must stay read-only on ``gold`` only (least privilege: the
``query_reader`` role has SELECT on gold, not on silver). Exposing a thin gold
view keeps that boundary: the view reads silver with the owner's rights, the API
reads the view. No new data, just a curated projection.

Revision ID: 0008_gold_transactions_view
Revises: 0007_drop_raw_rows
"""

from __future__ import annotations

from alembic import op

revision = "0008_gold_transactions_view"
down_revision = "0007_drop_raw_rows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE VIEW gold.v_transactions AS
        SELECT id, value_date, booking_date, description, amount, currency,
               account, source, category, category_source, category_confidence,
               transfer_group, natural_key
        FROM silver.transactions
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS gold.v_transactions")
