"""Drive error taxonomy — every Google condition maps to exactly one class."""
from __future__ import annotations


class DriveError(Exception):
    """Base. `retryable` decides whether the worker backs off or fails the item."""
    retryable = False
    code = "drive_error"
    user_key = "error"

    def __init__(self, message: str = "", *, status: int = 0, detail: str = "",
                 retry_after: float = 0.0) -> None:
        super().__init__(message or self.__class__.__name__)
        self.status = status
        self.detail = detail
        self.retry_after = retry_after

    def __str__(self) -> str:  # keep messages short for the UI
        base = super().__str__()
        return f"{base} ({self.detail})" if self.detail else base


class RetryableDriveError(DriveError):
    retryable = True
    code = "retryable"


class RateLimited(RetryableDriveError):
    code = "rate_limited"
    user_key = "rate_limited"


class ServerError(RetryableDriveError):
    code = "server_error"


class NetworkError(RetryableDriveError):
    code = "network_error"


class AuthExpired(DriveError):
    code = "auth_expired"
    user_key = "auth_expired"


class SessionGone(DriveError):
    """404 on a resumable session URI → start a new session."""
    code = "session_gone"
    retryable = True


class StorageQuota(DriveError):
    code = "storage_quota"
    user_key = "drive_full"


class DailyLimit(DriveError):
    code = "daily_limit"
    user_key = "drive_daily"


class NotFound(DriveError):
    code = "not_found"


class PermissionDenied(DriveError):
    code = "permission_denied"


class InvalidRequest(DriveError):
    code = "invalid_request"


def classify(status: int, body: str = "", reason: str = "") -> DriveError:
    """Map a Google error response to the right class."""
    body_l = (body or "").lower()
    reason_l = (reason or "").lower()
    if status == 401:
        return AuthExpired("Google token rejected", status=status, detail=reason_l)
    if status == 403:
        if "storagequotaexceeded" in body_l:
            return StorageQuota("Drive storage is full", status=status, detail="storageQuotaExceeded")
        if "dailyLimitExceeded" in body or "userRateLimitExceeded".lower() in body_l \
                or "uploadLimitExceeded" in body_l or "dailylimitexceeded" in body_l:
            return DailyLimit("Daily upload cap reached", status=status, detail="dailyLimitExceeded")
        if "ratelimitexceeded" in body_l or "ratelimitexceeded" in reason_l:
            return RateLimited("Rate limited by Google", status=status, detail="rateLimitExceeded")
        if "permission" in body_l or "forbidden" in body_l:
            return PermissionDenied("No permission for that folder", status=status, detail=reason_l)
        return RateLimited("Google refused the request", status=status, detail=body_l[:200])
    if status == 404:
        return NotFound("File or folder not found", status=status)
    if status == 400:
        return InvalidRequest("Google rejected the request", status=status, detail=body_l[:200])
    if status == 429:
        return RateLimited("Rate limited", status=status)
    if 500 <= status < 600:
        return ServerError(f"Google server error {status}", status=status)
    return DriveError(f"Drive error {status}", status=status, detail=body_l[:200])
