from datetime import UTC, datetime

from eor_control.timezone import as_hungarian_time, format_hungarian_time


def test_hungarian_timezone_applies_winter_and_summer_offsets() -> None:
    winter = as_hungarian_time(datetime(2026, 1, 15, 12, 0, tzinfo=UTC))
    summer = as_hungarian_time(datetime(2026, 7, 15, 12, 0, tzinfo=UTC))

    assert winter.isoformat() == "2026-01-15T13:00:00+01:00"
    assert summer.isoformat() == "2026-07-15T14:00:00+02:00"
    assert format_hungarian_time(summer).endswith("CEST")


def test_naive_timestamp_is_rejected() -> None:
    try:
        as_hungarian_time(datetime(2026, 1, 1))
    except ValueError as error:
        assert "timezone" in str(error)
    else:
        raise AssertionError("naive timestamp must be rejected")
