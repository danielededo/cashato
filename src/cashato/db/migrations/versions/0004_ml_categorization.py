"""ML categorization: confidence/source/mcc on silver + label/feedback tables

Revision ID: 0004_ml_categorization
Revises: 0003_gold_views
Create Date: 2026-07-18
"""

from alembic import op

revision = "0004_ml_categorization"
down_revision = "0003_gold_views"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE silver.transactions ADD COLUMN categoria_confidence REAL")
    op.execute("ALTER TABLE silver.transactions ADD COLUMN categoria_source TEXT")
    op.execute("ALTER TABLE silver.transactions ADD COLUMN mcc TEXT")

    # Training dataset: canonical labels (rules/LLM/corrections), keyed by
    # normalized description. Provider-agnostic.
    op.execute(
        """
        CREATE TABLE gold.training_labels (
            id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            text_norm   TEXT NOT NULL,
            categoria   TEXT NOT NULL,
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
            categoria    TEXT NOT NULL,
            corrected_by TEXT,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS gold.category_feedback")
    op.execute("DROP TABLE IF EXISTS gold.training_labels")
    op.execute("ALTER TABLE silver.transactions DROP COLUMN IF EXISTS mcc")
    op.execute("ALTER TABLE silver.transactions DROP COLUMN IF EXISTS categoria_source")
    op.execute("ALTER TABLE silver.transactions DROP COLUMN IF EXISTS categoria_confidence")
