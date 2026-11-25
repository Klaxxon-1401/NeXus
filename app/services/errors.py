"""Domain-level errors translated into friendly HTTP responses by routes."""

from __future__ import annotations


class ServiceError(Exception):
    """A safe, user-presentable domain error.

    The message is deliberately user-facing. Never include stack traces, SQL or
    secrets in this exception.
    """

    def __init__(self, message: str, *, code: str = "invalid_request") -> None:
        super().__init__(message)
        self.message = message
        self.code = code
