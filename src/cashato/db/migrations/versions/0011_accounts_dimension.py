"""silver.accounts: what each statement says about the account it covers

The account *id* is hashed into ``natural_key``, so it can never be renamed
without invalidating every key ever computed. Everything a document tells us
about an account — which bank, which product, whether it is held jointly — is
therefore kept HERE, as display metadata keyed by that stable id, and composed
into a human name at read time.

All descriptive columns are nullable on purpose: sources disclose very different
amounts of metadata. In particular ``holding_modality`` NULL means "the document
did not say", which is NOT the same as individual.

Revision ID: 0011_accounts_dimension
Revises: 0010_raw_files_account_holder
"""

from __future__ import annotations

from alembic import op

revision = "0011_accounts_dimension"
down_revision = "0010_raw_files_account_holder"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE silver.accounts (
            account_id       TEXT PRIMARY KEY,
            source           TEXT        NOT NULL,
            bank_name        TEXT,
            product          TEXT,
            holding_modality TEXT CHECK (holding_modality IN ('individual','joint')),
            currency         TEXT,
            iban             TEXT,
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    # Gold projection so the read API stays gold-only (same rationale as
    # v_transactions). `is_joint` is exposed as a nullable boolean rather than
    # collapsing the undisclosed case to false, and `display_name` composes the
    # label from parts so no bank name is ever hardcoded in the frontend.
    op.execute(
        """
        CREATE VIEW gold.v_accounts AS
        SELECT
            a.account_id,
            a.source,
            a.bank_name,
            a.product,
            a.holding_modality,
            CASE a.holding_modality
                WHEN 'joint' THEN true
                WHEN 'individual' THEN false
                ELSE NULL
            END                                        AS is_joint,
            a.currency,
            a.iban,
            COALESCE(a.bank_name, a.source)
              || COALESCE(' · ' || a.product, '')
              || CASE WHEN a.holding_modality = 'joint' THEN ' (Joint)' ELSE '' END
                                                       AS display_name,
            COUNT(t.natural_key)                       AS transactions,
            MIN(t.value_date)                          AS first_movement,
            MAX(t.value_date)                          AS last_movement
        FROM silver.accounts a
        LEFT JOIN silver.transactions t ON t.account = a.account_id
        GROUP BY a.account_id, a.source, a.bank_name, a.product,
                 a.holding_modality, a.currency, a.iban
        """
    )
    op.execute("""DO $$ BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'query_reader') THEN
        GRANT SELECT ON gold.v_accounts TO query_reader;
    END IF;
END $$""")


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS gold.v_accounts")
    op.execute("DROP TABLE IF EXISTS silver.accounts")
