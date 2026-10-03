"""Bounded RSS/Atom metadata reader. Never fetches article URLs or retains raw feeds."""

from __future__ import annotations

import asyncio
from datetime import datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

import httpx

from navox.news.contracts import (
    FeedType,
    NewsError,
    NewsItemInput,
    SourceDefinition,
    aware_utc,
)
from navox.news.images import feed_image

MAX_FEED_BYTES = 1_048_576
MAX_FEED_ITEMS = 100


class TextOnly(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "iframe"}:
            self.hidden += 1
        if not self.hidden:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "iframe"} and self.hidden:
            self.hidden -= 1
        if not self.hidden:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def plain_text(value: str, *, limit: int) -> str:
    parser = TextOnly()
    parser.feed(value)
    return " ".join("".join(parser.parts).split())[:limit]


def _value(node: ET.Element, path: str) -> str:
    element = node.find(path)
    return "" if element is None else "".join(element.itertext()).strip()


def _time(value: str) -> datetime:
    try:
        return aware_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return aware_utc(parsedate_to_datetime(value))


def parse_feed(data: bytes, definition: SourceDefinition) -> tuple[list[NewsItemInput], int]:
    if len(data) > MAX_FEED_BYTES:
        raise NewsError("feed_too_large")
    if definition.feed_type not in {FeedType.RSS, FeedType.ATOM}:
        raise NewsError("invalid_feed")
    try:
        xml = data.decode("utf-8-sig")
        if "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper():
            raise NewsError("invalid_feed")
        root = ET.fromstring(xml)
    except (UnicodeError, ET.ParseError):
        raise NewsError("invalid_feed") from None
    atom = "{http://www.w3.org/2005/Atom}"
    is_atom = definition.feed_type == FeedType.ATOM
    if (is_atom and root.tag != atom + "feed") or (not is_atom and root.tag != "rss"):
        raise NewsError("invalid_feed")
    nodes = root.findall(atom + "entry" if is_atom else "channel/item")
    result: list[NewsItemInput] = []
    rejected = 0
    for node in nodes[:MAX_FEED_ITEMS]:
        try:
            prefix = atom if is_atom else ""
            link = _value(node, "link")
            if is_atom:
                links = [
                    el.attrib.get("href", "")
                    for el in node.findall(atom + "link")
                    if el.attrib.get("rel", "alternate") == "alternate"
                ]
                link = links[0] if links else ""
            if urlsplit(link).hostname not in definition.article_domains:
                raise ValueError("Unregistered article domain")
            published = _value(node, atom + "published" if is_atom else "pubDate")
            # Missing publication time is not replaced by retrieval or event time.
            updated = _value(node, atom + "updated") if is_atom else ""
            published = published or (updated if is_atom else "")
            description = None
            if definition.rights.snippet_storage_allowed:
                description = (
                    plain_text(
                        _value(node, atom + "summary" if is_atom else "description"), limit=4000
                    )
                    or None
                )
            media = "{http://search.yahoo.com/mrss/}"
            image = node.find(media + "content")
            result.append(
                NewsItemInput(
                    external_id=_value(node, atom + "id" if is_atom else "guid") or link,
                    headline=plain_text(_value(node, prefix + "title"), limit=500),
                    canonical_url=link,
                    author=plain_text(
                        _value(node, atom + "author/" + atom + "name" if is_atom else "author"),
                        limit=200,
                    )
                    or None,
                    published_at=_time(published),
                    updated_at=_time(updated) if updated else None,
                    description=description,
                    image=feed_image(
                        image.attrib.get("url") if image is not None else None,
                        _value(node, media + "description"),
                        _value(node, media + "credit"),
                        definition,
                    )
                    if definition.rights.image_display_allowed
                    and image is not None
                    and (
                        image.attrib.get("medium") == "image"
                        or image.attrib.get("type", "").startswith("image/")
                    )
                    else None,
                    categories=(definition.category,),
                    language=definition.language,
                    region=definition.region,
                )
            )
        except (ValueError, TypeError, OverflowError):
            rejected += 1
    return result, rejected


async def fetch_feed(client: httpx.AsyncClient, definition: SourceDefinition) -> bytes:
    """Only a reviewed endpoint is fetched; redirects never broaden the destination."""
    try:
        async with asyncio.timeout(20):
            return await _fetch_feed(client, definition)
    except TimeoutError:
        raise NewsError("feed_unavailable") from None


async def _fetch_feed(client: httpx.AsyncClient, definition: SourceDefinition) -> bytes:
    try:
        async with client.stream(
            "GET",
            definition.endpoint,
            headers={"Accept": "application/atom+xml, application/rss+xml, application/xml"},
            follow_redirects=False,
            timeout=httpx.Timeout(15),
        ) as response:
            if response.status_code != 200:
                raise NewsError("feed_unavailable")
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if content_type not in {
                "application/atom+xml",
                "application/rss+xml",
                "application/xml",
                "text/xml",
            }:
                raise NewsError("invalid_feed")
            data = bytearray()
            async for chunk in response.aiter_bytes():
                if len(data) + len(chunk) > MAX_FEED_BYTES:
                    raise NewsError("feed_too_large")
                data.extend(chunk)
            return bytes(data)
    except (httpx.HTTPError, ValueError, UnicodeError) as error:
        if isinstance(error, NewsError):
            raise
        raise NewsError("feed_unavailable") from None
