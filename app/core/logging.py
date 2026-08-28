import logging
import sys
from collections.abc import Mapping
from typing import Any, cast

import structlog
from structlog.stdlib import BoundLogger, ProcessorFormatter
from structlog.typing import EventDict, WrappedLogger

SENSITIVE_KEY_PARTS = (
    "secret",
    "token",
    "api_key",
    "apikey",
    "password",
    "authorization",
    "phone",
    "email",
    "contact",
)
REDACTED = "[REDACTED]"


def redact_sensitive(
    logger: WrappedLogger,
    method_name: str,
    event_dict: EventDict,
) -> EventDict:
    return cast(EventDict, _redact_mapping(event_dict))


def configure_logging(level: str = "INFO") -> None:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_sensitive,
    ]
    formatter = ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(ensure_ascii=False),
        foreign_pre_chain=shared_processors,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(numeric_level)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    structlog.configure(
        processors=[*shared_processors, ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> BoundLogger:
    return cast(BoundLogger, structlog.get_logger(name))


def _redact_mapping(values: Mapping[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in values.items():
        normalized_key = key.casefold().replace("-", "_")
        if any(part in normalized_key for part in SENSITIVE_KEY_PARTS):
            redacted[key] = REDACTED
        elif isinstance(value, Mapping):
            redacted[key] = _redact_mapping(value)
        elif isinstance(value, list | tuple):
            redacted[key] = [
                _redact_mapping(item) if isinstance(item, Mapping) else item for item in value
            ]
        else:
            redacted[key] = value
    return redacted
