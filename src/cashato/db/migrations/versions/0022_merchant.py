"""silver.transactions.merchant + purchase_time, backfilled from descriptions

Statements bury the counterparty inside boilerplate; parsers/merchant.py digs
out the merchant (and the time of day, where the text carries one) so gold can
aggregate spending by merchant instead of by raw text. The columns are stored
— not computed per request — because the list/aggregate endpoints filter and
group on them server-side.

Both fields are derived from the description, so the backfill here uses the
exact extractor the loader uses from now on; rows whose form yields nothing
(wire transfers, ATM, securities) legitimately stay NULL.

Revision ID: 0022_merchant
Revises: 0021_balance_month
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from cashato.parsers.merchant import extract_merchant

revision = "0022_merchant"
down_revision = "0021_balance_month"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("merchant", sa.Text(), nullable=True), schema="silver")
    op.add_column(
        "transactions", sa.Column("purchase_time", sa.Time(), nullable=True), schema="silver"
    )

    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, source, description FROM silver.transactions")
    ).fetchall()
    updates = []
    for row in rows:
        info = extract_merchant(row.source, row.description)
        if info.merchant is not None or info.purchase_time is not None:
            updates.append({"id": row.id, "m": info.merchant, "t": info.purchase_time})
    if updates:
        conn.execute(
            sa.text(
                "UPDATE silver.transactions SET merchant = :m, purchase_time = :t WHERE id = :id"
            ),
            updates,
        )

    op.execute(
        """
        CREATE OR REPLACE VIEW gold.v_transactions AS
        SELECT id, value_date, booking_date, description, amount, currency,
               account, source, category, category_source, category_confidence,
               transfer_group, natural_key, merchant, purchase_time
        FROM silver.transactions
        """
    )
    op.execute(
        """
        CREATE OR REPLACE VIEW gold.v_transaction_detail AS
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
            tr.side,
            t.merchant,
            t.purchase_time
        FROM silver.transactions t
        LEFT JOIN bronze.raw_files f ON f.id = t.file_id
        LEFT JOIN silver.trades   tr ON tr.natural_key = t.natural_key
        """
    )


def downgrade() -> None:
    # OR REPLACE cannot remove view columns: recreate both views, then re-grant.
    op.execute("DROP VIEW IF EXISTS gold.v_transaction_detail")
    op.execute("DROP VIEW IF EXISTS gold.v_transactions")
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
    op.execute(
        """
        CREATE VIEW gold.v_transactions AS
        SELECT id, value_date, booking_date, description, amount, currency,
               account, source, category, category_source, category_confidence,
               transfer_group, natural_key
        FROM silver.transactions
        """
    )
    op.execute("""DO $$ BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'query_reader') THEN
        GRANT SELECT ON gold.v_transactions, gold.v_transaction_detail TO query_reader;
    END IF;
END $$""")
    op.drop_column("transactions", "purchase_time", schema="silver")
    op.drop_column("transactions", "merchant", schema="silver")
