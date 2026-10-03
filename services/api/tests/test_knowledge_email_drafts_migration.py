"""Structural checks for the tagged communication-draft binding migration.

The migration is rendered offline for PostgreSQL and compared with the ORM
metadata, so no database service is required or pretended to have run.
"""

import importlib.util
import io
import re
import types
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint
from sqlalchemy.dialects import postgresql

from navox.db import models  # noqa: F401 - registers model metadata
from navox.db.base import Base

API_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_FILE = API_ROOT / "migrations" / "versions" / "0034_knowledge_email_drafts.py"
REVISION = "0034_knowledge_email_drafts"
DOWN_REVISION = "0033_news_semantic_clustering"
DIALECT = postgresql.dialect()

STATEMENTS = {
    "binding_kind": (
        "ALTER TABLE communication_drafts ADD COLUMN binding_kind VARCHAR(32)"
        " DEFAULT 'COMMITMENT' NOT NULL"
    ),
    "source_external_id": (
        "ALTER TABLE communication_drafts ADD COLUMN source_external_id VARCHAR(512)"
    ),
    "drop_not_null": ("ALTER TABLE communication_drafts ALTER COLUMN commitment_id DROP NOT NULL"),
    "binding_kind_check": (
        "ALTER TABLE communication_drafts ADD CONSTRAINT"
        " ck_communication_drafts_binding_kind CHECK"
        " (binding_kind IN ('COMMITMENT', 'KNOWLEDGE_EMAIL'))"
    ),
    "binding_commitment_check": (
        "ALTER TABLE communication_drafts ADD CONSTRAINT"
        " ck_communication_drafts_binding_commitment CHECK"
        " ((binding_kind = 'COMMITMENT' AND commitment_id IS NOT NULL) OR"
        " (binding_kind = 'KNOWLEDGE_EMAIL' AND commitment_id IS NULL"
        " AND source_external_id IS NOT NULL))"
    ),
}


def migration_module() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("navox_migration_0034", MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render(direction: str) -> str:
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": buffer, "literal_binds": True},
    )
    with Operations.context(context):
        if direction == "upgrade":
            migration_module().upgrade()
        else:
            migration_module().downgrade()
    return " ".join(buffer.getvalue().split())


def test_migration_precedes_the_additive_news_images_head() -> None:
    module = migration_module()
    assert module.revision == REVISION
    assert module.down_revision == DOWN_REVISION
    assert len(module.revision) <= 32
    script = ScriptDirectory.from_config(Config(str(API_ROOT / "alembic.ini")))
    assert script.get_heads() == ["0035_news_article_images"]
    assert script.get_revision(script.get_heads()[0]).down_revision == REVISION


def test_upgrade_only_tags_the_binding_and_allows_the_null_commitment() -> None:
    sql = render("upgrade")
    for statement in STATEMENTS.values():
        assert statement in sql, statement
    # The new binding is additive: no column is dropped and no existing
    # constraint is replaced on upgrade.
    assert "DROP COLUMN" not in sql
    assert "DROP CONSTRAINT" not in sql
    assert sql.count("ADD COLUMN") == 2


def test_downgrade_removes_the_tag_and_restores_the_previous_contract() -> None:
    sql = render("downgrade")
    assert (
        "ALTER TABLE communication_drafts DROP CONSTRAINT"
        " ck_communication_drafts_binding_commitment"
    ) in sql
    assert (
        "ALTER TABLE communication_drafts DROP CONSTRAINT ck_communication_drafts_binding_kind"
        in sql
    )
    assert "knowledge-email drafts prevent this downgrade" in sql
    assert "DELETE FROM communication_drafts" not in sql
    assert "ALTER TABLE communication_drafts ALTER COLUMN commitment_id SET NOT NULL" in sql
    assert sql.count("DROP COLUMN") == 2


def test_orm_columns_match_the_migration() -> None:
    table = Base.metadata.tables["communication_drafts"]
    commitment = table.c.commitment_id
    assert commitment.nullable is True
    binding_kind = table.c.binding_kind
    assert binding_kind.nullable is False
    assert str(binding_kind.type.compile(dialect=DIALECT)) == "VARCHAR(32)"
    assert binding_kind.server_default is not None
    assert binding_kind.server_default.arg == "COMMITMENT"
    source_external_id = table.c.source_external_id
    assert source_external_id.nullable is True
    assert str(source_external_id.type.compile(dialect=DIALECT)) == "VARCHAR(512)"


def test_orm_enforces_the_same_kind_commitment_relationship() -> None:
    table = Base.metadata.tables["communication_drafts"]
    checks = {
        c.name: " ".join(str(c.sqltext).split())
        for c in table.constraints
        if isinstance(c, CheckConstraint)
    }
    assert checks["ck_communication_drafts_binding_kind"] == (
        "binding_kind IN ('COMMITMENT', 'KNOWLEDGE_EMAIL')"
    )
    assert checks["ck_communication_drafts_binding_commitment"] == (
        "(binding_kind = 'COMMITMENT' AND commitment_id IS NOT NULL)"
        " OR (binding_kind = 'KNOWLEDGE_EMAIL' AND commitment_id IS NULL"
        " AND source_external_id IS NOT NULL)"
    )


def test_no_other_table_is_touched_by_the_migration() -> None:
    sql = render("upgrade")
    assert "ALTER TABLE communication_drafts " in sql
    for table in ("communication_draft_versions", "actions", "approvals", "plans", "commitments"):
        assert f"ALTER TABLE {table} " not in sql
        assert f"DROP TABLE {table}" not in sql
    assert re.search(r"ALTER TABLE communication_drafts ADD COLUMN binding_kind", sql)
