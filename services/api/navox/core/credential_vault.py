from cryptography.fernet import Fernet, InvalidToken

from navox.core.settings import Settings


class CredentialVaultError(ValueError):
    """Raised when a protected connection credential cannot be used safely."""


class CredentialVault:
    def __init__(self, settings: Settings) -> None:
        key = settings.google_token_encryption_key
        if key is None or not key.get_secret_value():
            raise CredentialVaultError("Google credential encryption is not configured")
        try:
            self._fernet = Fernet(key.get_secret_value().encode("utf-8"))
        except ValueError as error:
            raise CredentialVaultError("Google credential encryption key is invalid") from error

    def seal_refresh_token(self, refresh_token: str) -> str:
        return self._fernet.encrypt(refresh_token.encode("utf-8")).decode("utf-8")

    def reveal_refresh_token(self, encrypted_refresh_token: str) -> str:
        try:
            return self._fernet.decrypt(encrypted_refresh_token.encode("utf-8")).decode("utf-8")
        except InvalidToken as error:
            raise CredentialVaultError("Google credential cannot be decrypted") from error
