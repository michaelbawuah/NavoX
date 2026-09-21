from navox.db import models  # noqa: F401 - register models before assertion
from navox.db.base import Base


def test_tenant_foundation_tables_are_registered() -> None:
    assert {
        "users",
        "workspaces",
        "workspace_memberships",
        "user_sessions",
    } <= set(Base.metadata.tables)


def test_membership_has_workspace_and_user_foreign_keys() -> None:
    membership = Base.metadata.tables["workspace_memberships"]
    foreign_keys = {foreign_key.target_fullname for foreign_key in membership.foreign_keys}

    assert foreign_keys == {"users.id", "workspaces.id"}


def test_session_has_a_user_foreign_key() -> None:
    session = Base.metadata.tables["user_sessions"]
    foreign_keys = {foreign_key.target_fullname for foreign_key in session.foreign_keys}

    assert foreign_keys == {"users.id"}
