from pathlib import Path

from multi_profile.feature_flags import config_path, is_enabled


def test_feature_flag_defaults_to_false():
    assert is_enabled({}) is False


def test_feature_flag_accepts_explicit_true_values():
    for value in ("true", "1", "yes", "TRUE"):
        assert is_enabled({"MULTI_PROFILE_ENABLED": value}) is True


def test_feature_flag_rejects_other_values():
    for value in ("false", "0", "no", "unexpected", ""):
        assert is_enabled({"MULTI_PROFILE_ENABLED": value}) is False


def test_config_path_defaults_to_project_file(tmp_path):
    assert config_path({}, project_dir=tmp_path) == tmp_path / "multi_profile_config.yaml"


def test_config_path_uses_explicit_absolute_path(tmp_path):
    expected = tmp_path / "custom.yaml"
    assert config_path(
        {"MULTI_PROFILE_CONFIG": str(expected)},
        project_dir=Path("/unused"),
    ) == expected
