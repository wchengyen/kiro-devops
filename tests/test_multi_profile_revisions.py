import json
import os

import pytest

from multi_profile.revisions import (
    RevisionStore,
    atomic_write,
    config_checksum,
    revision_dir_from_env,
)


CURRENT = "version: 1\napps: {}\nprofiles: {}\nroutes: []\n"


def test_atomic_write_replaces_content(tmp_path):
    path = tmp_path / "config.yaml"
    atomic_write(path, "first")
    atomic_write(path, "second")

    assert path.read_text(encoding="utf-8") == "second"
    # 不殘留暫存檔
    assert [p.name for p in tmp_path.iterdir()] == ["config.yaml"]


def test_atomic_write_uses_same_directory_temp_and_replace(tmp_path):
    """fsync + os.replace 必須發生，且暫存檔與目標同目錄（跨檔案系統 replace 會失敗）。"""
    path = tmp_path / "config.yaml"
    calls = []
    real_replace = os.replace

    def spy_replace(src, dst):
        calls.append((src, dst))
        return real_replace(src, dst)

    import multi_profile.revisions as revisions

    original = revisions.os.replace
    revisions.os.replace = spy_replace
    try:
        atomic_write(path, "data")
    finally:
        revisions.os.replace = original

    assert len(calls) == 1
    src, dst = calls[0]
    assert os.path.dirname(src) == str(tmp_path)
    assert dst == str(path)


def test_checksum_is_stable_sha256(tmp_path):
    assert config_checksum("abc") == config_checksum("abc")
    assert config_checksum("abc") != config_checksum("abd")
    assert len(config_checksum("abc")) == 64


def test_revision_dir_defaults_under_runtime(tmp_path):
    assert revision_dir_from_env({}, project_dir=tmp_path) == (
        tmp_path / "runtime" / "config-revisions" / "multi-profile"
    )
    custom = tmp_path / "custom"
    assert revision_dir_from_env(
        {"MULTI_PROFILE_REVISION_DIR": str(custom)}, project_dir=tmp_path,
    ) == custom


def test_save_list_read_roundtrip(tmp_path):
    store = RevisionStore(tmp_path / "revs")
    info = store.save(
        CURRENT, generation=3, source="publish", validation_summary="8/8 stages ok",
    )

    listed = store.list()
    assert [r.revision_id for r in listed] == [info.revision_id]
    assert listed[0].generation == 3
    assert listed[0].source == "publish"
    assert listed[0].checksum == config_checksum(CURRENT)
    assert store.read(info.revision_id) == CURRENT
    # revision id 嵌入 generation 與 checksum 前綴，方便人工辨識
    assert "gen3" in info.revision_id
    assert info.revision_id.endswith(config_checksum(CURRENT)[:8])


def test_prune_keeps_only_newest_20_revisions(tmp_path):
    store = RevisionStore(tmp_path / "revs")
    for generation in range(1, 26):
        store.save(
            f"# gen {generation}\n{CURRENT}", generation=generation,
            source="publish", validation_summary="ok",
        )
        store.prune(keep=20)

    listed = store.list()
    assert len(listed) == 20
    generations = {r.generation for r in listed}
    assert generations == set(range(6, 26))


def test_diff_against_current_and_against_other_revision(tmp_path):
    store = RevisionStore(tmp_path / "revs")
    old = store.save("# old\n" + CURRENT, generation=1, source="publish", validation_summary="ok")
    new = store.save("# new\n" + CURRENT, generation=2, source="publish", validation_summary="ok")

    diff = store.diff(old.revision_id, against_text="# new\n" + CURRENT)
    assert "-# old" in diff
    assert "+# new" in diff

    diff_two = store.diff(old.revision_id, against_revision=new.revision_id)
    assert diff_two == diff


def test_last_known_good_updated_atomically(tmp_path):
    store = RevisionStore(tmp_path / "revs")
    store.update_last_known_good(CURRENT)

    lkg = tmp_path / "revs" / "last-known-good.yaml"
    assert lkg.read_text(encoding="utf-8") == CURRENT


def test_unknown_revision_raises(tmp_path):
    store = RevisionStore(tmp_path / "revs")
    with pytest.raises(KeyError):
        store.read("no-such-revision")


def test_revision_metadata_contains_no_secret_values(tmp_path):
    store = RevisionStore(tmp_path / "revs")
    info = store.save(
        CURRENT, generation=1, source="publish", validation_summary="ok",
    )
    meta = json.loads((tmp_path / "revs" / f"{info.revision_id}.json").read_text())
    assert set(meta) == {
        "revision_id", "created_at", "generation", "checksum", "source",
        "validation_summary",
    }
