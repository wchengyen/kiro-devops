import json

from multi_profile.external_validation import run_validation_pipeline
from multi_profile.sts import StsResult


VALID_YAML = """
version: 1
apps:
  ops-bot:
    app_id_env: FEISHU_OPS_APP_ID
    app_secret_env: FEISHU_OPS_APP_SECRET
    default_profile: prod-cn
profiles:
  prod-cn:
    aws_profile: production
    aws_region: cn-northwest-1
    expected_account_id: "123456789012"
    kiro_agent: my-dev-bot
    model: claude-sonnet
    working_dir: {working_dir}
routes:
  - app: ops-bot
    chat_id: oc_prod
    profile: prod-cn
"""


def make_env(tmp_path):
    (tmp_path / "kiro" / "agents").mkdir(parents=True)
    (tmp_path / "kiro" / "agents" / "my-dev-bot.json").write_text(
        json.dumps({"name": "my-dev-bot"})
    )
    (tmp_path / "aws").mkdir()
    (tmp_path / "aws" / "config").write_text(
        "[profile production]\nregion = cn-northwest-1\n"
    )
    return {
        "environ": {
            "FEISHU_OPS_APP_ID": "cli_test",
            "FEISHU_OPS_APP_SECRET": "secret_test",
        },
        "kiro_agents_dir": tmp_path / "kiro" / "agents",
        "aws_config_dir": tmp_path / "aws",
        "model_lister": lambda: ["claude-sonnet", "claude-opus"],
        "sts_runner": lambda profile, **kw: StsResult(True, "123456789012", None, "ok"),
    }


def stage_names(report):
    return [s.stage for s in report.stages]


def test_full_pipeline_passes_in_spec_order(tmp_path):
    report = run_validation_pipeline(
        VALID_YAML.format(working_dir=tmp_path), **make_env(tmp_path),
    )

    assert report.ok is True
    assert stage_names(report) == [
        "yaml_schema",
        "env_refs",
        "referential_integrity",
        "paths_timeouts",
        "kiro_agent_model",
        "aws_cli_profile",
        "sts_identity",
        "expected_account",
    ]
    assert all(s.ok for s in report.stages)


def test_schema_failure_short_circuits_external_stages(tmp_path):
    report = run_validation_pipeline("version: [", **make_env(tmp_path))

    assert report.ok is False
    assert report.stages[0].stage == "yaml_schema"
    assert report.stages[0].ok is False
    # 外部階段不執行（不浪費 STS 呼叫）
    assert "sts_identity" not in stage_names(report)


def test_missing_kiro_agent_fails_before_aws_stages(tmp_path):
    env = make_env(tmp_path)
    (tmp_path / "kiro" / "agents" / "my-dev-bot.json").unlink()

    report = run_validation_pipeline(
        VALID_YAML.format(working_dir=tmp_path), **env,
    )

    assert report.ok is False
    kiro_stage = next(s for s in report.stages if s.stage == "kiro_agent_model")
    assert kiro_stage.ok is False
    assert "my-dev-bot" in kiro_stage.detail
    assert "aws_cli_profile" not in stage_names(report)


def test_unavailable_model_fails_validation(tmp_path):
    env = make_env(tmp_path)
    env["model_lister"] = lambda: ["claude-opus"]

    report = run_validation_pipeline(
        VALID_YAML.format(working_dir=tmp_path), **env,
    )

    stage = next(s for s in report.stages if s.stage == "kiro_agent_model")
    assert stage.ok is False
    assert "claude-sonnet" in stage.detail


def test_missing_aws_cli_profile_blocks_before_sts(tmp_path):
    env = make_env(tmp_path)
    (tmp_path / "aws" / "config").write_text("[profile other]\n")
    called = []
    env["sts_runner"] = lambda *a, **kw: called.append(1)

    report = run_validation_pipeline(
        VALID_YAML.format(working_dir=tmp_path), **env,
    )

    assert report.ok is False
    stage = next(s for s in report.stages if s.stage == "aws_cli_profile")
    assert stage.ok is False
    assert called == []  # profile 不存在就不呼叫 STS


def test_sts_timeout_fails_sts_stage(tmp_path):
    env = make_env(tmp_path)
    env["sts_runner"] = lambda profile, **kw: StsResult(False, None, "timeout", "t/o")

    report = run_validation_pipeline(
        VALID_YAML.format(working_dir=tmp_path), **env,
    )

    stage = next(s for s in report.stages if s.stage == "sts_identity")
    assert stage.ok is False
    assert "expected_account" not in stage_names(report)


def test_account_mismatch_fails_final_stage(tmp_path):
    env = make_env(tmp_path)
    env["sts_runner"] = lambda profile, **kw: StsResult(True, "999999999999", None, "ok")

    report = run_validation_pipeline(
        VALID_YAML.format(working_dir=tmp_path), **env,
    )

    stage = next(s for s in report.stages if s.stage == "expected_account")
    assert stage.ok is False
    # detail 只含遮罩帳號，絕不含 Secret
    assert "********9999" in stage.detail
    assert "999999999999" not in stage.detail
