from wax.domain.preferences import is_in_quiet_hours, DEFAULT_PREFERENCES


def test_no_quiet_hours_means_not_quiet():
    assert is_in_quiet_hours({}, "23:00") is False
    assert is_in_quiet_hours(DEFAULT_PREFERENCES, "23:00") is False


def test_overnight_window():
    prefs = {"quiet_hours": {"start": "22:00", "end": "07:00"}}
    assert is_in_quiet_hours(prefs, "23:30") is True
    assert is_in_quiet_hours(prefs, "06:00") is True
    assert is_in_quiet_hours(prefs, "12:00") is False


def test_same_day_window():
    prefs = {"quiet_hours": {"start": "13:00", "end": "15:00"}}
    assert is_in_quiet_hours(prefs, "14:00") is True
    assert is_in_quiet_hours(prefs, "16:00") is False
