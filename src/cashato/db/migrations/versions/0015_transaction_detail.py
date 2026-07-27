"""gold.v_transaction_detail: everything known about one movement

The list view is deliberately narrow. Investigating a single movement needs the
things normally left out: which file it came from, why it got its category and
how sure that was, the raw provider signals (MCC, the provider's own category),
and the instrument if it was a trade.

Assembled as a gold view for the same reason as v_transactions — query-api holds
SELECT on gold only, so the joins into silver and bronze happen with the view
owner's rights rather than by widening the reader role.

Revision ID: 0015_transaction_detail
Revises: 0014_investments
"""

from __future__ import annotations

from alembic import op

revision = "0015_transaction_detail"
down_revision = "0014_investments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE VIEW gold.v_transaction_detail AS
        SELECT
            t.natural_key,
            t.value_date,
            t.booking_date,
            t.description,
            t.amount,
            t.currency,
            t.account,
            t.source,
            t.category,
            t.category_source,
            t.category_confidence,
            -- Provider signals kept for transparency: mcc feeds the resolver,
            -- native_category never does (bootstrap-only) but seeing it explains
            -- a lot when a categorization looks odd.
            t.mcc,
            t.native_category,
            t.transfer_group,
            f.filename        AS file_name,
            f.uploaded_at     AS file_uploaded_at,
            f.sha256          AS file_sha256,
            tr.isin,
            tr.instrument,
            tr.asset_class,
            tr.quantity,
            tr.unit_price,
            tr.side
        FROM silver.transactions t
        LEFT JOIN bronze.raw_files f ON f.id = t.file_id
        LEFT JOIN silver.trades   tr ON tr.natural_key = t.natural_key
        """
    )
    op.execute("""DO $$ BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'query_reader') THEN
        GRANT SELECT ON gold.v_transaction_detail TO query_reader;
    END IF;
END $$""")


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS gold.v_transaction_detail")
