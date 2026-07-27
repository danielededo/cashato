"""Balance anchors declare their date basis; reconciliation sums by it

Classic bank statements (Intesa) order and total by the BOOKING date, while
the dedup key — and therefore the previous reconciliation view — lives on the
VALUE date. Every valuta crossing a quarter boundary showed up as a pair of
mirrored discrepancies: benign, but red, and a check that is habitually red
stops protecting anything.

Each anchor now carries ``basis`` ('value' | 'booking', declared by the
adapter that extracted it) and ``v_reconciliation`` sums the movements by that
date. This only works because the loader now converges ``booking_date`` to the
statement that actually knows it (a row whose two dates differ can only come
from the quarterly; the 13-month export's single date flattened booking to
value wherever it inserted the row first) — re-running ingestion over the
stored files repairs the flattened history.

Revision ID: 0020_balance_basis
Revises: 0019_balances_reconciliation
"""

from __future__ import annotations

from alembic import op

revision = "0020_balance_basis"
down_revision = "0019_balances_reconciliation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE silver.balances
            ADD COLUMN basis TEXT NOT NULL DEFAULT 'value'
                CHECK (basis IN ('value', 'booking'))
        """
    )
    # Anchors already extracted from Intesa quarterlies are booking-based; the
    # loader stamps this on every future upsert, this covers the rows in place.
    op.execute("UPDATE silver.balances SET basis = 'booking' WHERE source = 'intesa'")

    # Same columns, new join date: OR REPLACE keeps existing grants.
    op.execute(
        """
        CREATE OR REPLACE VIEW gold.v_reconciliation AS
        WITH anchors AS (
            SELECT account, balance_date, balance, basis,
                   LEAD(balance_date) OVER w AS next_date,
                   LEAD(balance)      OVER w AS next_balance
            FROM silver.balances
            WINDOW w AS (PARTITION BY account ORDER BY balance_date)
        )
        SELECT a.account,
               a.balance_date                    AS from_date,
               a.next_date                       AS to_date,
               a.balance                         AS from_balance,
               a.next_balance                    AS to_balance,
               a.next_balance - a.balance        AS expected_delta,
               COALESCE(sum(t.amount), 0)        AS actual_delta,
               COALESCE(sum(t.amount), 0)
                 - (a.next_balance - a.balance)  AS discrepancy,
               count(t.natural_key)              AS n_movements
        FROM anchors a
        LEFT JOIN silver.transactions t
          ON t.account = a.account
         AND (CASE WHEN a.basis = 'booking' THEN t.booking_date
                   ELSE t.value_date END) >  a.balance_date
         AND (CASE WHEN a.basis = 'booking' THEN t.booking_date
                   ELSE t.value_date END) <= a.next_date
        WHERE a.next_date IS NOT NULL
        GROUP BY a.account, a.balance_date, a.next_date, a.balance, a.next_balance
        """
    )

    op.execute(
        """
        CREATE OR REPLACE VIEW gold.v_balances AS
        SELECT account, balance_date, balance, currency, source, basis
        FROM silver.balances
        """
    )


def downgrade() -> None:
    # OR REPLACE cannot DROP a view column: recreate and re-grant.
    op.execute("DROP VIEW gold.v_balances")
    op.execute(
        """
        CREATE VIEW gold.v_balances AS
        SELECT account, balance_date, balance, currency, source
        FROM silver.balances
        """
    )
    op.execute(
        """DO $$ BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'query_reader') THEN
        GRANT SELECT ON gold.v_balances TO query_reader;
    END IF;
END $$"""
    )
    op.execute(
        """
        CREATE OR REPLACE VIEW gold.v_reconciliation AS
        WITH anchors AS (
            SELECT account, balance_date, balance,
                   LEAD(balance_date) OVER w AS next_date,
                   LEAD(balance)      OVER w AS next_balance
            FROM silver.balances
            WINDOW w AS (PARTITION BY account ORDER BY balance_date)
        )
        SELECT a.account, a.balance_date AS from_date, a.next_date AS to_date,
               a.balance AS from_balance, a.next_balance AS to_balance,
               a.next_balance - a.balance AS expected_delta,
               COALESCE(sum(t.amount), 0) AS actual_delta,
               COALESCE(sum(t.amount), 0) - (a.next_balance - a.balance) AS discrepancy,
               count(t.natural_key) AS n_movements
        FROM anchors a
        LEFT JOIN silver.transactions t
          ON t.account = a.account
         AND t.value_date >  a.balance_date
         AND t.value_date <= a.next_date
        WHERE a.next_date IS NOT NULL
        GROUP BY a.account, a.balance_date, a.next_date, a.balance, a.next_balance
        """
    )
    op.execute("ALTER TABLE silver.balances DROP COLUMN basis")
