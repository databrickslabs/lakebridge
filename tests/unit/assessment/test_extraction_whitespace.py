import zoneinfo
import pytest


def test_zoneinfo_creation_with_stripped_whitespace() -> None:
    """Test that zoneinfo.ZoneInfo works correctly with stripped timezone strings."""
    # This tests the core behavior that our code relies on
    tz_with_whitespace = ' America/New_York '
    tz_stripped = tz_with_whitespace.strip()

    # This should work without raising an exception
    tz_stripped_zoneinfo = zoneinfo.ZoneInfo(tz_stripped)
    assert str(tz_stripped_zoneinfo) == 'America/New_York'

    # Verify that unstripped whitespace would cause issues
    # (This is the bug we're fixing)
    with pytest.raises(zoneinfo.ZoneInfoNotFoundError):
        zoneinfo.ZoneInfo(tz_with_whitespace)


def test_zoneinfo_with_various_whitespace() -> None:
    """Test that various types of whitespace are properly handled."""
    test_cases = [
        '\tUTC\n',
        ' Europe/London ',
        '  Asia/Tokyo  ',
    ]

    for tz_with_whitespace in test_cases:
        # With strip, should work
        tz_stripped_zoneinfo = zoneinfo.ZoneInfo(tz_with_whitespace.strip())
        assert isinstance(tz_stripped_zoneinfo, zoneinfo.ZoneInfo)

        # Without strip, should fail
        with pytest.raises(zoneinfo.ZoneInfoNotFoundError):
            zoneinfo.ZoneInfo(tz_with_whitespace)


def test_string_strip_preserves_internal_spaces() -> None:
    """Test that .strip() only removes leading/trailing whitespace, not internal spaces."""
    # This is a sanity check for the behavior we rely on
    test_cases = [
        (' value ', 'value'),
        ('  value  ', 'value'),
        ('\tvalue\n', 'value'),
        (' value with spaces ', 'value with spaces'),
        ('  ODBC Driver 17 for SQL Server  ', 'ODBC Driver 17 for SQL Server'),
    ]

    for input_val, expected in test_cases:
        assert input_val.strip() == expected
