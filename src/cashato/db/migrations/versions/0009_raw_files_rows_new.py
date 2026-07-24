"""bronze.raw_files: track newly-inserted rows (vs duplicates)

Ingestion is idempotent (dedup on natural_key): re-uploading a file inserts 0 new
rows. Storing ``rows_new`` lets the API/UI report "imported N, M already present"
instead of leaving the user guessing. Duplicates = ``rows_total - rows_new``.

Revision ID: 0009_raw_files_rows_new
Revises: 0008_gold_transactions_view
"""

from __future__ import annotations

from alembic import op

revision = "0009_raw_files_rows_new"
down_revision = "0008_gold_transactions_view"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE bronze.raw_files ADD COLUMN rows_new INTEGER NOT NULL DEFAULT 0")


def downgrade() -> None:
    op.execute("ALTER TABLE bronze.raw_files DROP COLUMN IF EXISTS rows_new")
