"""Public sync diagnostics contain classifications, never provider or source prose."""

from navox.ai.errors import AIProviderError
from navox.intelligence.extraction import InvalidOperationalExtraction
from navox.providers.google_oauth import GoogleAccessTokenError
from navox.providers.google_sources import GOOGLE_SOURCE_DIAGNOSTIC_CODES, GoogleSourceError

SYNC_DIAGNOSTIC_CODES = GOOGLE_SOURCE_DIAGNOSTIC_CODES | {
    "google_token_unavailable",
    "provider_request_failed",
    "extraction_validation_failed",
    "intelligence_processing_failed",
}


def sanitize_diagnostic(value: object) -> dict[str, str | int]:
    """Revalidate persisted diagnostics before showing them to a workspace owner."""
    result: dict[str, str | int] = {"code": "intelligence_processing_failed"}
    if not isinstance(value, dict):
        return result
    code = value.get("code")
    if isinstance(code, str) and code in SYNC_DIAGNOSTIC_CODES:
        result["code"] = code
    status = value.get("http_status")
    if type(status) is int and 100 <= status <= 599:
        result["http_status"] = status
    return result


def processing_diagnostic(error: Exception) -> dict[str, str | int]:
    """Inspect only known exception types; never call arbitrary diagnostic methods."""
    if isinstance(error, GoogleSourceError):
        return sanitize_diagnostic(error.diagnostic())
    if isinstance(error, GoogleAccessTokenError):
        return {"code": "google_token_unavailable"}
    if isinstance(error, AIProviderError):
        return sanitize_diagnostic(
            {"code": "provider_request_failed", "http_status": error.http_status}
        )
    if isinstance(error, InvalidOperationalExtraction):
        return {"code": "extraction_validation_failed"}
    return {"code": "intelligence_processing_failed"}
