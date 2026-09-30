"""Structural checks for the additive SPEC-007 M1 knowledge migration.

Everything here is portable: the migration is rendered offline for PostgreSQL
and compared with the SQLAlchemy metadata, so no PostgreSQL service is required
and none is pretended to have run.
"""

import importlib.util
import io
import os
import re
import subprocess
import sys
import types
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql

from navox.db import models  # noqa: F401 - registers model metadata
from navox.db.base import Base

API_ROOT = Path(__file__).resolve().parents[1]
VERSIONS = API_ROOT / "migrations" / "versions"
M1_FILE = VERSIONS / "0029_knowledge_foundation.py"
SEARCH_FILE = VERSIONS / "0030_knowledge_search.py"
INTEL_FILE = VERSIONS / "0031_knowledge_intelligence.py"
M1_REVISION = "0029_knowledge_foundation"
REVISION = "0031_knowledge_intelligence"
DOWN_REVISION = "0030_knowledge_search"
SEARCH_REVISION = "0030_knowledge_search"
NEWS_RETRIEVAL_REVISION = "0028_news_retrieval"
KNOWLEDGE_TABLES = ("knowledge_resources", "knowledge_resource_permissions", "knowledge_chunks")
SEARCH_TABLES = (
    "knowledge_resource_index",
    "knowledge_exclusions",
    "knowledge_recent_searches",
)
INTEL_TABLES = (
    "knowledge_embeddings",
    "knowledge_embedding_requests",
    "knowledge_answer_requests",
    "knowledge_sessions",
    "knowledge_turns",
    "knowledge_entities",
    "knowledge_relationships",
)
DATABASE_URL = "postgresql+asyncpg://navox:navox@localhost:5432/navox"
DIALECT = postgresql.dialect()

# Supporting unique constraints this migration adds to existing connector
# tables, and the identity each one must cover.
SUPPORTED_CONSTRAINTS = {
    "connector_connections": (
        "uq_connector_connections_workspace_identity",
        ("workspace_id", "id", "user_id"),
    ),
    "connector_resources": (
        "uq_connector_resources_workspace_connection_external_id",
        ("workspace_id", "connector_connection_id", "external_id", "id"),
    ),
}


def migration_module(path: Path = M1_FILE) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(f"navox_migration_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render(*, direction: str, path: Path = M1_FILE) -> str:
    module = migration_module(path)
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": buffer, "literal_binds": True},
    )
    with Operations.context(context):
        if direction == "upgrade":
            module.upgrade()
        else:
            module.downgrade()
    return buffer.getvalue()


def normalize(text: str) -> str:
    return " ".join(text.split())


def parenthesized(text: str, opening: int) -> tuple[str, int]:
    """Return the balanced group starting at ``opening`` and the index after it."""
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return text[opening + 1 : index], index + 1
    raise AssertionError("Unbalanced parentheses in rendered DDL")


def split_columns(text: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in text.split(",") if part.strip())


def parse_created_tables(sql: str) -> dict[str, dict[str, object]]:
    """Parse columns and constraints out of the rendered CREATE TABLE blocks."""
    tables: dict[str, dict[str, object]] = {}
    for match in re.finditer(r"CREATE TABLE (\w+) \(\n(.*?)\n\);", sql, re.DOTALL):
        name, body = match.group(1), match.group(2)
        columns: dict[str, str] = {}
        not_null: set[str] = set()
        unique: dict[str, tuple[str, ...]] = {}
        checks: dict[str, str] = {}
        foreign_keys: set[tuple[tuple[str, ...], str, tuple[str, ...], str | None]] = set()
        primary_key: tuple[str, ...] = ()
        for raw in body.splitlines():
            line = raw.strip().removesuffix(",").strip()
            if not line:
                continue
            if line.startswith("CONSTRAINT "):
                constraint_name, _, definition = line[len("CONSTRAINT ") :].partition(" ")
                if definition.startswith("FOREIGN KEY"):
                    child, cursor = parenthesized(definition, definition.index("("))
                    reference = re.match(r" REFERENCES (\w+) \(", definition[cursor:])
                    assert reference is not None, definition
                    target_table = reference.group(1)
                    target, end = parenthesized(definition, cursor + reference.end() - 1)
                    action = re.search(r" ON DELETE (\w+)", definition[end:])
                    foreign_keys.add(
                        (
                            split_columns(child),
                            target_table,
                            split_columns(target),
                            action.group(1) if action else None,
                        )
                    )
                elif definition.startswith("UNIQUE "):
                    group, _ = parenthesized(definition, definition.index("("))
                    unique[constraint_name] = split_columns(group)
                elif definition.startswith("CHECK "):
                    group, _ = parenthesized(definition, definition.index("("))
                    checks[constraint_name] = normalize(group)
                else:
                    raise AssertionError(definition)
                continue
            if line.startswith("PRIMARY KEY "):
                group, _ = parenthesized(line, line.index("("))
                primary_key = split_columns(group)
                continue
            column_name, _, remainder = line.partition(" ")
            type_text = remainder
            for marker in (" DEFAULT ", " NOT NULL"):
                if marker in type_text:
                    type_text = type_text.split(marker)[0]
            columns[column_name] = type_text.strip()
            if " NOT NULL" in remainder:
                not_null.add(column_name)
        tables[name] = {
            "columns": columns,
            "not_null": not_null,
            "unique": unique,
            "checks": checks,
            "foreign_keys": foreign_keys,
            "primary_key": primary_key,
        }
    return tables


def parse_indexes(sql: str) -> set[tuple[str, str, tuple[str, ...]]]:
    return {
        (match.group(1), match.group(2), split_columns(match.group(3)))
        for match in re.finditer(r"CREATE INDEX (\w+) ON (\w+) \(([^)]*)\);", sql)
    }


def parse_added_unique_constraints(sql: str) -> dict[tuple[str, str], tuple[str, ...]]:
    return {
        (match.group(1), match.group(2)): split_columns(match.group(3))
        for match in re.finditer(r"ALTER TABLE (\w+) ADD CONSTRAINT (\w+) UNIQUE \(([^)]*)\);", sql)
    }


def orm_columns(table_name: str) -> tuple[dict[str, str], set[str]]:
    table = Base.metadata.tables[table_name]
    columns = {column.name: str(column.type.compile(dialect=DIALECT)) for column in table.columns}
    not_null = {column.name for column in table.columns if not column.nullable}
    return columns, not_null


def orm_unique(table_name: str) -> dict[str, tuple[str, ...]]:
    return {
        constraint.name or "": tuple(column.name for column in constraint.columns)
        for constraint in Base.metadata.tables[table_name].constraints
        if isinstance(constraint, UniqueConstraint)
    }


def orm_checks(table_name: str) -> dict[str, str]:
    return {
        constraint.name or "": normalize(str(constraint.sqltext))
        for constraint in Base.metadata.tables[table_name].constraints
        if isinstance(constraint, CheckConstraint)
    }


def orm_foreign_keys(
    table_name: str,
) -> set[tuple[tuple[str, ...], str, tuple[str, ...], str | None]]:
    return {
        (
            tuple(column.name for column in constraint.columns),
            list(constraint.elements)[0].target_fullname.split(".")[0],
            tuple(element.target_fullname.split(".")[1] for element in constraint.elements),
            constraint.ondelete,
        )
        for constraint in Base.metadata.tables[table_name].constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }


def orm_indexes(table_name: str) -> set[tuple[str, str, tuple[str, ...]]]:
    return {
        (index.name or "", table_name, tuple(column.name for column in index.columns))
        for index in Base.metadata.tables[table_name].indexes
    }


def test_migration_is_the_single_head_after_the_m1_foundation() -> None:
    foundation = migration_module(M1_FILE)
    assert foundation.revision == M1_REVISION
    assert foundation.down_revision == NEWS_RETRIEVAL_REVISION
    search = migration_module(SEARCH_FILE)
    assert search.revision == SEARCH_REVISION
    assert search.down_revision == M1_REVISION
    intelligence = migration_module(INTEL_FILE)
    assert intelligence.revision == REVISION
    assert intelligence.down_revision == DOWN_REVISION == SEARCH_REVISION
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["0033_news_semantic_clustering"]
    importance = script.get_revision("0032_news_importance")
    assert importance is not None and importance.down_revision == REVISION
    clustering = script.get_revision("0033_news_semantic_clustering")
    assert clustering is not None and clustering.down_revision == "0032_news_importance"
    assert len(REVISION) <= 32


def test_migration_columns_types_and_nullability_match_orm() -> None:
    tables = parse_created_tables(render(direction="upgrade"))
    for table_name in KNOWLEDGE_TABLES:
        expected_columns, expected_not_null = orm_columns(table_name)
        assert tables[table_name]["columns"] == expected_columns
        assert tables[table_name]["not_null"] == expected_not_null


def test_migration_constraints_match_orm() -> None:
    tables = parse_created_tables(render(direction="upgrade"))
    for table_name in KNOWLEDGE_TABLES:
        rendered = tables[table_name]
        table = Base.metadata.tables[table_name]
        assert rendered["unique"] == orm_unique(table_name)
        assert rendered["checks"] == orm_checks(table_name)
        assert rendered["foreign_keys"] == orm_foreign_keys(table_name)
        assert rendered["primary_key"] == tuple(column.name for column in table.primary_key.columns)


def test_migration_indexes_match_orm() -> None:
    indexes = parse_indexes(render(direction="upgrade"))
    for table_name in KNOWLEDGE_TABLES:
        assert {entry for entry in indexes if entry[1] == table_name} == orm_indexes(table_name)


def test_connector_support_constraints_match_orm() -> None:
    added = parse_added_unique_constraints(render(direction="upgrade"))
    for table_name, (constraint_name, columns) in SUPPORTED_CONSTRAINTS.items():
        assert added[(table_name, constraint_name)] == columns
        assert orm_unique(table_name)[constraint_name] == columns
    assert set(added) == {
        (table_name, name) for table_name, (name, _) in SUPPORTED_CONSTRAINTS.items()
    }


def test_key_security_constraints_are_rendered() -> None:
    tables = parse_created_tables(render(direction="upgrade"))
    assert tables["knowledge_resources"]["unique"][
        "uq_knowledge_resources_workspace_connection_external"
    ] == ("workspace_id", "source_connection_id", "external_resource_id")
    checks = tables["knowledge_resource_permissions"]["checks"]
    assert "principal_type <> 'WORKSPACE' OR principal_id = workspace_id" in checks.values()
    assert (
        "valid_from IS NULL OR valid_until IS NULL OR valid_until > valid_from" in checks.values()
    )
    foreign_keys = tables["knowledge_resources"]["foreign_keys"]
    assert any(
        columns == ("workspace_id", "source_connection_id", "owner_user_id")
        and target == "connector_connections"
        and target_columns == ("workspace_id", "id", "user_id")
        for columns, target, target_columns, _ in foreign_keys
    )
    assert any(
        columns
        == ("workspace_id", "source_connection_id", "external_resource_id", "source_resource_id")
        and target == "connector_resources"
        and action == "CASCADE"
        for columns, target, _, action in foreign_keys
    )
    resource_checks = tables["knowledge_resources"]["checks"]
    assert "ck_knowledge_resources_source_type" in resource_checks
    assert "ck_knowledge_resources_sensitivity" in resource_checks


def test_downgrade_drops_tables_and_supporting_constraints() -> None:
    sql = render(direction="downgrade")
    for table_name in KNOWLEDGE_TABLES:
        assert f"DROP TABLE {table_name}" in sql
    for table_name, (constraint_name, _) in SUPPORTED_CONSTRAINTS.items():
        assert f"ALTER TABLE {table_name} DROP CONSTRAINT {constraint_name}" in sql


def test_search_migration_adds_only_the_exclusion_scope_constraint() -> None:
    added = parse_added_unique_constraints(render(direction="upgrade", path=SEARCH_FILE))
    assert added == {
        ("connector_connections", "uq_connector_connections_workspace_id"): (
            "workspace_id",
            "id",
        )
    }
    assert orm_unique("connector_connections")["uq_connector_connections_workspace_id"] == (
        "workspace_id",
        "id",
    )


def test_search_migration_columns_types_and_nullability_match_orm() -> None:
    tables = parse_created_tables(render(direction="upgrade", path=SEARCH_FILE))
    for table_name in SEARCH_TABLES:
        expected_columns, expected_not_null = orm_columns(table_name)
        assert tables[table_name]["columns"] == expected_columns
        assert tables[table_name]["not_null"] == expected_not_null


def test_search_migration_constraints_and_indexes_match_orm() -> None:
    sql = render(direction="upgrade", path=SEARCH_FILE)
    tables = parse_created_tables(sql)
    indexes = parse_indexes(sql)
    for table_name in SEARCH_TABLES:
        rendered = tables[table_name]
        table = Base.metadata.tables[table_name]
        assert rendered["unique"] == orm_unique(table_name)
        assert rendered["checks"] == orm_checks(table_name)
        assert rendered["foreign_keys"] == orm_foreign_keys(table_name)
        assert rendered["primary_key"] == tuple(column.name for column in table.primary_key.columns)
        assert {entry for entry in indexes if entry[1] == table_name} == orm_indexes(table_name)


def test_search_migration_binds_index_and_exclusions_to_their_scope() -> None:
    tables = parse_created_tables(render(direction="upgrade", path=SEARCH_FILE))
    index_foreign = tables["knowledge_resource_index"]["foreign_keys"]
    assert any(
        columns == ("resource_id", "workspace_id")
        and target == "knowledge_resources"
        and action == "CASCADE"
        for columns, target, _, action in index_foreign
    )
    exclusion_foreign = tables["knowledge_exclusions"]["foreign_keys"]
    assert any(
        columns == ("workspace_id", "source_connection_id")
        and target == "connector_connections"
        and action == "CASCADE"
        for columns, target, _, action in exclusion_foreign
    )
    assert any(
        columns == ("resource_id", "workspace_id") and target == "knowledge_resources"
        for columns, target, _, _ in exclusion_foreign
    )
    checks = tables["knowledge_exclusions"]["checks"]
    assert "ck_knowledge_exclusions_scope" in checks
    assert any("scope = 'FOLDER'" in text for text in checks.values())
    index_checks = tables["knowledge_resource_index"]["checks"]
    assert "index_state IN ('INDEXED', 'NO_CONTENT', 'STALE')" in index_checks.values()


def test_search_migration_downgrade_drops_its_tables_and_constraint() -> None:
    sql = render(direction="downgrade", path=SEARCH_FILE)
    for table_name in SEARCH_TABLES:
        assert f"DROP TABLE {table_name}" in sql
    assert (
        "ALTER TABLE connector_connections DROP CONSTRAINT "
        "uq_connector_connections_workspace_id" in sql
    )
    assert "DROP TABLE knowledge_resources" not in sql


def test_intelligence_migration_columns_constraints_and_indexes_match_orm() -> None:
    sql = render(direction="upgrade", path=INTEL_FILE)
    tables = parse_created_tables(sql)
    indexes = parse_indexes(sql)
    for table_name in INTEL_TABLES:
        expected_columns, expected_not_null = orm_columns(table_name)
        rendered = tables[table_name]
        assert rendered["columns"] == expected_columns
        assert rendered["not_null"] == expected_not_null
        assert rendered["unique"] == orm_unique(table_name)
        assert rendered["checks"] == orm_checks(table_name)
        assert rendered["foreign_keys"] == orm_foreign_keys(table_name)
        assert {entry for entry in indexes if entry[1] == table_name} == orm_indexes(table_name)


def test_intelligence_migration_bounds_vectors_and_sessions() -> None:
    tables = parse_created_tables(render(direction="upgrade", path=INTEL_FILE))
    checks = tables["knowledge_embeddings"]["checks"]
    assert "dimension >= 1 AND dimension <= 4096" in checks.values()
    assert any(
        columns == ("resource_id", "workspace_id")
        and target == "knowledge_resources"
        and action == "CASCADE"
        for columns, target, _, action in tables["knowledge_embeddings"]["foreign_keys"]
    )
    turn_uniques = tables["knowledge_turns"]["unique"]
    assert turn_uniques["uq_knowledge_turns_request"] == ("session_id", "request_id")
    assert "ck_knowledge_turns_status" in tables["knowledge_turns"]["checks"]
    assert "answer" not in tables["knowledge_turns"]["columns"]
    assert any(
        columns == ("session_id", "workspace_id", "user_id") and target == "knowledge_sessions"
        for columns, target, _, _ in tables["knowledge_turns"]["foreign_keys"]
    )
    assert any(
        columns == ("workspace_id", "user_id") and target == "workspace_memberships"
        for columns, target, _, _ in tables["knowledge_sessions"]["foreign_keys"]
    )
    assert tables["knowledge_embeddings"]["unique"]["uq_knowledge_embeddings_namespace"] == (
        "resource_id",
        "chunk_index",
        "provider",
        "model",
        "registry_revision",
        "embedding_version",
        "dimension",
    )
    assert tables["knowledge_entities"]["unique"]["uq_knowledge_entities_identity"] == (
        "workspace_id",
        "entity_type",
        "canonical_key",
    )
    assert any(
        columns == ("from_entity_id", "workspace_id") and target == "knowledge_entities"
        for columns, target, _, _ in tables["knowledge_relationships"]["foreign_keys"]
    )
    request_uniques = tables["knowledge_embedding_requests"]["unique"]
    assert request_uniques["uq_knowledge_embedding_requests_request"] == (
        "workspace_id",
        "user_id",
        "request_id",
    )
    request_checks = tables["knowledge_embedding_requests"]["checks"]
    assert "ck_knowledge_embedding_requests_status" in request_checks
    assert "ck_knowledge_embedding_requests_scope" in request_checks
    assert "ck_knowledge_embedding_requests_chunk_index" in request_checks
    assert "ck_knowledge_embedding_requests_cost" in request_checks
    assert any(
        columns == ("workspace_id", "user_id") and target == "workspace_memberships"
        for columns, target, _, _ in tables["knowledge_embedding_requests"]["foreign_keys"]
    )
    answer_uniques = tables["knowledge_answer_requests"]["unique"]
    assert answer_uniques["uq_knowledge_answer_requests_request"] == (
        "workspace_id",
        "user_id",
        "request_id",
    )
    assert "ck_knowledge_answer_requests_status" in tables["knowledge_answer_requests"]["checks"]
    assert "ck_knowledge_answer_requests_cost" in tables["knowledge_answer_requests"]["checks"]
    assert any(
        columns == ("workspace_id", "user_id") and target == "workspace_memberships"
        for columns, target, _, _ in tables["knowledge_answer_requests"]["foreign_keys"]
    )


def test_intelligence_migration_downgrade_drops_only_its_own_tables() -> None:
    sql = render(direction="downgrade", path=INTEL_FILE)
    for table_name in INTEL_TABLES:
        assert f"DROP TABLE {table_name}" in sql
    assert "DROP TABLE knowledge_resources" not in sql
    assert "DROP TABLE knowledge_chunks" not in sql


def test_alembic_renders_the_whole_chain_offline_for_postgresql() -> None:
    environment = {
        **os.environ,
        "DATABASE_URL": DATABASE_URL,
        "PYTHONPATH": str(API_ROOT),
    }
    environment.pop("NAVOX_CONNECTOR_TEST_DSN", None)
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=API_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    for table_name in KNOWLEDGE_TABLES:
        assert f"CREATE TABLE {table_name} (" in completed.stdout
    assert completed.stdout.index("CREATE TABLE news_intelligence_runs") < completed.stdout.index(
        "CREATE TABLE knowledge_resources ("
    )
    for table_name in SEARCH_TABLES:
        assert f"CREATE TABLE {table_name} (" in completed.stdout
    for table_name in INTEL_TABLES:
        assert f"CREATE TABLE {table_name} (" in completed.stdout
    assert completed.stdout.index("CREATE TABLE knowledge_resources (") < completed.stdout.index(
        "CREATE TABLE knowledge_resource_index ("
    )
