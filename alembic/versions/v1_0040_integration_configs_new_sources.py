"""Add Zeek / Suricata / DFIR-IRIS / Velociraptor / Shuffle config columns
to integration_configs (issue: settings for these 5 integrations were only
persisted to a local JSON file in legacy_stubs.py, never reaching the DB —
so they never reached the tenant adapter/worker process on restart).

Follows the existing plaintext-at-rest pattern already used on this table
(wazuh_password_plain, llm_api_key_plain, etc.) — same compromise, same
follow-up note about KMS/Fernet hardening later.

Existing rows default every *_enabled to false and every path/url/key
column to NULL — no behavior change for tenants that never touched these
integrations.

Revision ID: v1_0040_integration_configs_new_sources
Revises: v1_0039_run_transient_retry
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "v1_0040_integration_configs_new_sources"
down_revision: str | None = "v1_0039_run_transient_retry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Zeek
    op.add_column(
        "integration_configs",
        sa.Column("zeek_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "integration_configs",
        sa.Column("zeek_log_path", sa.String(length=500), nullable=True),
    )
    # Suricata
    op.add_column(
        "integration_configs",
        sa.Column("suricata_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "integration_configs",
        sa.Column("suricata_log_path", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "integration_configs",
        sa.Column(
            "suricata_ingest_all_events",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    # DFIR-IRIS
    op.add_column(
        "integration_configs",
        sa.Column("dfir_iris_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "integration_configs",
        sa.Column("dfir_iris_url", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "integration_configs",
        sa.Column("dfir_iris_verify_ssl", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column(
        "integration_configs",
        sa.Column("dfir_iris_api_key_plain", sa.String(length=4096), nullable=True),
    )
    # Velociraptor
    op.add_column(
        "integration_configs",
        sa.Column(
            "velociraptor_enabled", sa.Boolean(), nullable=False, server_default="false"
        ),
    )
    op.add_column(
        "integration_configs",
        sa.Column(
            "velociraptor_api_client_config_path", sa.String(length=500), nullable=True
        ),
    )
    # Shuffle
    op.add_column(
        "integration_configs",
        sa.Column("shuffle_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "integration_configs",
        sa.Column("shuffle_webhook_url", sa.String(length=2048), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("integration_configs", "shuffle_webhook_url")
    op.drop_column("integration_configs", "shuffle_enabled")
    op.drop_column("integration_configs", "velociraptor_api_client_config_path")
    op.drop_column("integration_configs", "velociraptor_enabled")
    op.drop_column("integration_configs", "dfir_iris_api_key_plain")
    op.drop_column("integration_configs", "dfir_iris_verify_ssl")
    op.drop_column("integration_configs", "dfir_iris_url")
    op.drop_column("integration_configs", "dfir_iris_enabled")
    op.drop_column("integration_configs", "suricata_ingest_all_events")
    op.drop_column("integration_configs", "suricata_log_path")
    op.drop_column("integration_configs", "suricata_enabled")
    op.drop_column("integration_configs", "zeek_log_path")
    op.drop_column("integration_configs", "zeek_enabled")
