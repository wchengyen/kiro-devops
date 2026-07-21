#!/usr/bin/env python3
"""Multi Profile Dashboard API（全部 require_auth；規格 §13.2、§17、§20.1）。

執行期依賴由 gateway 以 init_multi_profile_api() 注入；
本模組不保存、不回傳任何 Secret 值。
"""

from __future__ import annotations

import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping

from flask import jsonify, request

from dashboard import dashboard_bp, require_auth

# 注意：本模組在 import 時「不」匯入 multi_profile。匯入鏈
# multi_profile/__init__ → group_alerts → alert_analysis → alert_matcher →
# dashboard.config_store → 本模組 會在 multi_profile package 初始化完成前
# 回到這裡（反向 alert_matcher → dashboard → 本模組 → multi_profile 亦同），
# 因此所有 multi_profile 參照都延後到首次使用時才匯入（TYPE_CHECKING 除外）。
if TYPE_CHECKING:
    from multi_profile.external_validation import ValidationReport
    from multi_profile.models import ConfigSnapshot
    from multi_profile.operational_settings import OperationalSettings
    from multi_profile.publisher import ConfigPublisher, LastActionResult
    from multi_profile.registry import ConfigRegistry
    from multi_profile.revisions import RevisionStore

MAX_DRAFT_BYTES = 512 * 1024


@dataclass
class MultiProfileDeps:
    mode: str  # "multi-profile" | "legacy"
    config_path: Path
    revision_dir: Path
    registry: ConfigRegistry | None = None
    publisher: ConfigPublisher | None = None
    revision_store: RevisionStore | None = None
    health_monitor: Any = None
    app_manager: Any = None
    task_registry: Any = None
    settings: OperationalSettings | None = None
    environ: Mapping[str, str] | None = None
    validator: Callable[[str], Any] | None = None
    last_load: LastActionResult | None = None

    def __post_init__(self):
        if self.settings is None:
            from multi_profile.operational_settings import OperationalSettings

            self.settings = OperationalSettings()
        if self.validator is None:
            from multi_profile.external_validation import run_validation_pipeline

            self.validator = run_validation_pipeline


_deps: MultiProfileDeps | None = None
_lock = threading.Lock()
_pending_restart: tuple[str, ...] = ()
_last_publish: LastActionResult | None = None
_last_rollback: LastActionResult | None = None


def init_multi_profile_api(deps: MultiProfileDeps) -> None:
    global _deps, _pending_restart, _last_publish, _last_rollback
    with _lock:
        _deps = deps
        _pending_restart = ()
        _last_publish = None
        _last_rollback = None


def reset_multi_profile_api() -> None:
    """測試用：清除注入的依賴與模組狀態。"""
    global _deps, _pending_restart, _last_publish, _last_rollback
    with _lock:
        _deps = None
        _pending_restart = ()
        _last_publish = None
        _last_rollback = None


def _require_deps() -> MultiProfileDeps | None:
    return _deps


def _unavailable():
    return jsonify({"ok": False, "error": "multi-profile api is not initialized"}), 503


def _read_draft() -> tuple[str | None, Any]:
    payload = request.get_json(silent=True) or {}
    yaml_text = payload.get("yaml")
    if not isinstance(yaml_text, str) or not yaml_text.strip():
        return None, (jsonify({"ok": False, "error": "yaml is required"}), 400)
    if len(yaml_text.encode("utf-8")) > MAX_DRAFT_BYTES:
        return None, (jsonify({"ok": False, "error": "draft too large"}), 413)
    return yaml_text, None


def _snapshot_dict(snapshot: ConfigSnapshot) -> dict:
    return {
        "version": snapshot.version,
        "generation": snapshot.generation,
        "apps": {key: asdict(app) for key, app in snapshot.apps.items()},
        "profiles": {
            key: asdict(profile) for key, profile in snapshot.profiles.items()
        },
        "routes": [asdict(route) for route in snapshot.routes],
    }


def _status_payload(deps: MultiProfileDeps) -> dict:
    from multi_profile.status import build_multi_profile_status

    config_text = None
    if deps.config_path.is_file():
        config_text = deps.config_path.read_text(encoding="utf-8")
    status = build_multi_profile_status(
        mode=deps.mode,
        registry=deps.registry,
        config_text=config_text,
        health_monitor=deps.health_monitor,
        app_manager=deps.app_manager,
        task_registry=deps.task_registry,
        settings=deps.settings,
        last_load=deps.last_load,
        last_publish=_last_publish,
        last_rollback=_last_rollback,
    )
    status["pending_restart"] = list(_pending_restart)
    return status


def _record_result(source: str, result: LastActionResult) -> None:
    global _last_publish, _last_rollback
    if source == "rollback":
        _last_rollback = result
    else:
        _last_publish = result


def _bootstrap(deps: MultiProfileDeps, yaml_text: str) -> dict:
    """legacy 模式首次發布（規格 §19.3）：建立離線 registry，不切換 runtime。"""
    from multi_profile.publisher import ConfigPublisher, PublishError
    from multi_profile.registry import ConfigRegistry
    from multi_profile.revisions import (
        RevisionStore,
        atomic_write,
        config_checksum,
    )

    report = deps.validator(yaml_text)
    if not report.ok:
        failed = next(s for s in report.stages if not s.ok)
        raise PublishError(
            f"validation failed at {failed.stage}: {failed.detail}", report,
        )
    atomic_write(deps.config_path, yaml_text)
    registry = ConfigRegistry(
        deps.config_path,
        environ=deps.environ if deps.environ is not None else os.environ,
    )
    snapshot = registry.load_initial()
    store = RevisionStore(deps.revision_dir)
    summary = f"{sum(1 for s in report.stages if s.ok)}/{len(report.stages)} stages ok"
    info = store.save(
        yaml_text, generation=snapshot.generation,
        source="bootstrap", validation_summary=summary,
    )
    store.prune(deps.settings.revision_keep)
    store.update_last_known_good(yaml_text)
    publisher = ConfigPublisher(
        registry=registry,
        revision_store=store,
        health_monitor=deps.health_monitor,
        validator=deps.validator,
    )
    deps.registry = registry
    deps.revision_store = store
    deps.publisher = publisher
    return {
        "generation": snapshot.generation,
        "checksum": config_checksum(yaml_text),
        "revision_id": info.revision_id,
        "change_summary": {"hot_reloadable": [], "pending_restart": []},
    }


@dashboard_bp.route("/api/dashboard/multi-profile/config", methods=["GET"])
@require_auth
def get_multi_profile_config():
    deps = _require_deps()
    if deps is None:
        return _unavailable()
    body = {
        "ok": True,
        "mode": deps.mode,
        "config_path": str(deps.config_path),
        "exists": deps.config_path.is_file(),
        "config_text": None,
        "snapshot": None,
        "pending_restart": list(_pending_restart),
    }
    if deps.config_path.is_file():
        body["config_text"] = deps.config_path.read_text(encoding="utf-8")
    if deps.registry is not None:
        try:
            body["snapshot"] = _snapshot_dict(deps.registry.snapshot())
        except RuntimeError:
            pass
    return jsonify(body)


@dashboard_bp.route("/api/dashboard/multi-profile/status", methods=["GET"])
@require_auth
def get_multi_profile_status():
    deps = _require_deps()
    if deps is None:
        return _unavailable()
    payload = _status_payload(deps)
    payload["ok"] = True
    return jsonify(payload)


@dashboard_bp.route("/api/dashboard/multi-profile/validate", methods=["POST"])
@require_auth
def validate_multi_profile_draft():
    deps = _require_deps()
    if deps is None:
        return _unavailable()
    yaml_text, error = _read_draft()
    if error:
        return error
    report = deps.validator(yaml_text)
    return jsonify({
        "ok": report.ok,
        "stages": [asdict(stage) for stage in report.stages],
    })


@dashboard_bp.route("/api/dashboard/multi-profile/publish", methods=["POST"])
@require_auth
def publish_multi_profile_draft():
    from multi_profile.publisher import PublishError

    global _pending_restart
    deps = _require_deps()
    if deps is None:
        return _unavailable()
    yaml_text, error = _read_draft()
    if error:
        return error
    try:
        if deps.publisher is None:
            result = _bootstrap(deps, yaml_text)
        else:
            published = deps.publisher.publish(yaml_text)
            _pending_restart = published.change_summary.pending_restart
            result = {
                "generation": published.generation,
                "checksum": published.checksum,
                "revision_id": published.revision_id,
                "change_summary": {
                    "hot_reloadable": list(published.change_summary.hot_reloadable),
                    "pending_restart": list(published.change_summary.pending_restart),
                },
            }
        _record_result("publish", deps.publisher.last_result)
    except PublishError as exc:
        body = {"ok": False, "error": str(exc)}
        if exc.report is not None:
            body["stages"] = [asdict(stage) for stage in exc.report.stages]
        return jsonify(body), 422
    result["ok"] = True
    return jsonify(result)


@dashboard_bp.route("/api/dashboard/multi-profile/revisions", methods=["GET"])
@require_auth
def list_multi_profile_revisions():
    from multi_profile.revisions import RevisionStore, config_checksum

    deps = _require_deps()
    if deps is None:
        return _unavailable()
    store = deps.revision_store or RevisionStore(deps.revision_dir)
    current = None
    if deps.config_path.is_file():
        current = config_checksum(deps.config_path.read_text(encoding="utf-8"))
    revisions = []
    for info in reversed(store.list()):
        item = asdict(info)
        item["is_current"] = info.checksum == current
        revisions.append(item)
    return jsonify({"ok": True, "revisions": revisions})


@dashboard_bp.route(
    "/api/dashboard/multi-profile/revisions/<revision_id>/diff", methods=["GET"],
)
@require_auth
def diff_multi_profile_revision(revision_id):
    from multi_profile.revisions import RevisionStore

    deps = _require_deps()
    if deps is None:
        return _unavailable()
    store = deps.revision_store or RevisionStore(deps.revision_dir)
    against = request.args.get("against", "current")
    try:
        if against == "current":
            current_text = (
                deps.config_path.read_text(encoding="utf-8")
                if deps.config_path.is_file() else ""
            )
            diff = store.diff(revision_id, against_text=current_text)
        else:
            diff = store.diff(revision_id, against_revision=against)
    except (KeyError, ValueError):
        return jsonify({"ok": False, "error": "unknown revision"}), 404
    return jsonify({"ok": True, "diff": diff})


@dashboard_bp.route("/api/dashboard/multi-profile/rollback", methods=["POST"])
@require_auth
def rollback_multi_profile():
    from multi_profile.publisher import PublishError

    global _pending_restart
    deps = _require_deps()
    if deps is None or deps.publisher is None:
        return _unavailable()
    payload = request.get_json(silent=True) or {}
    revision_id = payload.get("revision_id", "")
    try:
        published = deps.publisher.rollback(revision_id)
    except PublishError as exc:
        body = {"ok": False, "error": str(exc)}
        if exc.report is not None:
            body["stages"] = [asdict(stage) for stage in exc.report.stages]
        return jsonify(body), 422
    _pending_restart = published.change_summary.pending_restart
    _record_result("rollback", deps.publisher.last_result)
    return jsonify({
        "ok": True,
        "generation": published.generation,
        "checksum": published.checksum,
        "revision_id": published.revision_id,
        "change_summary": {
            "hot_reloadable": list(published.change_summary.hot_reloadable),
            "pending_restart": list(published.change_summary.pending_restart),
        },
    })
