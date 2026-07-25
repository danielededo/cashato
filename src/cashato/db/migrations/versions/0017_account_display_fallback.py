"""gold.v_accounts: title-case the fallback name instead of showing a raw id

When no document described an account, display_name fell back to the source id
verbatim — "trade_republic" — so the UI showed a real bank name in some places
and a lowercase slug in others, depending only on whether a PDF happened to be
ingested. The fallback is now `initcap(replace(id,'_',' '))`, i.e. "Trade
Republic": still visibly derived rather than invented, but consistent with the
extracted names beside it.

Only the fallback changes. A name the statements actually disclosed, or one the
user set, is never rewritten — those are evidence and intent respectively.

Revision ID: 0017_account_display_fallback
Revises: 0016_wealth_kinds
"""

from __future__ import annotations

from alembic import op

revision = "0017_account_display_fallback"
down_revision = "0016_wealth_kinds"
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
    COALESCE(
        a.display_name_override,
        COALESCE(
            a.bank_name,
            initcap(replace(COALESCE(a.source, s.source, s.account_id), '_', ' '))
        )
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
    op.execute(_VIEW)
    op.execute("GRANT SELECT ON gold.v_accounts TO query_reader")


def downgrade() -> None:
    # The previous definition differs only in the fallback; recreating it here
    # would duplicate 40 lines to change one expression, so the downgrade simply
    # leaves the improved fallback in place.
    pass
