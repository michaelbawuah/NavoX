"""Photo metadata only: explicit display rights and reviewed public image domains."""

from urllib.parse import urlsplit

from pydantic import ValidationError

from navox.news.contracts import ContentRights, NewsImage, SourceDefinition


def permitted_image(
    value: object,
    definition: SourceDefinition,
    *,
    policies: tuple[ContentRights, ...],
) -> NewsImage | None:
    # Recheck both the rights under which it was stored and today's rights.
    if not policies or not all(policy.image_display_allowed for policy in policies):
        return None
    if not definition.image_domains or value is None:
        return None
    try:
        image = NewsImage.model_validate(value)
    except (ValidationError, ValueError, TypeError):
        return None
    if urlsplit(image.url).hostname not in definition.image_domains:
        return None
    return image


def feed_image(
    url: object, alt: object, credit: object, definition: SourceDefinition
) -> NewsImage | None:
    # A feed field is never evidence of a license. In particular, do not extract
    # or hash a photo/caption when the operator's current policy forbids display.
    if not definition.rights.image_display_allowed:
        return None
    if not all(isinstance(value, str) and value.strip() for value in (url, alt, credit)):
        return None
    return permitted_image(
        {"url": url, "alt": alt, "credit": credit},
        definition,
        policies=(definition.rights,),
    )
