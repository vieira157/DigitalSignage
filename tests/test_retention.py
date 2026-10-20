"""Retention tests use a synthetic SQLite database and temporary directories."""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def retention():
    path = ROOT / "deploy/linux/retention_server.py"
    spec = importlib.util.spec_from_file_location("retention_test_module", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def state(tmp_path):
    media = tmp_path / "media"; releases = tmp_path / "releases"; temp = tmp_path / "upload-tmp"
    for path in (media / "uploads", releases, temp):
        path.mkdir(parents=True)
    files = {
        "active": media / "uploads/active.png",
        "archived_free": media / "uploads/archived-free.png",
        "archived_draft": media / "uploads/archived-draft.png",
        "archived_published": media / "uploads/archived-published.png",
        "partial": temp / "abandoned.part",
    }
    for path in files.values():
        path.write_bytes(b"synthetic")
    old = 1_000_000_000 - 100 * 86400
    os.utime(files["partial"], (old, old))
    db = tmp_path / "db.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
        CREATE TABLE contents(id INTEGER PRIMARY KEY, filename TEXT, pages_json TEXT, archived_at REAL);
        CREATE TABLE playlist_items(content_id INTEGER);
        CREATE TABLE tv_contents(content_id INTEGER);
        CREATE TABLE playlist_publications(snapshot_json TEXT);
        CREATE TABLE playlist_publication_history(snapshot_json TEXT);
        CREATE TABLE tv_monitor_settings(id INTEGER, recommended_player_version TEXT);
        CREATE TABLE player_releases(id INTEGER PRIMARY KEY, version TEXT, status TEXT);
        CREATE TABLE player_update_jobs(release_id INTEGER);
        CREATE TABLE audit_log(created_at REAL,user_id INTEGER,username TEXT,action TEXT,target_type TEXT,target_id TEXT,detail TEXT,ip_address TEXT);
        """)
        conn.executemany(
            "INSERT INTO contents VALUES(?,?,?,?)",
            [
                (1, "uploads/active.png", "[]", None),
                (2, "uploads/archived-free.png", "[]", old),
                (3, "uploads/archived-draft.png", "[]", old),
                (4, "uploads/archived-published.png", "[]", old),
            ],
        )
        conn.execute("INSERT INTO playlist_items VALUES(3)")
        conn.execute("INSERT INTO playlist_publications VALUES(?)", (json.dumps({"items": [{"content_id": 4}]}),))
        conn.execute("INSERT INTO playlist_publication_history VALUES(?)", (json.dumps({"items": [{"content_id": 4}]}),))
        conn.execute("INSERT INTO tv_monitor_settings VALUES(1,'stable')")
        conn.executemany("INSERT INTO player_releases VALUES(?,?,?)", [(1, "stable", "stable"), (2, "old", "published")])
        conn.execute("INSERT INTO player_update_jobs VALUES(1)")
    return db, media, releases, temp, files


def test_retention_defaults_to_dry_run_and_classifies(retention, state):
    db, media, releases, temp, files = state
    report = retention.run_retention(
        db, media, releases, temp, now=1_000_000_000,
        temp_days=7, archived_days=90, dry_run=True, delete_archived=False,
    )
    assert report["temp_candidates"] == 1 and report["temp_deleted"] == 0
    assert report["archived_candidates"] == 1 and report["archived_deleted"] == 0
    assert report["active"] == 1 and report["unreferenced"] == 1
    assert report["releases_recommended_or_in_use"] == 1
    assert report["releases_historical"] == 1
    assert all(path.exists() for path in files.values())
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='retention.run'").fetchone()[0] == 1


def test_retention_deletes_only_explicit_safe_candidates(retention, state):
    db, media, releases, temp, files = state
    report = retention.run_retention(
        db, media, releases, temp, now=1_000_000_000,
        temp_days=7, archived_days=90, dry_run=False, delete_archived=True,
    )
    assert report["temp_deleted"] == 1
    assert report["archived_deleted"] == 1
    assert not files["partial"].exists()
    assert not files["archived_free"].exists()
    assert files["active"].exists()
    assert files["archived_draft"].exists()
    assert files["archived_published"].exists()


def test_archived_deletion_flag_does_not_override_dry_run(retention, state):
    db, media, releases, temp, files = state
    retention.run_retention(
        db, media, releases, temp, now=1_000_000_000,
        temp_days=7, archived_days=90, dry_run=True, delete_archived=True,
    )
    assert files["archived_free"].exists()


def test_unsafe_media_path_is_rejected_before_deletion(retention, state):
    db, media, releases, temp, _ = state
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE contents SET filename='../outside' WHERE id=2")
    with pytest.raises(ValueError, match="fora da raiz"):
        retention.run_retention(
            db, media, releases, temp, now=1_000_000_000,
            temp_days=7, archived_days=90, dry_run=False, delete_archived=True,
        )
