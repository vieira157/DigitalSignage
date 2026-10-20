"""All data are synthetic; never import app against an operational database."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

ROOT = Path(__file__).resolve().parents[1]


def login(client, ds, username="testadmin", password="synthetic-password-123", headers=None):
    client.get("/login")
    data = {
        "username": username,
        "password": password,
        "csrf_token": client.cookies.get(ds.LOGIN_CSRF_COOKIE_NAME),
    }
    return client.post("/login", data=data, headers=headers or {}, follow_redirects=False)


def csrf_headers(client, ds, headers=None):
    result = dict(headers or {})
    result["X-CSRF-Token"] = ds.csrf_token_for_session(
        client.cookies.get(ds.SESSION_COOKIE_NAME)
    )
    return result


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def ds(tmp_path, monkeypatch):
    code = tmp_path / "code"
    code.mkdir()
    shutil.copy2(ROOT / "app.py", code / "app.py")
    shutil.copytree(ROOT / "templates", code / "templates")
    monkeypatch.syspath_prepend(str(ROOT))
    data = tmp_path / "data"
    for key, value in {
        "DS_DB_FILE": str(data / "db.sqlite"),
        "DS_MEDIA_DIR": str(data / "nested/media"),
        "DS_RELEASE_DIR": str(data / "nested/releases"),
        "DS_INITIAL_CREDENTIALS_FILE": str(data / "credentials.txt"),
        "DS_INITIAL_ADMIN_USER": "testadmin",
        "DS_INITIAL_ADMIN_PASSWORD": "synthetic-password-123",
        "DS_COOKIE_SECURE": "0",
    }.items():
        monkeypatch.setenv(key, value)
    return load(code / "app.py", "linux_test_app")


def test_health_readonly_public(ds, monkeypatch):
    original = ds.DB_FILE.read_bytes()
    monkeypatch.setattr(ds, "get_db", lambda: pytest.fail("health used writable DB connection"))
    result = TestClient(ds.app).get("/health")
    assert result.status_code == 200
    assert result.json()["database"] == "ok"
    assert result.json()["app_version"] == ds.APP_VERSION
    assert "timestamp" in result.json()
    assert str(ds.DB_FILE) not in result.text
    assert "testadmin" not in result.text
    assert ds.DB_FILE.read_bytes() == original
    assert not ds.CREDENTIALS_FILE.exists()


@pytest.mark.parametrize("failure", ["missing_db", "corrupt_db", "missing_media", "missing_releases", "permissions"])
def test_health_unavailable(ds, monkeypatch, failure):
    if failure == "missing_db":
        monkeypatch.setattr(ds, "DB_FILE", ds.DB_FILE.parent / "absent.db")
    elif failure == "corrupt_db":
        ds.DB_FILE.write_bytes(b"not sqlite")
    elif failure == "missing_media":
        shutil.rmtree(ds.MEDIA_DIR)
    elif failure == "missing_releases":
        ds.RELEASE_DIR.rmdir()
    else:
        monkeypatch.setattr(ds.os, "access", lambda *args: False)
    result = TestClient(ds.app).get("/health")
    assert result.status_code == 503
    assert result.json()["status"] == "unavailable"
    if failure == "missing_db":
        assert not ds.DB_FILE.exists()


def test_existing_admin_preserved(ds, monkeypatch):
    conn = ds.get_db()
    before = [tuple(r) for r in conn.execute("SELECT * FROM users")]
    monkeypatch.setenv("DS_INITIAL_ADMIN_PASSWORD", "different-password")
    ds.ensure_initial_admin(conn)
    assert [tuple(r) for r in conn.execute("SELECT * FROM users")] == before
    conn.close()
    assert not ds.CREDENTIALS_FILE.exists()


def test_generated_credentials_exclusive(ds, monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE users(username,full_name,password_hash,role,is_active,must_change_password,created_at)")
    monkeypatch.delenv("DS_INITIAL_ADMIN_PASSWORD")
    ds.ensure_initial_admin(conn)
    original = ds.CREDENTIALS_FILE.read_bytes()
    assert b"Senha:" in original
    if os.name == "posix":
        assert ds.CREDENTIALS_FILE.stat().st_mode & 0o777 == 0o600
    conn.execute("DELETE FROM users")
    with pytest.raises(FileExistsError):
        ds.ensure_initial_admin(conn)
    assert ds.CREDENTIALS_FILE.read_bytes() == original
    monkeypatch.setattr(ds, "CREDENTIALS_FILE", ds.BASE_DIR / "secret.txt")
    with pytest.raises(RuntimeError, match="fora do código"):
        ds.ensure_initial_admin(conn)
    monkeypatch.setattr(ds, "DB_WAS_NEW", False)
    with pytest.raises(RuntimeError, match="Banco existente"):
        ds.ensure_initial_admin(conn)
    conn.close()


def test_http_proxy_cookie_and_audit(ds):
    proxied = ProxyHeadersMiddleware(ds.app, trusted_hosts=["127.0.0.1"])
    client = TestClient(proxied, client=("127.0.0.1", 12345))
    response = login(client, ds, headers={"x-forwarded-for": "198.51.100.7", "x-forwarded-proto": "http"})
    assert response.status_code == 303
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "secure" not in cookie
    conn = ds.get_db()
    assert conn.execute("SELECT ip_address FROM audit_log WHERE action='auth.login' ORDER BY id DESC").fetchone()[0] == "198.51.100.7"
    conn.close()
    untrusted = TestClient(proxied, client=("198.51.100.9", 12345))
    response = login(untrusted, ds, headers={"x-forwarded-for": "198.51.100.7", "x-forwarded-proto": "https"})
    assert "secure" not in response.headers["set-cookie"].lower()
    conn = ds.get_db()
    assert conn.execute("SELECT ip_address FROM audit_log WHERE action='auth.login' ORDER BY id DESC").fetchone()[0] == "198.51.100.9"
    conn.close()
    assert client.post("/logout", headers=csrf_headers(client, ds), follow_redirects=False).status_code == 303


def test_release_upload_duplicate_preserves_original(ds):
    client = TestClient(ds.app)
    login(client, ds)
    packages = []
    for marker in (b"first synthetic package", b"duplicate package"):
        stream = BytesIO()
        with __import__("zipfile").ZipFile(stream, "w") as archive:
            archive.writestr("marker.txt", marker)
        packages.append(stream.getvalue())
    for payload in packages:
        response = client.post("/releases", data={"version": "test-1"},
                               files={"package": ("release.zip", payload, "application/zip")},
                               headers=csrf_headers(client, ds),
                               follow_redirects=False)
        assert response.status_code == 303
    files = list(ds.RELEASE_DIR.glob("*.zip"))
    assert len(files) == 1
    assert files[0].read_bytes() == packages[0]
    (ds.UPLOAD_DIR / "sample.txt").write_text("synthetic media")
    conn = ds.get_db()
    owner = conn.execute("SELECT id FROM users WHERE username='testadmin'").fetchone()[0]
    conn.execute(
        "INSERT INTO contents(name,filename,content_type,created_at,owner_user_id) VALUES(?,?,?,?,?)",
        ("sample", "uploads/sample.txt", "image", 1, owner),
    )
    conn.commit(); conn.close()
    assert client.get("/media/uploads/sample.txt").text == "synthetic media"


@pytest.fixture
def ops():
    return load(ROOT / "deploy/linux/server_ops.py", "test_server_ops")


@pytest.fixture
def synthetic(tmp_path):
    data = tmp_path / "data"
    (data / "media").mkdir(parents=True)
    (data / "releases").mkdir()
    (data / "media/test.txt").write_text("media")
    (data / "releases/release.zip").write_bytes(b"synthetic")
    with sqlite3.connect(data / "digital_signage.db") as conn:
        conn.execute("CREATE TABLE users(id INTEGER)")
        conn.execute("INSERT INTO users VALUES(1)")
    conn.close()
    env = tmp_path / "server.env"
    shutil.copyfile(ROOT / "deploy/linux/digital-signage.env.example", env)
    return data, env, tmp_path / "backups"


def test_snapshot_integrity_and_tamper(ops, synthetic):
    data, env, backups = synthetic
    result = ops.snapshot(data, env, backups, {"commit": "test", "version": "test"})
    manifest = ops.verify_snapshot(result)
    assert manifest["file_count"] == 4
    assert (result / "server.env").read_bytes() == env.read_bytes()
    (result / "media/test.txt").write_text("tampered")
    with pytest.raises(ValueError, match="Checksum"):
        ops.verify_snapshot(result)


def test_sqlite_backup_live_wal(ops, tmp_path):
    source, target = tmp_path / "source.db", tmp_path / "copy.db"
    conn = sqlite3.connect(source)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t(value)")
    conn.execute("INSERT INTO t VALUES(42)")
    conn.commit()
    ops.sqlite_backup(source, target)
    with sqlite3.connect(target) as copied:
        assert copied.execute("SELECT value FROM t").fetchone()[0] == 42
    conn.close()
    with pytest.raises(sqlite3.OperationalError):
        ops.sqlite_backup(tmp_path / "absent.db", tmp_path / "never.db")
    assert not (tmp_path / "absent.db").exists()


def test_backup_failure_not_promoted_and_retention_scoped(ops, synthetic, monkeypatch):
    data, env, backups = synthetic
    first = ops.snapshot(data, env, backups, {})
    second = ops.snapshot(data, env, backups, {})
    unrelated = backups / "unrelated"
    unrelated.mkdir()
    (unrelated / "keep").write_text("keep")
    ops.retain(backups, 1, protected=(second,))
    assert second.is_dir() and (unrelated / "keep").exists()
    monkeypatch.setattr(ops, "sqlite_backup", lambda *args: (_ for _ in ()).throw(ValueError("failed")))
    before = set(backups.glob("backup-*"))
    with pytest.raises(ValueError):
        ops.snapshot(data, env, backups, {})
    assert set(backups.glob("backup-*")) == before


def test_environment_validation(ops, tmp_path):
    example = ROOT / "deploy/linux/digital-signage.env.example"
    values = ops.validate_env(example)
    assert values["DS_COOKIE_SECURE"] == "0"
    assert values["DS_REQUIRE_DEVICE_TOKEN"] == "1"
    assert values["DS_CLEANUP_DRY_RUN"] == "1"
    assert values["DS_CLEANUP_DELETE_ARCHIVED"] == "0"
    bad = tmp_path / "bad.env"
    bad.write_text("DS_COOKIE_SECURE=0\n", newline="\n")
    with pytest.raises(ValueError):
        ops.validate_env(bad)
    bad.write_text("DS_COOKIE_SECURE=0\nDS_COOKIE_SECURE=1\n", newline="\n")
    with pytest.raises(ValueError):
        ops.read_env(bad)


def test_restore_rolls_back_on_health_failure(ops, synthetic, monkeypatch):
    data, env, backups = synthetic
    source = ops.snapshot(data, env, backups, {"commit": "test"})
    (data / "media/test.txt").write_text("current state")
    for name, value in (("DATA", data), ("ENV", env), ("BACKUPS", backups)):
        monkeypatch.setattr(ops, name, value)
    monkeypatch.setattr(ops, "validate_env", lambda *args: {})
    monkeypatch.setattr(ops, "permissions", lambda *args: None)
    monkeypatch.setattr(ops.os, "chown", lambda *args: None, raising=False)
    monkeypatch.setattr(ops, "output", lambda *args: "loaded" if args[0] == "systemctl" else "test")
    monkeypatch.setattr(ops, "active", lambda: True)
    monkeypatch.setattr(ops, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(ops, "live_snapshot", lambda: ops.snapshot(data, env, backups, {"commit": "test"}))
    def health(**kwargs):
        if (data / "media/test.txt").read_text() == "media":
            raise RuntimeError("synthetic failure")
    monkeypatch.setattr(ops, "wait_health", health)
    with pytest.raises(RuntimeError, match="synthetic"):
        ops.restore(source, True)
    assert (data / "media/test.txt").read_text() == "current state"


def test_service_and_scripts_contract():
    unit = (ROOT / "deploy/linux/digital-signage.service").read_text()
    assert "--workers 1" in unit and "--forwarded-allow-ips=127.0.0.1" in unit
    assert "--reload" not in unit and "ReadWritePaths=/var/lib/digital-signage" in unit
    assert "User=digital-signage" in unit
    for script in (ROOT / "deploy/linux").glob("*.sh"):
        assert b"\r" not in script.read_bytes()
    nginx = (ROOT / "deploy/linux/nginx-digital-signage.conf.example").read_text()
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in nginx
    assert "client_max_body_size 2g;" in nginx
    assert "listen 443" not in nginx
    backup_service = (ROOT / "deploy/linux/digital-signage-backup.service").read_text()
    backup_timer = (ROOT / "deploy/linux/digital-signage-backup.timer").read_text()
    assert "Type=oneshot" in backup_service and "backup-server.sh --keep 14" in backup_service
    assert "Persistent=true" in backup_timer and "RandomizedDelaySec=" in backup_timer
    retention_service = (ROOT / "deploy/linux/digital-signage-retention.service").read_text()
    retention_timer = (ROOT / "deploy/linux/digital-signage-retention.timer").read_text()
    assert "retention_server.py" in retention_service and "ReadWritePaths=/var/lib/digital-signage" in retention_service
    assert "Persistent=true" in retention_timer and "RandomizedDelaySec=" in retention_timer
    all_linux = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "deploy/linux").iterdir() if path.is_file())
    assert "libreoffice" not in all_linux.lower()
    assert "fitz" not in all_linux


def test_preflight_reports_blockers_without_starting_app(ops, synthetic, monkeypatch, capsys):
    data, env, backups = synthetic
    backups.mkdir()
    for name, value in (("DATA", data), ("ENV", env), ("BACKUPS", backups), ("APP", ROOT), ("VENV", ROOT / ".venv")):
        monkeypatch.setattr(ops, name, value)
    monkeypatch.setitem(sys.modules, "pwd", SimpleNamespace(getpwnam=lambda name: object()))
    monkeypatch.setitem(sys.modules, "grp", SimpleNamespace(getgrnam=lambda name: object()))
    monkeypatch.setattr(ops, "protected_code", lambda *args: None)
    monkeypatch.setattr(ops, "validate_env", lambda *args: (_ for _ in ()).throw(ValueError("missing")))
    monkeypatch.setattr(ops, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(ops.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1))
    monkeypatch.setattr(ops.shutil, "which", lambda name: None)
    monkeypatch.setattr(ops.socket, "getaddrinfo", lambda *args: [])
    monkeypatch.setattr(ops, "active", lambda: False)
    monkeypatch.setattr(ops, "wait_health", lambda **kwargs: None)
    with pytest.raises(RuntimeError, match="Preflight reprovado"):
        ops.preflight("online")
    assert "configuração" in capsys.readouterr().out


def test_startup_starts_only_one_monitor(ds, monkeypatch):
    starts = []
    class Worker:
        def __init__(self, **kwargs):
            assert kwargs["daemon"] is True
        def start(self):
            starts.append(True)
    monkeypatch.setattr(ds.threading, "Thread", Worker)
    ds.start_monitor_worker()
    ds.start_monitor_worker()
    assert starts == [True]


def test_update_rolls_back_code_and_venv_without_restoring_database(ops, tmp_path, monkeypatch):
    # All systemctl, network, pip and test invocations are replaced by local fakes.
    app = tmp_path / "app"
    venv = tmp_path / "venv"
    (app / "deploy/linux").mkdir(parents=True)
    (app / "deploy/linux/digital-signage.service").write_text("same unit")
    (app / "identity").write_text("old")
    venv.mkdir()
    (venv / "identity").write_text("old venv")
    monkeypatch.setattr(ops, "APP", app)
    monkeypatch.setattr(ops, "VENV", venv)
    monkeypatch.setattr(ops, "active", lambda: True)
    monkeypatch.setattr(ops, "live_snapshot", lambda: tmp_path / "preventive-backup")
    monkeypatch.setattr(ops, "protected_code", lambda *args: None)
    monkeypatch.setattr(ops, "runtime_readable", lambda *args: None)
    monkeypatch.setattr(ops, "static_checks", lambda *args: None)
    commit = "a" * 40
    def output(*args):
        if args[0] == "systemctl":
            return "loaded"
        if "status" in args:
            return ""
        if "branch" in args:
            return "release"
        if "get-url" in args:
            return "https://example.invalid/reviewed.git"
        if "FETCH_HEAD" in args:
            return commit
        return "b" * 40
    monkeypatch.setattr(ops, "output", output)
    def run(*args, **kwargs):
        if args[:2] == ("git", "clone"):
            target = Path(args[-1])
            shutil.copytree(app, target)
            (target / "identity").write_text("new")
        if "-m" in args and "venv" in args:
            Path(args[-1]).mkdir()
    monkeypatch.setattr(ops, "run", run)
    calls = []
    def wait_health(**kwargs):
        calls.append(kwargs)
        # first check resumes after initial backup; second checks new deployment
        if len(calls) == 2:
            raise RuntimeError("new version failed")
    monkeypatch.setattr(ops, "wait_health", wait_health)
    # Do not require Windows symlink privilege: exercise failure during switch too.
    if os.name == "nt":
        original = Path.symlink_to
        def symbolic(self, target, **kwargs):
            if self == venv:
                raise RuntimeError("new version failed")
            return original(self, target, **kwargs)
        monkeypatch.setattr(Path, "symlink_to", symbolic)
        monkeypatch.setattr(ops, "wait_health", lambda **kwargs: None)
    with pytest.raises(RuntimeError, match="new version failed"):
        ops.update(commit, "release")
    assert (app / "identity").read_text() == "old"
    assert (venv / "identity").read_text() == "old venv"
