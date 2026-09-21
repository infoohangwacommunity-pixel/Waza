"""Pure delivery retry backoff — no I/O, no ORM."""


def retry_backoff_seconds(attempt: int, *, base: int = 10, cap: int = 600) -> int:
    """Exponential backoff for delivery retries (pure, testable)."""
    a = max(1, int(attempt or 1))
    return min(cap, (2**a) * base)
