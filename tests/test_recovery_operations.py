"""Synthetic restore/update fault injection. No app import, host or service access."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import mock_open

import pytest

ROOT = Path(__file__).resolve().parents[1]
OLD = "b" * 40
NEW = "a" * 40


@pytest.fixture
def lab(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("recovery_ops", ROOT / "deploy/linux/server_ops.py")
    ops = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ops)
    app, venv, data = (tmp_path / name for name in ("app", "venv", "data"))
    env, backups = tmp_path / "etc/server.env", tmp_path / "backups"
    (app / "deploy/linux").mkdir(parents=True)
    (app / "deploy/linux/digital-signage.service").write_text("synthetic unit\n")
    (app / "identity").write_text("old")
    (app / "app.py").write_text("APP_VERSION = 'synthetic'\n")
    venv.mkdir()
    (venv / "runtime.txt").write_text("old venv")
    (data / "media").mkdir(parents=True)
    (data / "releases").mkdir()
    (data / "media/identity").write_text("backup")
    (data / "releases/package.zip").write_bytes(b"synthetic package")
    with sqlite3.connect(data / "digital_signage.db") as db:
        db.execute("CREATE TABLE users(id INTEGER)")
        db.execute("INSERT INTO users VALUES(7)")
    db.close()
    env.parent.mkdir()
    text = (ROOT / "deploy/linux/digital-signage.env.example").read_text()
    env.write_text(text.replace("/var/lib/digital-signage", data.as_posix()), newline="\n")
    os.chmod(env, 0o600)
    for key, value in {"APP": app, "VENV": venv, "DATA": data, "ENV": env, "BACKUPS": backups}.items():
        monkeypatch.setattr(ops, key, value)
    monkeypatch.setattr(ops, "EXPECTED", {
        "DS_DB_FILE": (data / "digital_signage.db").as_posix(),
        "DS_MEDIA_DIR": (data / "media").as_posix(),
        "DS_RELEASE_DIR": (data / "releases").as_posix(),
    })
    validate = ops.validate_env
    monkeypatch.setattr(ops, "validate_env", lambda path=None: validate(path or env))
    source = ops.snapshot(data, env, backups, {"commit": OLD, "version": "synthetic"})
    (data / "media/identity").write_text("live")
    state = SimpleNamespace(ops=ops, root=tmp_path, source=source, fault=None, events=[],
                            running=True, start_attempts=0, snapshots=0)
    def output(*args):
        state.events.append(("output", *map(str, args)))
        if args[0] == "systemctl":
            return "not-found" if state.fault == "missing_service" else "loaded"
        if "status" in args:
            return " M identity" if state.fault == "dirty" else ""
        if "branch" in args:
            return "release"
        if "get-url" in args:
            return "https://example.invalid/synthetic.git"
        if "FETCH_HEAD" in args:
            return NEW
        if "freeze" in args:
            return "synthetic-package==1"
        return OLD
    def run(*args, **kwargs):
        parts = tuple(map(str, args))
        state.events.append(parts)
        if parts[:2] == ("systemctl", "stop"):
            state.running = False
        elif parts[:2] == ("systemctl", "start"):
            state.start_attempts += 1
            # Initial update backup resumes old code; fail only after replacement.
            changed = ((app / "identity").read_text() == "new"
                       or (data / "media/identity").read_text() == "backup")
            if state.fault == "restart" and changed:
                state.fault = None
                raise subprocess.CalledProcessError(1, ["simulated-start"])
            state.running = True
        elif parts[:2] == ("git", "clone"):
            if state.fault == "clone":
                raise subprocess.CalledProcessError(1, ["simulated-clone"])
            target = Path(args[-1])
            shutil.copytree(app, target)
            (target / "identity").write_text("new")
        elif "cat-file" in parts and state.fault == "missing_commit":
            raise subprocess.CalledProcessError(1, ["simulated-cat-file"])
        elif "-m" in parts and "venv" in parts:
            if state.fault == "venv":
                raise subprocess.CalledProcessError(1, ["simulated-venv"])
            destination = Path(args[-1])
            destination.mkdir()
            (destination / "runtime.txt").write_text("new venv")
        elif "pip" in parts and "install" in parts and state.fault == "dependencies":
            raise subprocess.CalledProcessError(1, ["simulated-pip"])
        elif "pytest" in parts:
            test_env = kwargs["env"]
            assert Path(test_env["DS_DB_FILE"]).parent != data
            assert test_env["DS_MEDIA_DIR"] != str(data / "media")
            if state.fault == "tests":
                raise subprocess.CalledProcessError(1, ["simulated-pytest"])
    def health(proxy=False):
        state.events.append(("health", proxy))
        changed = ((app / "identity").read_text() == "new"
                   or (data / "media/identity").read_text() == "backup")
        if changed and (state.fault == "health" or state.fault == "proxy" and proxy == "local"):
            state.fault = None
            raise RuntimeError("simulated HTTP 503")
    monkeypatch.setattr(ops, "output", output)
    monkeypatch.setattr(ops, "run", run)
    monkeypatch.setattr(ops, "wait_health", health)
    monkeypatch.setattr(ops, "active", lambda: state.running)
    # POSIX ownership is exercised on Linux; Windows has no chown.
    monkeypatch.setattr(ops.os, "chown", lambda *args: None, raising=False)
    def permissions(path):
        ops.safe_tree(path)
        for item in [path, *path.rglob("*")]:
            os.chmod(item, 0o700 if item.is_dir() else 0o600)
    monkeypatch.setattr(ops, "permissions", permissions)
    monkeypatch.setattr(ops, "protected_code", lambda path: ops.safe_tree(path))
    monkeypatch.setattr(ops, "runtime_readable", lambda path: None)
    monkeypatch.setattr(ops, "static_checks", lambda *args: state.events.append(("static-checks",)))
    def live_snapshot():
        state.snapshots += 1
        if state.fault == "preventive" and state.snapshots == 2:
            raise OSError("simulated disk full")
        return ops.snapshot(data, env, backups, {"commit": OLD})
    monkeypatch.setattr(ops, "live_snapshot", live_snapshot)
    # Prevent accidental networking even if a future test forgets the health stub.
    monkeypatch.setattr(ops.urllib.request, "build_opener",
                        lambda *args: pytest.fail("Network access is forbidden in recovery tests"))
    if os.name == "nt":
        # Model directory-link semantics where Windows symlink privilege is absent.
        real_link, real_unlink, real_is_link, real_rename = Path.symlink_to, Path.unlink, Path.is_symlink, Path.rename
        fake_links = set()
        def rename(path, target):
            # Windows scanners can briefly retain handles after SQLite checks.
            for attempt in range(20):
                try:
                    return real_rename(path, target)
                except PermissionError:
                    if attempt == 19:
                        raise
                    time.sleep(0.01)
        def link(path, target, **kwargs):
            if path != venv:
                return real_link(path, target, **kwargs)
            assert path.parent == tmp_path and Path(target).parent == tmp_path
            shutil.copytree(target, path)
            fake_links.add(path)
        def unlink(path, **kwargs):
            if path in fake_links:
                assert path.parent == tmp_path
                for child in path.iterdir():
                    assert child.is_file()
                    real_unlink(child)
                path.rmdir()
                fake_links.remove(path)
            else:
                real_unlink(path, **kwargs)
        monkeypatch.setattr(Path, "symlink_to", link)
        monkeypatch.setattr(Path, "is_symlink", lambda path: path in fake_links or real_is_link(path))
        monkeypatch.setattr(Path, "unlink", unlink)
        monkeypatch.setattr(Path, "rename", rename)
    return state


def original_files(lab):
    return {p.relative_to(lab.root).as_posix(): p.read_bytes()
            for base in (lab.ops.APP, lab.ops.VENV, lab.ops.DATA, lab.ops.ENV.parent)
            for p in base.rglob("*") if p.is_file()}


def assert_live_restored(lab, env_bytes, db_bytes):
    ops = lab.ops
    assert (ops.DATA / "media/identity").read_text() == "live"
    assert ops.ENV.read_bytes() == env_bytes
    assert (ops.DATA / "digital_signage.db").read_bytes() == db_bytes
    assert lab.running


def test_restore_valid(lab, capsys):
    ops = lab.ops
    ops.restore(lab.source, True)
    assert (ops.DATA / "media/identity").read_text() == "backup"
    assert lab.running
    records = list(ops.BACKUPS.glob("before-restore-*/preservation.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text())
    assert record["entries"]["data/digital_signage.db"]["present"]
    assert (records[0].parent / "data/media/identity").read_text() == "live"
    assert ("health", "local") in lab.events
    assert ops.ENV.read_text() not in capsys.readouterr().out


@pytest.mark.parametrize("fault", ["manifest", "checksum", "database", "outside", "traversal", "manifest_traversal", "missing_service"])
def test_restore_rejects_before_stop(lab, fault):
    ops, source = lab.ops, lab.source
    if fault == "manifest":
        (source / "manifest.json").unlink()
    elif fault == "checksum":
        (source / "media/identity").write_text("tampered")
    elif fault == "database":
        db = source / "digital_signage.db"
        db.write_bytes(b"corrupt")
        path = source / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["files"]["digital_signage.db"] = {"bytes": db.stat().st_size, "sha256": ops.digest(db)}
        manifest["total_bytes"] = sum(item["bytes"] for item in manifest["files"].values())
        path.write_text(json.dumps(manifest))
    elif fault == "outside":
        source = lab.root / "unauthorized"
        shutil.copytree(lab.source, source)
    elif fault == "traversal":
        source = source / ".." / source.name
    elif fault == "manifest_traversal":
        path = source / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["files"]["../escape"] = {"bytes": 0, "sha256": "0" * 64}
        path.write_text(json.dumps(manifest))
    else:
        lab.fault = fault
    with pytest.raises((ValueError, RuntimeError, OSError, sqlite3.Error)):
        ops.restore(source, True)
    assert ("systemctl", "stop", ops.SERVICE) not in lab.events
    assert (ops.DATA / "media/identity").read_text() == "live"


@pytest.mark.parametrize("missing", ["database", "env", "media", "data", "corrupt_database", "invalid_env"])
def test_restore_accepts_damaged_current_state(lab, missing):
    ops = lab.ops
    if missing == "database":
        (ops.DATA / "digital_signage.db").unlink()
    elif missing == "env":
        ops.ENV.unlink()
    elif missing == "media":
        assert (ops.DATA / "media").is_relative_to(lab.root)
        shutil.rmtree(ops.DATA / "media")
    elif missing == "data":
        assert ops.DATA.is_relative_to(lab.root)
        shutil.rmtree(ops.DATA)
    elif missing == "corrupt_database":
        (ops.DATA / "digital_signage.db").write_bytes(b"corrupt current state")
    else:
        ops.ENV.write_text("invalid current config")
    ops.restore(lab.source, True)
    ops.integrity(ops.DATA / "digital_signage.db")
    ops.validate_env(ops.ENV)
    record = next(ops.BACKUPS.glob("before-restore-*/preservation.json"))
    if missing == "corrupt_database":
        assert (record.parent / "data/digital_signage.db").read_bytes() == b"corrupt current state"
    elif missing == "invalid_env":
        assert (record.parent / "server.env").read_text() == "invalid current config"
    elif missing in {"database", "env", "media", "data"}:
        key = {"database": "data/digital_signage.db", "env": "server.env",
               "media": "data/media", "data": "data"}[missing]
        assert json.loads(record.read_text())["entries"][key] == {"present": False}


@pytest.mark.parametrize("phase", ["stage", "preventive"])
def test_restore_copy_failure_recovers_service(lab, monkeypatch, phase):
    ops = lab.ops
    env_before, db_before = ops.ENV.read_bytes(), (ops.DATA / "digital_signage.db").read_bytes()
    copier = ops.copy_verified
    def fail(source, destination):
        preventive = ".before-restore-" in str(destination)
        if (phase == "preventive") == preventive:
            raise OSError("simulated copy failure")
        return copier(source, destination)
    monkeypatch.setattr(ops, "copy_verified", fail)
    with pytest.raises(OSError):
        ops.restore(lab.source, True)
    assert_live_restored(lab, env_before, db_before)
    if phase == "stage":
        assert lab.start_attempts == 0
    else:
        assert lab.start_attempts == 1
        assert not list(ops.BACKUPS.glob("before-restore-*"))


def test_restore_failure_during_swap_recovers_original(lab, monkeypatch):
    ops = lab.ops
    env_before, db_before = ops.ENV.read_bytes(), (ops.DATA / "digital_signage.db").read_bytes()
    real_rename = Path.rename
    def fail_install(path, target):
        if path.name.startswith("digital-signage-restore-") and target == ops.DATA:
            raise OSError("simulated swap failure")
        return real_rename(path, target)
    monkeypatch.setattr(Path, "rename", fail_install)
    with pytest.raises(OSError, match="swap failure"):
        ops.restore(lab.source, True)
    assert_live_restored(lab, env_before, db_before)
    assert lab.start_attempts == 1


@pytest.mark.parametrize("fault", ["restart", "health", "proxy"])
def test_restore_rollback_once(lab, fault):
    ops = lab.ops
    env_before, db_before = ops.ENV.read_bytes(), (ops.DATA / "digital_signage.db").read_bytes()
    lab.fault = fault
    with pytest.raises((RuntimeError, subprocess.CalledProcessError)):
        ops.restore(lab.source, True)
    assert_live_restored(lab, env_before, db_before)
    assert len(list(ops.BACKUPS.glob("before-restore-*"))) == 1
    assert lab.start_attempts == 2


def test_restore_cli_does_not_validate_lost_live_env(lab, monkeypatch):
    ops = lab.ops
    calls = []
    monkeypatch.setattr(sys, "argv", ["server_ops.py", "restore", "--backup", str(lab.source), "--confirm-restore"])
    monkeypatch.setattr(ops, "os", SimpleNamespace(name="posix", geteuid=lambda: 0, umask=lambda value: 0))
    monkeypatch.setattr(ops, "open", mock_open(), raising=False)
    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=lambda *args: None))
    monkeypatch.setitem(sys.modules, "syslog", SimpleNamespace(LOG_INFO=1, LOG_ERR=2, openlog=lambda *args: None, syslog=lambda *args: None))
    monkeypatch.setattr(ops, "validate_env", lambda *args: pytest.fail("live env must not be required"))
    monkeypatch.setattr(ops, "restore", lambda path, confirmed: calls.append((str(path), confirmed)))
    ops.main()
    assert calls == [(str(lab.source), True)]


@pytest.mark.parametrize("fault", ["dirty", "missing_commit", "clone", "venv", "dependencies", "tests", "missing_service"])
def test_update_preparation_failure_keeps_active_files(lab, fault):
    before = original_files(lab)
    lab.fault = fault
    with pytest.raises((ValueError, RuntimeError, subprocess.CalledProcessError)):
        lab.ops.update(NEW, "release")
    assert original_files(lab) == before
    assert lab.running


def test_update_preventive_failure_resumes_original(lab):
    before = original_files(lab)
    lab.fault = "preventive"
    with pytest.raises(OSError, match="disk full"):
        lab.ops.update(NEW, "release")
    assert original_files(lab) == before
    assert lab.running
    assert not list(lab.root.glob("app-before-*"))
    assert lab.start_attempts == 2  # resume after each of the two backup windows


@pytest.mark.parametrize("fault", ["restart", "health", "proxy"])
def test_update_rolls_back_code_and_venv(lab, fault):
    before = original_files(lab)
    lab.fault = fault
    with pytest.raises((RuntimeError, subprocess.CalledProcessError)):
        lab.ops.update(NEW, "release")
    assert original_files(lab) == before
    assert lab.running
    assert (lab.ops.VENV / "runtime.txt").read_text() == "old venv"
    assert (lab.ops.APP / "identity").read_text() == "old"
    assert lab.start_attempts == 3


def test_update_success_preserves_config_data_previous_versions(lab, capsys):
    ops = lab.ops
    env_before, db_before = ops.ENV.read_bytes(), (ops.DATA / "digital_signage.db").read_bytes()
    ops.update(NEW, "release")
    assert (ops.APP / "identity").read_text() == "new"
    assert (ops.VENV / "runtime.txt").read_text() == "new venv"
    assert next(lab.root.glob("app-before-*/identity")).read_text() == "old"
    assert next(lab.root.glob("venv-before-*/runtime.txt")).read_text() == "old venv"
    assert ops.ENV.read_bytes() == env_before
    assert (ops.DATA / "digital_signage.db").read_bytes() == db_before
    assert (ops.DATA / "media/identity").read_text() == "live"
    assert lab.running and lab.snapshots == 2
    assert ("health", "local") in lab.events
    log = capsys.readouterr().out
    assert "branch=release" in log and OLD in log and NEW in log
    assert env_before.decode() not in log


def test_update_success_preserves_initially_stopped_service(lab):
    lab.running = False
    lab.ops.update(NEW, "release")
    assert not lab.running
    assert lab.start_attempts == 1
    assert (lab.ops.APP / "identity").read_text() == "new"
    assert (lab.ops.VENV / "runtime.txt").read_text() == "new venv"


def test_recovery_failure_stops_without_loop(lab, monkeypatch):
    ops = lab.ops
    monkeypatch.setattr(ops, "wait_health", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("503")))
    with pytest.raises(RuntimeError, match="intervenção"):
        ops.restore(lab.source, True)
    assert not lab.running and lab.start_attempts == 2
    assert (ops.DATA / "media/identity").read_text() == "live"
    assert len(list(ops.BACKUPS.glob("before-restore-*"))) == 1


def test_health_proxy_uses_loopback_and_host_header(lab, monkeypatch):
    # Exercise real polling function with a synthetic HTTP response, no sockets.
    spec = importlib.util.spec_from_file_location("health_ops", ROOT / "deploy/linux/server_ops.py")
    ops = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ops)
    import io
    requests = []
    class Response(io.BytesIO):
        status = 200
    def open_request(request, timeout):
        requests.append(request)
        return Response(b'{"status":"ok"}')
    monkeypatch.setattr(ops.urllib.request, "build_opener", lambda *args: SimpleNamespace(open=open_request))
    ops.wait_health(proxy="local")
    assert requests[0].full_url == "http://127.0.0.1/health"
    assert requests[0].get_header("Host") == "signage.amb.local"


def test_health_503_exhausts_bounded_retries_without_network(monkeypatch):
    spec = importlib.util.spec_from_file_location("unhealthy_ops", ROOT / "deploy/linux/server_ops.py")
    ops = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ops)
    from urllib.error import HTTPError
    requests = []
    def open_request(request, timeout):
        requests.append(request)
        raise HTTPError(request.full_url, 503, "synthetic unavailable", {}, None)
    monkeypatch.setattr(ops.urllib.request, "build_opener", lambda *args: SimpleNamespace(open=open_request))
    monkeypatch.setattr(ops.time, "sleep", lambda seconds: None)
    with pytest.raises(RuntimeError, match="Health check"):
        ops.wait_health()
    assert len(requests) == 30


def test_restore_rollback_preserves_missing_env_and_inactive_service(lab):
    ops = lab.ops
    ops.ENV.unlink()
    lab.running = False
    lab.fault = "health"
    with pytest.raises(RuntimeError, match="503"):
        ops.restore(lab.source, True)
    assert not ops.ENV.exists()
    assert not lab.running and lab.start_attempts == 1
    assert (ops.DATA / "media/identity").read_text() == "live"


@pytest.mark.parametrize("failure", [
    RuntimeError("synthetic failure"),
    subprocess.CalledProcessError(1, ["synthetic-command", "synthetic-secret-never-log"]),
])
def test_cli_failure_exits_nonzero_and_sanitizes_command(failure, capsys):
    # Exercise the real __main__ error handler; replace only main's body in memory.
    path = ROOT / "deploy/linux/server_ops.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    main.body = [ast.Raise(exc=ast.Name(id="injected_error", ctx=ast.Load()), cause=None)]
    ast.fix_missing_locations(tree)
    with pytest.raises(SystemExit) as result:
        exec(compile(tree, str(path), "exec"), {"__name__": "__main__", "injected_error": failure})
    assert result.value.code == 1
    assert "synthetic-secret-never-log" not in capsys.readouterr().err
