import base64
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from navox.providers.google_sources import MAX_CONTENT_CHARS, gmail_document


def part(mime_type: str, content: str, **extra: Any) -> dict[str, Any]:
    return {
        "mimeType": mime_type,
        "body": {"data": base64.urlsafe_b64encode(content.encode()).decode()},
        **extra,
    }


def normalize(payload: dict[str, Any]) -> str | None:
    return gmail_document(
        {"id": "html-message", "payload": payload},
        workspace_id=uuid4(),
        connection_id=uuid4(),
        now=datetime.now(UTC),
    ).content


def test_html_only_mail_retains_request_and_excludes_executable_content() -> None:
    content = normalize(
        part(
            "text/html",
            "<html><head><style>STYLE SECRET</style><title>HEAD SECRET</title></head>"
            "<body><p>Please <strong>send</strong> the budget &amp; forecast tomorrow.</p>"
            '<script>fetch("https://example.com/SECRET")</script>'
            "<template>TEMPLATE SECRET</template><!-- COMMENT SECRET -->"
            '<p>Meeting at <a href="https://example.com/LINK_SECRET">09:00</a>.</p>'
            '<img src="https://example.com/IMAGE_SECRET" /></body></html>',
        )
    )
    assert content == "Please send the budget & forecast tomorrow.\nMeeting at 09:00."
    assert "SECRET" not in content


def test_multipart_alternative_prefers_plaintext_without_duplicate_evidence() -> None:
    assert (
        normalize(
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    part("text/html", "<p>HTML alternative must not appear</p>"),
                    part("text/plain", "Please send the budget tomorrow."),
                ],
            }
        )
        == "Please send the budget tomorrow."
    )


def test_empty_plaintext_falls_back_to_html_but_attachments_stay_excluded() -> None:
    content = normalize(
        {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [part("text/plain", ""), part("text/html", "<p>Reply today.</p>")],
                },
                part("text/html", "<p>ATTACHMENT SECRET</p>", filename="attachment.html"),
                part(
                    "text/html",
                    "<p>ATTACHMENT SECRET</p>",
                    headers=[{"name": "Content-Disposition", "value": "attachment"}],
                ),
            ],
        }
    )
    assert content is not None
    assert content.strip() == "Reply today."
    assert "SECRET" not in content


def test_html_content_and_mime_recursion_are_bounded() -> None:
    content = normalize(part("text/html", "<p>" + "x" * (MAX_CONTENT_CHARS * 8) + "</p>"))
    assert content is not None and len(content) <= MAX_CONTENT_CHARS
    assert set(content) == {"x"}
    payload = part("text/html", "<p>Too deeply nested</p>")
    for _ in range(14):
        payload = {"mimeType": "multipart/mixed", "parts": [payload]}
    assert normalize(payload) is None
