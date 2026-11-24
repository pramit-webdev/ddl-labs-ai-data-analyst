"""Domain errors mapped to clean API responses."""

from __future__ import annotations


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class InvalidPeriodError(AppError):
    status_code = 422
    code = "invalid_period"


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class DatabaseUnavailableError(AppError):
    status_code = 503
    code = "database_unavailable"


class LLMUnavailableError(AppError):
    status_code = 502
    code = "llm_unavailable"


class RateLimitError(AppError):
    status_code = 429
    code = "rate_limited"
