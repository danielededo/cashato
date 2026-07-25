"""bronze.raw_files: account holder (intestatario) read off the statement

Statement PDFs address the holder in a header block; the CSV/XLSX exports do not
carry it at all. The column is therefore NULLable by design: "unknown" is a normal
outcome for a perfectly good file, not a parse failure.

Revision ID: 0010_raw_files_account_holder
Revises: 0009_raw_files_rows_new
"""

from __future__ import annotations

from alembic import op

revision = "0010_raw_files_account_holder"
down_revision = "0009_raw_files_rows_new"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE bronze.raw_files ADD COLUMN account_holder TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE bronze.raw_files DROP COLUMN IF EXISTS account_holder")
