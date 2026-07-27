"""silver.balances + gold reconciliation views

Statements declare their own balances (Revolut/Trade Republic a per-row running
balance, Intesa opening/closing lines). Those are the only ground truth we have
for parser completeness: between two consecutive anchors of one account, the
sum of the parsed movements must equal the balance delta. ``v_reconciliation``
computes exactly that; a non-zero discrepancy means a parser lost or invented
rows in that interval (or two files disagree).

An anchor means: balance AFTER every movement with ``value_date <= balance_date``.
The sums are taken over value_date — for Intesa the booking date is unreliable
across formats (the 13-month export's single date is the quarterly's VALUE
date), so a valuta crossing the quarter boundary can show up as a small
symmetric discrepancy on the two adjacent intervals. That is a property of the
data, not a bug: the view exposes it rather than hiding it.

Also deletes the historical Revolut "Fee: …" rows: the statement's own balance
chain proves the Fees column is informational (the fee is already inside
``Money in/out``), so the separate fee transactions the parser used to emit
double-counted every fee. The parser no longer emits them; this removes the
ones already ingested.

Revision ID: 0019_balances_reconciliation
Revises: 0018_data_model_review
"""

from __future__ import annotations

from alembic import op

revision = "0019_balances_reconciliation"
down_revision = "0018_data_model_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE silver.balances (
            account      TEXT          NOT NULL,
            balance_date DATE          NOT NULL,
            balance      NUMERIC(14,2) NOT NULL,
            currency     TEXT          NOT NULL DEFAULT 'EUR',
            source       TEXT          NOT NULL,
            file_id      BIGINT        REFERENCES bronze.raw_files(id) ON DELETE SET NULL,
            updated_at   TIMESTAMPTZ   NOT NULL DEFAULT now(),
            PRIMARY KEY (account, balance_date)
        )
        """
    )

    op.execute(
        """
        CREATE VIEW gold.v_balances AS
        SELECT account, balance_date, balance, currency, source
        FROM silver.balances
        """
    )

    op.execute(
        """
        CREATE VIEW gold.v_reconciliation AS
        WITH anchors AS (
            SELECT account, balance_date, balance,
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
         AND t.value_date >  a.balance_date
         AND t.value_date <= a.next_date
        WHERE a.next_date IS NOT NULL
        GROUP BY a.account, a.balance_date, a.next_date, a.balance, a.next_balance
        """
    )

    op.execute(
        "DELETE FROM silver.transactions "
        "WHERE source = 'revolut' AND description LIKE 'Fee: %'"
    )

    # Cluster roles when present; a plain local Postgres has none of them.
    op.execute(
        """DO $$ BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'etl_writer') THEN
        GRANT SELECT, INSERT, UPDATE ON silver.balances TO etl_writer;
    END IF;
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'query_reader') THEN
        GRANT SELECT ON gold.v_balances, gold.v_reconciliation TO query_reader;
    END IF;
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'ml_reader') THEN
        GRANT SELECT ON silver.balances TO ml_reader;
    END IF;
END $$"""
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS gold.v_reconciliation")
    op.execute("DROP VIEW IF EXISTS gold.v_balances")
    op.execute("DROP TABLE IF EXISTS silver.balances")
