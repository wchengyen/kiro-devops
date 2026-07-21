import pytest

from multi_profile.operational_settings import (
    OperationalSettings,
    load_operational_settings,
)


def test_defaults_match_spec_section_14_1():
    settings = load_operational_settings({})

    assert settings.sts_timeout_sec == 10
    assert settings.health_check_interval_sec == 600
    assert settings.health_grace_sec == 1800
    assert settings.health_jitter_max_sec == 60
    assert settings.revision_keep == 20


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("AWS_STS_TIMEOUT_SEC", "2"),      # 允許 3–60
        ("AWS_STS_TIMEOUT_SEC", "61"),
        ("PROFILE_HEALTH_CHECK_INTERVAL_SEC", "59"),   # 允許 60–3600
        ("PROFILE_HEALTH_CHECK_INTERVAL_SEC", "3601"),
        ("PROFILE_HEALTH_GRACE_SEC", "-1"),            # 允許 0–86400
        ("PROFILE_HEALTH_GRACE_SEC", "86401"),
        ("AWS_STS_TIMEOUT_SEC", "not-a-number"),
    ],
)
def test_out_of_range_or_invalid_values_are_rejected(key, value):
    with pytest.raises(ValueError, match=key):
        load_operational_settings({key: value})


def test_boundary_values_are_accepted():
    settings = load_operational_settings({
        "AWS_STS_TIMEOUT_SEC": "3",
        "PROFILE_HEALTH_CHECK_INTERVAL_SEC": "3600",
        "PROFILE_HEALTH_GRACE_SEC": "0",
    })

    assert settings.sts_timeout_sec == 3
    assert settings.health_check_interval_sec == 3600
    assert settings.health_grace_sec == 0
