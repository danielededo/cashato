"""Data-model corrections.

Six independent corrections, batched:

1. ``v_income_expense_month`` loses ``net_excl_investments`` — dead since 0014:
   the asset exclusion moved into the view's WHERE, so the old FILTER matched
   every remaining row and the column was identically equal to ``net``.
2. ``category``/``category_source`` become NOT NULL (+ CHECK on the source
   vocabulary). Every writer always sets them; a NULL category would silently
   vanish from every spend view (``NOT IN`` is NULL-hostile) — the worst
   failure mode for a finance dashboard, so the DB now forbids it.
3. Index hygiene: ``booking_date`` was indexed (as ``ix_tx_data_contabile``,
   a name 0005 never renamed) but appears in no query; ``value_date`` drives
   every view, filter and sort and had no index. The two Italian index names
   are renamed while at it.
4. ``bronze.raw_files.rows_failed`` dropped: orphan of the row-level bronze
   that 0007 removed — no reader, no writer, always 0.
5. ``v_holdings``: unknown-instrument trades (NULL isin) are excluded — GROUP
   BY folds all NULLs into one phantom holding (their money is already
   reported as ``v_investment_month.into_unknown``); the last-price pick gets
   a deterministic tiebreak and skips NULL prices; ``invested`` now matches
   its own comment ("cash cost basis") by summing buys only.
6. ``silver.trades`` enforces the sign convention its comment declares;
   ``v_accounts``' fallback name uses the ACCOUNT ID, not the source — two
   seen-only accounts of one source both rendered as "Revolut".

Revision ID: 0018_data_model_review
Revises: 0017_account_display_fallback
Create Date: 2026-07-26
"""

from alembic import op

revision = "0018_data_model_review"
down_revision = "0017_account_display_fallback"
branch_labels = None
depends_on = None

_SPEND_WHERE = """
WHERE transfer_group IS NULL
  AND category NOT IN (SELECT category FROM silver.asset_categories)
"""

_INCOME_EXPENSE_VIEW = f"""
CREATE VIEW gold.v_income_expense_month AS
SELECT date_trunc('month', value_date)::date AS month,
       sum(amount) FILTER (WHERE amount > 0) AS income,
       sum(amount) FILTER (WHERE amount < 0) AS expense,
       sum(amount)                           AS net
FROM silver.transactions {_SPEND_WHERE}
GROUP BY 1
"""

_HOLDINGS_VIEW = """
CREATE OR REPLACE VIEW gold.v_holdings AS
SELECT
    tr.isin,
    min(tr.instrument)                                   AS instrument,
    min(tr.asset_class)                                  AS asset_class,
    sum(tr.quantity)                                     AS units,
    -- Cash cost basis: what actually LEFT the account, so buys only — with
    -- sells included this silently became "net invested" on the first sale,
    -- contradicting this very comment.
    sum(-t.amount) FILTER (WHERE tr.side = 'buy')        AS invested,
    count(*)                                             AS n_trades,
    min(t.value_date)                                    AS first_trade,
    max(t.value_date)                                    AS last_trade,
    -- Last price we ever saw quoted. NOT a market quote: it ages, and the UI
    -- must say so. Deterministic tiebreak on same-day trades; NULL prices
    -- never shadow an older real one.
    (array_agg(tr.unit_price ORDER BY t.value_date DESC, t.natural_key DESC)
        FILTER (WHERE tr.unit_price IS NOT NULL))[1]     AS last_price,
    sum(tr.quantity) *
    (array_agg(tr.unit_price ORDER BY t.value_date DESC, t.natural_key DESC)
        FILTER (WHERE tr.unit_price IS NOT NULL))[1]     AS value_at_last_price
FROM silver.trades tr
JOIN silver.transactions t ON t.natural_key = tr.natural_key
-- Unknown instruments must not GROUP into one phantom holding: their
-- contributions are already reported by v_investment_month.into_unknown.
WHERE tr.isin IS NOT NULL
GROUP BY tr.isin
"""

_ACCOUNTS_VIEW = """
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
            -- Fallback on the ACCOUNT ID, not the source: two undescribed
            -- accounts of one source (revolut_savings_eur, revolut_crypto)
            -- both rendered as "Revolut" — one label, two accounts.
            initcap(replace(COALESCE(a.account_id, s.account_id), '_', ' '))
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
    # 1. net_excl_investments: dropping a column needs DROP + CREATE.
    op.execute("DROP VIEW gold.v_income_expense_month")
    op.execute(_INCOME_EXPENSE_VIEW)

    # 2. Constraints the code already honors everywhere.
    op.execute("ALTER TABLE silver.transactions ALTER COLUMN category SET NOT NULL")
    op.execute("ALTER TABLE silver.transactions ALTER COLUMN category_source SET NOT NULL")
    op.execute(
        "ALTER TABLE silver.transactions ADD CONSTRAINT category_source_vocabulary "
        "CHECK (category_source IN ('mcc', 'model', 'rule', 'manual', 'default'))"
    )

    # 3. Index on what is actually queried; English names.
    op.execute("DROP INDEX silver.ix_tx_data_contabile")
    op.execute("CREATE INDEX ix_tx_value_date ON silver.transactions (value_date, id)")
    op.execute("ALTER INDEX silver.ix_tx_conto RENAME TO ix_tx_account")
    op.execute("ALTER INDEX silver.ix_tx_categoria RENAME TO ix_tx_category")

    # 4. Dead column.
    op.execute("ALTER TABLE bronze.raw_files DROP COLUMN rows_failed")

    # 5./6. View + constraint corrections.
    op.execute(_HOLDINGS_VIEW)
    op.execute(
        "ALTER TABLE silver.trades ADD CONSTRAINT trades_sign_matches_side "
        "CHECK ((side = 'buy' AND quantity > 0) OR (side = 'sell' AND quantity < 0))"
    )
    op.execute(_ACCOUNTS_VIEW)


def downgrade() -> None:
    op.execute("ALTER TABLE silver.trades DROP CONSTRAINT trades_sign_matches_side")
    op.execute("ALTER TABLE bronze.raw_files ADD COLUMN rows_failed INT NOT NULL DEFAULT 0")
    op.execute("ALTER INDEX silver.ix_tx_category RENAME TO ix_tx_categoria")
    op.execute("ALTER INDEX silver.ix_tx_account RENAME TO ix_tx_conto")
    op.execute("DROP INDEX silver.ix_tx_value_date")
    op.execute("CREATE INDEX ix_tx_data_contabile ON silver.transactions (booking_date)")
    op.execute(
        "ALTER TABLE silver.transactions DROP CONSTRAINT category_source_vocabulary"
    )
    op.execute("ALTER TABLE silver.transactions ALTER COLUMN category_source DROP NOT NULL")
    op.execute("ALTER TABLE silver.transactions ALTER COLUMN category DROP NOT NULL")
    op.execute("DROP VIEW gold.v_income_expense_month")
    op.execute(
        _INCOME_EXPENSE_VIEW.replace(
            "sum(amount)                           AS net",
            "sum(amount)                           AS net,\n"
            "       sum(amount) FILTER (WHERE category NOT IN ('investments', 'crypto'))"
            "                                             AS net_excl_investments",
        )
    )
    # v_holdings / v_accounts: previous definitions live in 0014 / 0017.
