from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .config_loader import load_config
from .external_validation import ValidationReport, run_validation_pipeline
from .health import ProfileHealthMonitor
from .models import ConfigSnapshot
from .registry import ConfigRegistry
from .revisions import RevisionStore, atomic_write, config_checksum


class PublishError(RuntimeError):
    def __init__(self, message: str, report: ValidationReport | None = None):
        super().__init__(message)
        self.report = report


@dataclass(frozen=True)
class ChangeSummary:
    hot_reloadable: tuple[str, ...]
    pending_restart: tuple[str, ...]


@dataclass(frozen=True)
class PublishResult:
    generation: int
    checksum: str
    revision_id: str
    change_summary: ChangeSummary


@dataclass(frozen=True)
class LastActionResult:
    action: str  # "load" | "publish" | "rollback"
    ok: bool
    at: str
    error: str | None
    detail: str


def classify_changes(old: ConfigSnapshot, new: ConfigSnapshot) -> ChangeSummary:
    """規格 §13.5：區分可熱載入與需要重啟的變更。"""
    hot: list[str] = []
    restart: list[str] = []

    for app_key in sorted(set(new.apps) - set(old.apps)):
        restart.append(f"app {app_key} added")
    for app_key in sorted(set(old.apps) - set(new.apps)):
        restart.append(f"app {app_key} removed")
    for app_key in sorted(set(old.apps) & set(new.apps)):
        before, after = old.apps[app_key], new.apps[app_key]
        if (before.app_id_env, before.app_secret_env) != (
            after.app_id_env, after.app_secret_env,
        ):
            restart.append(f"app {app_key} credential env changed")
        if before.enabled != after.enabled:
            restart.append(f"app {app_key} enabled changed")
        if before.default_profile != after.default_profile:
            hot.append(f"app {app_key} default_profile changed")

    for pid in sorted(set(new.profiles) | set(old.profiles)):
        if old.profiles.get(pid) != new.profiles.get(pid):
            hot.append(f"profile {pid} execution fields changed")

    if old.routes != new.routes:
        hot.append("routes changed")

    return ChangeSummary(tuple(hot), tuple(restart))


class ConfigPublisher:
    """原子發布、失敗回復與回滾（規格 §13.4、§20.1）。"""

    def __init__(
        self,
        *,
        registry: ConfigRegistry,
        revision_store: RevisionStore,
        health_monitor: ProfileHealthMonitor | None = None,
        validator: Callable[[str], ValidationReport] | None = None,
    ):
        self._registry = registry
        self._store = revision_store
        self._health = health_monitor
        self._validator = validator or (lambda text: run_validation_pipeline(text))
        self._lock = threading.Lock()
        self._last_result = LastActionResult(
            "load", True, _utcnow(), None, "initial load",
        )

    @property
    def last_result(self) -> LastActionResult:
        return self._last_result

    def publish(self, yaml_text: str, *, source: str = "publish") -> PublishResult:
        with self._lock:
            try:
                return self._publish_locked(yaml_text, source=source)
            except PublishError as exc:
                self._last_result = LastActionResult(
                    source, False, _utcnow(), str(exc), "publish failed",
                )
                raise

    def rollback(self, revision_id: str) -> PublishResult:
        """規格 §20.1：歷史內容完整重新驗證（含 STS）後發布為新 revision。"""
        try:
            yaml_text = self._store.read(revision_id)
        except KeyError:
            raise PublishError(f"unknown revision: {revision_id}") from None
        return self.publish(yaml_text, source="rollback")

    def _publish_locked(self, yaml_text: str, *, source: str) -> PublishResult:
        # 1. 伺服器端完整重新驗證（規格 §13.2：不信任瀏覽器結果）
        report = self._validator(yaml_text)
        if not report.ok:
            failed = next(s for s in report.stages if not s.ok)
            raise PublishError(
                f"validation failed at {failed.stage}: {failed.detail}", report,
            )

        config_path = self._registry.path
        old_snapshot = self._registry.snapshot()
        previous_text = config_path.read_text(encoding="utf-8")

        # 2. 原子替換主設定（暫存 → fsync → os.replace）
        atomic_write(config_path, yaml_text)

        # 3. 重建 snapshot；失敗立即恢復上一 revision 本文
        try:
            new_snapshot = self._registry.reload()
        except Exception as exc:
            atomic_write(config_path, previous_text)
            try:
                # 只驗證恢復後的檔案可建立 snapshot；不經 registry.reload()，
                # 讓執行中的 Registry 保留舊 snapshot（generation 不推進）
                load_config(
                    config_path,
                    environ=self._registry._environ,
                    generation=old_snapshot.generation,
                )
            except Exception:
                # 連恢復都失敗：保留執行中的舊 snapshot，明確回報
                raise PublishError(
                    f"snapshot build failed ({exc}); restored file but reload "
                    "still failing, runtime keeps previous snapshot"
                ) from exc
            raise PublishError(
                f"snapshot build failed ({exc}); restored previous revision"
            ) from exc

        # 4. snapshot 成功才記錄 revision、prune、更新 last-known-good
        summary = (
            f"{sum(1 for s in report.stages if s.ok)}/{len(report.stages)} stages ok"
        )
        info = self._store.save(
            yaml_text,
            generation=new_snapshot.generation,
            source=source,
            validation_summary=summary,
        )
        self._store.prune()
        self._store.update_last_known_good(yaml_text)

        if self._health is not None:
            self._health.on_config_reload(new_snapshot)

        change_summary = classify_changes(old_snapshot, new_snapshot)
        result = PublishResult(
            generation=new_snapshot.generation,
            checksum=config_checksum(yaml_text),
            revision_id=info.revision_id,
            change_summary=change_summary,
        )
        self._last_result = LastActionResult(
            source, True, _utcnow(), None,
            f"generation {result.generation}, revision {info.revision_id}",
        )
        return result


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
