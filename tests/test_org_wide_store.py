"""Org-wide SQLite aggregation must cover GitLab (namespaced) repos too."""

from __future__ import annotations

import sqlite3

import pytest

from mira.index.store import (
    IndexStore,
    _iter_repo_dbs,
    count_vulnerabilities_org_wide_sqlite,
    search_packages_org_wide_sqlite,
)

_PKG = [{"name": "lodash", "kind": "npm", "version": "4.17.20", "file_path": "package.json"}]


@pytest.fixture
def index_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRA_INDEX_DIR", str(tmp_path))
    return tmp_path


def test_iter_finds_github_and_gitlab(index_dir):
    IndexStore.open("acme", "web").close()
    IndexStore.open("group/sub", "web", platform="gitlab").close()
    found = {(p, o, r) for p, o, r, _ in _iter_repo_dbs(str(index_dir))}
    assert ("github", "acme", "web") in found
    assert ("gitlab", "group/sub", "web") in found  # nested-group owner preserved


def test_package_search_includes_gitlab(index_dir):
    gh = IndexStore.open("acme", "web")
    gh.replace_manifest_packages("package.json", _PKG)
    gh.close()
    gl = IndexStore.open("grp", "app", platform="gitlab")
    gl.replace_manifest_packages("package.json", _PKG)
    gl.close()

    hits = search_packages_org_wide_sqlite(name="lodash")
    by_platform = {(h["platform"], h["owner"], h["repo"]) for h in hits}
    assert ("github", "acme", "web") in by_platform
    assert ("gitlab", "grp", "app") in by_platform


def _write_severities(path, severities):
    """A legacy DB needs only the column being counted; no migrations required."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE vulnerabilities (severity TEXT)")
        conn.executemany("INSERT INTO vulnerabilities VALUES (?)", [(s,) for s in severities])
        conn.commit()
    finally:
        conn.close()


@pytest.mark.parametrize("missing_directory", [False, True])
def test_vulnerability_counts_empty_index(index_dir, monkeypatch, missing_directory):
    if missing_directory:
        monkeypatch.setenv("MIRA_INDEX_DIR", str(index_dir / "absent"))
    assert count_vulnerabilities_org_wide_sqlite() == {}
    assert list(index_dir.iterdir()) == []


def test_vulnerability_counts_sum_raw_groups_across_platforms(index_dir):
    stores = {
        "acme/web.db": ["critical", "high", "unknown", "HIGH", ""],
        "_gitlab/acme/web.db": ["high", "moderate", "unknown"],
        "_gitlab/group/sub/app.db": ["low", "high", "medium"],
        "_forgejo/acme/web.db": ["low", " high "],
    }
    for name, severities in stores.items():
        _write_severities(index_dir / name, severities)
    before = {name: (index_dir / name).read_bytes() for name in stores}

    assert count_vulnerabilities_org_wide_sqlite() == {
        "critical": 1,
        "high": 3,
        "moderate": 1,
        "low": 2,
        "unknown": 2,
        "HIGH": 1,
        "": 1,
        "medium": 1,
        " high ": 1,
    }
    assert {name: (index_dir / name).read_bytes() for name in stores} == before


def test_vulnerability_counts_uri_special_characters_and_relative_index(index_dir, monkeypatch):
    _write_severities(index_dir / "org ?#%" / "repo ?#%.db", ["high"])
    monkeypatch.chdir(index_dir.parent)
    monkeypatch.setenv("MIRA_INDEX_DIR", index_dir.name)
    assert count_vulnerabilities_org_wide_sqlite() == {"high": 1}


@pytest.mark.parametrize(
    "failure", ["corrupt", "missing-table", "missing-column", "query-error", "connect-error"]
)
def test_vulnerability_counts_skip_failed_store_and_close_connections(
    index_dir, monkeypatch, failure
):
    broken = index_dir / "acme" / "broken.db"
    healthy = index_dir / "acme" / "healthy.db"
    _write_severities(healthy, ["high", "high"])
    if failure == "corrupt":
        broken.write_bytes(b"not a SQLite database")
    elif failure in {"missing-table", "missing-column"}:
        conn = sqlite3.connect(broken)
        try:
            table = "other" if failure == "missing-table" else "vulnerabilities"
            conn.execute(f"CREATE TABLE {table} (unrelated TEXT)")
            conn.commit()
        finally:
            conn.close()
    else:
        _write_severities(broken, ["critical"])
    before = {path: path.read_bytes() for path in (broken, healthy)}
    real_connect = sqlite3.connect
    opened = []

    def connect(database, *args, **kwargs):
        # Deterministic database boundary faults avoid OS permission assumptions.
        is_broken = str(database).startswith(broken.as_uri())
        if is_broken and failure == "connect-error":
            raise sqlite3.OperationalError("synthetic unavailable database")
        conn = real_connect(database, *args, **kwargs)
        opened.append(conn)
        if is_broken and failure == "query-error":
            conn.set_authorizer(lambda *_: sqlite3.SQLITE_DENY)
        return conn

    monkeypatch.setattr(sqlite3, "connect", connect)
    assert count_vulnerabilities_org_wide_sqlite() == {"high": 2}
    assert opened
    for conn in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            conn.execute("SELECT 1")
    assert {path: path.read_bytes() for path in (broken, healthy)} == before


def test_vulnerability_counts_do_not_recreate_disappearing_database(index_dir, monkeypatch):
    vanished = index_dir / "acme" / "vanished.db"
    _write_severities(vanished, ["critical"])
    real_connect = sqlite3.connect

    def connect(database, *args, **kwargs):
        vanished.unlink()
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    assert count_vulnerabilities_org_wide_sqlite() == {}
    assert not vanished.exists()
