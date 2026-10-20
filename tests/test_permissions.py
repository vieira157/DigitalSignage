"""Testes de isolamento por TV, perfis e compatibilidade do player.
Execução: python -m pytest -q tests/test_permissions.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

# Banco temporário independente do banco de produção.
TEST_DIR = tempfile.TemporaryDirectory(prefix="signage_rbac_test_")
ROOT = Path(TEST_DIR.name)
os.environ["DS_DB_FILE"] = str(ROOT / "test.db")
os.environ["DS_MEDIA_DIR"] = str(ROOT / "media")
os.environ["DS_RELEASE_DIR"] = str(ROOT / "releases")
os.environ["DS_INITIAL_ADMIN_PASSWORD"] = "temporary-test-password"
os.environ["DS_INITIAL_CREDENTIALS_FILE"] = str(ROOT / "first_admin_credentials.txt")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient as RawTestClient
import app as ds


class TestClient(RawTestClient):
    def request(self, method, url, **kwargs):
        method = str(method).upper()
        if method == "POST" and url == "/login":
            super().request("GET", "/login")
            data = dict(kwargs.get("data") or {})
            data["csrf_token"] = self.cookies.get(ds.LOGIN_CSRF_COOKIE_NAME)
            kwargs["data"] = data
        elif method in {"POST", "PUT", "PATCH", "DELETE"}:
            session = self.cookies.get(ds.SESSION_COOKIE_NAME)
            if session:
                headers = dict(kwargs.get("headers") or {})
                headers.setdefault("X-CSRF-Token", ds.csrf_token_for_session(session))
                kwargs["headers"] = headers
        return super().request(method, url, **kwargs)


C = TestClient(ds.app)


def user(name, role, tvs=()):
    conn = ds.get_db()
    uid = conn.execute(
        "INSERT INTO users(username, password_hash, role, is_active, created_at) VALUES(?,?,?,1,?)",
        (name, ds.password_hash("test_password_123"), role, time.time()),
    ).lastrowid
    ds.sync_user_tv_permissions(conn, uid, tvs, role)
    conn.commit()
    conn.close()
    return uid


def login(name):
    client = TestClient(ds.app)
    res = client.post("/login", data={"username": name, "password": "test_password_123"}, follow_redirects=False)
    assert res.status_code == 303, (name,res.text)
    return client


def setup_module(_):
    conn = ds.get_db()
    conn.execute("INSERT INTO tvs(tv_id,name,location) VALUES('TV-A','TV da Área A','A')")
    conn.execute("INSERT INTO tvs(tv_id,name,location) VALUES('TV-B','TV da Área B','B')")
    conn.commit();conn.close()
    ids = {r: user('rbac_'+r,r,('TV-A',)) for r in ('admin','operator','editor','viewer')}
    user('rbac_empty','viewer',())
    conn = ds.get_db()
    def content(name, owner=None):
        return conn.execute('INSERT INTO contents(name,filename,content_type,created_at,owner_user_id) VALUES(?,?,?,?,?)',
                            (name, 'uploads/'+name+'.png', 'image', time.time(), owner)).lastrowid
    ca = content('Conteudo-A', ids['editor'])
    cb = content('Conteudo-B', None)
    def playlist(name,content_id, tvs):
        pid = conn.execute('INSERT INTO playlists(name,created_at,owner_user_id) VALUES(?,?,?)',(name,time.time(),ids['editor'])).lastrowid
        conn.execute('INSERT INTO playlist_items(playlist_id,content_id,position,duration_sec) VALUES(?,?,1,10)',(pid,content_id))
        for tv in tvs:conn.execute('INSERT INTO tv_playlists(tv_id,playlist_id) VALUES(?,?)',(tv,pid))
        return pid
    global PID_A, PID_B, PID_SHARED, CID_A, CID_B
    PID_A=playlist('Playlist-A',ca,('TV-A',)); PID_B=playlist('Playlist-B',cb,('TV-B',)); PID_SHARED=playlist('Playlist-COMPARTILHADA',ca,())
    # Agendamento liga playlist compartilhada à TV-B; vínculo TV-A está na atribuição.
    conn.execute('INSERT INTO schedules(tv_id,playlist_id,day_of_week,start_time,end_time,enabled,created_at) VALUES(?,?,?,?,?,1,?)',
                 ('TV-B',PID_SHARED,0,'10:00','11:00',time.time()))
    conn.execute('INSERT INTO schedules(tv_id,playlist_id,day_of_week,start_time,end_time,enabled,created_at) VALUES(?,?,?,?,?,1,?)',('TV-A',PID_SHARED,0,'10:00','11:00',time.time()))
    CID_A, CID_B = ca,cb
    conn.commit();conn.close()


def test_editor_can_see_own_tv_but_not_other():
    c=login('rbac_editor')
    assert c.get('/players/TV-A').status_code==200
    assert c.get('/players/TV-B').status_code==403
    assert c.get('/players/TV-B/events.csv').status_code==403
    status=c.get('/api/tvs/status')
    assert status.status_code==200
    assert [tv['tv_id'] for tv in status.json()['tvs']]==['TV-A']
    assert c.get('/playlists').status_code==200
    assert 'Playlist-A' in c.get('/playlists').text
    assert 'Playlist-B' not in c.get('/playlists').text
    assert c.get(f'/playlist/{PID_B}/preview').status_code==403
    assert c.get(f'/playlist/{PID_A}/preview').status_code==200
    assert c.get(f'/preview/{CID_B}').status_code==403
    assert c.get('/users').status_code==403
    assert c.get('/audit').status_code==403
    assert c.get('/releases').status_code==403
    assert c.get('/players/settings').status_code==403


def test_editor_cannot_mutate_foreign_or_shared_playlist():
    c=login('rbac_editor')
    assert c.post(f'/admin/playlist/{PID_B}/delete', follow_redirects=False).status_code==403
    assert c.post(f'/api/playlist/{PID_B}/reorder', json={'item_ids':[]}).status_code==403
    assert c.post(f'/admin/playlist/{PID_SHARED}/publish', follow_redirects=False).status_code==403
    assert c.post(f'/admin/playlist/{PID_SHARED}/delete', follow_redirects=False).status_code==403
    assert c.post(f'/admin/content/{CID_B}/rename',data={'name':'ataque'},follow_redirects=False).status_code==403
    assert c.post('/admin/tv',data={'tv_id':'NEW','name':'NEW'},follow_redirects=False).status_code==403
    assert c.post('/releases',data={'version':'fake'},follow_redirects=False).status_code==403
    assert c.post('/players/TV-A/command',data={'command':'restart_browser'},follow_redirects=False).status_code==403


def test_only_admin_can_delete_tv_and_related_operational_data():
    conn = ds.get_db()
    conn.execute("INSERT INTO tvs(tv_id,name,location) VALUES('TV-DELETE','TV temporária','Teste')")
    conn.execute("INSERT INTO tv_playlists(tv_id,playlist_id) VALUES('TV-DELETE',?)", (PID_A,))
    conn.execute("INSERT INTO schedules(tv_id,playlist_id,day_of_week,start_time,end_time,enabled,created_at) VALUES('TV-DELETE',?,1,'08:00','09:00',1,?)", (PID_A,time.time()))
    conn.execute("INSERT INTO tv_runtime_status(tv_id,updated_at,payload_json) VALUES('TV-DELETE',?, '{}')", (time.time(),))
    conn.execute("INSERT INTO tv_event_log(tv_id,created_at,event_type,severity,title,detail) VALUES('TV-DELETE',?,'health_ok','info','Teste','Teste')", (time.time(),))
    conn.execute("INSERT INTO tv_monitor_state(tv_id,health,updated_at) VALUES('TV-DELETE','ok',?)", (time.time(),))
    conn.execute("INSERT INTO tv_maintenance(tv_id,enabled,updated_at) VALUES('TV-DELETE',0,?)", (time.time(),))
    conn.execute("INSERT INTO stability_samples(tv_id,bucket,observed_at) VALUES('TV-DELETE',1,?)", (time.time(),))
    conn.execute("INSERT INTO stability_events(tv_id,event_uid,occurred_at,received_at,event_type,severity) VALUES('TV-DELETE',?,?,?,'monitor_started','info')", ('a'*32,time.time(),time.time()))
    command_id = conn.execute("INSERT INTO player_commands(tv_id,command,status,created_at) VALUES('TV-DELETE','sync_now','pending',?)", (time.time(),)).lastrowid
    release_id = conn.execute("INSERT INTO player_releases(version,filename,sha256,size_bytes,status,created_at) VALUES('delete-test','delete.zip','00',1,'draft',?)", (time.time(),)).lastrowid
    conn.execute("INSERT INTO player_update_jobs(release_id,tv_id,command_id,status,created_at) VALUES(?, 'TV-DELETE',?,'queued',?)", (release_id,command_id,time.time()))
    viewer_id = conn.execute("SELECT id FROM users WHERE username='rbac_viewer'").fetchone()[0]
    conn.execute("INSERT INTO user_tv_permissions(user_id,tv_id,granted_at) VALUES(?, 'TV-DELETE',?)", (viewer_id,time.time()))
    conn.commit(); conn.close()

    operator = login('rbac_operator')
    assert operator.post('/players/TV-DELETE/delete',data={'confirm_tv_id':'TV-DELETE'},follow_redirects=False).status_code == 403

    admin = login('rbac_admin')
    assert admin.post('/players/TV-DELETE/delete',data={'confirm_tv_id':'incorreto'},follow_redirects=False).status_code == 400
    detail = admin.get('/players/TV-DELETE')
    assert detail.status_code == 200
    assert 'action="/players/TV-DELETE/delete"' in detail.text
    assert 'Excluir tela definitivamente' in detail.text
    operator_detail = operator.get('/players/TV-A')
    assert operator_detail.status_code == 200
    assert 'Excluir tela definitivamente' not in operator_detail.text
    response = admin.post('/players/TV-DELETE/delete',data={'confirm_tv_id':'TV-DELETE'},follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['location'].startswith('/players?')

    conn = ds.get_db()
    assert conn.execute("SELECT 1 FROM tvs WHERE tv_id='TV-DELETE'").fetchone() is None
    for table in ('player_update_jobs','player_commands','schedules','tv_playlists','tv_contents','user_tv_permissions','tv_runtime_status','tv_event_log','tv_monitor_state','tv_maintenance','stability_events','stability_samples'):
        assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE tv_id='TV-DELETE'").fetchone()[0] == 0
    audit = conn.execute("SELECT action,target_id FROM audit_log WHERE action='tv.delete' ORDER BY id DESC LIMIT 1").fetchone()
    assert conn.execute("SELECT 1 FROM playlists WHERE id=?", (PID_A,)).fetchone() is not None
    assert conn.execute("SELECT 1 FROM player_releases WHERE id=?", (release_id,)).fetchone() is not None
    conn.close()
    assert audit and audit['target_id'] == 'TV-DELETE'
    assert admin.get('/players/TV-DELETE').status_code == 404


def test_operator_may_operate_own_tv_without_admin_functions():
    c=login('rbac_operator')
    assert c.post('/players/TV-A/command',data={'command':'sync_now'},follow_redirects=False).status_code==303
    assert c.post('/players/TV-B/command',data={'command':'sync_now'},follow_redirects=False).status_code==403
    assert c.post('/players/TV-A/command',data={'command':'update_player'},follow_redirects=False).status_code==403
    assert c.post('/players/TV-A/maintenance',data={'action':'enable','note':'teste'},follow_redirects=False).status_code==303
    assert c.get('/releases').status_code==403
    assert c.post('/releases',data={'version':'fake'},follow_redirects=False).status_code==403
    assert c.post('/admin/tv/TV-B/playlist',data={'playlist_id': PID_A},follow_redirects=False).status_code==403


def test_viewer_is_read_only_and_unassigned_sees_nothing():
    c=login('rbac_viewer')
    assert c.get('/players/TV-A').status_code==200
    assert c.post('/admin/playlist',data={'name':'No'},follow_redirects=False).status_code==403
    assert c.post('/api/contents/delete-bulk',json={'content_ids':[CID_A]}).status_code==403
    assert c.post('/admin/schedule',data={'tv_id':'TV-A','playlist_id':PID_A,'day_of_week':0,'start_time':'12:00','end_time':'13:00'},follow_redirects=False).status_code==403
    assert c.post('/players/TV-A/maintenance',data={'action':'disable'},follow_redirects=False).status_code==403
    empty=login('rbac_empty')
    assert empty.get('/api/tvs/status').json()['tvs']==[]
    assert empty.get('/players/TV-A').status_code==403
    assert 'Playlist-A' not in empty.get('/playlists').text


def test_admin_has_full_visibility_and_user_tv_assignment_is_editable():
    c=login('rbac_admin')
    assert c.get('/audit').status_code==200
    assert c.get('/releases').status_code==200
    assert c.get('/users').status_code==200
    assert c.get('/players/TV-B').status_code==200
    assert c.post('/users',data={'username':'new_tv_user','full_name':'Teste','password':'super_secret_123','role':'viewer','tv_ids':'TV-B'},follow_redirects=False).status_code==303
    conn=ds.get_db(); res=conn.execute("SELECT u.id,p.tv_id FROM users u JOIN user_tv_permissions p ON p.user_id=u.id WHERE u.username='new_tv_user'").fetchall();conn.close()
    assert len(res)==1 and res[0]['tv_id']=='TV-B'
    new_client=TestClient(ds.app)
    new_client.post('/login',data={'username':'new_tv_user','password':'super_secret_123'},follow_redirects=False)
    assert new_client.get('/players/TV-A').status_code==403
    assert new_client.get('/players/TV-B').status_code==200


def test_raspberry_endpoints_do_not_demand_dashboard_login():
    # Player Web exige sessão; o Raspberry usa somente APIs com token.
    assert C.get('/player/TV-A', follow_redirects=False).status_code == 303
    assert login('rbac_viewer').get('/player/TV-A').status_code == 200
    # TVs ainda não provisionadas mantêm a compatibilidade legada (será desativada após migração).
    assert C.get('/api/player/TV-A').status_code == 200
    conn=ds.get_db()
    conn.execute("UPDATE tvs SET device_token_hash=? WHERE tv_id='TV-A'", (ds.hash_token('device_secret'),))
    conn.commit();conn.close()
    assert C.get('/api/player/TV-A').status_code == 401
    assert C.get('/api/player/TV-A/cache-manifest').status_code == 401
    assert C.get('/api/player/TV-A',headers={'X-Device-Token':'device_secret'}).status_code == 200


def test_gallery_and_agenda_do_not_expose_other_areas():
    c=login('rbac_editor')
    gallery=c.get('/gallery')
    assert gallery.status_code==200
    assert 'Conteudo-A' in gallery.text
    assert 'Conteudo-B' not in gallery.text
    sched=c.get('/schedules')
    assert sched.status_code==200
    assert 'TV da Área B' not in sched.text
    assert 'TV da Área A' in sched.text
    viewer=login('rbac_viewer')
    assert viewer.get('/gallery').status_code==200
    assert 'Acesso exclusivo' not in viewer.get('/gallery').text


def test_owned_upload_can_be_renamed_and_foreign_media_cannot():
    c=login('rbac_editor')
    from base64 import b64decode
    png=b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL/nwAAAABJRU5ErkJggg==')
    res=c.post('/admin/upload',data={'name':'MinhaImagem'},files={'file':('arquivo.png', png, 'image/png')},headers={'Accept':'application/json'})
    assert res.status_code==200, res.text
    content_id=res.json()['id']
    conn=ds.get_db();row=conn.execute('SELECT owner_user_id FROM contents WHERE id=?',(content_id,)).fetchone();uid=conn.execute("SELECT id FROM users WHERE username='rbac_editor'").fetchone()['id'];conn.close()
    assert row['owner_user_id']==uid
    res=c.post(f'/admin/content/{content_id}/rename',data={'name':'Alterada'},follow_redirects=False)
    assert res.status_code==303
    assert c.post(f'/admin/content/{CID_B}/rename',data={'name':'Ataque'},follow_redirects=False).status_code==403


def test_reassign_permissions_without_recreating_user():
    admin=login('rbac_admin')
    conn=ds.get_db();target_id=conn.execute("SELECT id FROM users WHERE username='rbac_viewer'").fetchone()['id'];conn.close()
    res=admin.post(f'/users/{target_id}/update',data={'full_name':'Viewer','role':'viewer','is_active':'1','tv_ids':['TV-B']},follow_redirects=False)
    assert res.status_code==303
    viewer=login('rbac_viewer')
    assert viewer.get('/players/TV-A').status_code==403
    assert viewer.get('/players/TV-B').status_code==200
    # revogar tudo
    res=admin.post(f'/users/{target_id}/update',data={'full_name':'Viewer','role':'viewer','is_active':'1'},follow_redirects=False)
    assert res.status_code==303
    assert viewer.get('/api/tvs/status').json()['tvs']==[]
