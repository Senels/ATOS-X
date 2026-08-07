from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return the current UTC time as a naive datetime for legacy callers.

    The codebase stores and compares UTC timestamps as naive datetimes/strings,
    so this helper preserves that convention while avoiding deprecated
    `datetime.utcnow()` usage.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
