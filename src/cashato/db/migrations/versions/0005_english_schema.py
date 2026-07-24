"""rename schema to English (columns + gold views); data preserved

Revision ID: 0005_english_schema
Revises: 0004_ml_categorization
Create Date: 2026-07-18

Renames columns (RENAME COLUMN: data preserved) from Italian to English and
recreates the GOLD views with English names/columns. Migrations 0001-0004 keep
their original (Italian) column names: they represent the schema history.
"""

from alembic import op

revision = "0005_english_schema"
down_revision = "0004_ml_categorization"
branch_labels = None
depends_on = None

# old -> new (silver.transactions)
_SILVER = {
    "data_valuta": "value_date",
    "data_contabile": "booking_date",
    "descrizione": "description",
    "importo": "amount",
    "valuta": "currency",
    "conto": "account",
    "tipo_origine": "source",
    "categoria": "category",
    "categoria_nativa": "native_category",
    "categoria_confidence": "category_confidence",
    "categoria_source": "category_source",
}

_OLD_VIEWS = ["v_spese_categoria_mese", "v_entrate_uscite_mese", "v_totali_categoria"]
_NEW_VIEWS = ["v_category_month", "v_income_expense_month", "v_category_totals"]


def _create_english_views() -> None:
    op.execute(
        """
        CREATE VIEW gold.v_category_month AS
        SELECT date_trunc('month', value_date)::date AS month,
               category,
               count(*)     AS n_movements,
               sum(amount)  AS total
        FROM silver.transactions
        GROUP BY 1, 2
        """
    )
    op.execute(
        """
        CREATE VIEW gold.v_income_expense_month AS
        SELECT date_trunc('month', value_date)::date AS month,
               sum(amount) FILTER (WHERE amount > 0) AS income,
               sum(amount) FILTER (WHERE amount < 0) AS expense,
               sum(amount)                           AS net,
               sum(amount) FILTER (WHERE category NOT IN ('investments', 'crypto'))
                                                     AS net_excl_investments
        FROM silver.transactions
        GROUP BY 1
        """
    )
    op.execute(
        """
        CREATE VIEW gold.v_category_totals AS
        SELECT category,
               count(*)     AS n_movements,
               sum(amount) FILTER (WHERE amount < 0) AS expense,
               sum(amount) FILTER (WHERE amount > 0) AS income,
               sum(amount) AS net
        FROM silver.transactions
        GROUP BY 1
        """
    )


def upgrade() -> None:
    # views depend on the columns: drop them first
    for v in _OLD_VIEWS:
        op.execute(f"DROP VIEW IF EXISTS gold.{v}")
    # rename silver columns (data preserved)
    for old, new in _SILVER.items():
        op.execute(f"ALTER TABLE silver.transactions RENAME COLUMN {old} TO {new}")
    # gold ML tables
    op.execute("ALTER TABLE gold.training_labels RENAME COLUMN categoria TO category")
    op.execute("ALTER TABLE gold.category_feedback RENAME COLUMN categoria TO category")
    # recreate the views in English
    _create_english_views()


def downgrade() -> None:
    for v in _NEW_VIEWS:
        op.execute(f"DROP VIEW IF EXISTS gold.{v}")
    op.execute("ALTER TABLE gold.category_feedback RENAME COLUMN category TO categoria")
    op.execute("ALTER TABLE gold.training_labels RENAME COLUMN category TO categoria")
    for old, new in _SILVER.items():
        op.execute(f"ALTER TABLE silver.transactions RENAME COLUMN {new} TO {old}")
    op.execute(
        """
        CREATE VIEW gold.v_spese_categoria_mese AS
        SELECT date_trunc('month', data_valuta)::date AS mese, categoria,
               count(*) AS n_movimenti, sum(importo) AS totale
        FROM silver.transactions GROUP BY 1, 2
        """
    )
    op.execute(
        """
        CREATE VIEW gold.v_entrate_uscite_mese AS
        SELECT date_trunc('month', data_valuta)::date AS mese,
               sum(importo) FILTER (WHERE importo > 0) AS entrate,
               sum(importo) FILTER (WHERE importo < 0) AS uscite,
               sum(importo) AS netto,
               sum(importo) FILTER (WHERE categoria NOT IN ('investments','crypto'))
                   AS netto_escl_investimenti
        FROM silver.transactions GROUP BY 1
        """
    )
    op.execute(
        """
        CREATE VIEW gold.v_totali_categoria AS
        SELECT categoria, count(*) AS n_movimenti,
               sum(importo) FILTER (WHERE importo < 0) AS uscite,
               sum(importo) FILTER (WHERE importo > 0) AS entrate,
               sum(importo) AS netto
        FROM silver.transactions GROUP BY 1
        """
    )
