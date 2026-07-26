"""silver.accounts: user-chosen display name, overriding the extracted one

What the documents disclose is often unwieldy ("Revolut Bank UAB (Italian
Branch) · Personal Account"). The extracted parts stay exactly as read — they
are evidence — and the override is a separate column layered on top, so clearing
it restores the derived name rather than losing it.

Revision ID: 0013_account_display_override
Revises: 0012_v_accounts_full_outer
"""

from __future__ import annotations

from alembic import op

revision = "0013_account_display_override"
down_revision = "0012_v_accounts_full_outer"
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
    a.display_name_override,
    -- The user's name wins; otherwise compose from the parts we actually have,
    -- falling back to the opaque id when nothing described this account.
    COALESCE(
        a.display_name_override,
        COALESCE(a.bank_name, a.source, s.source, s.account_id)
          || COALESCE(' · ' || a.product, '')
          || CASE WHEN a.holding_modality = 'joint' THEN ' (Joint)' ELSE '' END
    )                                             AS display_name,
    COALESCE(s.transactions, 0)                   AS transactions,
    s.first_movement,
    s.last_movement
FROM silver.accounts a
FULL OUTER JOIN seen s ON s.account_id = a.account_id
"""


def upgrade() -> None:
    op.execute("ALTER TABLE silver.accounts ADD COLUMN display_name_override TEXT")
    op.execute("DROP VIEW IF EXISTS gold.v_accounts")
    op.execute(_VIEW)
    op.execute("GRANT SELECT ON gold.v_accounts TO query_reader")


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS gold.v_accounts")
    op.execute("ALTER TABLE silver.accounts DROP COLUMN IF EXISTS display_name_override")
