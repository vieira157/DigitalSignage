"""Run: .venv/Scripts/python -m pytest -q -p no:cacheprovider tests/test_player_version_health.py

The application (including import-time initialization) runs from a temporary copy.
No operational database, credentials, releases, device or HTTP server is used.
"""
import asyncio
import importlib.util
import json
import shutil
from pathlib import Path

import pytest
from starlette.requests import Request

ROOT = Path(__file__).resolve().parents[1]
NOW = 2_000_000_000.0


@pytest.fixture
def ds(tmp_path, monkeypatch):
    for name in ("app.py", "stability_monitor.py"):
        shutil.copy2(ROOT / name, tmp_path / name)
    shutil.copytree(ROOT / "templates", tmp_path / "templates")
    monkeypatch.syspath_prepend(str(tmp_path))
    for key, value in {"DS_DB_FILE": "test.db", "DS_MEDIA_DIR": "media", "DS_RELEASE_DIR": "releases"}.items():
        monkeypatch.setenv(key, str(tmp_path / value))
    monkeypatch.setenv("DS_INITIAL_ADMIN_PASSWORD", "temporary-test-password")
    monkeypatch.setenv("DS_INITIAL_CREDENTIALS_FILE", str(tmp_path / "first_admin_credentials.txt"))
    spec = importlib.util.spec_from_file_location("isolated_version_app", tmp_path / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.time, "time", lambda: NOW)
    assert module.DB_FILE.parent == tmp_path
    assert module.CREDENTIALS_FILE.parent == tmp_path
    return module


def seed(ds):
    conn = ds.get_db()
    # init_db seeds a demonstration TV in this temporary database.
    conn.execute("DELETE FROM tvs")
    runtime = {"browser_running": True, "playback_state": "playing", "sync_status": "ok", "disk_free_mb": 2048}
    for tv_id, version in (("VERSION-A", "3.2.2-stability-preview"), ("VERSION-B", "3.2.1-stable")):
        conn.execute("INSERT INTO tvs(tv_id,name,last_seen,last_sync_at,player_version) VALUES(?,?,?,?,?)",
                     (tv_id, tv_id, NOW, NOW, version))
        conn.execute("INSERT INTO tv_runtime_status VALUES(?,?,?)", (tv_id, NOW, json.dumps(runtime)))
    conn.execute("UPDATE tv_monitor_settings SET recommended_player_version='3.2.1-stable' WHERE id=1")
    uid = conn.execute("INSERT INTO users(username,password_hash,role,is_active,created_at) VALUES('restricted','unused','viewer',1,?)", (NOW,)).lastrowid
    ds.sync_user_tv_permissions(conn, uid, ["VERSION-A"], "viewer")
    admin = dict(conn.execute("SELECT * FROM users WHERE role='admin'").fetchone())
    viewer = dict(conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())
    conn.commit()
    conn.close()
    return admin, viewer


def request(ds, user, path="/admin"):
    req = Request({"type": "http", "method": "GET", "path": path, "raw_path": path.encode(),
                   "query_string": b"", "headers": [], "scheme": "http", "server": ("testserver", 80),
                   "client": ("127.0.0.1", 1), "router": ds.app.router, "app": ds.app})
    req.state.user = user
    return req


@pytest.mark.parametrize("version,recommended,tv_changes,runtime_changes,maintenance,health,version_status", [
    ("3.2.1-stable", "3.2.1-stable", {}, {}, False, "ok", "current"),
    ("3.2.0-stable", "3.2.1-stable", {}, {}, False, "ok", "different"),
    (None, "3.2.1-stable", {}, {}, False, "ok", "unknown"),
    ("3.2.1-stable", None, {}, {}, False, "ok", "unknown"),
    ("3.2.2-stability-preview", "3.2.1-stable", {}, {}, False, "ok", "different"),
    ("preview", "stable", {"last_seen": NOW - 31}, {}, False, "offline", "different"),
    ("preview", "stable", {}, {"browser_running": False}, False, "error", "different"),
    ("preview", "stable", {}, {"playback_state": "error"}, False, "error", "different"),
    ("preview", "stable", {}, {"sync_status": "offline"}, False, "warning", "different"),
    ("preview", "stable", {}, {"disk_free_mb": 100}, False, "warning", "different"),
    ("preview", "stable", {}, {}, True, "maintenance", "different"),
    ("preview", "stable", {"last_error": "failure"}, {}, False, "error", "different"),
    ("preview", "stable", {"last_sync_at": NOW - 121}, {}, False, "warning", "different"),
])
def test_health_matrix(ds, version, recommended, tv_changes, runtime_changes, maintenance, health, version_status):
    tv = {"last_seen": NOW, "last_sync_at": NOW, "last_error": "", "player_version": version, **tv_changes}
    runtime = {"browser_running": True, "playback_state": "playing", "sync_status": "ok", "disk_free_mb": 2048, **runtime_changes}
    actual, reason = ds.evaluate_tv_health(tv, runtime, {"recommended_player_version": recommended}, {"enabled": maintenance}, NOW)
    assert actual == health
    assert "versão recomendada:" not in reason
    assert ds.evaluate_player_version_status(version, recommended)["version_status"] == version_status


@pytest.mark.parametrize("reported,recommended,status,label", [
    (" stable ", "stable", "current", "Versão recomendada"),
    ("Stable", "stable", "different", "Versão diferente da recomendada"),
    ("3.10", "3.9", "different", "Versão diferente da recomendada"),
    (" ", "stable", "unknown", "Versão não informada"),
    (None, None, "unknown", "Versão não informada"),
    ("preview", "  ", "unknown", "Sem versão recomendada"),
])
def test_version_normalization(ds, reported, recommended, status, label):
    result = ds.evaluate_player_version_status(reported, recommended)
    assert result == {"player_version": str(reported or "").strip(),
                      "recommended_player_version": str(recommended or "").strip(),
                      "version_status": status, "version_status_label": label,
                      "version_outdated": status == "different"}


def test_dashboard_api_templates_and_permissions(ds):
    admin, viewer = seed(ds)
    conn = ds.get_db()
    conn.execute("INSERT INTO tv_monitor_state(tv_id,health,reason,updated_at) VALUES('VERSION-A','warning','Player preview; versão recomendada: stable.',?)", (NOW - 10,))
    conn.execute("UPDATE tvs SET last_error='real incident' WHERE tv_id='VERSION-B'")
    conn.commit()
    conn.close()
    for user, problems, count in ((admin, ["VERSION-B"], 2), (viewer, [], 1)):
        req = request(ds, user)
        dashboard = ds.admin(req).context
        assert dashboard["stats"]["tvs_problem"] == len(problems)
        assert [tv["tv_id"] for tv in dashboard["problem_tvs"]] == problems
        api = ds.tvs_status(req)
        assert len(api["tvs"]) == count
        tv = next(tv for tv in api["tvs"] if tv["tv_id"] == "VERSION-A")
        assert tv["health"] == "ok"
        assert tv["version_status"] == "different"
        assert tv["version_outdated"] is True
        assert {"health", "health_reason", "player_version", "recommended_player_version", "version_outdated"} <= tv.keys()
        detail = ds.player_details_page(req, "VERSION-A")
        assert detail.context["status"]["version_status_label"] == tv["version_status_label"]
        assert b'version-status different' in detail.body
        listing = ds.players_page(req).body.decode()
        assert "setInterval(loadStatus,3000)" in listing
        assert "tv.version_status_label" in listing
        assert "version_outdated" not in listing
        assert "only&&tv.health==='ok'" in listing
        assert "['warning','error'].includes(x.health)" in listing
    with pytest.raises(ds.HTTPException) as exc:
        ds.player_details_page(request(ds, viewer), "VERSION-B")
    assert exc.value.status_code == 403


@pytest.mark.parametrize("version", [None, "", "   ", "3.2.2-stability-preview", "1.1.0"])
def test_heartbeat_latest_observation_not_server_version(ds, version):
    admin, _ = seed(ds)
    payload = {"tv_id": "VERSION-A", "sync_status": "ok", "browser_running": True, "playback_state": "playing"}
    if version is not None:
        payload["player_version"] = version
    ds.heartbeat(request(ds, admin), payload)
    expected = (version or "").strip()
    conn = ds.get_db()
    assert conn.execute("SELECT player_version FROM tvs WHERE tv_id='VERSION-A'").fetchone()[0] == expected
    assert conn.execute("SELECT player_version FROM tv_monitor_state WHERE tv_id='VERSION-A'").fetchone()[0] == expected
    conn.close()
    result = ds.tvs_status(request(ds, admin))["tvs"][0]
    assert result["player_version"] == expected
    assert result["version_status"] == ("different" if expected else "unknown")
    assert result["health"] == "ok"
    if not expected:
        assert result["version_status_label"] == "Versão não informada"
        assert result["version_outdated"] is False
        assert "Versão não informada" in ds.player_details_page(request(ds, admin), "VERSION-A").body.decode()


def test_snapshot_policy_transition_once_preserves_history(ds):
    admin, _ = seed(ds)
    conn = ds.get_db()
    reason = "Player 3.2.2-stability-preview; versão recomendada: 3.2.1-stable."
    conn.execute("INSERT INTO tv_monitor_state(tv_id,health,reason,player_version,updated_at) VALUES('VERSION-A','warning',?,'3.2.2-stability-preview',?)", (reason, NOW - 10))
    ds.log_tv_event(conn, "VERSION-A", "health_warning", "warning", "Player em atenção", reason)
    original = tuple(conn.execute("SELECT * FROM tv_event_log").fetchone())
    conn.commit()
    conn.close()
    for _ in range(3):
        ds.tvs_status(request(ds, admin))
    conn = ds.get_db()
    assert tuple(conn.execute("SELECT * FROM tv_event_log WHERE id=?", (original[0],)).fetchone()) == original
    events = conn.execute("SELECT * FROM tv_event_log WHERE tv_id='VERSION-A' AND event_type='health_ok'").fetchall()
    assert len(events) == 1
    assert events[0]["title"] == "Avaliação operacional atualizada"
    assert "separa divergência de versão" in events[0]["detail"]
    conn.close()


@pytest.mark.parametrize("recovery", ["health_ok", "health_warning", "health_error"])
def test_offline_duration_ends_on_communication_even_with_warning(ds, recovery):
    admin, _ = seed(ds)
    conn = ds.get_db()
    for timestamp, kind in ((NOW - 100, "health_offline"), (NOW - 40, recovery)):
        conn.execute("INSERT INTO tv_event_log(tv_id,created_at,event_type,severity,title,detail) VALUES('VERSION-A',?,?,'info','test','')", (timestamp, kind))
    conn.commit()
    conn.close()
    events = ds.player_details_page(request(ds, admin), "VERSION-A").context["events"]
    duration = next(e["duration_text"] for e in events if e["event_type"] == "health_offline")
    assert "em andamento" not in duration
    assert duration == ds.format_event_duration(60)


async def asgi_get(app, path, cookie=None):
    """Exercise routing/middleware without adding an HTTP test dependency."""
    messages = []
    waiting = asyncio.Event()
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await waiting.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    headers = [(b"cookie", cookie.encode())] if cookie else []
    await app({"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1",
               "method": "GET", "scheme": "http", "path": path, "raw_path": path.encode(),
               "query_string": b"", "root_path": "", "headers": headers, "server": ("testserver", 80),
               "client": ("127.0.0.1", 1)}, receive, send)
    return next(m["status"] for m in messages if m["type"] == "http.response.start"), b"".join(m.get("body", b"") for m in messages)


def test_actual_routes_and_auth_middleware(ds):
    admin, viewer = seed(ds)
    assert asyncio.run(asgi_get(ds.app, "/api/tvs/status"))[0] == 401
    for user in (admin, viewer):
        conn = ds.get_db()
        token = ds.create_session(conn, user["id"])
        conn.commit()
        conn.close()
        cookie = f"{ds.SESSION_COOKIE_NAME}={token}"
        for path in ("/admin", "/players", "/players/VERSION-A", "/api/tvs/status"):
            code, body = asyncio.run(asgi_get(ds.app, path, cookie))
            assert code == 200, (path, body)
            if path == "/api/tvs/status":
                data = json.loads(body)
                assert len(data["tvs"]) == (2 if user["role"] == "admin" else 1)
        code, _ = asyncio.run(asgi_get(ds.app, "/players/VERSION-B", cookie))
        assert code == (200 if user["role"] == "admin" else 403)
