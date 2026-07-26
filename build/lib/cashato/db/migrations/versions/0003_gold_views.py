"""gold: aggregate views (spend per category/month, income/expense)

Revision ID: 0003_gold_views
Revises: 0002_categoria_nativa
Create Date: 2026-07-18

The views expose the language-neutral category CODE; label localization happens
in the export/visualization layer.
"""

from alembic import op

revision = "0003_gold_views"
down_revision = "0002_categoria_nativa"
branch_labels = None
depends_on = None

_VIEWS = ["v_spese_categoria_mese", "v_entrate_uscite_mese", "v_totali_categoria"]


def upgrade() -> None:
    op.execute(
        """
        CREATE VIEW gold.v_spese_categoria_mese AS
        SELECT date_trunc('month', data_valuta)::date AS mese,
               categoria,
               count(*)      AS n_movimenti,
               sum(importo)  AS totale
        FROM silver.transactions
        GROUP BY 1, 2
        """
    )
    op.execute(
        """
        CREATE VIEW gold.v_entrate_uscite_mese AS
        SELECT date_trunc('month', data_valuta)::date AS mese,
               sum(importo) FILTER (WHERE importo > 0) AS entrate,
               sum(importo) FILTER (WHERE importo < 0) AS uscite,
               sum(importo)                            AS netto,
               sum(importo) FILTER (WHERE categoria NOT IN ('investments', 'crypto'))
                                                       AS netto_escl_investimenti
        FROM silver.transactions
        GROUP BY 1
        """
    )
    op.execute(
        """
        CREATE VIEW gold.v_totali_categoria AS
        SELECT categoria,
               count(*)     AS n_movimenti,
               sum(importo) FILTER (WHERE importo < 0) AS uscite,
               sum(importo) FILTER (WHERE importo > 0) AS entrate,
               sum(importo) AS netto
        FROM silver.transactions
        GROUP BY 1
        """
    )


def downgrade() -> None:
    for v in _VIEWS:
        op.execute(f"DROP VIEW IF EXISTS gold.{v}")
