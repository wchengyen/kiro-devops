from __future__ import annotations

import difflib
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


LAST_KNOWN_GOOD = "last-known-good.yaml"


@dataclass(frozen=True)
class RevisionInfo:
    revision_id: str
    created_at: str
    generation: int
    checksum: str
    source: str  # "publish" | "rollback" | "bootstrap"
    validation_summary: str


def config_checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def revision_dir_from_env(
    environ: Mapping[str, str], *, project_dir: str | Path,
) -> Path:
    configured = environ.get("MULTI_PROFILE_REVISION_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(project_dir) / "runtime" / "config-revisions" / "multi-profile"


def atomic_write(path: str | Path, text: str) -> None:
    """同目錄暫存檔 → flush + fsync → os.replace → fsync 目錄（規格 §13.4）。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, str(target))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    dir_fd = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


class RevisionStore:
    """保存非敏感設定 revision：YAML 本文 + JSON metadata（規格 §16、§20.1）。"""

    def __init__(self, revision_dir: str | Path):
        self._dir = Path(revision_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def directory(self) -> Path:
        return self._dir

    def save(
        self,
        yaml_text: str,
        *,
        generation: int,
        source: str,
        validation_summary: str,
    ) -> RevisionInfo:
        checksum = config_checksum(yaml_text)
        created = datetime.now(timezone.utc)
        revision_id = (
            f"{created.strftime('%Y%m%dT%H%M%SZ')}-gen{generation}-{checksum[:8]}"
        )
        info = RevisionInfo(
            revision_id=revision_id,
            created_at=created.isoformat(),
            generation=generation,
            checksum=checksum,
            source=source,
            validation_summary=validation_summary,
        )
        atomic_write(self._dir / f"{revision_id}.yaml", yaml_text)
        atomic_write(
            self._dir / f"{revision_id}.json",
            json.dumps(info.__dict__, ensure_ascii=False, indent=2),
        )
        return info

    def list(self) -> list[RevisionInfo]:
        infos = []
        for meta_path in sorted(self._dir.glob("*.json")):
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                infos.append(RevisionInfo(**data))
            except (ValueError, TypeError, KeyError):
                continue
        # 以 created_at（ISO、含微秒）排序：revision id 的秒級時間戳在
        # 同秒多筆時無法保證 generation 順序
        infos.sort(key=lambda i: (i.created_at, i.generation))
        return infos

    def read(self, revision_id: str) -> str:
        path = self._dir / f"{self._guard(revision_id)}.yaml"
        if not path.is_file():
            raise KeyError(f"unknown revision: {revision_id}")
        return path.read_text(encoding="utf-8")

    def diff(
        self,
        revision_id: str,
        *,
        against_text: str | None = None,
        against_revision: str | None = None,
    ) -> str:
        old = self.read(revision_id)
        if against_revision is not None:
            new = self.read(against_revision)
        elif against_text is not None:
            new = against_text
        else:
            raise ValueError("diff requires against_text or against_revision")
        return "".join(
            difflib.unified_diff(
                old.splitlines(keepends=True),
                new.splitlines(keepends=True),
                fromfile=f"revision:{revision_id}",
                # 固定 tofile 標籤：對同一內容，against_text 與 against_revision
                # 兩種呼叫必須產生完全一致的 diff（含 header）
                tofile="current",
            )
        )

    def update_last_known_good(self, yaml_text: str) -> None:
        atomic_write(self._dir / LAST_KNOWN_GOOD, yaml_text)

    def prune(self, keep: int = 20) -> None:
        infos = self.list()
        for stale in infos[:-keep] if len(infos) > keep else []:
            for suffix in (".yaml", ".json"):
                try:
                    (self._dir / f"{stale.revision_id}{suffix}").unlink()
                except OSError:
                    pass

    @staticmethod
    def _guard(revision_id: str) -> str:
        # 防止路徑穿越；revision id 只允許安全字元
        if not revision_id or any(c in revision_id for c in "/\\.. "):
            raise KeyError(f"invalid revision id: {revision_id!r}")
        return revision_id
