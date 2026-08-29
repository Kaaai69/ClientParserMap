class AppError(Exception):
    """Application error safe to translate into structured logs/API responses."""

    def __init__(self, code: str, public_message: str, *, retryable: bool = False) -> None:
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message
        self.retryable = retryable


class ConfigurationError(AppError):
    def __init__(self, public_message: str) -> None:
        super().__init__("CONFIGURATION_ERROR", public_message)


class SourceRequestError(AppError):
    pass


class UnsafeTargetError(AppError):
    def __init__(self, code: str = "UNSAFE_TARGET") -> None:
        super().__init__(code, "Адрес сайта запрещён политикой безопасности")
