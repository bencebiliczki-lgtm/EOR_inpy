from datetime import datetime
from zoneinfo import ZoneInfo

HUNGARIAN_TIMEZONE_NAME = "Europe/Budapest"
HUNGARIAN_TIMEZONE = ZoneInfo(HUNGARIAN_TIMEZONE_NAME)


def as_hungarian_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must include timezone information")
    return value.astimezone(HUNGARIAN_TIMEZONE)


def format_hungarian_time(
    value: datetime, pattern: str = "%Y-%m-%d %H:%M:%S %Z"
) -> str:
    return as_hungarian_time(value).strftime(pattern)
