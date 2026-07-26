"""initial schema: bronze + silver (medallion)

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-18
"""

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Schemas (namespaces in one DB, not separate databases) ---
    op.execute("CREATE SCHEMA IF NOT EXISTS bronze")
    op.execute("CREATE SCHEMA IF NOT EXISTS silver")
    op.execute("CREATE SCHEMA IF NOT EXISTS gold")

    # --- BRONZE: file tracking and raw rows ---
    op.execute(
        """
        CREATE TABLE bronze.raw_files (
            id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            source       TEXT        NOT NULL,
            filename     TEXT        NOT NULL,
            sha256       TEXT        NOT NULL UNIQUE,
            size_bytes   BIGINT,
            uploaded_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            status       TEXT        NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending','parsed','failed')),
            rows_total   INTEGER     NOT NULL DEFAULT 0,
            rows_failed  INTEGER     NOT NULL DEFAULT 0,
            error        TEXT
        )
        """
    )

    op.execute(
        """
        CREATE TABLE bronze.raw_rows (
            id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            file_id   BIGINT NOT NULL REFERENCES bronze.raw_files(id) ON DELETE CASCADE,
            source    TEXT   NOT NULL,
            line_no   INTEGER,
            payload   JSONB  NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_raw_rows_file ON bronze.raw_rows(file_id)")

    # --- SILVER: normalized transactions (common schema) ---
    op.execute(
        """
        CREATE TABLE silver.transactions (
            id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            data_valuta    DATE          NOT NULL,
            data_contabile DATE          NOT NULL,
            descrizione    TEXT          NOT NULL,
            importo        NUMERIC(18,4) NOT NULL,
            valuta         TEXT          NOT NULL,
            conto          TEXT          NOT NULL,
            tipo_origine   TEXT          NOT NULL,
            categoria      TEXT,
            natural_key    TEXT          NOT NULL UNIQUE,
            file_id        BIGINT        REFERENCES bronze.raw_files(id) ON DELETE SET NULL,
            created_at     TIMESTAMPTZ   NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_tx_data_contabile ON silver.transactions(data_contabile)")
    op.execute("CREATE INDEX ix_tx_conto ON silver.transactions(conto)")
    op.execute("CREATE INDEX ix_tx_categoria ON silver.transactions(categoria)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS silver.transactions")
    op.execute("DROP TABLE IF EXISTS bronze.raw_rows")
    op.execute("DROP TABLE IF EXISTS bronze.raw_files")
    op.execute("DROP SCHEMA IF EXISTS gold")
    op.execute("DROP SCHEMA IF EXISTS silver")
    op.execute("DROP SCHEMA IF EXISTS bronze")
