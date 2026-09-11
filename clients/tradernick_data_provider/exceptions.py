class DataProviderError(Exception):
    pass


class DataProviderHTTPError(DataProviderError):
    """An error response from data_provider.

    ``error`` is the machine-readable slug (e.g. ``range_too_large``) and
    ``detail`` the server's human-facing guidance, when it sends one — a 413
    explains how to narrow the request. Both are kept as attributes AND folded
    into str() so a bare `except ... as e: print(e)` still shows the advice."""

    def __init__(self, status_code: int, message: str, detail: str | None = None):
        self.status_code = status_code
        self.error = message
        self.detail = detail
        super().__init__(
            f"HTTP {status_code}: {message}" + (f" — {detail}" if detail else "")
        )
