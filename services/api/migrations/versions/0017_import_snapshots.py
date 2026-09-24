"""Encrypted, owner-bound immutable file import snapshots.

Revision ID: 0017_import_snapshots
Revises: 0016_connector_sync_recovery
"""

import json
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from alembic import op

revision = "0017_import_snapshots"
down_revision = "0016_connector_sync_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connector_import_snapshots",
        sa.Column(
            "connection_id",
            sa.Uuid(),
            sa.ForeignKey("connector_connections.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("format", sa.String(8), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "workspace_id", "user_id", "digest", name="uq_import_snapshot_owner_digest"
        ),
    )
    op.create_index(
        "ix_connector_import_snapshots_workspace_id", "connector_import_snapshots", ["workspace_id"]
    )
    op.create_index(
        "ix_connector_import_snapshots_user_id", "connector_import_snapshots", ["user_id"]
    )

    manifest = {
        "id": "generic-import",
        "version": "1.0.0",
        "displayName": "File Import",
        "category": "data",
        "connectorClass": "IMPORT",
        "auth": [{"kind": "none", "label": "No authentication", "scopes": []}],
        "resourceTypes": ["import.calendar_event", "import.csv_row", "import.json_item"],
        "capabilities": {
            "read": [
                {
                    "name": "imports.calendar.read",
                    "description": "Read validated ICS events",
                    "sensitive": True,
                },
                {
                    "name": "imports.tabular.read",
                    "description": "Read validated CSV rows",
                    "sensitive": True,
                },
                {
                    "name": "imports.json.read",
                    "description": "Read validated JSON items",
                    "sensitive": True,
                },
            ],
            "write": [],
            "events": [],
            "incrementalSync": False,
        },
        "requiredSecrets": [],
        "rateLimitStrategy": "none",
        "minimumNavoxConnectorApiVersion": "1",
    }
    identifier = str(uuid5(NAMESPACE_URL, "navox:generic-import:1.0.0"))
    encoded = json.dumps(manifest).replace("'", "''")
    op.execute(
        "INSERT INTO connector_definitions "
        "(id,connector_key,version,display_name,connector_class,trust_level,manifest,active) "
        f"SELECT '{identifier}','generic-import','1.0.0','File Import',"
        f"'IMPORT','NAVOX_FIRST_PARTY','{encoded}',true "
        "WHERE NOT EXISTS (SELECT 1 FROM connector_definitions "
        "WHERE connector_key='generic-import' AND version='1.0.0')"
    )


def downgrade() -> None:
    op.drop_table("connector_import_snapshots")
