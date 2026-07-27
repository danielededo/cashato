"""drop unused bronze.raw_rows

The row-level bronze landing table (JSONB payload per extracted row) was created
in 0001 for "audit + reprocess without re-reading the file", but it was never
populated or read: the etl-worker parses file -> silver directly, the original
files are retained on disk and idempotency relies on ``natural_key``. Dropping
it. Can be reintroduced if raw-extraction audit is ever needed.

Revision ID: 0007_drop_raw_rows
Revises: 0006_internal_transfers
"""

from __future__ import annotations

from alembic import op

revision = "0007_drop_raw_rows"
down_revision = "0006_internal_transfers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS bronze.raw_rows")


def downgrade() -> None:
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
