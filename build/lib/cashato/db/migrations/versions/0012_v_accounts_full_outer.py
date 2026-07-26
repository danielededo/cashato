"""gold.v_accounts: list every account, described or not

The first cut drove the view off silver.accounts alone, so an account only
appeared once some document described it. That hid real accounts: the Trade
Republic transaction-export CSV is columnar data with no header block, so
ingesting only the CSV yields 254 movements on an account the view never showed.

The two sets genuinely differ in both directions — a statement can describe an
account whose currency we skip (Revolut MAD/RON/GBP: metadata, no movements),
and an account can have movements with nothing describing it (Trade Republic
CSV) — so it is a FULL OUTER JOIN, and display_name degrades to the account id
rather than inventing a name.

Revision ID: 0012_v_accounts_full_outer
Revises: 0011_accounts_dimension
"""

from __future__ import annotations

from alembic import op

revision = "0012_v_accounts_full_outer"
down_revision = "0011_accounts_dimension"
branch_labels = None
depends_on = None

_VIEW = """
CREATE OR REPLACE VIEW gold.v_accounts AS
WITH seen AS (
    SELECT account          AS account_id,
           MIN(source)      AS source,
           COUNT(*)         AS transactions,
           MIN(value_date)  AS first_movement,
           MAX(value_date)  AS last_movement
    FROM silver.transactions
    GROUP BY account
)
SELECT
    COALESCE(a.account_id, s.account_id)          AS account_id,
    COALESCE(a.source, s.source)                  AS source,
    a.bank_name,
    a.product,
    a.holding_modality,
    CASE a.holding_modality
        WHEN 'joint' THEN true
        WHEN 'individual' THEN false
        ELSE NULL
    END                                           AS is_joint,
    a.currency,
    a.iban,
    -- Composed from the parts we actually have, so no bank name is ever
    -- hardcoded downstream; falls back to the opaque id when nothing described
    -- this account.
    COALESCE(a.bank_name, a.source, s.source, s.account_id)
      || COALESCE(' · ' || a.product, '')
      || CASE WHEN a.holding_modality = 'joint' THEN ' (Joint)' ELSE '' END
                                                  AS display_name,
    COALESCE(s.transactions, 0)                   AS transactions,
    s.first_movement,
    s.last_movement
FROM silver.accounts a
FULL OUTER JOIN seen s ON s.account_id = a.account_id
"""


def upgrade() -> None:
    # CREATE OR REPLACE cannot change a view's column set; drop first.
    op.execute("DROP VIEW IF EXISTS gold.v_accounts")
    op.execute(_VIEW)
    op.execute("GRANT SELECT ON gold.v_accounts TO query_reader")


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS gold.v_accounts")
