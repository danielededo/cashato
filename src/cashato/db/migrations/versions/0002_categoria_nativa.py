"""silver.transactions: add categoria_nativa (source seed)

Revision ID: 0002_categoria_nativa
Revises: 0001_initial
Create Date: 2026-07-18
"""

from alembic import op

revision = "0002_categoria_nativa"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE silver.transactions ADD COLUMN categoria_nativa TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE silver.transactions DROP COLUMN categoria_nativa")
