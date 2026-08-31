class SMSProviderError(Exception):
    """Base error raised by an SMS provider."""


class SMSProviderAuthError(SMSProviderError):
    """Raised when the provider rejects our credentials (e.g. 401)."""


class SMSProviderUnavailableError(SMSProviderError):
    """Raised on network/timeout failures talking to the provider API."""
