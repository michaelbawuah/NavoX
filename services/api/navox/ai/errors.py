AI_PROVIDER_DIAGNOSTIC_CODES = frozenset(
    {
        "authentication_failed",
        "permission_denied",
        "model_unavailable",
        "quota_exhausted",
        "rate_limited",
        "invalid_schema",
        "unsupported_parameter",
        "invalid_request",
        "provider_unavailable",
        "timeout",
        "transport_error",
        "incomplete_response",
        "invalid_response",
        "provider_error",
    }
)


class AIProviderError(RuntimeError):
    """A configured provider could not produce an accepted response."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "provider_error",
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code if code in AI_PROVIDER_DIAGNOSTIC_CODES else "provider_error"
        self.http_status = (
            http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        )

    def diagnostic(self) -> dict[str, str | int]:
        """Only fixed classifications and numeric status, never exception/provider text."""
        result: dict[str, str | int] = {"code": self.code}
        if self.http_status is not None:
            result["http_status"] = self.http_status
        return result


class AIProviderRejectedOutput(AIProviderError):
    """The completed generation refused or failed its output contract."""
