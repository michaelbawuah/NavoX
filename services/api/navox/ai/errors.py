class AIProviderError(RuntimeError):
    """A configured provider could not produce an accepted response."""


class AIProviderRejectedOutput(AIProviderError):
    """The completed generation refused or failed its output contract."""
