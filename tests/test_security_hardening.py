"""Security regression tests use only isolated databases and synthetic files."""
from base64 import b64decode
import importlib.util
import json
from pathlib import Path
import re
import shutil

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
PNG = b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL/nwAAAABJRU5ErkJggg==")


@pytest.fixture
def ds(tmp_path, monkeypatch):
    code = tmp_path / "code"
    code.mkdir()
    shutil.copy2(ROOT / "app.py", code / "app.py")
    shutil.copytree(ROOT / "templates", code / "templates")
    shutil.copytree(ROOT / "static", code / "static")
    monkeypatch.syspath_prepend(str(ROOT))
    data = tmp_path / "data"
    values = {
        "DS_DB_FILE": data / "db.sqlite",
        "DS_MEDIA_DIR": data / "media",
        "DS_RELEASE_DIR": data / "releases",
        "DS_UPLOAD_TEMP_DIR": data / "upload-tmp",
        "DS_INITIAL_CREDENTIALS_FILE": data / "credentials.txt",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, str(value))
    monkeypatch.setenv("DS_INITIAL_ADMIN_USER", "admin")
    monkeypatch.setenv("DS_INITIAL_ADMIN_PASSWORD", "synthetic-admin-password")
    spec = importlib.util.spec_from_file_location("security_test_app", code / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    conn = module.get_db()
    for tv_id, token in (("TV-A", "token-a"), ("TV-B", "token-b")):
        conn.execute(
            "INSERT INTO tvs(tv_id,name,device_token_hash) VALUES(?,?,?)",
            (tv_id, tv_id, module.hash_token(token)),
        )
    users = {}
    for name, role, tvs in (
        ("operator", "operator", ("TV-A",)),
        ("editor", "editor", ("TV-A",)),
        ("viewer-a", "viewer", ("TV-A",)),
        ("viewer-b", "viewer", ("TV-B",)),
    ):
        user_id = conn.execute(
            "INSERT INTO users(username,password_hash,role,is_active,created_at) VALUES(?,?,?,1,1)",
            (name, module.password_hash("synthetic-password"), role),
        ).lastrowid
        module.sync_user_tv_permissions(conn, user_id, tvs, role)
        users[name] = int(user_id)

    media = module.UPLOAD_DIR / "active.png"
    media.write_bytes(PNG)
    active_id = conn.execute(
        "INSERT INTO contents(name,filename,content_type,created_at,owner_user_id,pages_json) VALUES(?,?,?,?,?,?)",
        ("active", "uploads/active.png", "image", 1, users["editor"], '["uploads/active.png"]'),
    ).lastrowid
    (module.UPLOAD_DIR / "archived.png").write_bytes(PNG)
    archived_id = conn.execute(
        "INSERT INTO contents(name,filename,content_type,created_at,owner_user_id,pages_json,archived_at) VALUES(?,?,?,?,?,?,?)",
        ("archived", "uploads/archived.png", "image", 1, users["editor"], "[]", 2),
    ).lastrowid
    (module.UPLOAD_DIR / "orphan.png").write_bytes(PNG)
    orphan_id = conn.execute(
        "INSERT INTO contents(name,filename,content_type,created_at,owner_user_id,pages_json) VALUES(?,?,?,?,?,?)",
        ("orphan", "uploads/orphan.png", "image", 1, users["editor"], "[]"),
    ).lastrowid
    playlist_id = conn.execute(
        "INSERT INTO playlists(name,created_at,owner_user_id) VALUES('published',1,?)",
        (users["editor"],),
    ).lastrowid
    conn.execute(
        "INSERT INTO playlist_items(playlist_id,content_id,position,duration_sec) VALUES(?,?,1,10)",
        (playlist_id, active_id),
    )
    conn.execute("INSERT INTO tv_playlists(tv_id,playlist_id) VALUES('TV-A',?)", (playlist_id,))
    snapshot = {"items": [{"content_id": active_id, "content_type": "image", "filename": "uploads/active.png", "pages": ["uploads/active.png"]}]}
    conn.execute(
        "INSERT INTO playlist_publications(playlist_id,version,published_at,snapshot_json) VALUES(?,1,1,?)",
        (playlist_id, json.dumps(snapshot)),
    )
    conn.commit(); conn.close()
    module._test_ids = {"active": active_id, "archived": archived_id, "orphan": orphan_id}
    return module


def login(client, ds, username, password="synthetic-password"):
    client.get("/login")
    return client.post(
        "/login",
        data={
            "username": username,
            "password": password,
            "csrf_token": client.cookies.get(ds.LOGIN_CSRF_COOKIE_NAME),
        },
        follow_redirects=False,
    )


def csrf(client, ds):
    return {"X-CSRF-Token": ds.csrf_token_for_session(client.cookies.get(ds.SESSION_COOKIE_NAME))}


def test_csrf_form_ajax_session_binding_and_login_logout(ds):
    missing_login = TestClient(ds.app)
    csrf_error = missing_login.post(
        "/login",
        data={"username": "operator", "password": "synthetic-password", "next": "/players"},
    )
    assert csrf_error.status_code == 403
    assert csrf_error.headers["content-type"].startswith("text/html")
    assert "Sua página de login expirou. Tente novamente." in csrf_error.text
    assert 'name="next" value="/players"' in csrf_error.text
    assert "RequisiÃ" not in csrf_error.text
    refreshed_token = re.search(r'name="csrf_token" value="([^"]+)"', csrf_error.text).group(1)
    assert refreshed_token == missing_login.cookies.get(ds.LOGIN_CSRF_COOKIE_NAME)
    recovered = missing_login.post(
        "/login",
        data={
            "username": "operator",
            "password": "synthetic-password",
            "csrf_token": refreshed_token,
            "next": "/players",
        },
        follow_redirects=False,
    )
    assert recovered.status_code == 303
    assert recovered.headers["location"] == "/players"

    first = TestClient(ds.app); second = TestClient(ds.app)
    assert login(first, ds, "operator").status_code == 303
    assert login(second, ds, "operator").status_code == 303
    endpoint = "/players/TV-A/maintenance"
    assert first.post(endpoint, data={"action": "enable"}).status_code == 403
    assert first.post(endpoint, data={"action": "enable"}, headers={"X-CSRF-Token": "invalid"}).status_code == 403
    assert first.post(endpoint, data={"action": "enable"}, headers=csrf(second, ds)).status_code == 403
    assert first.post(endpoint, data={"action": "enable", "csrf_token": csrf(first, ds)["X-CSRF-Token"]}, follow_redirects=False).status_code == 303
    assert first.post("/api/playlist/999/reorder", json={"item_ids": []}, headers=csrf(first, ds)).status_code != 403
    assert first.post("/api/playlist/999/reorder", json={"item_ids": []}).status_code == 403
    assert first.post("/logout", headers=csrf(first, ds), follow_redirects=False).status_code == 303


def test_maintenance_uses_admin_auth_and_is_audited(ds):
    client = TestClient(ds.app)
    login(client, ds, "operator")
    response = client.post(
        "/players/TV-A/maintenance",
        data={"action": "enable", "note": "synthetic"},
        headers=csrf(client, ds),
        follow_redirects=False,
    )
    assert response.status_code == 303
    conn = ds.get_db()
    row = conn.execute("SELECT username,target_id,detail FROM audit_log WHERE action='player.maintenance_enabled'").fetchone()
    conn.close()
    assert row["username"] == "operator" and row["target_id"] == "TV-A"
    assert json.loads(row["detail"])["enabled"] is True


def test_protected_media_sessions_devices_range_and_web_player(ds):
    anonymous = TestClient(ds.app)
    assert anonymous.get("/media/uploads/active.png").status_code == 403
    assert anonymous.get("/media/%2e%2e/app.py").status_code in {400, 404}

    allowed = TestClient(ds.app); login(allowed, ds, "viewer-a")
    denied = TestClient(ds.app); login(denied, ds, "viewer-b")
    admin = TestClient(ds.app); login(admin, ds, "admin", "synthetic-admin-password")
    assert allowed.get("/media/uploads/active.png").status_code == 200
    assert denied.get("/media/uploads/active.png").status_code == 403
    assert admin.get("/media/uploads/orphan.png").status_code == 200
    assert admin.get("/media/uploads/archived.png").status_code == 404
    assert allowed.get("/player/TV-A").status_code == 200
    assert denied.get("/player/TV-A").status_code == 403
    assert allowed.get("/api/web-player/TV-A").status_code == 200

    initial = allowed.get("/media/uploads/active.png", headers={"Range": "bytes=0-7"})
    middle = allowed.get("/media/uploads/active.png", headers={"Range": "bytes=8-15"})
    invalid = allowed.get("/media/uploads/active.png", headers={"Range": "bytes=99999-100000"})
    assert initial.status_code == 206 and len(initial.content) == 8
    assert middle.status_code == 206 and len(middle.content) == 8
    assert invalid.status_code == 416

    assert anonymous.get("/media/uploads/active.png", headers={"Authorization": "Bearer token-a"}).status_code == 200
    assert anonymous.get("/media/uploads/active.png", headers={"Authorization": "Bearer token-b"}).status_code == 403
    assert anonymous.get("/media/uploads/active.png", headers={"Authorization": "Bearer invalid"}).status_code == 403
    assert anonymous.get("/media/uploads/orphan.png", headers={"Authorization": "Bearer token-a"}).status_code == 403
    conn = ds.get_db(); conn.execute("UPDATE tvs SET device_token_hash=? WHERE tv_id='TV-A'", (ds.hash_token("rotated"),)); conn.commit(); conn.close()
    assert anonymous.get("/media/uploads/active.png", headers={"Authorization": "Bearer token-a"}).status_code == 403


def test_disabled_user_loses_media_access(ds):
    client = TestClient(ds.app); login(client, ds, "viewer-a")
    conn = ds.get_db(); conn.execute("UPDATE users SET is_active=0 WHERE username='viewer-a'"); conn.commit(); conn.close()
    assert client.get("/media/uploads/active.png").status_code == 403


def test_device_heartbeat_requires_device_auth_but_not_csrf(ds):
    client = TestClient(ds.app)
    payload = {"tv_id": "TV-A", "player_version": "test", "sync_status": "ok"}
    assert client.post("/api/tv/heartbeat", json=payload).status_code == 401
    assert client.post("/api/tv/heartbeat", json=payload, headers={"Authorization": "Bearer token-a"}).status_code == 200


@pytest.mark.parametrize("name,mime", [("file.pdf", "application/pdf"), ("file.ppt", "application/vnd.ms-powerpoint"), ("file.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation")])
def test_legacy_document_uploads_are_rejected(ds, name, mime):
    client = TestClient(ds.app); login(client, ds, "editor")
    response = client.post(
        "/admin/upload", files={"file": (name, b"synthetic", mime)},
        headers={**csrf(client, ds), "Accept": "application/json"},
    )
    assert response.status_code == 400


def test_historical_legacy_content_remains_administrable_without_playback(ds):
    legacy_path = ds.UPLOAD_DIR / "historical.pdf"
    legacy_path.write_bytes(b"%PDF-synthetic")
    conn = ds.get_db()
    owner = conn.execute("SELECT id FROM users WHERE username='editor'").fetchone()[0]
    conn.execute(
        "INSERT INTO contents(name,filename,content_type,created_at,owner_user_id,pages_json) VALUES(?,?,?,?,?,?)",
        ("historical", "uploads/historical.pdf", "pdf", 1, owner, "[]"),
    )
    conn.commit(); conn.close()
    client = TestClient(ds.app); login(client, ds, "editor")
    page = client.get("/gallery")
    assert page.status_code == 200
    assert "Formato legado" in page.text
    assert "Formato legado não suportado" in page.text
    assert client.get("/media/uploads/historical.pdf").status_code == 200


def test_upload_signatures_mime_limits_and_cleanup(ds, monkeypatch):
    client = TestClient(ds.app); login(client, ds, "editor")
    headers = {**csrf(client, ds), "Accept": "application/json"}
    assert client.post("/admin/upload", files={"file": ("ok.png", PNG, "image/png")}, headers=headers).status_code == 200
    assert client.post("/admin/upload", files={"file": ("fake.png", b"MZ executable", "image/png")}, headers=headers).status_code == 415
    assert client.post("/admin/upload", files={"file": ("fake.png", PNG, "application/octet-stream")}, headers=headers).status_code == 415
    monkeypatch.setattr(ds, "MAX_IMAGE_BYTES", 8)
    assert client.post("/admin/upload", files={"file": ("large.png", PNG, "image/png")}, headers=headers).status_code == 413
    assert list(ds.UPLOAD_TEMP_DIR.iterdir()) == []


def test_supported_signatures_and_mp4_validation(ds, tmp_path, monkeypatch):
    samples = {
        ".jpg": b"\xff\xd8\xffdata\xff\xd9",
        ".jpeg": b"\xff\xd8\xffdata\xff\xd9",
        ".png": b"\x89PNG\r\n\x1a\nxxxxIHDRdata",
        ".gif": b"GIF89adata",
        ".webp": b"RIFFxxxxWEBPVP8 data",
        ".mp4": b"\x00\x00\x00\x18ftypisomdata",
    }
    for extension, content in samples.items():
        path = tmp_path / ("sample" + extension)
        path.write_bytes(content)
        assert ds.has_expected_media_signature(path, extension)
    monkeypatch.setattr(ds.shutil, "which", lambda _: None)
    video = tmp_path / "video.mp4"; video.write_bytes(samples[".mp4"])
    assert ds.probe_media_metadata(video, "video")["validation_status"] == "error"


def test_batch_file_count_limit(ds, monkeypatch):
    client = TestClient(ds.app); login(client, ds, "editor")
    conn = ds.get_db(); playlist_id = conn.execute("SELECT id FROM playlists LIMIT 1").fetchone()[0]; conn.close()
    monkeypatch.setattr(ds, "MAX_FILES_PER_UPLOAD", 1)
    response = client.post(
        f"/api/playlist/{playlist_id}/upload",
        files=[("files", ("a.png", PNG, "image/png")), ("files", ("b.png", PNG, "image/png"))],
        headers=csrf(client, ds),
    )
    assert response.status_code == 413

@pytest.mark.parametrize("token_kind", ["valid", "missing", "invalid"])
@pytest.mark.parametrize("password,authenticated", [("synthetic-password", True), ("wrong-password", False)])
def test_login_form_csrf_and_credentials(ds, token_kind, password, authenticated):
    client = TestClient(ds.app)
    page = client.get('/login', params={'next': '/players?view=all'})
    assert page.status_code == 200
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    assert token == client.cookies.get(ds.LOGIN_CSRF_COOKIE_NAME)
    assert 'name="next" value="/players?view=all"' in page.text
    second_page = client.get('/login', params={'next': '/admin'})
    second_token = re.search(r'name="csrf_token" value="([^"]+)"', second_page.text).group(1)
    assert second_token == token
    data = {'username': 'operator', 'password': password, 'next': '/players?view=all'}
    if token_kind != 'missing':
        data['csrf_token'] = token if token_kind == 'valid' else 'invalid-token'
    result = client.post('/login', data=data, follow_redirects=False)
    if token_kind != 'valid':
        assert result.status_code == 403
        assert not client.cookies.get(ds.SESSION_COOKIE_NAME)
        return
    assert result.status_code == 303
    if authenticated:
        assert result.headers['location'] == '/players?view=all'
        assert client.cookies.get(ds.SESSION_COOKIE_NAME)
        assert not client.cookies.get(ds.LOGIN_CSRF_COOKIE_NAME)
        logout = client.post('/logout', headers=csrf(client, ds), follow_redirects=False)
        assert logout.status_code == 303
        page = client.get(logout.headers['location'])
        assert page.status_code == 200
        assert 'Sessão encerrada.' in page.text
        assert 'id="auth-feedback" role="status"' in page.text
        assert not re.search(r'<input\b[^>]*aria-invalid="true"', page.text)
        assert not client.cookies.get(ds.SESSION_COOKIE_NAME)
    else:
        page = client.get(result.headers['location'])
        assert page.status_code == 200
        assert 'Usuário ou senha inválidos.' in page.text
        assert 'id="auth-feedback" role="alert"' in page.text
        assert 'name="next" value="/players?view=all"' in page.text
        assert not client.cookies.get(ds.SESSION_COOKIE_NAME)


def test_login_public_logo_and_escaped_feedback(ds):
    client = TestClient(ds.app)
    page = client.get('/login', params={'error': '<script>alert(1)</script>'})
    assert page.status_code == 200
    assert '&lt;script&gt;' in page.text
    assert '<script>alert(1)</script>' not in page.text
    assert ds.APP_VERSION in page.text
    assert page.text.count('<h1>') == 1
    logo = client.get('/static/images/arvedi-logo.png')
    assert logo.status_code == 200
    assert logo.content == (ROOT / 'static/images/arvedi-logo.png').read_bytes()
    for path in ('/static/app.py', '/static/digital_signage.db', '/static/%2e%2e/app.py'):
        assert client.get(path).status_code == 404


def test_deleted_default_tv_is_not_recreated_on_restart(ds):
    client = TestClient(ds.app)
    assert login(client, ds, "admin", "synthetic-admin-password").status_code == 303
    detail = client.get('/players/TV01')
    assert detail.status_code == 200
    assert 'Excluir tela definitivamente' in detail.text
    deleted = client.post(
        '/players/TV01/delete',
        data={'confirm_tv_id': 'TV01'},
        headers=csrf(client, ds),
        follow_redirects=False,
    )
    assert deleted.status_code == 303
    ds.init_db()
    conn = ds.get_db()
    assert conn.execute("SELECT 1 FROM tvs WHERE tv_id='TV01'").fetchone() is None
    conn.close()

