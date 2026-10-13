"""Reviewed public feeds remain optional, bounded, and individually owner-scoped."""

import json
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

import pytest

from navox.core.settings import Settings
from navox.db.models import User, Workspace, WorkspaceMembership
from navox.news.contracts import Category, FeedType, NewsError
from navox.news.feeds import parse_feed
from navox.news.registry import activate_source, catalog, owned_source
from navox.news.rights import Operation, require_operation

NOW = datetime(2026, 10, 13, 18, tzinfo=UTC)
FIXTURES = Path(__file__).parent / "fixtures" / "news_public"
REPO_ROOT = Path(__file__).resolve().parents[3]


def public_catalog():
    return catalog(Settings(_env_file=None, news_public_sources_enabled=True))


def test_public_templates_are_packaged_but_default_feature_gates_remain_off():
    settings = Settings(_env_file=None)
    assert not settings.news_feed_enabled
    assert not settings.news_public_sources_enabled
    assert not settings.news_chat_enabled
    assert catalog(settings) == {}

    enabled = Settings(_env_file=None, news_public_sources_enabled=True)
    assert not enabled.news_feed_enabled and not enabled.news_chat_enabled
    definitions = catalog(enabled)
    assert len(definitions) == 4
    assert {source.feed_type for source in definitions.values()} == {FeedType.RSS, FeedType.ATOM}
    assert all(source.api_connection_id is None for source in definitions.values())
    assert all(source.api_endpoint_name is None for source in definitions.values())
    assert all(source.evidence_policy is None for source in definitions.values())

    # The canonical source catalog is included in the package copied into the API container.
    canonical = files("navox.news").joinpath("public_sources.json")
    assert canonical.is_file()
    assert (REPO_ROOT / "config/news/public-sources.json").read_text() == canonical.read_text()


def test_original_usgs_fingerprint_is_preserved():
    source = public_catalog()["usgs-significant-earthquakes"]
    assert source.fingerprint == "6db66472591d7756730cafd2709d768271fa889658a29a4631231bde32064760"


def test_public_rights_have_review_windows_and_deny_full_articles_and_images():
    definitions = public_catalog()
    for source in definitions.values():
        rights = source.rights
        assert rights.reviewed_at <= NOW < rights.expires_at
        assert (rights.expires_at - rights.reviewed_at).days == 90
        assert rights.metadata_storage_allowed and rights.snippet_storage_allowed
        assert rights.attribution_required and rights.link_required
        assert not source.image_domains
        for operation in (Operation.PROCESS_TEXT, Operation.STORE_TEXT, Operation.IMAGE):
            with pytest.raises(NewsError, match="rights_denied"):
                require_operation(operation, rights)
    assert definitions["nasa-recent-news"].rights.retention_days == 7
    assert definitions["noaa-ocean-research"].rights.retention_days == 30
    assert definitions["bea-economic-releases"].category == Category.BUSINESS
    with pytest.raises(NewsError, match="rights_denied"):
        require_operation(Operation.SUMMARY, definitions["nasa-recent-news"].rights)


def test_operator_catalog_is_unchanged_when_public_templates_are_off():
    source = public_catalog()["nasa-recent-news"].model_dump(mode="json")
    source["key"] = "operator-nasa-link-feed"
    settings = Settings(_env_file=None, news_source_catalog=[source])
    assert set(catalog(settings)) == {"operator-nasa-link-feed"}
    enabled = settings.model_copy(update={"news_public_sources_enabled": True})
    assert len(catalog(enabled)) == 5


def test_identical_operator_definition_is_deduplicated_when_public_templates_are_enabled():
    source = public_catalog()["usgs-significant-earthquakes"].model_dump(mode="json")
    settings = Settings(
        _env_file=None, news_public_sources_enabled=True, news_source_catalog=[source]
    )
    assert len(catalog(settings)) == 4
    assert catalog(settings)["usgs-significant-earthquakes"].fingerprint == (
        public_catalog()["usgs-significant-earthquakes"].fingerprint
    )


@pytest.mark.parametrize("change", ["rights", "endpoint"])
def test_operator_collision_cannot_replace_or_widen_packaged_rights(change):
    source = public_catalog()["nasa-recent-news"].model_dump(mode="json")
    if change == "rights":
        source["rights"]["summary_generation_allowed"] = True
        source["rights"]["image_display_allowed"] = True
    else:
        source["endpoint"] = "https://www.nasa.gov/technology/feed/"
    settings = Settings(
        _env_file=None, news_public_sources_enabled=True, news_source_catalog=[source]
    )
    with pytest.raises(NewsError, match="invalid_source"):
        catalog(settings)


@pytest.mark.parametrize("enabled", [False, True])
def test_duplicate_operator_keys_remain_invalid_with_or_without_public_templates(enabled):
    source = public_catalog()["usgs-significant-earthquakes"].model_dump(mode="json")
    settings = Settings(
        _env_file=None, news_public_sources_enabled=enabled, news_source_catalog=[source, source]
    )
    with pytest.raises(NewsError, match="invalid_source"):
        catalog(settings)


@pytest.mark.parametrize(
    ("source_key", "fixture", "accepted", "rejected", "published"),
    [
        (
            "nasa-recent-news",
            "nasa.xml",
            2,
            1,
            datetime(2026, 10, 13, 17, 38, 37, tzinfo=UTC),
        ),
        (
            "noaa-ocean-research",
            "noaa.xml",
            1,
            0,
            datetime(2026, 10, 6, 12, tzinfo=UTC),
        ),
        (
            "bea-economic-releases",
            "bea.xml",
            2,
            1,
            datetime(2026, 10, 6, 12, 30, tzinfo=UTC),
        ),
        (
            "usgs-significant-earthquakes",
            "usgs.xml",
            1,
            0,
            datetime(2026, 10, 13, 17, tzinfo=UTC),
        ),
    ],
)
def test_public_feed_formats_preserve_dates_and_reject_unreviewed_links(
    source_key, fixture, accepted, rejected, published
):
    source = public_catalog()[source_key]
    items, rejected_count = parse_feed((FIXTURES / fixture).read_bytes(), source)
    assert len(items) == accepted and rejected_count == rejected
    assert items[0].published_at == published
    assert items[0].description
    assert all(item.image is None for item in items)
    assert "UNREVIEWED_FULL_ARTICLE" not in json.dumps([item.model_dump_json() for item in items])


@pytest.mark.asyncio
async def test_each_owner_can_activate_public_template_without_sharing_private_sources(ai_database):
    first_user, second_user, workspace_id = uuid4(), uuid4(), uuid4()
    source = public_catalog()["nasa-recent-news"]
    async with ai_database() as database:
        database.add_all(
            [
                User(id=first_user, email="public-first@example.com"),
                User(id=second_user, email="public-second@example.com"),
                Workspace(id=workspace_id, name="Public news"),
            ]
        )
        await database.flush()
        database.add_all(
            [
                WorkspaceMembership(workspace_id=workspace_id, user_id=first_user),
                WorkspaceMembership(workspace_id=workspace_id, user_id=second_user),
            ]
        )
        await database.flush()
        first = await activate_source(
            database, source, workspace_id=workspace_id, user_id=first_user, now=NOW
        )
        second = await activate_source(
            database, source, workspace_id=workspace_id, user_id=second_user, now=NOW
        )
        assert first.id != second.id
        assert first.user_id == first_user and second.user_id == second_user
        assert first.config_digest == second.config_digest == source.fingerprint
        with pytest.raises(NewsError, match="source_unavailable"):
            await owned_source(database, first.id, workspace_id=workspace_id, user_id=second_user)
        with pytest.raises(NewsError, match="rights_expired"):
            await activate_source(
                database,
                source,
                workspace_id=workspace_id,
                user_id=second_user,
                now=source.rights.expires_at,
            )
