import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from flask import Flask

import dashboard.multi_profile_api as mp_api
from dashboard import dashboard_bp, _sessions
from dashboard.multi_profile_api import MultiProfileDeps, init_multi_profile_api
from multi_profile.external_validation import StageResult, ValidationReport
from multi_profile.operational_settings import OperationalSettings
from multi_profile.publisher import ConfigPublisher
from multi_profile.registry import ConfigRegistry
from multi_profile.revisions import RevisionStore, config_checksum
from multi_profile.sts import StsResult


BASE_YAML = """
version: 1
apps:
  ops-bot:
    app_id_env: FEISHU_OPS_APP_ID
    app_secret_env: FEISHU_OPS_APP_SECRET
    default_profile: prod-cn
profiles:
  prod-cn:
    aws_profile: production
    expected_account_id: "123456789012"
    working_dir: {working_dir}
routes: []
"""

ENVIRON = {
    "FEISHU_OPS_APP_ID": "cli_test",
    "FEISHU_OPS_APP_SECRET": "s3cret-value-must-never-leak",
}


def ok_report(text, snapshot=None):
    return ValidationReport(
        True,
        (StageResult("yaml_schema", True, "ok"), StageResult("sts_identity", True, "ok")),
        snapshot,
    )


@pytest.fixture
def stack(tmp_path):
    config_path = tmp_path / "multi_profile_config.yaml"
    config_path.write_text(BASE_YAML.format(working_dir=tmp_path), encoding="utf-8")
    registry = ConfigRegistry(config_path, environ=ENVIRON)
    registry.load_initial()
    store = RevisionStore(tmp_path / "revs")
    publisher = ConfigPublisher(
        registry=registry, revision_store=store,
        validator=lambda text: ok_report(text),
    )
    deps = MultiProfileDeps(
        mode="multi-profile",
        config_path=config_path,
        revision_dir=tmp_path / "revs",
        registry=registry,
        publisher=publisher,
        revision_store=store,
        settings=OperationalSettings(),
        validator=lambda text: ok_report(text),
    )
    init_multi_profile_api(deps)
    yield deps, config_path, store, publisher
    mp_api.reset_multi_profile_api()


@pytest.fixture
def client(stack):
    _sessions.clear()
    app = Flask(__name__)
    app.register_blueprint(dashboard_bp)
    with app.test_client() as c:
        _sessions["test-session"] = {
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        c.set_cookie("dashboard_session", "test-session")
        yield c
    _sessions.clear()


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/dashboard/multi-profile/config"),
        ("GET", "/api/dashboard/multi-profile/status"),
        ("POST", "/api/dashboard/multi-profile/validate"),
        ("POST", "/api/dashboard/multi-profile/publish"),
        ("GET", "/api/dashboard/multi-profile/revisions"),
        ("GET", "/api/dashboard/multi-profile/revisions/rev-x/diff"),
        ("POST", "/api/dashboard/multi-profile/rollback"),
    ],
)
def test_all_routes_require_auth(method, path):
    mp_api.reset_multi_profile_api()
    _sessions.clear()
    app = Flask(__name__)
    app.register_blueprint(dashboard_bp)
    with app.test_client() as c:
        resp = c.open(path, method=method, json={})
        assert resp.status_code == 401
    _sessions.clear()


def test_uninitialized_api_returns_503():
    mp_api.reset_multi_profile_api()
    _sessions.clear()
    app = Flask(__name__)
    app.register_blueprint(dashboard_bp)
    with app.test_client() as c:
        _sessions["s"] = {"created_at": datetime.now(timezone.utc).isoformat()}
        c.set_cookie("dashboard_session", "s")
        resp = c.get("/api/dashboard/multi-profile/config")
        assert resp.status_code == 503
    _sessions.clear()


def test_get_config_returns_text_snapshot_and_never_secrets(client, stack):
    resp = client.get("/api/dashboard/multi-profile/config")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["mode"] == "multi-profile"
    assert "version: 1" in body["config_text"]
    assert body["snapshot"]["apps"]["ops-bot"]["default_profile"] == "prod-cn"
    assert body["pending_restart"] == []
    # env 值絕不出現在 response（只有 env 名稱）
    raw = resp.get_data(as_text=True)
    assert "s3cret-value-must-never-leak" not in raw
    assert "FEISHU_OPS_APP_SECRET" in raw  # 名稱可見，值不可見


def test_validate_returns_pipeline_stages(client, stack):
    deps, *_ = stack
    deps.validator = lambda text: ValidationReport(
        False,
        (
            StageResult("yaml_schema", True, "ok"),
            StageResult("sts_identity", False, "prod-cn: sts timeout"),
        ),
        None,
    )

    resp = client.post(
        "/api/dashboard/multi-profile/validate", json={"yaml": "version: 1\n"},
    )

    assert resp.status_code == 200  # 驗證失敗仍是合法 response
    body = resp.get_json()
    assert body["ok"] is False
    assert [s["stage"] for s in body["stages"]] == ["yaml_schema", "sts_identity"]
    assert body["stages"][1]["ok"] is False


def test_validate_rejects_oversized_draft(client):
    resp = client.post(
        "/api/dashboard/multi-profile/validate",
        json={"yaml": "x" * (513 * 1024)},
    )
    assert resp.status_code == 413


def test_publish_reruns_validation_server_side(client, stack):
    """規格 §13.2：即使瀏覽器宣稱已驗證，伺服器仍完整重新驗證。"""
    deps, config_path, store, _ = stack
    calls = []
    original = deps.publisher._validator
    deps.publisher._validator = lambda text: (calls.append(text), original(text))[1]
    new_yaml = config_path.read_text() + "# v2\n"

    resp = client.post(
        "/api/dashboard/multi-profile/publish",
        json={"yaml": new_yaml, "prevalidated": True},
    )

    assert resp.status_code == 200
    assert calls == [new_yaml]  # 不信任 prevalidated 旗標
    body = resp.get_json()
    assert body["generation"] == 2
    assert body["checksum"] == config_checksum(new_yaml)
    assert body["change_summary"]["pending_restart"] == []


def test_publish_failure_returns_422_and_keeps_file(client, stack):
    deps, config_path, store, _ = stack
    before = config_path.read_text()
    deps.publisher._validator = lambda text: ValidationReport(
        False, (StageResult("expected_account", False, "mismatch"),), None,
    )

    resp = client.post(
        "/api/dashboard/multi-profile/publish", json={"yaml": "version: 1\n"},
    )

    assert resp.status_code == 422
    assert resp.get_json()["ok"] is False
    assert config_path.read_text() == before
    assert store.list() == []


def test_publish_pending_restart_is_surfaced_in_status(client, stack):
    deps, config_path, *_ = stack
    new_yaml = config_path.read_text().replace(
        "app_secret_env: FEISHU_OPS_APP_SECRET",
        "app_secret_env: FEISHU_OPS_APP_SECRET2",
    )
    # 新 env 名稱也要存在，否則 loader 會拒絕
    deps.registry._environ = {**ENVIRON, "FEISHU_OPS_APP_SECRET2": "x"}

    resp = client.post("/api/dashboard/multi-profile/publish", json={"yaml": new_yaml})
    assert resp.status_code == 200
    assert resp.get_json()["change_summary"]["pending_restart"] == [
        "app ops-bot credential env changed",
    ]

    status = client.get("/api/dashboard/multi-profile/status").get_json()
    assert status["pending_restart"] == ["app ops-bot credential env changed"]


def test_revisions_list_and_diff(client, stack):
    deps, config_path, store, _ = stack
    first = config_path.read_text()
    client.post(
        "/api/dashboard/multi-profile/publish",
        json={"yaml": first + "# v2\n"},
    )
    client.post(
        "/api/dashboard/multi-profile/publish",
        json={"yaml": first + "# v2\n# v3\n"},
    )

    listed = client.get("/api/dashboard/multi-profile/revisions").get_json()
    assert listed["ok"] is True
    assert len(listed["revisions"]) == 2
    newest, older = listed["revisions"]  # 最新在前
    assert newest["is_current"] is True
    assert older["is_current"] is False
    assert newest["checksum"] == config_checksum(first + "# v2\n# v3\n")

    resp = client.get(
        f"/api/dashboard/multi-profile/revisions/{older['revision_id']}/diff?against=current",
    )
    assert resp.status_code == 200
    assert "+# v3" in resp.get_json()["diff"]


def test_diff_with_path_traversal_revision_id_is_rejected(client, stack):
    resp = client.get("/api/dashboard/multi-profile/revisions/..%2F..%2F.env/diff")
    assert resp.status_code in (400, 404)
    assert "s3cret" not in resp.get_data(as_text=True)


def test_rollback_revalidates_and_publishes_new_revision(client, stack):
    deps, config_path, store, _ = stack
    first = config_path.read_text()
    second = first.replace(
        "routes: []",
        "routes:\n  - app: ops-bot\n    chat_id: oc_prod\n    profile: prod-cn\n",
    )
    client.post("/api/dashboard/multi-profile/publish", json={"yaml": second})
    # 手動保存 first 為可回滾 revision（publish 只保存新內容）
    store.save(first, generation=1, source="publish", validation_summary="ok")
    target = next(
        r.revision_id for r in store.list() if r.checksum == config_checksum(first)
    )

    calls = []
    original = deps.publisher._validator
    deps.publisher._validator = lambda text: (calls.append(text), original(text))[1]

    resp = client.post(
        "/api/dashboard/multi-profile/rollback", json={"revision_id": target},
    )

    assert resp.status_code == 200
    assert calls == [first]  # 歷史內容經完整重新驗證（含 STS）
    assert config_path.read_text() == first
    assert any(r.source == "rollback" for r in store.list())


def test_rollback_unknown_revision_returns_422(client):
    resp = client.post(
        "/api/dashboard/multi-profile/rollback", json={"revision_id": "no-such"},
    )
    assert resp.status_code == 422


def test_status_payload_matches_spec_17(client, stack):
    resp = client.get("/api/dashboard/multi-profile/status")

    body = resp.get_json()
    assert body["mode"] == "multi-profile"
    assert body["generation"] == 1
    assert len(body["checksum"]) == 64
    assert "settings" in body and body["settings"]["sts_timeout_sec"] == 10
    assert body["tasks"] == {"total": 0, "by_profile": {}}
    assert "last_load" in body and "last_publish" in body and "last_rollback" in body


def test_bootstrap_publish_in_legacy_mode(tmp_path):
    """legacy 模式、設定檔不存在：publish 走 bootstrap，不觸碰 runtime（§19.3）。"""
    config_path = tmp_path / "multi_profile_config.yaml"
    deps = MultiProfileDeps(
        mode="legacy",
        config_path=config_path,
        revision_dir=tmp_path / "revs",
        settings=OperationalSettings(),
        environ=ENVIRON,
        validator=lambda text: ok_report(text),
    )
    init_multi_profile_api(deps)
    try:
        _sessions.clear()
        app = Flask(__name__)
        app.register_blueprint(dashboard_bp)
        with app.test_client() as c:
            _sessions["s"] = {"created_at": datetime.now(timezone.utc).isoformat()}
            c.set_cookie("dashboard_session", "s")
            resp = c.post(
                "/api/dashboard/multi-profile/publish",
                json={"yaml": BASE_YAML.format(working_dir=tmp_path)},
            )
        _sessions.clear()

        assert resp.status_code == 200
        assert resp.get_json()["generation"] == 1
        assert config_path.is_file()
        assert deps.registry is not None and deps.registry.snapshot().generation == 1
        store = RevisionStore(tmp_path / "revs")
        assert store.list()[0].source == "bootstrap"
        assert (tmp_path / "revs" / "last-known-good.yaml").is_file()
    finally:
        mp_api.reset_multi_profile_api()
