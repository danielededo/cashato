"""Baseline: the full schema, squashed from the original chain 0001-0022.

One migration creates everything the 22-step history converged to: the three
medallion schemas, the bronze/silver/gold tables with their final columns and
constraints, every gold view in its final definition, and the
``asset_categories`` seed rows (reference data a fresh database needs).

The revision id is kept equal to the pre-squash head (``0022_merchant``), so a
database migrated with the old chain already has this id in ``alembic_version``
and ``upgrade head`` is a no-op — no manual stamp anywhere. The next migration
starts at 0023 with ``down_revision = "0022_merchant"``.

The old chain's data REPAIRS are deliberately dropped: the Revolut fee-row
delete (0019), the Intesa ``basis`` backfill (0020) and the merchant backfill
(0022) fixed rows ingested by older parser versions. A fresh database ingests
with the current parsers, which get all of it right at insert time; an
existing database already ran them.

Revision ID: 0022_merchant (pre-squash head, see above)
Revises: -
"""

from __future__ import annotations

from alembic import op

revision = "0022_merchant"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Schemas (namespaces in one DB, not separate databases) ---------------
    op.execute("CREATE SCHEMA IF NOT EXISTS bronze")
    op.execute("CREATE SCHEMA IF NOT EXISTS silver")
    op.execute("CREATE SCHEMA IF NOT EXISTS gold")

    # --- BRONZE: uploaded-file registry ---------------------------------------
    # There is no row-level landing table: the original files are retained on
    # disk, sha256 identifies them, and idempotency lives on natural_key.
    op.execute(
        """
        CREATE TABLE bronze.raw_files (
            id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            source         TEXT        NOT NULL,
            filename       TEXT        NOT NULL,
            sha256         TEXT        NOT NULL UNIQUE,
            size_bytes     BIGINT,
            uploaded_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            status         TEXT        NOT NULL DEFAULT 'pending'
                           CHECK (status IN ('pending','parsed','failed')),
            rows_total     INTEGER     NOT NULL DEFAULT 0,
            error          TEXT,
            -- Ingestion is idempotent: rows_new lets the UI say "imported N,
            -- M already present". Duplicates = rows_total - rows_new.
            rows_new       INTEGER     NOT NULL DEFAULT 0,
            -- Holder addressed in the statement header; the CSV/XLSX exports
            -- carry none, so NULL is a normal outcome, not a parse failure.
            account_holder TEXT
        )
        """
    )

    # --- SILVER: normalized transactions (the common schema) ------------------
    # category/category_source are NOT NULL: a NULL category would silently
    # vanish from every spend view (NOT IN is NULL-hostile).
    op.execute(
        """
        CREATE TABLE silver.transactions (
            id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            value_date          DATE          NOT NULL,
            booking_date        DATE          NOT NULL,
            description         TEXT          NOT NULL,
            amount              NUMERIC(18,4) NOT NULL,
            currency            TEXT          NOT NULL,
            account             TEXT          NOT NULL,
            source              TEXT          NOT NULL,
            category            TEXT          NOT NULL,
            natural_key         TEXT          NOT NULL UNIQUE,
            file_id             BIGINT        REFERENCES bronze.raw_files(id)
                                              ON DELETE SET NULL,
            created_at          TIMESTAMPTZ   NOT NULL DEFAULT now(),
            -- Provider's own category: bootstrap-only, never used at runtime.
            native_category     TEXT,
            category_confidence REAL,
            category_source     TEXT          NOT NULL
                CONSTRAINT category_source_vocabulary
                CHECK (category_source IN ('mcc', 'model', 'rule', 'manual', 'default')),
            mcc                 TEXT,
            -- Shared id of the two legs of an internal transfer.
            transfer_group      TEXT,
            -- Both derived from the description by parsers/merchant.py; stored
            -- because the list/aggregate endpoints filter and group on them.
            merchant            TEXT,
            purchase_time       TIME
        )
        """
    )
    op.execute("CREATE INDEX ix_tx_value_date ON silver.transactions (value_date, id)")
    op.execute("CREATE INDEX ix_tx_account ON silver.transactions (account)")
    op.execute("CREATE INDEX ix_tx_category ON silver.transactions (category)")
    op.execute("CREATE INDEX ix_tx_transfer_group ON silver.transactions (transfer_group)")

    # --- SILVER: account dimension ---------------------------------------------
    # The account *id* is hashed into natural_key, so it can never be renamed;
    # everything a document says about an account is display metadata keyed by
    # that stable id. All descriptive columns are nullable on purpose: sources
    # disclose very different amounts of metadata, and holding_modality NULL
    # means "the document did not say", which is NOT the same as individual.
    op.execute(
        """
        CREATE TABLE silver.accounts (
            account_id            TEXT PRIMARY KEY,
            source                TEXT        NOT NULL,
            bank_name             TEXT,
            product               TEXT,
            holding_modality      TEXT CHECK (holding_modality IN ('individual','joint')),
            currency              TEXT,
            iban                  TEXT,
            updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            -- The user's name, layered over the extracted parts (which stay
            -- as read — they are evidence); clearing it restores the derived name.
            display_name_override TEXT
        )
        """
    )

    # --- SILVER: which categories are wealth, not spending ---------------------
    # A row here removes a category from every spend view, so membership is
    # reference data (an INSERT, not a migration) and etl_writer cannot touch
    # it (revoked in the grants job).
    op.execute(
        """
        CREATE TABLE silver.asset_categories (
            category TEXT PRIMARY KEY,
            note     TEXT
        )
        """
    )
    op.execute(
        """
        INSERT INTO silver.asset_categories (category, note) VALUES
            ('investments',       'Securities, funds, savings plans'),
            ('crypto',            'Crypto assets'),
            ('pension_fund',      'Pension fund contributions (locked until retirement)'),
            ('deposits',          'Term/savings deposits'),
            ('insurance_savings', 'Accumulation policies — assigned by hand, never inferred')
        """
    )

    # --- SILVER: instrument-level trades ---------------------------------------
    # Keyed by natural_key, so a trade inherits the cash movement's dedup: the
    # same purchase read from the PDF (no instrument detail) and from the CSV
    # (full detail) is one movement with at most one trade row.
    op.execute(
        """
        CREATE TABLE silver.trades (
            natural_key TEXT PRIMARY KEY
                        REFERENCES silver.transactions(natural_key) ON DELETE CASCADE,
            -- Signed: positive when acquiring, negative when disposing, so a
            -- position is the running sum and needs no side-aware arithmetic.
            quantity    NUMERIC(28, 10) NOT NULL,
            side        TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
            isin        TEXT,
            instrument  TEXT,
            asset_class TEXT,
            unit_price  NUMERIC(20, 10),
            CONSTRAINT trades_sign_matches_side
                CHECK ((side = 'buy' AND quantity > 0) OR (side = 'sell' AND quantity < 0))
        )
        """
    )
    op.execute("CREATE INDEX ix_trades_isin ON silver.trades(isin)")

    # --- SILVER: statement-declared balance anchors -----------------------------
    # The only ground truth for parser completeness: between two consecutive
    # anchors of one account, the parsed movements must sum to the balance
    # delta. `basis` declares which date the source's balances follow
    # ('booking' for Intesa, 'value' where the dates coincide).
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
            basis        TEXT          NOT NULL DEFAULT 'value'
                         CHECK (basis IN ('value', 'booking')),
            PRIMARY KEY (account, balance_date)
        )
        """
    )

    # --- GOLD: ML tables ---------------------------------------------------------
    # Training dataset: canonical labels (rules/LLM/corrections), keyed by
    # normalized description. Provider-agnostic.
    op.execute(
        """
        CREATE TABLE gold.training_labels (
            id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            text_norm   TEXT NOT NULL,
            category    TEXT NOT NULL,
            source      TEXT NOT NULL,       -- rule | llm | manual | native
            confidence  REAL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (text_norm, source)
        )
        """
    )
    # User corrections (active learning): they feed the next retrains.
    op.execute(
        """
        CREATE TABLE gold.category_feedback (
            id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            natural_key  TEXT NOT NULL,
            category     TEXT NOT NULL,
            corrected_by TEXT,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    # --- GOLD: spend views -------------------------------------------------------
    # All three exclude internal-transfer legs (they net to zero, not spending)
    # and asset movements (buying an ETF is wealth changing shape, not
    # consumption; returns are excluded symmetrically — not income either).
    spend_where = """
        WHERE transfer_group IS NULL
          AND category NOT IN (SELECT category FROM silver.asset_categories)
    """
    op.execute(
        f"""
        CREATE VIEW gold.v_category_month AS
        SELECT date_trunc('month', value_date)::date AS month, category,
               count(*) AS n_movements, sum(amount) AS total
        FROM silver.transactions {spend_where}
        GROUP BY 1, 2
        """
    )
    op.execute(
        f"""
        CREATE VIEW gold.v_income_expense_month AS
        SELECT date_trunc('month', value_date)::date AS month,
               sum(amount) FILTER (WHERE amount > 0) AS income,
               sum(amount) FILTER (WHERE amount < 0) AS expense,
               sum(amount)                           AS net
        FROM silver.transactions {spend_where}
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
        FROM silver.transactions {spend_where}
        GROUP BY 1
        """
    )

    # Dedicated view to inspect the detected internal transfers.
    op.execute(
        """
        CREATE VIEW gold.v_internal_transfers AS
        SELECT transfer_group, value_date, account, amount, description
        FROM silver.transactions
        WHERE transfer_group IS NOT NULL
        ORDER BY transfer_group, amount
        """
    )

    # --- GOLD: row-level projections ---------------------------------------------
    # query-api holds SELECT on gold only (least privilege), so the reads into
    # silver/bronze happen here, with the view owner's rights.
    op.execute(
        """
        CREATE VIEW gold.v_transactions AS
        SELECT id, value_date, booking_date, description, amount, currency,
               account, source, category, category_source, category_confidence,
               transfer_group, natural_key, merchant, purchase_time
        FROM silver.transactions
        """
    )
    # Everything known about one movement: provenance file, category evidence,
    # raw provider signals (mcc feeds the resolver; native_category never does,
    # but seeing it explains a lot), and the instrument if it was a trade.
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
            tr.side,
            t.merchant,
            t.purchase_time
        FROM silver.transactions t
        LEFT JOIN bronze.raw_files f ON f.id = t.file_id
        LEFT JOIN silver.trades   tr ON tr.natural_key = t.natural_key
        """
    )

    # --- GOLD: accounts ------------------------------------------------------------
    # FULL OUTER JOIN: a statement can describe an account whose currency we
    # skip (metadata, no movements), and an account can have movements with
    # nothing describing it (Trade Republic CSV). The user's name wins;
    # otherwise compose from the parts we actually have, falling back to the
    # title-cased ACCOUNT ID (not the source: two undescribed accounts of one
    # source would render as one label).
    op.execute(
        """
        CREATE VIEW gold.v_accounts AS
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
    )

    # --- GOLD: investments -----------------------------------------------------------
    # Positions, from the sources that disclose instruments. Unknown
    # instruments (NULL isin) are excluded — GROUP BY would fold them into one
    # phantom holding; their money is already v_investment_month.into_unknown.
    op.execute(
        """
        CREATE VIEW gold.v_holdings AS
        SELECT
            tr.isin,
            min(tr.instrument)                                   AS instrument,
            min(tr.asset_class)                                  AS asset_class,
            sum(tr.quantity)                                     AS units,
            -- Cash cost basis: what actually LEFT the account, so buys only.
            sum(-t.amount) FILTER (WHERE tr.side = 'buy')        AS invested,
            count(*)                                             AS n_trades,
            min(t.value_date)                                    AS first_trade,
            max(t.value_date)                                    AS last_trade,
            -- Last price we ever saw quoted. NOT a market quote: it ages, and
            -- the UI must say so. Deterministic tiebreak on same-day trades;
            -- NULL prices never shadow an older real one.
            (array_agg(tr.unit_price ORDER BY t.value_date DESC, t.natural_key DESC)
                FILTER (WHERE tr.unit_price IS NOT NULL))[1]     AS last_price,
            sum(tr.quantity) *
            (array_agg(tr.unit_price ORDER BY t.value_date DESC, t.natural_key DESC)
                FILTER (WHERE tr.unit_price IS NOT NULL))[1]     AS value_at_last_price
        FROM silver.trades tr
        JOIN silver.transactions t ON t.natural_key = tr.natural_key
        WHERE tr.isin IS NOT NULL
        GROUP BY tr.isin
        """
    )

    # Contributions over time, by destination kind. Split by whether we know
    # what was bought: a transfer to an outside broker is money invested whose
    # contents are not in our documents — a real state, not missing data.
    op.execute(
        """
        CREATE VIEW gold.v_investment_month AS
        SELECT
            date_trunc('month', t.value_date)::date AS month,
            t.category,
            sum(-t.amount) FILTER (WHERE t.amount < 0)                   AS contributed,
            sum(t.amount)  FILTER (WHERE t.amount > 0)                   AS returned,
            sum(-t.amount)                                               AS net_invested,
            sum(-t.amount) FILTER (WHERE t.amount < 0 AND tr.natural_key IS NOT NULL)
                                                                         AS into_known,
            sum(-t.amount) FILTER (WHERE t.amount < 0 AND tr.natural_key IS NULL)
                                                                         AS into_unknown,
            count(*)                                                     AS n_movements
        FROM silver.transactions t
        LEFT JOIN silver.trades tr ON tr.natural_key = t.natural_key
        WHERE t.transfer_group IS NULL
          AND t.category IN (SELECT category FROM silver.asset_categories)
        GROUP BY 1, 2
        """
    )

    # --- GOLD: balances & reconciliation ---------------------------------------------
    op.execute(
        """
        CREATE VIEW gold.v_balances AS
        SELECT account, balance_date, balance, currency, source, basis
        FROM silver.balances
        """
    )

    # An anchor means: balance AFTER every movement dated <= balance_date, on
    # the anchor's declared basis. A non-zero discrepancy localizes genuinely
    # lost/invented rows to one account and date range.
    op.execute(
        """
        CREATE VIEW gold.v_reconciliation AS
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

    # Month-end declared balance per account, carried forward from the last
    # anchor: a balance stays what it was until a statement says otherwise.
    # `as_of` keeps the anchor date the figure was carried from. Months before
    # an account's first anchor have no row — the account's history has not
    # started, which is different from a zero balance.
    op.execute(
        """
        CREATE VIEW gold.v_balance_month AS
        WITH bounds AS (
            SELECT date_trunc('month', min(balance_date))::date AS first_month,
                   date_trunc('month', max(balance_date))::date AS last_month
            FROM silver.balances
        ),
        months AS (
            SELECT generate_series(first_month, last_month, interval '1 month')::date AS month
            FROM bounds
        ),
        accts AS (
            SELECT DISTINCT account, currency FROM silver.balances
        )
        SELECT m.month,
               a.account,
               a.currency,
               lb.balance,
               lb.balance_date AS as_of
        FROM months m
        CROSS JOIN accts a
        CROSS JOIN LATERAL (
            SELECT b.balance, b.balance_date
            FROM silver.balances b
            WHERE b.account = a.account
              AND b.balance_date < (m.month + interval '1 month')::date
            ORDER BY b.balance_date DESC
            LIMIT 1
        ) lb
        """
    )

    # --- Grants (cluster roles when present; a plain local Postgres has none) ---
    # The db-grants Job re-applies the full grant set at wave 2 anyway; this
    # keeps a freshly-migrated cluster DB queryable in between.
    op.execute(
        """DO $$ BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'query_reader') THEN
        GRANT SELECT ON ALL TABLES IN SCHEMA gold TO query_reader;
    END IF;
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'etl_writer') THEN
        GRANT SELECT, INSERT, UPDATE ON silver.balances TO etl_writer;
    END IF;
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'ml_reader') THEN
        GRANT SELECT ON silver.balances TO ml_reader;
    END IF;
END $$"""
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS gold CASCADE")
    op.execute("DROP SCHEMA IF EXISTS silver CASCADE")
    op.execute("DROP SCHEMA IF EXISTS bronze CASCADE")
