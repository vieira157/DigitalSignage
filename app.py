from datetime import datetime
from collections import Counter
from contextlib import closing
import csv
import io
import hashlib
import hmac
import secrets
import zipfile
from zoneinfo import ZoneInfo
import json
import os
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode
from stability_monitor import init_schema as stability_init_schema, ingest as stability_ingest, summarize as stability_summarize, window as stability_window

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles


# =========================================================
# CONFIGURAÇÃO
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = (
    Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "DigitalSignage"
    if os.name == "nt" else Path("/var/lib/digital-signage")
)
# Keep an existing Windows installation working until its explicit data migration.
# New installations and Linux always default to an external data directory.
LEGACY_WINDOWS = os.name == "nt" and (BASE_DIR / "digital_signage.db").is_file()
DATA_DIR = BASE_DIR if LEGACY_WINDOWS else DEFAULT_DATA_DIR

MEDIA_DIR = Path(os.getenv("DS_MEDIA_DIR", str(DATA_DIR / "media"))).expanduser().resolve()
UPLOAD_DIR = MEDIA_DIR / "uploads"
RENDER_DIR = MEDIA_DIR / "rendered"
RELEASE_DIR = Path(os.getenv("DS_RELEASE_DIR", str(DATA_DIR / "releases"))).expanduser().resolve()
UPLOAD_TEMP_DIR = Path(
    os.getenv("DS_UPLOAD_TEMP_DIR", str(DATA_DIR / "upload-tmp"))
).expanduser().resolve()


def positive_env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} deve ser um inteiro positivo.") from exc
    if value <= 0:
        raise RuntimeError(f"{name} deve ser um inteiro positivo.")
    return value


MAX_IMAGE_BYTES = positive_env_int("DS_MAX_IMAGE_MB", 25) * 1024 * 1024
MAX_VIDEO_BYTES = positive_env_int("DS_MAX_VIDEO_MB", 1024) * 1024 * 1024
MAX_RELEASE_BYTES = positive_env_int("DS_MAX_RELEASE_MB", 512) * 1024 * 1024
MAX_FILES_PER_UPLOAD = positive_env_int("DS_MAX_FILES_PER_UPLOAD", 20)
MAX_REQUEST_BYTES = positive_env_int("DS_MAX_REQUEST_MB", 1536) * 1024 * 1024

DB_FILE = Path(os.getenv("DS_DB_FILE", str(DATA_DIR / "digital_signage.db"))).expanduser().resolve()
DB_WAS_NEW = not DB_FILE.exists()
CREDENTIALS_FILE = Path(os.getenv(
    "DS_INITIAL_CREDENTIALS_FILE",
    str((DEFAULT_DATA_DIR if DB_FILE.is_relative_to(BASE_DIR) else DB_FILE.parent)
        / "first_admin_credentials.txt"),
)).expanduser().resolve()
DB_FILE.parent.mkdir(parents=True, exist_ok=True)

MEDIA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(exist_ok=True)
RENDER_DIR.mkdir(exist_ok=True)
RELEASE_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_TEMP_DIR.mkdir(parents=True, exist_ok=True)


app = FastAPI(title="Digital Signage")
# Only packaged public assets; never expose operational data directories.
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static"), check_dir=False), name="static")

templates = Jinja2Templates(
    directory=str(BASE_DIR / "templates")
)
APP_TIMEZONE = ZoneInfo("America/Sao_Paulo")
APP_VERSION = "2.3.1-stability-night"
OFFICIAL_PLAYER_VERSION = "3.2.2-stable"
MIN_PASSWORD_LENGTH = 12
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_FAILURES = 5


@app.get("/health")
def health():
    """Readiness only: never creates a database or writes a probe file."""
    database = "unavailable"
    try:
        with closing(sqlite3.connect(DB_FILE.as_uri() + "?mode=ro", uri=True, timeout=2)) as conn:
            conn.execute("PRAGMA query_only=ON")
            conn.execute("SELECT id FROM users LIMIT 1").fetchone()
        database = "ok"
    except (OSError, sqlite3.Error):
        pass
    directories = {
        name: "ok" if path.is_dir() and os.access(path, os.R_OK | os.W_OK | os.X_OK) else "unavailable"
        for name, path in (("media", MEDIA_DIR), ("releases", RELEASE_DIR))
    }
    ready = database == "ok" and all(value == "ok" for value in directories.values())
    return JSONResponse(
        {"status": "ok" if ready else "unavailable", "app_version": APP_VERSION,
         "database": database, **directories,
         "timestamp": datetime.now(APP_TIMEZONE).isoformat()},
        status_code=200 if ready else 503,
        headers={"Cache-Control": "no-store"},
    )

# =========================================================
# BANCO DE DADOS
# =========================================================

def get_db():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")

    return conn


def ensure_column(conn, table, column, definition):
    columns = conn.execute(
        f"PRAGMA table_info({table})"
    ).fetchall()

    existing = {
        row["name"]
        for row in columns
    }

    if column not in existing:
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        )



def build_playlist_snapshot(conn, playlist_id: int):
    """Serializa o rascunho atual da playlist em um snapshot imutável."""
    playlist = conn.execute(
        "SELECT id, name FROM playlists WHERE id = ?",
        (playlist_id,)
    ).fetchone()

    if not playlist:
        return None

    rows = conn.execute(
        """
        SELECT
            playlist_items.id AS item_id,
            playlist_items.position,
            playlist_items.duration_sec,
            contents.id AS content_id,
            contents.name AS content_name,
            contents.filename,
            contents.content_type,
            contents.pages_json
        FROM playlist_items
        INNER JOIN contents
            ON playlist_items.content_id = contents.id
        WHERE playlist_items.playlist_id = ?
        AND contents.archived_at IS NULL
        AND contents.content_type IN ('image', 'video')
        ORDER BY playlist_items.position
        """,
        (playlist_id,)
    ).fetchall()

    items = []

    for row in rows:
        try:
            parsed = json.loads(row["pages_json"] or "[]")
        except json.JSONDecodeError:
            parsed = []

        pages = []
        if row["content_type"] == "image" and isinstance(parsed, list):
            pages = [page for page in parsed if isinstance(page, str)]

        items.append({
            "item_id": row["item_id"],
            "position": row["position"],
            "duration_sec": row["duration_sec"],
            "content_id": row["content_id"],
            "content_name": row["content_name"],
            "filename": row["filename"],
            "content_type": row["content_type"],
            "pages": pages,
        })

    return {
        "playlist_id": playlist["id"],
        "playlist_name": playlist["name"],
        "items": items,
    }


def snapshot_signature(snapshot) -> str:
    if not isinstance(snapshot, dict):
        return ""
    return hashlib.sha256(
        json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def snapshot_to_player_playlist(
    snapshot,
    archived_ids=None
):
    """Converte o snapshot publicado para o formato já consumido pelo Raspberry."""
    if not isinstance(snapshot, dict):
        return []

    result = []

    archived_ids = archived_ids or set()

    for item in snapshot.get("items", []):
        if not isinstance(item, dict):
            continue

        content_id = item.get("content_id")

        if content_id in archived_ids:
            continue

        content_type = item.get("content_type")
        filename = item.get("filename")

        if content_type not in {"image", "video"} or not isinstance(filename, str):
            continue

        pages = []
        if content_type == "image":
            raw_pages = item.get("pages", [])
            if isinstance(raw_pages, list):
                pages = [
                    "/media/" + page.replace("\\", "/")
                    for page in raw_pages
                    if isinstance(page, str)
                ]

        result.append({
            "id": item.get("item_id"),
            "position": item.get("position"),
            "duration_sec": item.get("duration_sec", 10),
            "content": {
                "id": item.get("content_id"),
                "name": item.get("content_name") or "Conteúdo",
                "filename": filename,
                "content_type": content_type,
                "url": "/media/" + filename.replace("\\", "/"),
                "pages": pages,
            }
        })

    return result


# =========================================================
# SEGURANÇA / AUTENTICAÇÃO / AUDITORIA / DEVICE AUTH
# =========================================================

SESSION_COOKIE_NAME = "ds_session"
LOGIN_CSRF_COOKIE_NAME = "ds_login_csrf"
CSRF_FIELD_NAME = "csrf_token"
SESSION_TTL_SECONDS = int(os.getenv("DS_SESSION_TTL_SECONDS", "43200"))
PASSWORD_ITERATIONS = int(os.getenv("DS_PASSWORD_ITERATIONS", "210000"))
PBKDF_ALGORITHM = "sha256"
ROLE_LABELS = {
    "admin": "Administrador",
    "operator": "Operador",
    "editor": "Editor",
    "viewer": "Visualizador",
}
ROLE_LEVEL = {"viewer": 10, "editor": 20, "operator": 30, "admin": 40}
PROTECTED_PREFIXES = (
    "/admin",
    "/gallery",
    "/playlists",
    "/playlist/",
    "/schedules",
    "/players",
    "/releases",
    "/users",
    "/audit",
    "/preview",
    "/logout",
    "/player",
)
PROTECTED_API_PREFIXES = (
    "/api/tvs/status",
    "/api/contents",
    "/api/playlist",
    "/api/web-player",
)
PUBLIC_PREFIXES = (
    "/favicon.ico",
    "/bootstrap/",
    "/docs",
    "/openapi.json",
    "/redoc",
)
DEVICE_API_PREFIXES = (
    "/api/player/",
    "/api/tv/heartbeat",
)


def normalize_role(role: str) -> str:
    role = str(role or "viewer").strip().lower()
    return role if role in ROLE_LABELS else "viewer"


def password_hash(password: str) -> str:
    password = str(password or "")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        PBKDF_ALGORITHM,
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PASSWORD_ITERATIONS,
    ).hex()
    return f"pbkdf2_{PBKDF_ALGORITHM}${PASSWORD_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored_hash: str | None) -> bool:
    if not stored_hash:
        return False
    try:
        scheme, iterations, salt, digest = str(stored_hash).split("$", 3)
        if scheme != f"pbkdf2_{PBKDF_ALGORITHM}":
            return False
        candidate = hashlib.pbkdf2_hmac(
            PBKDF_ALGORITHM,
            str(password or "").encode("utf-8"),
            salt.encode("utf-8"),
            int(iterations),
        ).hex()
        return hmac.compare_digest(candidate, digest)
    except Exception:
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def csrf_token_for_session(session_token: str) -> str:
    """Derive a CSRF token that rotates with the opaque login session."""
    return hmac.new(
        str(session_token or "").encode("utf-8"),
        b"digital-signage:csrf:v1",
        hashlib.sha256,
    ).hexdigest()


def csrf_token(request: Request) -> str:
    session_token = request.cookies.get(SESSION_COOKIE_NAME, "")
    return csrf_token_for_session(session_token) if session_token else ""


templates.env.globals["csrf_token"] = csrf_token


def role_allows(user_role: str, minimum_role: str) -> bool:
    return ROLE_LEVEL.get(normalize_role(user_role), 0) >= ROLE_LEVEL.get(normalize_role(minimum_role), 999)


def current_user_from_request(request: Request):
    if hasattr(request.state, "user") and request.state.user:
        return request.state.user
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT users.id, users.username, users.full_name, users.role, users.is_active,
                   sessions.expires_at
            FROM sessions
            INNER JOIN users ON sessions.user_id = users.id
            WHERE sessions.token_hash = ?
            """,
            (hash_token(token),),
        ).fetchone()
        now = time.time()
        if not row or not row["is_active"] or float(row["expires_at"] or 0) < now:
            return None
        user = {
            "id": row["id"],
            "username": row["username"],
            "full_name": row["full_name"],
            "role": normalize_role(row["role"]),
            "role_label": ROLE_LABELS.get(normalize_role(row["role"]), "Usuário"),
        }
        request.state.user = user
        return user
    finally:
        conn.close()


def require_user(request: Request):
    user = current_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Login obrigatório.")
    return user


def require_role(request: Request, minimum_role: str):
    user = require_user(request)
    if not role_allows(user.get("role"), minimum_role):
        raise HTTPException(status_code=403, detail="Permissão insuficiente.")
    return user


# RBAC + escopo de TV (deny by default). Operações são validadas na rota.
def allowed_tv_ids(conn, user):
    if user["role"] == "admin":
        return {r["tv_id"] for r in conn.execute("SELECT tv_id FROM tvs")}
    return {r["tv_id"] for r in conn.execute(
        "SELECT tv_id FROM user_tv_permissions WHERE user_id = ?", (user["id"],)
    )}


def require_tv_access(request: Request, tv_id: str, minimum_role="viewer"):
    user = require_role(request, minimum_role)
    tv_id = str(tv_id).strip().upper()
    conn = get_db()
    try:
        if not conn.execute("SELECT 1 FROM tvs WHERE tv_id = ?", (tv_id,)).fetchone():
            raise HTTPException(404, "TV não encontrada.")
        if tv_id not in allowed_tv_ids(conn, user):
            raise HTTPException(403, "Você não possui acesso a esta TV.")
    finally:
        conn.close()
    return user


def playlist_tv_ids(conn, playlist_id):
    # Uso atual E futuro: uma edição de playlist compartilhada afeta todas essas TVs.
    return {r["tv_id"] for r in conn.execute(
        """SELECT tv_id FROM tv_playlists WHERE playlist_id = ?
        UNION SELECT tv_id FROM schedules WHERE playlist_id = ?""",
        (playlist_id, playlist_id),
    )}


def can_access_playlist(conn, user, playlist_id, edit=False):
    row = conn.execute("SELECT id, owner_user_id FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
    if not row:
        return False
    if user["role"] == "admin":
        return True
    if edit and not role_allows(user["role"], "editor"):
        return False
    linked = playlist_tv_ids(conn, playlist_id)
    allowed = allowed_tv_ids(conn, user)
    # Com vínculo, só edita se TODAS as TVs envolvidas estiverem permitidas.
    if linked:
        return linked.issubset(allowed) if edit else bool(linked.intersection(allowed))
    # Rascunhos sem TV são privados do criador (antigos: somente administrador).
    return row["owner_user_id"] == user["id"]


def require_playlist_access(request: Request, playlist_id: int, minimum_role="viewer"):
    user = require_role(request, minimum_role)
    conn = get_db()
    try:
        if not conn.execute("SELECT 1 FROM playlists WHERE id=?", (playlist_id,)).fetchone():
            raise HTTPException(404, "Playlist não encontrada.")
        if not can_access_playlist(conn, user, playlist_id, edit=minimum_role != "viewer"):
            raise HTTPException(403, "Sem permissão para esta playlist ou ela é compartilhada com outra TV.")
    finally:
        conn.close()
    return user


def require_item_playlist_access(request: Request, item_id: int, minimum_role="editor"):
    conn = get_db()
    try:
        row = conn.execute("SELECT playlist_id FROM playlist_items WHERE id=?", (item_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "Item não encontrado.")
    return require_playlist_access(request, row["playlist_id"], minimum_role)


def content_tv_ids(conn, content_id):
    return {r["tv_id"] for r in conn.execute(
        """SELECT tp.tv_id FROM tv_playlists tp
           JOIN playlist_items pi ON pi.playlist_id=tp.playlist_id
           WHERE pi.content_id=?
           UNION SELECT s.tv_id FROM schedules s
           JOIN playlist_items pi ON pi.playlist_id=s.playlist_id
           WHERE pi.content_id=?
           UNION SELECT tv_id FROM tv_contents WHERE content_id=?""",
        (content_id, content_id, content_id),
    )}


def can_access_content(conn, user, content_id, edit=False):
    row = conn.execute("SELECT id, owner_user_id FROM contents WHERE id=?", (content_id,)).fetchone()
    if not row:
        return False
    if user["role"] == "admin":
        return True
    if edit and not role_allows(user["role"], "editor"):
        return False
    linked = content_tv_ids(conn, content_id)
    allowed = allowed_tv_ids(conn, user)
    if edit:
        # Não permitir que se altere a biblioteca de outro autor ou uma TV não autorizada.
        return row["owner_user_id"] == user["id"] and linked.issubset(allowed)
    return row["owner_user_id"] == user["id"] or bool(linked.intersection(allowed))


def require_content_access(request: Request, content_id: int, minimum_role="viewer"):
    user = require_role(request, minimum_role)
    conn = get_db()
    try:
        if not conn.execute("SELECT 1 FROM contents WHERE id=?", (content_id,)).fetchone():
            raise HTTPException(404, "Conteúdo não encontrado.")
        if not can_access_content(conn, user, content_id, edit=minimum_role != "viewer"):
            raise HTTPException(403, "Sem permissão para este conteúdo.")
    finally:
        conn.close()
    return user


def require_schedule_access(request: Request, schedule_id: int):
    conn = get_db()
    try:
        row = conn.execute("SELECT tv_id, playlist_id FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "Agendamento não encontrado.")
    require_tv_access(request, row["tv_id"], "operator")
    # Não deixar que um agendamento em TV permitida altere playlist compartilhada proibida.
    require_playlist_access(request, row["playlist_id"], "operator")


def sync_user_tv_permissions(conn, user_id: int, tv_ids, role: str):
    ids = {str(tv_id).strip().upper() for tv_id in tv_ids if str(tv_id).strip()}
    existing = {r["tv_id"] for r in conn.execute("SELECT tv_id FROM tvs")}
    if ids - existing:
        raise HTTPException(400, "Uma ou mais TVs selecionadas não existem.")
    conn.execute("DELETE FROM user_tv_permissions WHERE user_id = ?", (user_id,))
    if role != "admin":
        conn.executemany(
            "INSERT INTO user_tv_permissions (user_id, tv_id, granted_at) VALUES (?, ?, ?)",
            [(user_id, tv_id, time.time()) for tv_id in sorted(ids)],
        )


def audit_event(conn, request: Request | None, action: str, target_type: str = "system", target_id: str = "", detail: str = ""):
    user_id = None
    username = "system"
    ip_address = None
    if request is not None:
        try:
            user = current_user_from_request(request)
            if user:
                user_id = user.get("id")
                username = user.get("username") or "system"
        except Exception:
            pass
        try:
            ip_address = request.client.host if request.client else None
        except Exception:
            ip_address = None
    conn.execute(
        """
        INSERT INTO audit_log
        (created_at, user_id, username, action, target_type, target_id, detail, ip_address)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (time.time(), user_id, username, action[:100], target_type[:80], str(target_id or "")[:120], str(detail or "")[:2000], ip_address),
    )


def ensure_initial_admin(conn):
    count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    if count:
        return
    if not DB_WAS_NEW:
        raise RuntimeError("Banco existente sem usuários: recuperação administrativa explícita necessária.")
    username = os.getenv("DS_INITIAL_ADMIN_USER", "admin").strip() or "admin"
    configured_password = os.getenv("DS_INITIAL_ADMIN_PASSWORD")
    password = configured_password or secrets.token_urlsafe(20)
    if not configured_password:
        if CREDENTIALS_FILE.is_relative_to(BASE_DIR):
            raise RuntimeError("DS_INITIAL_CREDENTIALS_FILE deve ficar fora do código.")
        CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Exclusive creation: never overwrite credentials from a previous attempt.
        # On Windows the external directory must also have a restricted user ACL.
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(CREDENTIALS_FILE, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(
                "Digital Signage - primeiro acesso\n"
                f"Usuário: {username}\nSenha: {password}\n"
                "Troque a senha e remova este arquivo após o primeiro acesso.\n"
            )
    conn.execute(
        """
        INSERT INTO users
        (username, full_name, password_hash, role, is_active, must_change_password, created_at)
        VALUES (?, ?, ?, 'admin', 1, 1, ?)
        """,
        (username, "Administrador inicial", password_hash(password), time.time()),
    )
    print("[AUTH] Administrador inicial criado; senha não registrada nos logs.")


def create_session(conn, user_id: int) -> str:
    raw = secrets.token_urlsafe(36)
    now = time.time()
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
    conn.execute(
        """
        INSERT INTO sessions
        (token_hash, user_id, created_at, expires_at)
        VALUES (?, ?, ?, ?)
        """,
        (hash_token(raw), user_id, now, now + SESSION_TTL_SECONDS),
    )
    return raw


def destroy_session(conn, token: str | None):
    if not token:
        return
    conn.execute("DELETE FROM sessions WHERE token_hash = ?", (hash_token(token),))


def device_token_from_request(request: Request) -> str:
    header = request.headers.get("Authorization", "").strip()
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.headers.get("X-Device-Token", "").strip()


def require_device_auth(request: Request, conn, tv_id: str):
    row = conn.execute(
        "SELECT tv_id, device_token_hash FROM tvs WHERE tv_id = ?",
        (tv_id.strip().upper(),),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="TV não encontrada.")
    stored = str(row["device_token_hash"] or "").strip()
    # Compatibilidade temporária: players antigos sem token continuam funcionando até a TI gerar credencial.
    if not stored:
        if os.getenv("DS_REQUIRE_DEVICE_TOKEN", "0") == "1":
            raise HTTPException(status_code=401, detail="Provisione esta TV com um token antes de utilizar a API.")
        return True
    received = device_token_from_request(request)
    if not received or not hmac.compare_digest(hash_token(received), stored):
        raise HTTPException(status_code=401, detail="Credencial do player inválida.")
    return True


def authenticated_device_tv_id(request: Request, conn) -> str | None:
    """Return the TV bound to a valid device credential, without URL secrets."""
    received = device_token_from_request(request)
    if not received:
        return None
    row = conn.execute(
        "SELECT tv_id FROM tvs WHERE device_token_hash = ?",
        (hash_token(received),),
    ).fetchone()
    return str(row["tv_id"]) if row else None


def enqueue_player_command(conn, request: Request | None, tv_id: str, command: str, args: dict | None = None):
    args = args if isinstance(args, dict) else {}
    conn.execute(
        """
        INSERT INTO player_commands
        (tv_id, command, args_json, status, progress, created_at)
        VALUES (?, ?, ?, 'pending', 0, ?)
        """,
        (tv_id.strip().upper(), command.strip(), json.dumps(args, ensure_ascii=False), time.time()),
    )
    command_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    audit_event(conn, request, f"player.command.{command}", "tv", tv_id, json.dumps(args, ensure_ascii=False))
    return command_id


def list_active_releases(conn):
    return conn.execute(
        """
        SELECT * FROM player_releases
        WHERE status IN ('draft', 'published', 'stable')
        ORDER BY created_at DESC
        """
    ).fetchall()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sync_packaged_releases(conn):
    """Registra releases empacotadas na pasta releases/ sem exigir upload manual."""
    for path in sorted(RELEASE_DIR.glob("*.zip")):
        try:
            with zipfile.ZipFile(path, "r") as zf:
                manifest_name = next((n for n in zf.namelist() if n.endswith("manifest.json")), None)
                if not manifest_name:
                    continue
                manifest = json.loads(zf.read(manifest_name).decode("utf-8"))
            version = str(manifest.get("version") or "").strip()
            if not version:
                continue
            existing = conn.execute("SELECT id FROM player_releases WHERE version = ?", (version,)).fetchone()
            if existing:
                continue
            channel = str(manifest.get("channel") or "stable").strip() or "stable"
            status = str(manifest.get("status") or ("stable" if channel == "stable" else "draft")).strip()
            conn.execute(
                """
                INSERT INTO player_releases
                (version, channel, filename, original_filename, sha256, size_bytes, status, notes, created_at, published_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version, channel, path.name, path.name, sha256_path(path), path.stat().st_size,
                    status, str(manifest.get("notes") or "Release empacotada com o servidor."),
                    time.time(), time.time() if status == "stable" else None,
                ),
            )
        except Exception as exc:
            print(f"[RELEASE] Falha ao registrar {path.name}: {exc}")


def init_db():
    global DB_WAS_NEW

    conn = get_db()

    # TVs
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tvs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tv_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            location TEXT,
            last_seen REAL
        )
    """)

    # Conteúdos
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            filename TEXT NOT NULL,
            content_type TEXT NOT NULL,
            created_at REAL NOT NULL
        )
    """)

    # Relação TV -> Conteúdo
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tv_contents (
            tv_id TEXT PRIMARY KEY,
            content_id INTEGER,
            FOREIGN KEY(content_id) REFERENCES contents(id)
        )
    """)

    # Migração do banco antigo
    ensure_column(
        conn,
        "contents",
        "original_filename",
        "TEXT"
    )

    ensure_column(
        conn,
        "contents",
        "pages_json",
        "TEXT"
    )

    # Exclusão lógica de conteúdo.
    # NULL = ativo
    # timestamp = arquivado
    ensure_column(
        conn,
        "contents",
        "archived_at",
        "REAL"
    )

    # Informações de monitoramento das TVs
    ensure_column(
        conn,
        "tvs",
        "ip_address",
        "TEXT"
    )

    ensure_column(
        conn,
        "tvs",
        "current_playlist_id",
        "INTEGER"
    )

    ensure_column(
        conn,
        "tvs",
        "current_content_id",
        "INTEGER"
    )

    ensure_column(
        conn,
        "tvs",
        "current_content_name",
        "TEXT"
    )

    ensure_column(
        conn,
        "tvs",
        "player_version",
        "TEXT"
    )

    ensure_column(
        conn,
        "tvs",
        "last_sync_at",
        "REAL"
    )

    ensure_column(
        conn,
        "tvs",
        "last_error",
        "TEXT"
    )


    # Runtime mais recente recebido de cada player.
    # Payload em JSON para permitir novos campos sem migração por versão.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tv_runtime_status (
            tv_id TEXT PRIMARY KEY,
            updated_at REAL NOT NULL,
            payload_json TEXT NOT NULL,
            FOREIGN KEY(tv_id) REFERENCES tvs(tv_id)
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_tv_runtime_updated_at
        ON tv_runtime_status(updated_at)
    """)


    # =====================================================
    # MONITORAMENTO OPERACIONAL v3
    # =====================================================

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tv_runtime_status (
            tv_id TEXT PRIMARY KEY,
            updated_at REAL NOT NULL,
            payload_json TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tv_monitor_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            recommended_player_version TEXT NOT NULL DEFAULT '3.2.2-stable',
            offline_seconds INTEGER NOT NULL DEFAULT 30,
            disk_warning_mb INTEGER NOT NULL DEFAULT 1024,
            sync_warning_seconds INTEGER NOT NULL DEFAULT 120,
            updated_at REAL
        )
    """)

    conn.execute("""
        INSERT OR IGNORE INTO tv_monitor_settings
        (
            id,
            recommended_player_version,
            offline_seconds,
            disk_warning_mb,
            sync_warning_seconds,
            updated_at
        )
        VALUES (1, '3.0.0-stable', 30, 1024, 120, ?)
    """, (time.time(),))

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tv_event_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tv_id TEXT NOT NULL,
            created_at REAL NOT NULL,
            event_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            title TEXT NOT NULL,
            detail TEXT
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_tv_event_log_tv_time
        ON tv_event_log(tv_id, created_at DESC)
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tv_monitor_state (
            tv_id TEXT PRIMARY KEY,
            health TEXT,
            reason TEXT,
            playlist_id INTEGER,
            playlist_version TEXT,
            sync_status TEXT,
            player_version TEXT,
            browser_running INTEGER,
            updated_at REAL NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tv_maintenance (
            tv_id TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 0,
            note TEXT,
            updated_at REAL NOT NULL
        )
    """)

    # =====================================================
    # CORPORATE v5 - METADATA / VERSIONAMENTO
    # =====================================================

    conn.execute("""
        CREATE TABLE IF NOT EXISTS content_metadata (
            content_id INTEGER PRIMARY KEY,
            size_bytes INTEGER,
            width INTEGER,
            height INTEGER,
            duration_sec REAL,
            codec TEXT,
            pix_fmt TEXT,
            bitrate INTEGER,
            validation_status TEXT,
            validation_message TEXT,
            updated_at REAL NOT NULL,
            FOREIGN KEY(content_id) REFERENCES contents(id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS playlist_publication_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            playlist_id INTEGER NOT NULL,
            version INTEGER NOT NULL,
            published_at REAL NOT NULL,
            snapshot_json TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'publish',
            UNIQUE(playlist_id, version),
            FOREIGN KEY(playlist_id) REFERENCES playlists(id)
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_playlist_history
        ON playlist_publication_history(playlist_id, version DESC)
    """)


    # =====================================================
    # SECURITY & REMOTE MANAGEMENT v2.1
    # =====================================================

    ensure_column(conn, "tvs", "device_token_hash", "TEXT")
    ensure_column(conn, "tvs", "device_token_last_rotated_at", "REAL")
    ensure_column(conn, "tvs", "update_channel", "TEXT DEFAULT 'lab'")
    ensure_column(conn, "tvs", "provision_code_hash", "TEXT")
    ensure_column(conn, "tvs", "provision_code_expires_at", "REAL")
    ensure_column(conn, "tvs", "enrolled_at", "REAL")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            full_name TEXT,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'viewer',
            is_active INTEGER NOT NULL DEFAULT 1,
            must_change_password INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_hash TEXT UNIQUE NOT NULL,
            user_id INTEGER NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_sessions_token
        ON sessions(token_hash)
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS login_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            ip_address TEXT NOT NULL,
            attempted_at REAL NOT NULL
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_login_attempts_lookup
        ON login_attempts(username, ip_address, attempted_at)
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL NOT NULL,
            user_id INTEGER,
            username TEXT,
            action TEXT NOT NULL,
            target_type TEXT,
            target_id TEXT,
            detail TEXT,
            ip_address TEXT
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_log_created
        ON audit_log(created_at DESC)
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS player_commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tv_id TEXT NOT NULL,
            command TEXT NOT NULL,
            args_json TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            progress INTEGER NOT NULL DEFAULT 0,
            message TEXT,
            result_json TEXT,
            created_at REAL NOT NULL,
            dispatched_at REAL,
            completed_at REAL,
            FOREIGN KEY(tv_id) REFERENCES tvs(tv_id)
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_player_commands_tv_status
        ON player_commands(tv_id, status, created_at)
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS player_releases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version TEXT NOT NULL UNIQUE,
            channel TEXT NOT NULL DEFAULT 'lab',
            filename TEXT NOT NULL,
            original_filename TEXT,
            sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            notes TEXT,
            created_by INTEGER,
            created_at REAL NOT NULL,
            published_at REAL,
            FOREIGN KEY(created_by) REFERENCES users(id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS player_update_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            release_id INTEGER NOT NULL,
            tv_id TEXT NOT NULL,
            command_id INTEGER,
            status TEXT NOT NULL DEFAULT 'queued',
            created_at REAL NOT NULL,
            updated_at REAL,
            FOREIGN KEY(release_id) REFERENCES player_releases(id),
            FOREIGN KEY(command_id) REFERENCES player_commands(id)
        )
    """)

    # Migração aditiva; não toca nas TVs, mídias, playlists nem usuários existentes.
    conn.execute("""CREATE TABLE IF NOT EXISTS user_tv_permissions (
        user_id INTEGER NOT NULL,
        tv_id TEXT NOT NULL,
        granted_at REAL NOT NULL,
        PRIMARY KEY (user_id, tv_id),
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(tv_id) REFERENCES tvs(tv_id)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_user_tv_permissions_tv ON user_tv_permissions(tv_id)")
    ensure_column(conn, "contents", "owner_user_id", "INTEGER")
    ensure_initial_admin(conn)

    # TV padrão
    existing_tv = conn.execute(
        "SELECT id FROM tvs WHERE tv_id = ?",
        ("TV01",)
    ).fetchone()

    if DB_WAS_NEW and not existing_tv:
        conn.execute(
            """
            INSERT INTO tvs
            (tv_id, name, location)
            VALUES (?, ?, ?)
            """,
            (
                "TV01",
                "TV Produção 01",
                "Produção"
            )
        )

    # Compatibilidade com imagens antigas
    rows = conn.execute("""
        SELECT id, filename, content_type
        FROM contents
        WHERE pages_json IS NULL
    """).fetchall()

    for row in rows:

        pages = []

        if row["content_type"] == "image":
            pages = [row["filename"]]

        conn.execute(
            """
            UPDATE contents
            SET pages_json = ?
            WHERE id = ?
            """,
            (
                json.dumps(pages),
                row["id"]
            )
        )

    # Playlists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS playlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at REAL NOT NULL
        )
    """)

    ensure_column(conn, "playlists", "owner_user_id", "INTEGER")

    # Itens das playlists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS playlist_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            playlist_id INTEGER NOT NULL,
            content_id INTEGER NOT NULL,
            position INTEGER NOT NULL,
            duration_sec INTEGER NOT NULL DEFAULT 10,
            FOREIGN KEY(playlist_id) REFERENCES playlists(id),
            FOREIGN KEY(content_id) REFERENCES contents(id)
        )
    """)

    # Playlist atribuída à TV
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tv_playlists (
            tv_id TEXT PRIMARY KEY,
            playlist_id INTEGER NOT NULL,
            FOREIGN KEY(playlist_id) REFERENCES playlists(id)
        )
    """)

    # Snapshot publicado de cada playlist.
    # playlist_items continua sendo o RASCUNHO editável.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS playlist_publications (
            playlist_id INTEGER PRIMARY KEY,
            version INTEGER NOT NULL DEFAULT 1,
            published_at REAL NOT NULL,
            snapshot_json TEXT NOT NULL,
            FOREIGN KEY(playlist_id) REFERENCES playlists(id)
        )
    """)

    # =====================================================
    # AGENDAMENTOS
    # =====================================================

    conn.execute("""
        CREATE TABLE IF NOT EXISTS schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tv_id TEXT NOT NULL,
            playlist_id INTEGER NOT NULL,
            day_of_week INTEGER NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,

            FOREIGN KEY(tv_id)
                REFERENCES tvs(tv_id),

            FOREIGN KEY(playlist_id)
                REFERENCES playlists(id)
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS
        idx_schedules_tv_day
        ON schedules(tv_id, day_of_week, enabled)
    """)

    # Migração v1.3: playlists já existentes com itens tornam-se
    # versão publicada 1 para não interromper TVs após a atualização.
    legacy_playlists = conn.execute("""
        SELECT DISTINCT playlists.id
        FROM playlists
        INNER JOIN playlist_items
            ON playlist_items.playlist_id = playlists.id
        LEFT JOIN playlist_publications
            ON playlist_publications.playlist_id = playlists.id
        WHERE playlist_publications.playlist_id IS NULL
    """).fetchall()

    for legacy in legacy_playlists:
        snapshot = build_playlist_snapshot(conn, legacy["id"])
        if snapshot and snapshot.get("items"):
            conn.execute(
                """
                INSERT INTO playlist_publications
                (playlist_id, version, published_at, snapshot_json)
                VALUES (?, 1, ?, ?)
                """,
                (
                    legacy["id"],
                    time.time(),
                    json.dumps(snapshot, ensure_ascii=False)
                )
            )

    # Preserva a publicação atual como ponto inicial do histórico.
    conn.execute("""
        INSERT OR IGNORE INTO playlist_publication_history
        (playlist_id, version, published_at, snapshot_json, source)
        SELECT playlist_id, version, published_at, snapshot_json, 'migrated'
        FROM playlist_publications
    """)

    sync_packaged_releases(conn)

    # Migração controlada: somente os defaults conhecidos das versões anteriores.
    current_monitor = conn.execute(
        "SELECT recommended_player_version FROM tv_monitor_settings WHERE id = 1"
    ).fetchone()
    if current_monitor and str(current_monitor["recommended_player_version"] or "").strip() in {
        "", "3.0.0-stable", "3.1.0-remote-mgmt", "3.2.0-stable",
        "3.2.1-stable", "3.2.2-stability-preview"
    }:
        conn.execute(
            "UPDATE tv_monitor_settings SET recommended_player_version = ?, updated_at = ? WHERE id = 1",
            (OFFICIAL_PLAYER_VERSION, time.time()),
        )

    conn.commit()
    conn.close()
    DB_WAS_NEW = False


init_db()
# Migração aditiva, sem alterar dados existentes.
_stability_db = get_db()
try:
    stability_init_schema(_stability_db)
finally:
    _stability_db.close()

# =========================================================
# MIDDLEWARE DE SESSÃO
# =========================================================

@app.middleware("http")
async def security_middleware(request: Request, call_next):
    path = request.url.path

    async def form_csrf_token() -> str:
        body = await request.body()
        try:
            form = await request.form()
            request.state.csrf_form = dict(form)
            supplied = str(form.get(CSRF_FIELD_NAME) or "")
        except Exception:
            request.state.csrf_form = {}
            supplied = ""
        delivered = False

        async def replay_body():
            nonlocal delivered
            if delivered:
                return {"type": "http.request", "body": b"", "more_body": False}
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        request._receive = replay_body
        return supplied

    is_upload = path in {"/admin/upload", "/releases"} or (
        path.startswith("/api/playlist/") and path.endswith("/upload")
    )
    if request.method in {"POST", "PUT", "PATCH"} and is_upload:
        raw_length = request.headers.get("Content-Length")
        if raw_length:
            try:
                if int(raw_length) > MAX_REQUEST_BYTES:
                    return JSONResponse(
                        {"detail": "Requisição de upload excede o limite configurado."},
                        status_code=413,
                    )
            except ValueError:
                return JSONResponse({"detail": "Content-Length inválido."}, status_code=400)
    if path == "/login" and request.method == "POST":
        supplied = await form_csrf_token()
        expected = request.cookies.get(LOGIN_CSRF_COOKIE_NAME, "")
        if not expected or not supplied or not hmac.compare_digest(expected, supplied):
            submitted = getattr(request.state, "csrf_form", {})
            return render_login_page(
                request,
                next_target=str(submitted.get("next") or "/admin"),
                error="Sua página de login expirou. Tente novamente.",
                status_code=403,
            )

    if path in {"/health", "/login"} or any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES):
        return await call_next(request)

    def _match_prefix(prefix: str) -> bool:
        return path.startswith(prefix) if prefix.endswith("/") else (path == prefix or path.startswith(prefix + "/"))
    is_protected = any(_match_prefix(prefix) for prefix in PROTECTED_PREFIXES)
    is_protected_api = any(path.startswith(prefix) for prefix in PROTECTED_API_PREFIXES)

    if is_protected or is_protected_api:
        user = current_user_from_request(request)
        if not user:
            if path.startswith("/api/"):
                return JSONResponse({"detail": "Login obrigatório."}, status_code=401)
            next_url = request.url.path
            if request.url.query:
                next_url += "?" + request.url.query
            return RedirectResponse("/login?" + urlencode({"next": next_url}), status_code=303)
        request.state.user = user
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            supplied = request.headers.get("X-CSRF-Token", "")
            if not supplied:
                supplied = await form_csrf_token()
            expected = csrf_token_for_session(
                request.cookies.get(SESSION_COOKIE_NAME, "")
            )
            if not supplied or not hmac.compare_digest(expected, supplied):
                if path.startswith("/api/"):
                    return JSONResponse(
                        {"detail": "Token CSRF ausente ou inválido."},
                        status_code=403,
                    )
                return Response(
                    "Token CSRF ausente ou inválido.",
                    status_code=403,
                    media_type="text/plain; charset=utf-8",
                )
        # Bloqueio antecipado para impedir inclusive validação das telas reservadas.
        if any(_match_prefix(prefix) for prefix in ("/users", "/audit", "/releases", "/players/settings")):
            if user["role"] != "admin":
                if path.startswith("/api/"):
                    return JSONResponse({"detail": "Acesso exclusivo do administrador."}, status_code=403)
                return Response("Acesso exclusivo do administrador.", status_code=403)
    else:
        current_user_from_request(request)

    response = await call_next(request)
    if is_protected or is_protected_api:
        response.headers["Cache-Control"] = "no-store"
    return response


# =========================================================
# LOGIN / LOGOUT
# =========================================================

def render_login_page(
    request: Request,
    next_target: str = "/admin",
    error: str = "",
    status_code: int = 200,
):
    # Keep the token stable across refreshes and multiple open login tabs.
    # A new token is created only when the browser has none (or after login).
    login_csrf = request.cookies.get(LOGIN_CSRF_COOKIE_NAME) or secrets.token_urlsafe(32)
    response = templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "app_version": APP_VERSION,
            "next": next_target or "/admin",
            "error": error,
            "login_csrf_token": login_csrf,
        },
        status_code=status_code,
    )
    response.set_cookie(
        LOGIN_CSRF_COOKIE_NAME,
        login_csrf,
        httponly=True,
        samesite="lax",
        secure=(request.url.scheme == "https" or os.getenv("DS_COOKIE_SECURE", "0") == "1"),
        max_age=600,
    )
    return response

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/admin", error: str = ""):
    return render_login_page(request, next_target=next, error=error)


@app.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/admin"),
):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM users WHERE lower(username) = lower(?)",
        (username.strip(),),
    ).fetchone()
    if not row or not row["is_active"] or not verify_password(password, row["password_hash"]):
        conn.close()
        return RedirectResponse(
            "/login?" + urlencode({"error": "Usuário ou senha inválidos.", "next": next or "/admin"}),
            status_code=303,
        )
    token = create_session(conn, row["id"])
    audit_event(conn, request, "auth.login", "user", row["username"], "Login realizado")
    conn.commit()
    conn.close()
    target = next if next and str(next).startswith("/") and not str(next).startswith("//") else "/admin"
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        max_age=SESSION_TTL_SECONDS,
        secure=(request.url.scheme == "https" or os.getenv("DS_COOKIE_SECURE", "0") == "1"),
    )
    response.delete_cookie(LOGIN_CSRF_COOKIE_NAME)
    return response


@app.post("/logout")
def logout(request: Request):
    conn = get_db()
    destroy_session(conn, request.cookies.get(SESSION_COOKIE_NAME))
    audit_event(conn, request, "auth.logout", "session", "", "Logout realizado")
    conn.commit(); conn.close()
    response = RedirectResponse("/login?" + urlencode({"error": "Sessão encerrada."}), status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


# =========================================================
# USUÁRIOS / AUDITORIA / RELEASES
# =========================================================

@app.get("/users", response_class=HTMLResponse)
def users_page(request: Request):
    require_role(request, "admin")
    conn = get_db()
    rows = conn.execute("SELECT * FROM users ORDER BY username").fetchall()
    tvs = conn.execute("SELECT tv_id, name, location FROM tvs ORDER BY name").fetchall()
    assignments = {}
    for row in conn.execute("SELECT user_id, tv_id FROM user_tv_permissions"):
        assignments.setdefault(row["user_id"], []).append(row["tv_id"])
    conn.close()
    return templates.TemplateResponse(request=request, name="users.html", context={
        "app_version": APP_VERSION, "users": rows, "role_labels": ROLE_LABELS, "tvs": tvs, "assignments": assignments,
    })


@app.post("/users")
def users_create(
    request: Request,
    username: str = Form(...),
    full_name: str = Form(""),
    password: str = Form(...),
    role: str = Form("viewer"),
    tv_ids: list[str] = Form([]),
):
    require_role(request, "admin")
    role = normalize_role(role)
    if len(password) < 8:
        raise HTTPException(400, "A senha inicial precisa de pelo menos 8 caracteres.")
    conn = get_db()
    try:
        new_user_id = conn.execute(
            """
            INSERT INTO users
            (username, full_name, password_hash, role, is_active, must_change_password, created_at)
            VALUES (?, ?, ?, ?, 1, 1, ?)
            """,
            (username.strip(), full_name.strip(), password_hash(password), role, time.time()),
        )
        sync_user_tv_permissions(conn, new_user_id.lastrowid, tv_ids, role)
        audit_event(conn, request, "user.create", "user", username.strip(), f"Perfil: {role}; TVs: {len(tv_ids)}")
        conn.commit()
        url = "/users?" + urlencode({"message": "Usuário criado.", "kind": "success"})
    except sqlite3.IntegrityError:
        conn.rollback()
        url = "/users?" + urlencode({"message": "Usuário já existe.", "kind": "error"})
    finally:
        conn.close()
    return RedirectResponse(url, status_code=303)


@app.post("/users/{user_id}/update")
def users_update(
    request: Request,
    user_id: int,
    full_name: str = Form(""),
    role: str = Form("viewer"),
    is_active: str = Form("0"),
    new_password: str = Form(""),
    tv_ids: list[str] = Form([]),
):
    user = require_role(request, "admin")
    role = normalize_role(role)
    conn = get_db()
    target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not target:
        conn.close(); raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    active_value = 1 if str(is_active) == "1" else 0
    # Não permite desativar o próprio usuário acidentalmente.
    if int(user_id) == int(user["id"]):
        active_value = 1
        role = "admin"  # impede auto-rebaixamento e perda do último administrador
    conn.execute(
        "UPDATE users SET full_name = ?, role = ?, is_active = ?, updated_at = ? WHERE id = ?",
        (full_name.strip(), role, active_value, time.time(), user_id),
    )
    if new_password.strip():
        if len(new_password) < 8:
            conn.rollback(); conn.close()
            raise HTTPException(400, "A nova senha precisa de pelo menos 8 caracteres.")
        conn.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 0, updated_at = ? WHERE id = ?",
            (password_hash(new_password), time.time(), user_id),
        )
    sync_user_tv_permissions(conn, user_id, tv_ids, role)
    audit_event(conn, request, "user.update", "user", target["username"], f"Perfil: {role}; ativo={active_value}; TVs: {len(tv_ids)}")
    conn.commit(); conn.close()
    return RedirectResponse("/users?" + urlencode({"message": "Usuário atualizado.", "kind": "success"}), status_code=303)


@app.get("/audit", response_class=HTMLResponse)
def audit_page(request: Request, action: str = "", username: str = "", days: int = 30):
    require_role(request, "admin")
    days = max(1, min(3650, int(days or 30)))
    conn = get_db(); conditions = ["created_at >= ?"]; params = [time.time() - days * 86400]
    if action.strip(): conditions.append("action LIKE ?"); params.append(f"%{action.strip()[:80]}%")
    if username.strip(): conditions.append("username LIKE ?"); params.append(f"%{username.strip()[:80]}%")
    rows = conn.execute(
        f"SELECT * FROM audit_log WHERE {' AND '.join(conditions)} ORDER BY created_at DESC LIMIT 500",
        params,
    ).fetchall(); conn.close()
    events = []
    for row in rows:
        item = dict(row)
        item["created_at_text"] = format_monitor_datetime(row["created_at"])
        events.append(item)
    return templates.TemplateResponse(request=request, name="audit.html", context={
        "app_version": APP_VERSION,
        "events": events,
        "filters": {"action": action, "username": username, "days": days},
    })


@app.get("/releases", response_class=HTMLResponse)
def releases_page(request: Request):
    require_role(request, "admin")
    conn = get_db()
    rows = conn.execute("SELECT * FROM player_releases ORDER BY created_at DESC").fetchall()
    tvs = conn.execute("SELECT tv_id, name, location, player_version, update_channel FROM tvs ORDER BY tv_id").fetchall()
    commands = conn.execute("SELECT * FROM player_commands ORDER BY created_at DESC LIMIT 50").fetchall()
    conn.close()
    releases = []
    for row in rows:
        item = dict(row)
        item["created_at_text"] = format_monitor_datetime(row["created_at"])
        item["size_text"] = human_bytes(row["size_bytes"])
        releases.append(item)
    recent_commands = []
    for row in commands:
        item = dict(row)
        item["created_at_text"] = format_monitor_datetime(row["created_at"])
        recent_commands.append(item)
    return templates.TemplateResponse(request=request, name="releases.html", context={
        "app_version": APP_VERSION,
        "releases": releases,
        "tvs": tvs,
        "commands": recent_commands,
    })


@app.post("/releases")
async def releases_upload(
    request: Request,
    version: str = Form(...),
    channel: str = Form("lab"),
    notes: str = Form(""),
    package: UploadFile = File(...),
):
    user = require_role(request, "admin")
    version = version.strip()
    channel = channel.strip().lower() if channel.strip().lower() in {"lab", "pilot", "production", "stable"} else "lab"
    if not version:
        raise HTTPException(status_code=400, detail="Versão obrigatória.")
    safe_version = "".join(ch for ch in version if ch.isalnum() or ch in ".-_")[:60]
    if not safe_version:
        raise HTTPException(status_code=400, detail="Versão inválida.")
    if Path(package.filename or "").suffix.lower() != ".zip":
        raise HTTPException(status_code=415, detail="A release deve ser um arquivo ZIP.")
    package_mime = str(package.content_type or "").split(";", 1)[0].strip().lower()
    if package_mime not in {"application/zip", "application/x-zip-compressed"}:
        raise HTTPException(status_code=415, detail="Tipo MIME inválido para release ZIP.")
    # Unique filename avoids overwriting an existing release before DB validation.
    filename = f"digital-signage-player-{safe_version}-{uuid.uuid4().hex}.zip"
    target = RELEASE_DIR / filename
    temp_target = UPLOAD_TEMP_DIR / f"release-{uuid.uuid4().hex}.part"
    size = 0
    digest = hashlib.sha256()
    try:
        with temp_target.open("xb") as handle:
            while chunk := await package.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_RELEASE_BYTES:
                    raise HTTPException(status_code=413, detail="Pacote excede o limite configurado.")
                digest.update(chunk)
                handle.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="Pacote vazio.")
        if not zipfile.is_zipfile(temp_target):
            raise HTTPException(status_code=415, detail="Assinatura ZIP inválida.")
        with zipfile.ZipFile(temp_target) as archive:
            if archive.testzip() is not None:
                raise HTTPException(status_code=400, detail="Pacote ZIP corrompido.")
        os.replace(temp_target, target)
    except BaseException:
        temp_target.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise
    sha = digest.hexdigest()
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO player_releases
            (version, channel, filename, original_filename, sha256, size_bytes, status, notes, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'published', ?, ?, ?)
            """,
            (version, channel, filename, package.filename or filename, sha, size, notes.strip(), user["id"], time.time()),
        )
        audit_event(conn, request, "release.upload", "release", version, f"Canal: {channel}; SHA256: {sha}")
        conn.commit()
        url = "/releases?" + urlencode({"message": "Release cadastrada.", "kind": "success"})
    except sqlite3.IntegrityError:
        conn.rollback()
        try: target.unlink()
        except OSError: pass
        url = "/releases?" + urlencode({"message": "Essa versão já existe.", "kind": "error"})
    finally:
        conn.close()
    return RedirectResponse(url, status_code=303)


@app.post("/releases/{release_id}/promote")
def release_promote(request: Request, release_id: int):
    require_role(request, "admin")
    conn = get_db()
    row = conn.execute("SELECT * FROM player_releases WHERE id = ?", (release_id,)).fetchone()
    if not row:
        conn.close(); raise HTTPException(status_code=404, detail="Release não encontrada.")
    conn.execute("UPDATE player_releases SET status = 'stable', published_at = ? WHERE id = ?", (time.time(), release_id))
    conn.execute("UPDATE tv_monitor_settings SET recommended_player_version = ?, updated_at = ? WHERE id = 1", (row["version"], time.time()))
    audit_event(conn, request, "release.promote", "release", row["version"], "Definida como versão recomendada")
    conn.commit(); conn.close()
    return RedirectResponse("/releases?" + urlencode({"message": "Versão recomendada atualizada.", "kind": "success"}), status_code=303)



# =========================================================
# AGENDAMENTO ATIVO
# =========================================================

def get_active_schedule(
    tv_id: str,
    now: datetime | None = None
):

    if now is None:
        now = datetime.now(APP_TIMEZONE)

    current_day = now.weekday()

    current_time = now.strftime(
        "%H:%M"
    )

    conn = get_db()

    rows = conn.execute("""
        SELECT
            schedules.*,
            playlists.name AS playlist_name
        FROM schedules

        INNER JOIN playlists
            ON schedules.playlist_id =
               playlists.id

        INNER JOIN playlist_publications
            ON playlist_publications.playlist_id =
               playlists.id

        WHERE schedules.tv_id = ?
        AND schedules.enabled = 1

        ORDER BY schedules.id DESC
    """, (
        tv_id,
    )).fetchall()

    conn.close()

    for row in rows:

        day = row["day_of_week"]

        start = row["start_time"]

        end = row["end_time"]


        # =================================================
        # HORÁRIO DO DIA INTEIRO
        # Ex.: segunda 00:00 -> 00:00
        # =================================================

        if start == end:

            if current_day == day:
                return row

            continue


        # =================================================
        # HORÁRIO NORMAL
        # Ex.: 08:00 -> 17:00
        # =================================================

        if start < end:

            if (
                current_day == day
                and
                start <= current_time < end
            ):

                return row

            continue


        # =================================================
        # HORÁRIO QUE PASSA DA MEIA-NOITE
        #
        # Ex.: sexta 22:00 -> 06:00
        # =================================================

        if current_day == day:

            if current_time >= start:
                return row


        next_day = (
            day + 1
        ) % 7


        if current_day == next_day:

            if current_time < end:
                return row


    return None

# =========================================================
# AUXILIARES
# =========================================================

def media_url(relative_path):
    return "/media/" + relative_path.replace("\\", "/")


def safe_media_path(relative_path):

    base = MEDIA_DIR.resolve()

    target = (
        MEDIA_DIR / relative_path
    ).resolve()

    if target != base and base not in target.parents:
        raise HTTPException(
            status_code=400,
            detail="Caminho de mídia inválido."
        )

    return target


def content_for_media_path(conn, relative_path: str):
    normalized = Path(relative_path.replace("\\", "/")).as_posix().lstrip("/")
    for row in conn.execute(
        "SELECT id, filename, pages_json, archived_at FROM contents"
    ):
        candidates = {str(row["filename"] or "").replace("\\", "/").lstrip("/")}
        try:
            pages = json.loads(row["pages_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            pages = []
        if isinstance(pages, list):
            candidates.update(
                str(page).replace("\\", "/").lstrip("/")
                for page in pages
                if isinstance(page, str)
            )
        if normalized in candidates:
            return row
    return None


def content_is_authorized_for_tv(conn, content_id: int, tv_id: str) -> bool:
    if conn.execute(
        "SELECT 1 FROM tv_contents WHERE tv_id = ? AND content_id = ?",
        (tv_id, content_id),
    ).fetchone():
        return True
    rows = conn.execute(
        """
        SELECT pp.snapshot_json
        FROM playlist_publications pp
        WHERE pp.playlist_id IN (
            SELECT playlist_id FROM tv_playlists WHERE tv_id = ?
            UNION
            SELECT playlist_id FROM schedules WHERE tv_id = ? AND enabled = 1
        )
        """,
        (tv_id, tv_id),
    ).fetchall()
    for row in rows:
        try:
            snapshot = json.loads(row["snapshot_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if any(
            isinstance(item, dict) and item.get("content_id") == content_id
            for item in snapshot.get("items", [])
        ):
            return True
    return False


@app.get("/media/{relative_path:path}")
def protected_media(request: Request, relative_path: str):
    path = safe_media_path(relative_path)
    if not path.is_file() or path.is_symlink():
        raise HTTPException(status_code=404, detail="Mídia não encontrada.")

    conn = get_db()
    try:
        content = content_for_media_path(conn, relative_path)
        if not content or content["archived_at"] is not None:
            raise HTTPException(status_code=404, detail="Mídia não encontrada.")

        user = current_user_from_request(request)
        if user:
            authorized = can_access_content(conn, user, content["id"])
        else:
            device_tv_id = authenticated_device_tv_id(request, conn)
            if device_tv_id:
                authorized = content_is_authorized_for_tv(
                    conn, content["id"], device_tv_id
                )
            elif os.getenv("DS_ALLOW_LEGACY_PUBLIC_MEDIA", "0") == "1":
                authorized = bool(content_tv_ids(conn, content["id"]))
            else:
                authorized = False
        if not authorized:
            raise HTTPException(status_code=403, detail="Acesso à mídia negado.")
    finally:
        conn.close()

    response = FileResponse(path)
    response.headers["Cache-Control"] = "private, max-age=300"
    return response


# =========================================================
# CORPORATE v5 - HELPERS
# =========================================================

def human_bytes(value):
    try:
        size = float(value or 0)
    except (TypeError, ValueError):
        return "—"
    units = ["B", "KB", "MB", "GB", "TB"]
    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1
    return f"{size:.1f} {units[index]}" if index else f"{int(size)} B"


def human_duration(seconds):
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return ""
    if total < 0:
        return ""
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def probe_media_metadata(path: Path, content_type: str):
    result = {
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "width": None,
        "height": None,
        "duration_sec": None,
        "codec": None,
        "pix_fmt": None,
        "bitrate": None,
        "validation_status": "ok",
        "validation_message": "Arquivo validado.",
    }
    warnings = []
    errors = []

    if not path.exists() or result["size_bytes"] <= 0:
        result["validation_status"] = "error"
        result["validation_message"] = "Arquivo vazio ou ausente."
        return result

    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            proc = subprocess.run(
                [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            data = json.loads(proc.stdout or "{}") if proc.returncode == 0 else {}
            streams = data.get("streams", []) if isinstance(data, dict) else []
            video = next((s for s in streams if s.get("codec_type") == "video"), None)
            if isinstance(video, dict):
                result["width"] = video.get("width")
                result["height"] = video.get("height")
                result["codec"] = video.get("codec_name")
                result["pix_fmt"] = video.get("pix_fmt")
                try:
                    result["bitrate"] = int(video.get("bit_rate")) if video.get("bit_rate") else None
                except (TypeError, ValueError):
                    pass
            elif content_type == "video":
                errors.append("O arquivo MP4 não contém fluxo de vídeo válido.")
            elif content_type == "image":
                errors.append("O arquivo não contém uma imagem válida.")
            fmt = data.get("format", {}) if isinstance(data, dict) else {}
            try:
                result["duration_sec"] = float(fmt.get("duration")) if fmt.get("duration") else None
            except (TypeError, ValueError):
                pass
            if not result["bitrate"]:
                try:
                    result["bitrate"] = int(fmt.get("bit_rate")) if fmt.get("bit_rate") else None
                except (TypeError, ValueError):
                    pass
        except Exception:
            if content_type == "video":
                errors.append("Não foi possível validar o vídeo com ffprobe.")
            else:
                warnings.append("Validação técnica parcial da imagem.")
    else:
        if content_type == "video":
            errors.append("ffprobe é obrigatório para validar vídeos MP4.")
        else:
            warnings.append("ffprobe não encontrado; metadados técnicos indisponíveis.")

    if content_type == "video":
        codec = (result.get("codec") or "").lower()
        if codec and codec not in {"h264", "avc1"}:
            errors.append(f"Codec {codec} não suportado no padrão de produção; use H.264/AVC.")
        if result.get("width") and result.get("height"):
            if result["width"] > 3840 or result["height"] > 2160:
                warnings.append("Resolução acima de 4K; recomenda-se no máximo 3840×2160.")
        pix = (result.get("pix_fmt") or "").lower()
        if pix and pix not in {"yuv420p", "yuvj420p"}:
            warnings.append(f"Formato de pixel {pix}; yuv420p é o padrão recomendado.")
    elif content_type == "image":
        if result.get("width") and result.get("height"):
            if result["width"] > 7680 or result["height"] > 4320:
                warnings.append("Imagem muito grande; considere reduzir a resolução.")

    if errors:
        result["validation_status"] = "error"
        result["validation_message"] = " ".join(errors)
    elif warnings:
        result["validation_status"] = "warning"
        result["validation_message"] = " ".join(warnings)
    return result


def save_content_metadata(conn, content_id, metadata):
    conn.execute(
        """
        INSERT INTO content_metadata
        (content_id, size_bytes, width, height, duration_sec, codec, pix_fmt, bitrate,
         validation_status, validation_message, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(content_id) DO UPDATE SET
            size_bytes=excluded.size_bytes,
            width=excluded.width,
            height=excluded.height,
            duration_sec=excluded.duration_sec,
            codec=excluded.codec,
            pix_fmt=excluded.pix_fmt,
            bitrate=excluded.bitrate,
            validation_status=excluded.validation_status,
            validation_message=excluded.validation_message,
            updated_at=excluded.updated_at
        """,
        (
            content_id, metadata.get("size_bytes"), metadata.get("width"), metadata.get("height"),
            metadata.get("duration_sec"), metadata.get("codec"), metadata.get("pix_fmt"),
            metadata.get("bitrate"), metadata.get("validation_status"),
            metadata.get("validation_message"), time.time()
        )
    )


def ensure_content_metadata(conn, content_row):
    row = conn.execute("SELECT * FROM content_metadata WHERE content_id = ?", (content_row["id"],)).fetchone()
    if row:
        return dict(row)
    try:
        metadata = probe_media_metadata(safe_media_path(content_row["filename"]), content_row["content_type"])
    except Exception as exc:
        metadata = {
            "size_bytes": 0, "width": None, "height": None, "duration_sec": None,
            "codec": None, "pix_fmt": None, "bitrate": None,
            "validation_status": "warning", "validation_message": f"Não foi possível validar: {exc}"
        }
    save_content_metadata(conn, content_row["id"], metadata)
    return metadata


def content_usage(conn, content_id):
    names = set()
    draft = conn.execute(
        """
        SELECT DISTINCT playlists.name
        FROM playlist_items
        INNER JOIN playlists ON playlist_items.playlist_id = playlists.id
        WHERE playlist_items.content_id = ?
        """, (content_id,)
    ).fetchall()
    names.update(row["name"] for row in draft)
    protected = False
    publication_rows = conn.execute(
        """
        SELECT playlists.name, playlist_publications.snapshot_json
        FROM playlist_publications
        INNER JOIN playlists ON playlists.id = playlist_publications.playlist_id
        """
    ).fetchall()
    history_rows = conn.execute(
        """
        SELECT playlists.name, playlist_publication_history.snapshot_json
        FROM playlist_publication_history
        INNER JOIN playlists ON playlists.id = playlist_publication_history.playlist_id
        """
    ).fetchall()
    for row in [*publication_rows, *history_rows]:
        try:
            snapshot = json.loads(row["snapshot_json"] or "{}")
        except json.JSONDecodeError:
            continue
        if any(isinstance(item, dict) and item.get("content_id") == content_id for item in snapshot.get("items", [])):
            names.add(row["name"])
            protected = True
    return sorted(names), protected


def archived_content_ids(conn):
    rows = conn.execute(
        """
        SELECT id
        FROM contents
        WHERE archived_at IS NOT NULL
        """
    ).fetchall()

    return {
        int(row["id"])
        for row in rows
    }


def normalize_playlist_positions(conn, playlist_id):
    rows = conn.execute(
        "SELECT id FROM playlist_items WHERE playlist_id = ? ORDER BY position, id",
        (playlist_id,)
    ).fetchall()
    for position, row in enumerate(rows, start=1):
        conn.execute("UPDATE playlist_items SET position = ? WHERE id = ?", (position, row["id"]))


def delete_content_safe(conn, content_id):

    content = conn.execute(
        """
        SELECT *
        FROM contents
        WHERE id = ?
        """,
        (content_id,)
    ).fetchone()

    if not content:
        return False, "Conteúdo não encontrado."

    if content["archived_at"] is not None:
        return False, "Conteúdo já está arquivado."

    conn.execute(
        """
        UPDATE contents
        SET archived_at = ?
        WHERE id = ?
        """,
        (
            time.time(),
            content_id,
        )
    )

    return (
        True,
        "Conteúdo arquivado. "
        "O arquivo físico foi preservado no servidor."
    )

def playlist_diff_summary(old_snapshot, new_snapshot):
    old_items = old_snapshot.get("items", []) if isinstance(old_snapshot, dict) else []
    new_items = new_snapshot.get("items", []) if isinstance(new_snapshot, dict) else []
    old_counter = Counter(item.get("content_name") for item in old_items if isinstance(item, dict))
    new_counter = Counter(item.get("content_name") for item in new_items if isinstance(item, dict))
    added, removed = [], []
    for name, count in (new_counter - old_counter).items(): added.extend([name or "Conteúdo"] * count)
    for name, count in (old_counter - new_counter).items(): removed.extend([name or "Conteúdo"] * count)
    old_by_id = {item.get("item_id"): item for item in old_items if isinstance(item, dict) and item.get("item_id") is not None}
    duration_changes = []
    for item in new_items:
        if not isinstance(item, dict): continue
        previous = old_by_id.get(item.get("item_id"))
        if previous and previous.get("duration_sec") != item.get("duration_sec"):
            duration_changes.append({"name": item.get("content_name") or "Conteúdo", "from": previous.get("duration_sec"), "to": item.get("duration_sec")})
    old_order = [item.get("item_id") for item in old_items if isinstance(item, dict) and item.get("item_id") in old_by_id]
    new_order = [item.get("item_id") for item in new_items if isinstance(item, dict) and item.get("item_id") in old_by_id]
    return {"added": added, "removed": removed, "duration_changes": duration_changes, "order_changed": old_order != new_order}


def schedule_parts(day, start_time, end_time):
    def minutes(value):
        h, m = [int(part) for part in value.split(":", 1)]
        return h * 60 + m
    start = minutes(start_time); end = minutes(end_time)
    if start == end:
        return [(day, 0, 1440)]
    if start < end:
        return [(day, start, end)]
    return [(day, start, 1440), ((day + 1) % 7, 0, end)]


def schedules_overlap(day1, start1, end1, day2, start2, end2):
    for d1, s1, e1 in schedule_parts(day1, start1, end1):
        for d2, s2, e2 in schedule_parts(day2, start2, end2):
            if d1 == d2 and max(s1, s2) < min(e1, e2):
                return True
    return False


def schedule_conflict_ids(rows):
    ids = set()
    active = [row for row in rows if bool(row["enabled"])]
    for index, first in enumerate(active):
        for second in active[index + 1:]:
            if first["tv_id"] != second["tv_id"]: continue
            if schedules_overlap(first["day_of_week"], first["start_time"], first["end_time"], second["day_of_week"], second["start_time"], second["end_time"]):
                ids.update({first["id"], second["id"]})
    return ids


def format_event_duration(seconds):
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return ""
    if seconds < 60: return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60: return f"{minutes}m {sec}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


# =========================================================
# ADMIN
# =========================================================

@app.get(
    "/admin",
    response_class=HTMLResponse
)
def admin(request: Request):
    conn = get_db()
    now = time.time()
    settings = monitor_settings(conn) if "tv_monitor_settings" in {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()} else {"offline_seconds": 30}
    user = require_user(request)
    allowed = allowed_tv_ids(conn, user)
    tv_rows = [r for r in conn.execute("""
        SELECT tvs.*, tv_runtime_status.payload_json AS runtime_payload_json
        FROM tvs LEFT JOIN tv_runtime_status ON tv_runtime_status.tv_id = tvs.tv_id
        ORDER BY tvs.tv_id
    """) if r["tv_id"] in allowed]
    problem_tvs = []
    online_count = 0
    for tv in tv_rows:
        online = tv["last_seen"] is not None and now - tv["last_seen"] <= int(settings.get("offline_seconds", 30))
        if online: online_count += 1
        health, reason = evaluate_tv_health(
            tv, parse_runtime_payload(tv["runtime_payload_json"]), settings,
            maintenance_for_tv(conn, tv["tv_id"]), now,
        )
        if health in {"warning", "error", "offline"}:
            problem_tvs.append({"tv_id": tv["tv_id"], "name": tv["name"], "health": health, "reason": reason})
    contents_total = sum(1 for r in conn.execute("SELECT id FROM contents WHERE content_type IN ('image','video') AND archived_at IS NULL") if can_access_content(conn, user, r["id"]))
    playlists_total = sum(1 for r in conn.execute("SELECT id FROM playlists") if can_access_playlist(conn, user, r["id"]))
    schedules_total = sum(1 for r in conn.execute("SELECT tv_id FROM schedules") if r["tv_id"] in allowed)
    events_rows = conn.execute("SELECT * FROM tv_event_log ORDER BY created_at DESC LIMIT 10").fetchall() if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tv_event_log'").fetchone() else []
    recent_events = [{"tv_id": row["tv_id"], "title": row["title"], "detail": row["detail"], "created_at_text": format_monitor_datetime(row["created_at"])} for row in events_rows if row["tv_id"] in allowed]
    conn.close()
    return templates.TemplateResponse(request=request, name="admin.html", context={
        "app_version": APP_VERSION,
        "stats": {"tvs_total": len(tv_rows), "tvs_online": online_count, "tvs_problem": len(problem_tvs), "contents_total": contents_total, "playlists_total": playlists_total, "schedules_total": schedules_total},
        "problem_tvs": problem_tvs[:8],
        "recent_events": recent_events,
    })


# =========================================================
# CADASTRAR TV
# =========================================================

@app.post("/admin/tv")
def create_tv(request: Request, 
    tv_id: str = Form(...),
    name: str = Form(...),
    location: str = Form("")
):
    require_role(request, "admin")
    clean_tv_id = tv_id.strip().upper()
    conn = get_db()
    created = True
    try:
        conn.execute(
            """
            INSERT INTO tvs
            (tv_id, name, location)
            VALUES (?, ?, ?)
            """,
            (clean_tv_id, name.strip(), location.strip())
        )
        conn.commit()
    except sqlite3.IntegrityError:
        created = False
    conn.close()

    if created:
        return RedirectResponse(
            f"/players/{clean_tv_id}?" + urlencode({
                "message": "TV cadastrada. Agora use Provisionar Raspberry para gerar a instalação.",
                "kind": "success",
            }),
            status_code=303,
        )
    return RedirectResponse(
        "/players?" + urlencode({"message": "Já existe uma TV com esse ID.", "kind": "error"}),
        status_code=303,
    )


@app.post("/players/{tv_id}/delete")
def delete_tv(
    request: Request,
    tv_id: str,
    confirm_tv_id: str = Form(...),
):
    require_role(request, "admin")
    clean_tv_id = tv_id.strip().upper()
    if confirm_tv_id.strip().upper() != clean_tv_id:
        raise HTTPException(
            status_code=400,
            detail="Digite o ID exato da TV para confirmar a exclusão.",
        )

    conn = get_db()
    try:
        tv = conn.execute(
            "SELECT tv_id, name FROM tvs WHERE tv_id = ?",
            (clean_tv_id,),
        ).fetchone()
        if not tv:
            raise HTTPException(status_code=404, detail="TV não encontrada.")

        linked_counts = {
            "schedules": conn.execute(
                "SELECT COUNT(*) AS total FROM schedules WHERE tv_id = ?",
                (clean_tv_id,),
            ).fetchone()["total"],
            "commands": conn.execute(
                "SELECT COUNT(*) AS total FROM player_commands WHERE tv_id = ?",
                (clean_tv_id,),
            ).fetchone()["total"],
            "events": conn.execute(
                "SELECT COUNT(*) AS total FROM tv_event_log WHERE tv_id = ?",
                (clean_tv_id,),
            ).fetchone()["total"],
        }

        # Children are removed explicitly because legacy databases did not
        # consistently enable SQLite foreign-key cascades.
        for table in (
            "player_update_jobs",
            "player_commands",
            "schedules",
            "tv_playlists",
            "tv_contents",
            "user_tv_permissions",
            "tv_runtime_status",
            "tv_event_log",
            "tv_monitor_state",
            "tv_maintenance",
            "stability_events",
            "stability_samples",
        ):
            conn.execute(f"DELETE FROM {table} WHERE tv_id = ?", (clean_tv_id,))

        conn.execute("DELETE FROM tvs WHERE tv_id = ?", (clean_tv_id,))
        audit_event(
            conn,
            request,
            "tv.delete",
            "tv",
            clean_tv_id,
            (
                f"TV excluída: {tv['name']}; "
                f"agendamentos={linked_counts['schedules']}; "
                f"comandos={linked_counts['commands']}; "
                f"eventos={linked_counts['events']}"
            ),
        )
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return RedirectResponse(
        "/players?" + urlencode({
            "message": f"TV {clean_tv_id} excluída com sucesso.",
            "kind": "success",
        }),
        status_code=303,
    )


# =========================================================
# UPLOAD DE CONTEÚDO
# =========================================================

IMAGE_MIME_BY_EXTENSION = {
    ".jpg": {"image/jpeg", "image/jpg"},
    ".jpeg": {"image/jpeg", "image/jpg"},
    ".png": {"image/png"},
    ".webp": {"image/webp"},
    ".gif": {"image/gif"},
}


def has_expected_media_signature(path: Path, extension: str) -> bool:
    with path.open("rb") as handle:
        head = handle.read(32)
        if extension in {".jpg", ".jpeg"}:
            if not head.startswith(b"\xff\xd8\xff"):
                return False
            handle.seek(-2, os.SEEK_END)
            return handle.read(2) == b"\xff\xd9"
    if extension == ".png":
        return head.startswith(b"\x89PNG\r\n\x1a\n") and head[12:16] == b"IHDR"
    if extension == ".gif":
        return head.startswith((b"GIF87a", b"GIF89a"))
    if extension == ".webp":
        return head.startswith(b"RIFF") and head[8:12] == b"WEBP" and head[12:16] in {b"VP8 ", b"VP8L", b"VP8X"}
    if extension == ".mp4":
        return len(head) >= 12 and head[4:8] == b"ftyp"
    return False

async def prepare_uploaded_media(
    file: UploadFile,
    name: str = "",
    upload_budget: dict | None = None,
):
    """Salva e valida a mídia, sem ainda criar o registro no banco."""
    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="Nenhum arquivo foi enviado."
        )

    extension = Path(file.filename).suffix.lower()
    image_extensions = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    video_extensions = {".mp4"}

    if extension in image_extensions:
        content_type = "image"
        allowed_mime = IMAGE_MIME_BY_EXTENSION[extension]
        max_bytes = MAX_IMAGE_BYTES
    elif extension in video_extensions:
        content_type = "video"
        allowed_mime = {"video/mp4"}
        max_bytes = MAX_VIDEO_BYTES
    else:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{file.filename}: formato não suportado. "
                "Use JPG, PNG, WEBP, GIF ou MP4."
            )
        )

    declared_mime = str(file.content_type or "").split(";", 1)[0].strip().lower()
    if declared_mime not in allowed_mime:
        raise HTTPException(
            status_code=415,
            detail=f"{file.filename}: tipo MIME incompatível com a extensão.",
        )

    uid = uuid.uuid4().hex
    source_filename = f"uploads/{uid}{extension}"
    source_path = safe_media_path(source_filename)
    temp_path = UPLOAD_TEMP_DIR / f"media-{uid}.part"
    size = 0
    budget = upload_budget if upload_budget is not None else {"remaining": MAX_REQUEST_BYTES}

    try:
        with temp_path.open("xb") as buffer:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes or len(chunk) > int(budget.get("remaining", 0)):
                    raise HTTPException(
                        status_code=413,
                        detail=f"{file.filename}: arquivo excede o limite configurado.",
                    )
                budget["remaining"] = int(budget.get("remaining", 0)) - len(chunk)
                buffer.write(chunk)

        if size == 0:
            raise HTTPException(status_code=400, detail=f"{file.filename}: arquivo vazio.")
        if not has_expected_media_signature(temp_path, extension):
            raise HTTPException(
                status_code=415,
                detail=f"{file.filename}: assinatura do arquivo inválida.",
            )

        metadata = probe_media_metadata(
            temp_path,
            content_type
        )

        if metadata.get("validation_status") == "error":
            raise HTTPException(
                status_code=400,
                detail=(
                    metadata.get("validation_message")
                    or f"{file.filename}: arquivo incompatível."
                )
            )
        os.replace(temp_path, source_path)

    except HTTPException:
        temp_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        temp_path.unlink(missing_ok=True)
        source_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=500,
            detail=f"Erro processando {file.filename}: {exc}"
        )

    return {
        "name": name.strip() or Path(file.filename).stem,
        "filename": source_filename,
        "original_filename": file.filename,
        "content_type": content_type,
        "pages_json": json.dumps(
            [source_filename] if content_type == "image" else []
        ),
        "metadata": metadata,
        "source_path": source_path,
    }


def insert_prepared_content(conn, prepared: dict) -> int:
    content_id = conn.execute(
        """
        INSERT INTO contents
        (name, filename, content_type, created_at, original_filename, pages_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            prepared["name"],
            prepared["filename"],
            prepared["content_type"],
            time.time(),
            prepared["original_filename"],
            prepared["pages_json"],
        )
    ).lastrowid

    save_content_metadata(
        conn,
        content_id,
        prepared["metadata"]
    )

    return int(content_id)


@app.post("/admin/upload")
async def upload_content(
    request: Request,
    name: str = Form(""),
    file: UploadFile = File(...)
):
    require_role(request, "editor")
    prepared = await prepare_uploaded_media(file, name)
    conn = get_db()

    try:
        content_id = insert_prepared_content(
            conn,
            prepared
        )
        conn.execute("UPDATE contents SET owner_user_id=? WHERE id=?", (require_user(request)["id"], content_id))
        conn.commit()
    except Exception:
        conn.rollback()
        prepared["source_path"].unlink(missing_ok=True)
        raise
    finally:
        conn.close()

    metadata = prepared["metadata"]
    payload = {
        "status": "ok",
        "id": content_id,
        "name": prepared["name"],
        "content_type": prepared["content_type"],
        "filename": prepared["filename"],
        "original_filename": prepared["original_filename"],
        "url": media_url(prepared["filename"]),
        "validation_status": metadata.get("validation_status"),
        "validation_message": metadata.get("validation_message"),
        "metadata": metadata,
    }

    if "application/json" in (request.headers.get("accept") or ""):
        return payload

    query = urlencode({
        "message": "Conteúdo enviado com sucesso.",
        "kind": "success"
    })
    return RedirectResponse(
        f"/gallery?{query}",
        status_code=303
    )

# =========================================================
# PUBLICAÇÃO DIRETA (LEGADO)
# =========================================================

@app.post("/admin/publish")
def publish_content(request: Request, ):
    require_role(request, "admin")
    raise HTTPException(
        status_code=410,
        detail=(
            "A publicação direta de conteúdo foi desativada. "
            "Adicione o conteúdo a uma playlist e publique a playlist na TV."
        )
    )


# =========================================================
# EXCLUIR CONTEÚDO
# =========================================================

@app.post("/admin/content/delete")
def delete_content(request: Request, 
    content_id: int = Form(...)
):
    require_content_access(request, content_id, "editor")
    conn = get_db()
    deleted, message = delete_content_safe(conn, content_id)
    if deleted:
        conn.commit()
        kind = "success"
    else:
        conn.rollback()
        kind = "warning"
    conn.close()
    return RedirectResponse(
        "/gallery?" + urlencode({"message": message, "kind": kind}),
        status_code=303
    )


@app.post(
    "/admin/content/{content_id}/restore"
)
def restore_content(request: Request, content_id: int):

    require_content_access(request, content_id, "editor")
    conn = get_db()

    content = conn.execute(
        """
        SELECT id, archived_at
        FROM contents
        WHERE id = ?
        """,
        (content_id,)
    ).fetchone()

    if not content:

        conn.close()

        raise HTTPException(
            status_code=404,
            detail="Conteúdo não encontrado."
        )

    if content["archived_at"] is None:

        conn.close()

        return RedirectResponse(
            "/gallery?" + urlencode({
                "message":
                    "O conteúdo já está ativo.",
                "kind": "info",
            }),
            status_code=303,
        )

    conn.execute(
        """
        UPDATE contents
        SET archived_at = NULL
        WHERE id = ?
        """,
        (content_id,)
    )

    conn.commit()
    conn.close()

    return RedirectResponse(
        "/gallery?archived=1&"
        + urlencode({
            "message":
                "Conteúdo restaurado.",
            "kind": "success",
        }),
        status_code=303,
    )


@app.post("/admin/content/{content_id}/rename")
def rename_content(request: Request, 
    content_id: int,
    name: str = Form(...)
):
    require_content_access(request, content_id, "editor")
    clean = name.strip()
    if not clean:
        raise HTTPException(status_code=400, detail="Informe um nome.")
    conn = get_db()
    exists = conn.execute("SELECT id FROM contents WHERE id = ?", (content_id,)).fetchone()
    if not exists:
        conn.close()
        raise HTTPException(status_code=404, detail="Conteúdo não encontrado.")
    conn.execute("UPDATE contents SET name = ? WHERE id = ?", (clean[:300], content_id))
    conn.commit(); conn.close()
    return RedirectResponse(
        "/gallery?" + urlencode({"message": "Conteúdo renomeado.", "kind": "success"}),
        status_code=303
    )


@app.post("/api/contents/delete-bulk")
async def delete_contents_bulk(request: Request):

    require_role(request, "editor")
    data = await request.json()

    raw_ids = data.get(
        "content_ids",
        []
    )

    if not isinstance(raw_ids, list):
        raise HTTPException(
            status_code=400,
            detail="Lista inválida."
        )

    try:
        ids = list(
            dict.fromkeys(
                int(x)
                for x in raw_ids
            )
        )

    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail="IDs inválidos."
        )

    if not ids:
        raise HTTPException(
            status_code=400,
            detail=(
                "Selecione pelo menos "
                "um conteúdo."
            )
        )

    # Validar a lista inteira antes de alterar qualquer conteúdo.
    for content_id in ids:
        require_content_access(request, content_id, "editor")

    conn = get_db()
    archived = 0
    blocked_items = []

    for content_id in ids:

        ok, message = delete_content_safe(
            conn,
            content_id
        )

        if ok:
            archived += 1

        else:
            blocked_items.append({
                "id": content_id,
                "reason": message,
            })

    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "archived": archived,

        # Compatibilidade temporária
        # com o JS antigo.
        "deleted": archived,

        "blocked": len(blocked_items),
        "blocked_items": blocked_items,
    }

@app.get(
    "/preview/{content_id}",
    response_class=HTMLResponse
)
def preview(
    request: Request,
    content_id: int
):

    require_content_access(request, content_id)
    conn = get_db()

    content = conn.execute(
        """
        SELECT *
        FROM contents
        WHERE id = ?
        """,
        (content_id,)
    ).fetchone()

    tvs = conn.execute(
        """
        SELECT *
        FROM tvs
        ORDER BY tv_id
        """
    ).fetchall()

    conn.close()

    if not content:

        raise HTTPException(
            status_code=404,
            detail="Conteúdo não encontrado."
        )

    try:

        parsed = json.loads(
            content["pages_json"] or "[]"
        )

    except json.JSONDecodeError:

        parsed = []

    pages = []
    layout = {}

    if content["content_type"] == "layout":

        if isinstance(parsed, dict):
            layout = parsed

    else:

        if isinstance(parsed, list):

            pages = [
                media_url(page)
                for page in parsed
            ]

    return templates.TemplateResponse(
        request=request,
        name="preview.html",
        context={
            "content": content,
            "tvs": tvs,
            "pages": pages,
            "layout": layout,
            "file_url": media_url(
                content["filename"]
            )
        }
    )


# =========================================================
# MONITORAMENTO OPERACIONAL v3
# =========================================================

_monitor_thread_started = False
_monitor_thread_lock = threading.Lock()


def monitor_settings(conn=None):

    own_conn = conn is None

    if own_conn:
        conn = get_db()

    row = conn.execute(
        """
        SELECT *
        FROM tv_monitor_settings
        WHERE id = 1
        """
    ).fetchone()

    if own_conn:
        conn.close()

    if not row:
        return {
            "recommended_player_version": OFFICIAL_PLAYER_VERSION,
            "offline_seconds": 30,
            "disk_warning_mb": 1024,
            "sync_warning_seconds": 120,
        }

    return {
        "recommended_player_version":
            row["recommended_player_version"] or "",
        "offline_seconds":
            int(row["offline_seconds"] or 30),
        "disk_warning_mb":
            int(row["disk_warning_mb"] or 1024),
        "sync_warning_seconds":
            int(row["sync_warning_seconds"] or 120),
    }


def parse_runtime_payload(raw):

    if not raw:
        return {}

    try:
        data = json.loads(raw)

        if isinstance(data, dict):
            return data

    except (json.JSONDecodeError, TypeError):
        pass

    return {}


def maintenance_for_tv(conn, tv_id):

    row = conn.execute(
        """
        SELECT enabled, note, updated_at
        FROM tv_maintenance
        WHERE tv_id = ?
        """,
        (tv_id,)
    ).fetchone()

    if not row:
        return {
            "enabled": False,
            "note": "",
            "updated_at": None,
        }

    return {
        "enabled": bool(row["enabled"]),
        "note": row["note"] or "",
        "updated_at": row["updated_at"],
    }


def log_tv_event(
    conn,
    tv_id,
    event_type,
    severity,
    title,
    detail=""
):

    conn.execute(
        """
        INSERT INTO tv_event_log
        (
            tv_id,
            created_at,
            event_type,
            severity,
            title,
            detail
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            tv_id,
            time.time(),
            str(event_type)[:80],
            str(severity)[:20],
            str(title)[:200],
            str(detail or "")[:2000],
        )
    )


def evaluate_player_version_status(reported_version, recommended_version):
    """Informational exact comparison; no precedence or release policy implied."""
    reported = str(reported_version or "").strip()
    recommended = str(recommended_version or "").strip()
    if not reported:
        status, label = "unknown", "Versão não informada"
    elif not recommended:
        status, label = "unknown", "Sem versão recomendada"
    elif reported == recommended:
        status, label = "current", "Versão recomendada"
    else:
        status, label = "different", "Versão diferente da recomendada"
    return {
        "player_version": reported,
        "recommended_player_version": recommended,
        "version_status": status,
        "version_status_label": label,
        # Legacy API field: textual inequality, not evidence of an older version.
        "version_outdated": status == "different",
    }


def evaluate_tv_health(
    tv,
    runtime,
    settings,
    maintenance,
    now=None
):

    now = now or time.time()

    if maintenance.get("enabled"):

        note = maintenance.get("note") or ""

        return (
            "maintenance",
            (
                "Modo manutenção ativo."
                + (f" {note}" if note else "")
            )
        )

    last_seen = tv["last_seen"]

    offline_seconds = int(
        settings.get("offline_seconds")
        or 30
    )

    if (
        last_seen is None
        or now - float(last_seen) > offline_seconds
    ):
        return (
            "offline",
            (
                f"Sem heartbeat há mais de "
                f"{offline_seconds} segundos."
            )
        )

    last_error = str(
        tv["last_error"]
        or ""
    ).strip()

    if last_error:
        return (
            "error",
            "O player reportou um erro."
        )

    browser_running = runtime.get(
        "browser_running"
    )

    if browser_running is False:
        return (
            "error",
            "Chromium não está em execução."
        )

    playback_state = str(
        runtime.get("playback_state")
        or ""
    ).lower()

    if playback_state == "error":
        return (
            "error",
            "Erro durante a reprodução."
        )

    sync_status = str(
        runtime.get("sync_status")
        or ""
    ).lower()

    if sync_status == "offline":
        return (
            "warning",
            (
                "Player está online, mas a "
                "sincronização está offline."
            )
        )

    sync_warning_seconds = int(
        settings.get(
            "sync_warning_seconds"
        )
        or 120
    )

    last_sync_at = tv["last_sync_at"]

    if (
        last_sync_at is not None
        and now - float(last_sync_at)
        > sync_warning_seconds
    ):
        return (
            "warning",
            (
                "Último sync OK ocorreu há mais de "
                f"{sync_warning_seconds} segundos."
            )
        )

    try:
        disk_free_mb = float(
            runtime.get("disk_free_mb")
        )
    except (TypeError, ValueError):
        disk_free_mb = None

    disk_warning_mb = int(
        settings.get("disk_warning_mb")
        or 1024
    )

    if (
        disk_free_mb is not None
        and disk_free_mb < disk_warning_mb
    ):
        return (
            "warning",
            (
                f"Espaço livre abaixo de "
                f"{disk_warning_mb} MB."
            )
        )

    return (
        "ok",
        "Player comunicando e sem alerta atual."
    )


def record_monitor_snapshot(
    conn,
    tv,
    runtime,
    health,
    reason
):

    tv_id = tv["tv_id"]

    previous = conn.execute(
        """
        SELECT *
        FROM tv_monitor_state
        WHERE tv_id = ?
        """,
        (tv_id,)
    ).fetchone()

    playlist_id = tv[
        "current_playlist_id"
    ]

    playlist_version = runtime.get(
        "current_playlist_version"
    )

    sync_status = str(
        runtime.get("sync_status")
        or ""
    ).lower()

    player_version = str(
        tv["player_version"]
        or runtime.get("player_version")
        or ""
    ).strip()

    browser_value = runtime.get(
        "browser_running"
    )

    browser_running = (
        1
        if browser_value is True
        else (
            0
            if browser_value is False
            else None
        )
    )

    if not previous:

        log_tv_event(
            conn,
            tv_id,
            "monitor_started",
            "info",
            "Monitoramento iniciado",
            reason
        )

    else:

        if previous["health"] != health:

            severity = (
                "error"
                if health in {
                    "error",
                    "offline"
                }
                else (
                    "warning"
                    if health == "warning"
                    else "info"
                )
            )

            labels = {
                "ok": "Player normalizado",
                "warning": "Player em atenção",
                "error": "Erro no player",
                "offline": "Player offline",
                "maintenance":
                    "Player em manutenção",
            }

            # Recognize the previous evaluator's version-only reason. Preserve
            # health_ok for existing consumers, without claiming a device repair.
            previous_reason = previous["reason"] or ""
            version_rule_changed = (
                previous["health"] == "warning" and health == "ok"
                and previous_reason.startswith("Player ")
                and "; versão recomendada: " in previous_reason
                and previous_reason.endswith(".")
            )
            if version_rule_changed:
                labels["ok"] = "Avaliação operacional atualizada"
            event_reason = (
                "A avaliação atual separa divergência de versão da saúde operacional. "
                "Nenhum alerta operacional detectado; não comprova reparo do dispositivo."
                if version_rule_changed else reason
            )
            log_tv_event(
                conn,
                tv_id,
                f"health_{health}",
                severity,
                labels.get(
                    health,
                    "Status alterado"
                ),
                event_reason
            )

        if (
            previous["playlist_id"]
            != playlist_id
            and (
                previous["playlist_id"]
                is not None
                or playlist_id is not None
            )
        ):
            log_tv_event(
                conn,
                tv_id,
                "playlist_changed",
                "info",
                "Playlist alterada",
                (
                    f"{previous['playlist_id']} "
                    f"→ {playlist_id}"
                )
            )

        old_version = (
            previous["playlist_version"]
            or ""
        )

        new_version = str(
            playlist_version
            or ""
        )

        if (
            old_version != new_version
            and (
                old_version
                or new_version
            )
        ):
            log_tv_event(
                conn,
                tv_id,
                "playlist_version",
                "info",
                "Nova versão de playlist",
                (
                    f"{old_version or '—'} "
                    f"→ {new_version or '—'}"
                )
            )

        if (
            (previous["sync_status"] or "")
            != sync_status
            and (
                previous["sync_status"]
                or sync_status
            )
        ):
            log_tv_event(
                conn,
                tv_id,
                "sync_status",
                (
                    "warning"
                    if sync_status == "offline"
                    else "info"
                ),
                "Estado de sincronização alterado",
                (
                    f"{previous['sync_status'] or '—'} "
                    f"→ {sync_status or '—'}"
                )
            )

        if (
            (previous["player_version"] or "")
            != player_version
            and player_version
        ):
            log_tv_event(
                conn,
                tv_id,
                "player_version",
                "info",
                "Versão do player alterada",
                (
                    f"{previous['player_version'] or '—'} "
                    f"→ {player_version}"
                )
            )

        if (
            previous["browser_running"]
            != browser_running
            and browser_running is not None
        ):
            log_tv_event(
                conn,
                tv_id,
                "browser_state",
                (
                    "error"
                    if browser_running == 0
                    else "info"
                ),
                (
                    "Chromium parado"
                    if browser_running == 0
                    else "Chromium voltou a executar"
                ),
                ""
            )

    conn.execute(
        """
        INSERT INTO tv_monitor_state
        (
            tv_id,
            health,
            reason,
            playlist_id,
            playlist_version,
            sync_status,
            player_version,
            browser_running,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)

        ON CONFLICT(tv_id)
        DO UPDATE SET
            health = excluded.health,
            reason = excluded.reason,
            playlist_id = excluded.playlist_id,
            playlist_version =
                excluded.playlist_version,
            sync_status = excluded.sync_status,
            player_version =
                excluded.player_version,
            browser_running =
                excluded.browser_running,
            updated_at = excluded.updated_at
        """,
        (
            tv_id,
            health,
            reason,
            playlist_id,
            str(playlist_version or ""),
            sync_status,
            player_version,
            browser_running,
            time.time(),
        )
    )


def monitor_cycle():

    conn = get_db()

    try:

        settings = monitor_settings(conn)

        rows = conn.execute(
            """
            SELECT
                tvs.*,
                tv_runtime_status.payload_json
                    AS runtime_payload_json
            FROM tvs
            LEFT JOIN tv_runtime_status
                ON tv_runtime_status.tv_id
                = tvs.tv_id
            ORDER BY tvs.tv_id
            """
        ).fetchall()

        now = time.time()

        for tv in rows:

            runtime = parse_runtime_payload(
                tv["runtime_payload_json"]
            )

            maintenance = maintenance_for_tv(
                conn,
                tv["tv_id"]
            )

            health, reason = evaluate_tv_health(
                tv,
                runtime,
                settings,
                maintenance,
                now
            )

            record_monitor_snapshot(
                conn,
                tv,
                runtime,
                health,
                reason
            )

        conn.commit()

    except Exception as exc:

        print(
            f"[MONITOR] Erro no ciclo: {exc}"
        )

        try:
            conn.rollback()
        except Exception:
            pass

    finally:
        conn.close()


def monitor_worker():

    while True:

        monitor_cycle()

        time.sleep(10)


@app.on_event("startup")
def start_monitor_worker():

    global _monitor_thread_started

    with _monitor_thread_lock:

        if _monitor_thread_started:
            return

        worker = threading.Thread(
            target=monitor_worker,
            name="digital-signage-monitor",
            daemon=True,
        )

        worker.start()

        _monitor_thread_started = True

        print(
            "[MONITOR] Worker operacional iniciado"
        )


def format_monitor_datetime(timestamp):

    if not timestamp:
        return "Nunca"

    try:
        return datetime.fromtimestamp(
            float(timestamp),
            APP_TIMEZONE
        ).strftime(
            "%d/%m/%Y %H:%M:%S"
        )

    except Exception:
        return "—"



# =========================================================
# HEARTBEAT
# =========================================================

@app.post("/api/tv/heartbeat")
def heartbeat(
    request: Request,
    data: dict
):

    tv_id = str(
        data.get("tv_id") or ""
    ).strip().upper()

    if not tv_id:
        return {
            "status": "error",
            "message": "tv_id não informado"
        }

    received_at = time.time()

    current_playlist_id = data.get(
        "current_playlist_id"
    )

    current_content_id = data.get(
        "current_content_id"
    )

    # This column describes the latest heartbeat, not a last-known inventory.
    # Missing/blank versions remain unknown; never substitute the server version.
    player_version = str(data.get("player_version") or "").strip()[:50]

    current_content_name = str(
        data.get(
            "current_content_name"
        )
        or ""
    )[:500]

    last_error = str(
        data.get("last_error")
        or ""
    )[:1000]

    sync_status = str(
        data.get("sync_status")
        or ""
    ).lower()

    # Hora do servidor, não do Raspberry.
    last_sync_at = (
        received_at
        if sync_status == "ok"
        else None
    )

    client_ip = (
        request.client.host
        if request.client
        else None
    )

    conn = get_db()
    require_device_auth(request, conn, tv_id)

    exists = conn.execute(
        """
        SELECT tv_id
        FROM tvs
        WHERE tv_id = ?
        """,
        (tv_id,)
    ).fetchone()

    if not exists:

        conn.close()

        return {
            "status": "error",
            "message": "TV não cadastrada"
        }

    conn.execute(
        """
        UPDATE tvs
        SET
            last_seen = ?,
            ip_address = ?,
            current_playlist_id = ?,
            current_content_id = ?,
            current_content_name = ?,
            player_version = ?,
            last_sync_at =
                COALESCE(?, last_sync_at),
            last_error = ?
        WHERE tv_id = ?
        """,
        (
            received_at,
            client_ip,
            current_playlist_id,
            current_content_id,
            current_content_name,
            player_version,
            last_sync_at,
            last_error,
            tv_id,
        )
    )

    runtime_payload = dict(data)

    runtime_payload[
        "_server_received_at"
    ] = received_at

    runtime_payload[
        "_client_ip"
    ] = client_ip

    conn.execute(
        """
        INSERT INTO tv_runtime_status
        (
            tv_id,
            updated_at,
            payload_json
        )
        VALUES (?, ?, ?)

        ON CONFLICT(tv_id)
        DO UPDATE SET
            updated_at =
                excluded.updated_at,
            payload_json =
                excluded.payload_json
        """,
        (
            tv_id,
            received_at,
            json.dumps(
                runtime_payload,
                ensure_ascii=False,
                separators=(",", ":")
            ),
        )
    )

    tv = conn.execute(
        """
        SELECT *
        FROM tvs
        WHERE tv_id = ?
        """,
        (tv_id,)
    ).fetchone()

    settings = monitor_settings(conn)

    maintenance = maintenance_for_tv(
        conn,
        tv_id
    )

    health, reason = evaluate_tv_health(
        tv,
        runtime_payload,
        settings,
        maintenance,
        received_at
    )

    record_monitor_snapshot(
        conn,
        tv,
        runtime_payload,
        health,
        reason
    )

    # Eventos da fila durável do Raspberry: reenvio idempotente.
    try:
        stability_accepted_ids = stability_ingest(conn, tv_id, runtime_payload, received_at)
    except Exception as exc:
        # Falha da telemetria adicional não deve derrubar o heartbeat já existente.
        stability_accepted_ids = []
        print(f"[STABILITY] Falha na coleta de {tv_id}: {exc}")

    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "server_time": received_at,
        "app_version": APP_VERSION,
        "accepted_stability_event_ids": stability_accepted_ids
    }


@app.get("/api/tvs/status")
def tvs_status(request: Request, ):
    user = require_user(request)
    conn = get_db()
    settings = monitor_settings(conn)
    rows = conn.execute(
        """
        SELECT tvs.*, playlists.name AS current_playlist_name,
               tv_runtime_status.updated_at AS runtime_updated_at,
               tv_runtime_status.payload_json AS runtime_payload_json
        FROM tvs
        LEFT JOIN playlists ON tvs.current_playlist_id = playlists.id
        LEFT JOIN tv_runtime_status ON tv_runtime_status.tv_id = tvs.tv_id
        ORDER BY tvs.tv_id
        """
    ).fetchall()
    allowed = allowed_tv_ids(conn, user)
    now = time.time(); result = []
    for row in rows:
        if row["tv_id"] not in allowed:
            continue
        runtime = parse_runtime_payload(row["runtime_payload_json"])
        maintenance = maintenance_for_tv(conn, row["tv_id"])
        health, reason = evaluate_tv_health(row, runtime, settings, maintenance, now)
        record_monitor_snapshot(conn, row, runtime, health, reason)
        position = runtime.get("playback_position"); duration = runtime.get("playback_duration"); progress = None
        try:
            if float(duration) > 0: progress = max(0.0, min(100.0, float(position) / float(duration) * 100.0))
        except (TypeError, ValueError): pass
        version = evaluate_player_version_status(
            row["player_version"] or runtime.get("player_version"),
            settings.get("recommended_player_version"),
        )
        online = row["last_seen"] is not None and now - float(row["last_seen"]) <= int(settings.get("offline_seconds") or 30)
        result.append({
            "tv_id": row["tv_id"], "name": row["name"], "location": row["location"],
            "online": online, "health": health, "health_reason": reason,
            "maintenance_enabled": maintenance["enabled"], "maintenance_note": maintenance["note"],
            "last_seen": row["last_seen"], "ip_address": row["ip_address"],
            "current_playlist_id": row["current_playlist_id"], "current_playlist_name": row["current_playlist_name"],
            "current_playlist_version": runtime.get("current_playlist_version"),
            "current_content_id": row["current_content_id"],
            "current_content_name": row["current_content_name"] or runtime.get("current_content_name") or "",
            "current_content_type": runtime.get("current_content_type"),
            "playback_position": position, "playback_duration": duration,
            "playback_state": runtime.get("playback_state"), "progress_percent": progress,
            "browser_running": runtime.get("browser_running"),
            **version,
            "sync_status": runtime.get("sync_status"), "last_sync_at": row["last_sync_at"],
            "last_error": row["last_error"] or "", "cache_files": runtime.get("cache_files"),
            "cache_bytes": runtime.get("cache_bytes"), "disk_free_mb": runtime.get("disk_free_mb"),
            "runtime_updated_at": row["runtime_updated_at"],
            "temperature_c": runtime.get("temperature_c", runtime.get("temperature")),
            "cpu_percent": runtime.get("cpu_percent"),
            "memory_percent": runtime.get("memory_percent", runtime.get("ram_percent")),
            "uptime_seconds": runtime.get("uptime_seconds"),
            "network_type": runtime.get("network_type"), "network_interface": runtime.get("network_interface"),
            "wifi_signal": runtime.get("wifi_signal"), "browser_restart_count": runtime.get("browser_restart_count"),
            "last_recovery_reason": runtime.get("last_recovery_reason"), "stall_count": runtime.get("stall_count"),
            "os_version": runtime.get("os_version"), "hw_video_decode": runtime.get("hw_video_decode"),
        })
    conn.commit(); conn.close()
    return {"server_time": now, "app_version": APP_VERSION, "settings": settings, "tvs": result}


@app.get(
    "/players",
    response_class=HTMLResponse
)
def players_page(request: Request):

    return templates.TemplateResponse(
        request=request,
        name="players.html",
        context={
            "app_version": APP_VERSION
        }
    )


@app.get(
    "/players/settings",
    response_class=HTMLResponse
)
def players_settings_page(
    request: Request
):

    require_role(request, "admin")
    return templates.TemplateResponse(
        request=request,
        name="monitor_settings.html",
        context={
            "app_version": APP_VERSION,
            "settings":
                monitor_settings(),
        }
    )


@app.post("/players/settings")
def players_settings_save(request: Request, 
    recommended_player_version:
        str = Form(""),
    offline_seconds:
        int = Form(...),
    disk_warning_mb:
        int = Form(...),
    sync_warning_seconds:
        int = Form(...)
):

    require_role(request, "admin")
    offline_seconds = max(
        10,
        min(
            3600,
            offline_seconds
        )
    )

    disk_warning_mb = max(
        100,
        min(
            1000000,
            disk_warning_mb
        )
    )

    sync_warning_seconds = max(
        30,
        min(
            86400,
            sync_warning_seconds
        )
    )

    conn = get_db()

    conn.execute(
        """
        UPDATE tv_monitor_settings
        SET
            recommended_player_version = ?,
            offline_seconds = ?,
            disk_warning_mb = ?,
            sync_warning_seconds = ?,
            updated_at = ?
        WHERE id = 1
        """,
        (
            recommended_player_version.strip()[:50],
            offline_seconds,
            disk_warning_mb,
            sync_warning_seconds,
            time.time(),
        )
    )

    conn.commit()
    conn.close()

    monitor_cycle()

    return RedirectResponse(
        "/players/settings?" + urlencode({"message": "Configurações salvas.", "kind": "success"}),
        status_code=303
    )


@app.get("/players/stability", response_class=HTMLResponse)
def stability_overview(request: Request, period: str = "night", date: str = ""):
    user = require_user(request)
    start, end, label, chosen = stability_window(period, date)
    conn = get_db()
    try:
        allowed = allowed_tv_ids(conn, user)
        tvs = conn.execute("SELECT tv_id,name,location FROM tvs ORDER BY name,tv_id").fetchall()
        summaries = []
        for tv in tvs:
            if tv["tv_id"] not in allowed:
                continue
            item = stability_summarize(conn,tv["tv_id"],start,end)
            item.update({"name":tv["name"], "location":tv["location"]})
            summaries.append(item)
    finally:
        conn.close()
    return templates.TemplateResponse(request=request,name="stability.html",context={
        "app_version":APP_VERSION,"overview":True,"summaries":summaries,
        "period":chosen,"night_date":date,"window_label":label})


@app.get("/players/{tv_id}/stability", response_class=HTMLResponse)
def stability_details(request: Request, tv_id: str, period: str = "night", date: str = ""):
    require_tv_access(request,tv_id,"viewer")
    tv_id=tv_id.strip().upper()
    start,end,label,chosen=stability_window(period,date)
    conn=get_db()
    try:
        tv=conn.execute("SELECT tv_id,name,location FROM tvs WHERE tv_id=?",(tv_id,)).fetchone()
        if not tv:
            raise HTTPException(status_code=404,detail="TV não encontrada")
        summary=stability_summarize(conn,tv_id,start,end)
    finally:
        conn.close()
    return templates.TemplateResponse(request=request,name="stability.html",context={
        "app_version":APP_VERSION,"overview":False,"tv":tv,"summary":summary,
        "period":chosen,"night_date":date,"window_label":label})


@app.get(
    "/players/{tv_id}",
    response_class=HTMLResponse
)
def player_details_page(
    request: Request,
    tv_id: str,
    severity: str = "",
    event_type: str = "",
    days: int = 7,
    page: int = 1
):
    require_tv_access(request, tv_id)
    tv_id = tv_id.strip().upper(); days = max(1, min(3650, int(days or 7))); page = max(1, int(page or 1)); page_size = 50
    conn = get_db()
    row = conn.execute(
        """
        SELECT tvs.*, playlists.name AS current_playlist_name,
               tv_runtime_status.updated_at AS runtime_updated_at,
               tv_runtime_status.payload_json AS runtime_payload_json
        FROM tvs
        LEFT JOIN playlists ON tvs.current_playlist_id = playlists.id
        LEFT JOIN tv_runtime_status ON tv_runtime_status.tv_id = tvs.tv_id
        WHERE tvs.tv_id = ?
        """, (tv_id,)
    ).fetchone()
    if not row:
        conn.close(); raise HTTPException(status_code=404, detail="TV não encontrada.")
    settings = monitor_settings(conn); runtime = parse_runtime_payload(row["runtime_payload_json"])
    maintenance = maintenance_for_tv(conn, tv_id); health, health_reason = evaluate_tv_health(row, runtime, settings, maintenance)

    conditions = ["tv_id = ?", "created_at >= ?"]; params = [tv_id, time.time() - days * 86400]
    if severity in {"info", "warning", "error"}:
        conditions.append("severity = ?"); params.append(severity)
    else: severity = ""
    event_type = event_type.strip()[:80]
    if event_type:
        conditions.append("event_type LIKE ?"); params.append(f"%{event_type}%")
    where = " AND ".join(conditions)
    total = conn.execute(f"SELECT COUNT(*) AS c FROM tv_event_log WHERE {where}", params).fetchone()["c"]
    total_pages = max(1, (total + page_size - 1) // page_size); page = min(page, total_pages)
    rows = conn.execute(
        f"SELECT * FROM tv_event_log WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        [*params, page_size, (page - 1) * page_size]
    ).fetchall()
    events = []
    for event in rows:
        duration_text = ""
        if event["event_type"] == "health_offline":
            recovery = conn.execute(
                "SELECT created_at FROM tv_event_log WHERE tv_id=? AND created_at>? AND event_type IN ('health_ok', 'health_warning', 'health_error') ORDER BY created_at ASC LIMIT 1",
                (tv_id, event["created_at"])
            ).fetchone()
            end = recovery["created_at"] if recovery else time.time()
            duration_text = ("em andamento · " if not recovery else "") + format_event_duration(end - event["created_at"])
        events.append({"created_at_text": format_monitor_datetime(event["created_at"]), "severity": event["severity"],
                       "event_type": event["event_type"], "title": event["title"], "detail": event["detail"],
                       "duration_text": duration_text})

    runtime_items = []
    for key in sorted(runtime.keys()):
        if key.startswith("_"): continue
        value = runtime[key]
        if isinstance(value, (dict, list)): value = json.dumps(value, ensure_ascii=False)
        runtime_items.append((key, str(value)))

    def diag(label, value, suffix=""):
        return {"label": label, "value": (f"{value}{suffix}" if value not in (None, "") else "Aguardando telemetria")}
    diagnostics = [
        diag("Temperatura", runtime.get("temperature_c", runtime.get("temperature")), " °C"),
        diag("CPU", runtime.get("cpu_percent"), "%"),
        diag("RAM", runtime.get("memory_percent", runtime.get("ram_percent")), "%"),
        diag("Uptime", human_duration(runtime.get("uptime_seconds")) if runtime.get("uptime_seconds") is not None else None),
        diag("Rede", runtime.get("network_type")), diag("Interface", runtime.get("network_interface")),
        diag("Sinal Wi‑Fi", runtime.get("wifi_signal")), diag("SO", runtime.get("os_version")),
        diag("Reinícios Chromium", runtime.get("browser_restart_count")), diag("Stalls", runtime.get("stall_count")),
        diag("Última recuperação", runtime.get("last_recovery_reason")), diag("HW decode", runtime.get("hw_video_decode")),
    ]
    health_labels = {"ok":"OK","warning":"Atenção","error":"Erro","offline":"Offline","maintenance":"Manutenção"}
    status = {
        "health": health, "health_label": health_labels.get(health, health), "health_reason": health_reason,
        **evaluate_player_version_status(
            row["player_version"] or runtime.get("player_version"),
            settings.get("recommended_player_version"),
        ),
        "ip_address": row["ip_address"],
        "current_playlist_name": row["current_playlist_name"], "current_content_name": row["current_content_name"],
        "playback_state": runtime.get("playback_state"), "browser_running": runtime.get("browser_running"),
        "sync_status": runtime.get("sync_status"), "last_seen_text": format_monitor_datetime(row["last_seen"]),
    }
    query_base = {"severity": severity, "event_type": event_type, "days": days}
    csv_url = f"/players/{tv_id}/events.csv?" + urlencode(query_base)
    prev_url = f"/players/{tv_id}?" + urlencode({**query_base, "page": max(1,page-1)})
    next_url = f"/players/{tv_id}?" + urlencode({**query_base, "page": min(total_pages,page+1)})
    releases = list_active_releases(conn) if require_user(request)["role"] == "admin" else []
    conn.close()
    return templates.TemplateResponse(request=request, name="player_detail.html", context={
        "app_version": APP_VERSION, "tv": row, "status": status, "maintenance": maintenance, "settings": settings,
        "events": events, "runtime_items": runtime_items, "diagnostics": diagnostics,
        "releases": releases,
        "filters": {"severity": severity, "event_type": event_type, "days": days}, "page": page,
        "total_pages": total_pages, "csv_url": csv_url, "prev_url": prev_url, "next_url": next_url,
    })


@app.get("/players/{tv_id}/events.csv")
def player_events_csv(request: Request, 
    tv_id: str,
    severity: str = "",
    event_type: str = "",
    days: int = 7
):
    require_tv_access(request, tv_id)
    tv_id = tv_id.strip().upper(); days = max(1, min(3650, int(days or 7)))
    conn = get_db(); conditions = ["tv_id = ?", "created_at >= ?"]; params = [tv_id, time.time() - days * 86400]
    if severity in {"info", "warning", "error"}: conditions.append("severity = ?"); params.append(severity)
    if event_type.strip(): conditions.append("event_type LIKE ?"); params.append(f"%{event_type.strip()[:80]}%")
    rows = conn.execute(f"SELECT * FROM tv_event_log WHERE {' AND '.join(conditions)} ORDER BY created_at DESC", params).fetchall(); conn.close()
    stream = io.StringIO(); writer = csv.writer(stream, delimiter=';')
    writer.writerow(["data_hora", "tv_id", "severidade", "tipo", "evento", "detalhe"])
    for row in rows:
        writer.writerow([format_monitor_datetime(row["created_at"]), row["tv_id"], row["severity"], row["event_type"], row["title"], row["detail"] or ""])
    body = '\ufeff' + stream.getvalue()
    return Response(content=body, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="eventos_{tv_id}.csv"'})


@app.post(
    "/players/{tv_id}/maintenance"
)
def player_maintenance(request: Request, 
    tv_id: str,
    action: str = Form(...),
    note: str = Form("")
):

    user = require_tv_access(request, tv_id, "operator")
    tv_id = tv_id.strip().upper()

    conn = get_db()

    exists = conn.execute(
        """
        SELECT tv_id
        FROM tvs
        WHERE tv_id = ?
        """,
        (tv_id,)
    ).fetchone()

    if not exists:

        conn.close()

        raise HTTPException(
            status_code=404,
            detail="TV não encontrada."
        )

    enable = (
        action.strip().lower()
        == "enable"
    )

    clean_note = (
        note.strip()[:1000]
        if enable
        else ""
    )

    conn.execute(
        """
        INSERT INTO tv_maintenance
        (
            tv_id,
            enabled,
            note,
            updated_at
        )
        VALUES (?, ?, ?, ?)

        ON CONFLICT(tv_id)
        DO UPDATE SET
            enabled =
                excluded.enabled,
            note =
                excluded.note,
            updated_at =
                excluded.updated_at
        """,
        (
            tv_id,
            1 if enable else 0,
            clean_note,
            time.time(),
        )
    )

    log_tv_event(
        conn,
        tv_id,
        (
            "maintenance_enabled"
            if enable
            else "maintenance_disabled"
        ),
        "info",
        (
            "Modo manutenção ativado"
            if enable
            else "Modo manutenção encerrado"
        ),
        clean_note
    )

    audit_event(
        conn,
        request,
        (
            "player.maintenance_enabled"
            if enable
            else "player.maintenance_disabled"
        ),
        "tv",
        tv_id,
        json.dumps(
            {
                "enabled": enable,
                "actor": user["username"],
                "note": clean_note,
            },
            ensure_ascii=False,
        ),
    )

    conn.commit()
    conn.close()

    monitor_cycle()

    return RedirectResponse(
        f"/players/{tv_id}",
        status_code=303
    )


# =========================================================
# MANIFESTO DE CACHE DO PLAYER
# =========================================================

@app.get("/api/player/{tv_id}/cache-manifest")
def player_cache_manifest(request: Request, tv_id: str):

    tv_id = tv_id.strip().upper()
    conn = get_db()
    if getattr(request.state, "web_player_tv_id", None) != tv_id:
        require_device_auth(request, conn, tv_id)

    tv = conn.execute(
        "SELECT tv_id FROM tvs WHERE tv_id = ?",
        (tv_id,)
    ).fetchone()

    if not tv:
        conn.close()
        raise HTTPException(status_code=404, detail="TV não encontrada.")

    playlist_rows = conn.execute(
        """
        SELECT playlist_id
        FROM tv_playlists
        WHERE tv_id = ?

        UNION

        SELECT playlist_id
        FROM schedules
        WHERE tv_id = ?
        AND enabled = 1
        """,
        (tv_id, tv_id)
    ).fetchall()

    playlist_ids = sorted({
        row["playlist_id"]
        for row in playlist_rows
        if row["playlist_id"] is not None
    })

    resources = set()
    playlists = []

    archived_ids = archived_content_ids(conn)

    for playlist_id in playlist_ids:
        publication = conn.execute(
            """
            SELECT
                playlist_publications.version,
                playlist_publications.snapshot_json,
                playlists.name
            FROM playlist_publications
            INNER JOIN playlists
                ON playlist_publications.playlist_id = playlists.id
            WHERE playlist_publications.playlist_id = ?
            """,
            (playlist_id,)
        ).fetchone()

        if not publication:
            continue

        try:
            snapshot = json.loads(publication["snapshot_json"] or "{}")
        except json.JSONDecodeError:
            snapshot = {}

        playlists.append({
            "id": playlist_id,
            "name": snapshot.get("playlist_name") or publication["name"],
            "version": publication["version"],
        })

        for item in snapshot.get("items", []):
            if not isinstance(item, dict):
                continue

            if item.get("content_id") in archived_ids:
                continue

            filename = item.get("filename")
            content_type = item.get("content_type")

            if content_type == "video" and isinstance(filename, str):
                resources.add(media_url(filename))

            elif content_type == "image":
                pages = item.get("pages", [])
                if isinstance(pages, list) and pages:
                    for page in pages:
                        if isinstance(page, str):
                            resources.add(media_url(page))
                elif isinstance(filename, str):
                    resources.add(media_url(filename))

    conn.close()

    return {
        "tv_id": tv_id,
        "app_version": APP_VERSION,
        "server_time": time.time(),
        "playlists": playlists,
        "resources": sorted(resources)
    }

# =========================================================
# API PLAYER
# =========================================================

@app.get("/api/player/{tv_id}")
def player_content(request: Request, tv_id: str):

    tv_id = tv_id.strip().upper()
    conn = get_db()
    if getattr(request.state, "web_player_tv_id", None) != tv_id:
        require_device_auth(request, conn, tv_id)

    scheduled = get_active_schedule(tv_id)

    playlist_id = None
    playlist_name = None
    schedule_id = None
    source = "default"

    if scheduled:
        playlist_id = scheduled["playlist_id"]
        playlist_name = scheduled["playlist_name"]
        schedule_id = scheduled["id"]
        source = "schedule"
    else:
        default_playlist = conn.execute(
            """
            SELECT
                playlists.id,
                playlists.name
            FROM tv_playlists
            INNER JOIN playlists
                ON tv_playlists.playlist_id = playlists.id
            INNER JOIN playlist_publications
                ON playlist_publications.playlist_id = playlists.id
            WHERE tv_playlists.tv_id = ?
            """,
            (tv_id,)
        ).fetchone()

        if default_playlist:
            playlist_id = default_playlist["id"]
            playlist_name = default_playlist["name"]

    if playlist_id is not None:
        publication = conn.execute(
            """
            SELECT version, published_at, snapshot_json
            FROM playlist_publications
            WHERE playlist_id = ?
            """,
            (playlist_id,)
        ).fetchone()

        if publication:
            try:
                snapshot = json.loads(publication["snapshot_json"] or "{}")
            except json.JSONDecodeError:
                snapshot = {}

            playlist = snapshot_to_player_playlist(
                snapshot,
                archived_content_ids(conn)
            )
            playlist_name = snapshot.get("playlist_name") or playlist_name
            published_version = publication["version"]

            revision_payload = {
                "playlist_id": playlist_id,
                "published_version": published_version,
                "source": source,
                "schedule_id": schedule_id,
                "playlist": playlist,
            }

            revision = hashlib.sha256(
                json.dumps(
                    revision_payload,
                    ensure_ascii=False,
                    sort_keys=True
                ).encode("utf-8")
            ).hexdigest()[:16]

            conn.close()

            return {
                "playlist_id": playlist_id,
                "playlist_name": playlist_name,
                "source": source,
                "schedule_id": schedule_id,
                "published_version": published_version,
                "revision": revision,
                "server_time": time.time(),
                "app_version": APP_VERSION,
                "playlist": playlist,
            }

    conn.close()

    revision_payload = {
        "playlist_id": None,
        "source": "none",
        "schedule_id": None,
        "playlist": []
    }

    revision = hashlib.sha256(
        json.dumps(
            revision_payload,
            ensure_ascii=False,
            sort_keys=True
        ).encode("utf-8")
    ).hexdigest()[:16]

    return {
        "playlist_id": None,
        "playlist_name": None,
        "source": "none",
        "schedule_id": None,
        "published_version": None,
        "revision": revision,
        "server_time": time.time(),
        "app_version": APP_VERSION,
        "playlist": []
    }


# =========================================================
# COMANDOS REMOTOS / DEVICE TOKEN / PROVISIONAMENTO
# =========================================================

@app.post("/players/{tv_id}/command")
def player_command(
    request: Request,
    tv_id: str,
    command: str = Form(...),
    release_id: int | None = Form(None),
):
    require_tv_access(request, tv_id, "operator")
    require_role(request, "operator")
    tv_id = tv_id.strip().upper()
    conn = get_db()
    tv = conn.execute("SELECT * FROM tvs WHERE tv_id = ?", (tv_id,)).fetchone()
    if not tv:
        conn.close(); raise HTTPException(status_code=404, detail="TV não encontrada.")
    args = {}
    if command == "update_player":
        require_role(request, "admin")
        if not release_id:
            conn.close(); raise HTTPException(status_code=400, detail="Selecione uma release.")
        rel = conn.execute("SELECT * FROM player_releases WHERE id = ?", (release_id,)).fetchone()
        if not rel:
            conn.close(); raise HTTPException(status_code=404, detail="Release não encontrada.")
        args = {
            "version": rel["version"],
            "url": f"/api/player/{tv_id}/release/{rel['filename']}",
            "sha256": rel["sha256"],
            "filename": rel["filename"],
        }
    allowed = {"sync_now", "restart_browser", "restart_player", "clear_cache", "diagnose", "update_player", "reboot"}
    if command not in allowed:
        conn.close(); raise HTTPException(status_code=400, detail="Comando inválido.")
    command_id = enqueue_player_command(conn, request, tv_id, command, args)
    if command == "update_player" and release_id:
        conn.execute(
            "INSERT INTO player_update_jobs (release_id, tv_id, command_id, status, created_at, updated_at) VALUES (?, ?, ?, 'queued', ?, ?)",
            (release_id, tv_id, command_id, time.time(), time.time()),
        )
    conn.commit(); conn.close()
    return RedirectResponse(f"/players/{tv_id}?" + urlencode({"message": "Comando enviado ao player.", "kind": "success"}), status_code=303)


@app.post("/players/{tv_id}/device-token", response_class=HTMLResponse)
def rotate_device_token(request: Request, tv_id: str):
    require_role(request, "admin")
    tv_id = tv_id.strip().upper()
    token = secrets.token_urlsafe(32)
    conn = get_db()
    row = conn.execute("SELECT * FROM tvs WHERE tv_id = ?", (tv_id,)).fetchone()
    if not row:
        conn.close(); raise HTTPException(status_code=404, detail="TV não encontrada.")
    conn.execute(
        "UPDATE tvs SET device_token_hash = ?, device_token_last_rotated_at = ? WHERE tv_id = ?",
        (hash_token(token), time.time(), tv_id),
    )
    audit_event(conn, request, "device.rotate_token", "tv", tv_id, "Nova credencial gerada")
    conn.commit(); conn.close()
    return templates.TemplateResponse(request=request, name="device_token.html", context={
        "app_version": APP_VERSION,
        "tv_id": tv_id,
        "token": token,
        "server_base_example": "http://SEU-SERVIDOR:8000",
    })


@app.post("/players/{tv_id}/provision-code", response_class=HTMLResponse)
def generate_provision_code(request: Request, tv_id: str):
    require_role(request, "admin")
    tv_id = tv_id.strip().upper()
    code = f"{secrets.randbelow(10000):04d}-{secrets.token_hex(2).upper()}"
    expires_at = time.time() + 3600
    conn = get_db()
    row = conn.execute("SELECT * FROM tvs WHERE tv_id = ?", (tv_id,)).fetchone()
    if not row:
        conn.close(); raise HTTPException(status_code=404, detail="TV não encontrada.")
    conn.execute(
        "UPDATE tvs SET provision_code_hash = ?, provision_code_expires_at = ? WHERE tv_id = ?",
        (hash_token(code), expires_at, tv_id),
    )
    audit_event(conn, request, "device.provision_code", "tv", tv_id, "Código de provisionamento gerado")
    conn.commit(); conn.close()
    server_base = str(request.base_url).rstrip("/")
    installer_command = (
        f"curl -fsSL {server_base}/bootstrap/install.sh | "
        f"bash -s -- --server {server_base} --tv-id {tv_id} --code {code}"
    )
    return templates.TemplateResponse(request=request, name="provision_code.html", context={
        "app_version": APP_VERSION,
        "tv_id": tv_id,
        "code": code,
        "expires_at": format_monitor_datetime(expires_at),
        "server_base": server_base,
        "installer_command": installer_command,
    })


@app.post("/api/player/provision")
def api_player_provision(request: Request, data: dict):
    tv_id = str(data.get("tv_id") or "").strip().upper()
    code = str(data.get("code") or "").strip()
    if not tv_id or not code:
        raise HTTPException(status_code=400, detail="tv_id e code são obrigatórios.")
    conn = get_db()
    row = conn.execute("SELECT * FROM tvs WHERE tv_id = ?", (tv_id,)).fetchone()
    if not row or not row["provision_code_hash"] or float(row["provision_code_expires_at"] or 0) < time.time():
        conn.close(); raise HTTPException(status_code=401, detail="Código inválido ou expirado.")
    if not hmac.compare_digest(hash_token(code), row["provision_code_hash"]):
        conn.close(); raise HTTPException(status_code=401, detail="Código inválido.")
    # Um novo provisionamento representa uma nova sessão lógica do dispositivo.
    # Comandos antigos (principalmente update_player) não podem sobreviver à troca
    # de token, pois poderiam ser executados pelo player recém-instalado e até
    # provocar downgrade para uma release anterior.
    now = time.time()
    conn.execute(
        """
        UPDATE player_commands
        SET status = 'failed', progress = 100,
            message = 'Cancelado automaticamente por novo provisionamento do dispositivo.',
            completed_at = COALESCE(completed_at, ?)
        WHERE tv_id = ? AND status IN ('pending', 'dispatched', 'running')
        """,
        (now, tv_id),
    )
    conn.execute(
        """
        UPDATE player_update_jobs
        SET status = 'failed', updated_at = ?
        WHERE tv_id = ? AND status = 'queued'
        """,
        (now, tv_id),
    )

    token = secrets.token_urlsafe(32)
    conn.execute(
        """
        UPDATE tvs
        SET device_token_hash = ?, device_token_last_rotated_at = ?, provision_code_hash = NULL,
            provision_code_expires_at = NULL, enrolled_at = COALESCE(enrolled_at, ?)
        WHERE tv_id = ?
        """,
        (hash_token(token), time.time(), time.time(), tv_id),
    )
    settings = conn.execute(
        "SELECT recommended_player_version FROM tv_monitor_settings WHERE id = 1"
    ).fetchone()
    recommended_version = str(settings["recommended_player_version"] or "") if settings else ""
    release = None
    if recommended_version:
        release = conn.execute(
            "SELECT * FROM player_releases WHERE version = ? ORDER BY id DESC LIMIT 1",
            (recommended_version,),
        ).fetchone()
    if not release:
        release = conn.execute(
            "SELECT * FROM player_releases WHERE status = 'stable' ORDER BY published_at DESC, created_at DESC LIMIT 1"
        ).fetchone()

    audit_event(conn, None, "device.provision", "tv", tv_id, "Player provisionado por código")
    conn.commit()
    response = {
        "status": "ok",
        "tv_id": tv_id,
        "device_token": token,
        "recommended_player_version": recommended_version,
        "server_time": time.time(),
    }
    if release:
        response["release"] = {
            "version": release["version"],
            "filename": release["filename"],
            "sha256": release["sha256"],
            "url": f"/api/player/{tv_id}/release/{release['filename']}",
        }
    conn.close()
    return response


@app.get("/api/player/{tv_id}/commands")
def api_player_commands(request: Request, tv_id: str):
    tv_id = tv_id.strip().upper()
    conn = get_db(); require_device_auth(request, conn, tv_id)
    rows = conn.execute(
        """
        SELECT * FROM player_commands
        WHERE tv_id = ? AND status IN ('pending', 'dispatched')
        ORDER BY created_at ASC
        LIMIT 5
        """,
        (tv_id,),
    ).fetchall()
    now = time.time()
    commands = []
    for row in rows:
        if row["status"] == "pending":
            conn.execute("UPDATE player_commands SET status = 'dispatched', dispatched_at = COALESCE(dispatched_at, ?) WHERE id = ?", (now, row["id"]))
        try: args = json.loads(row["args_json"] or "{}")
        except json.JSONDecodeError: args = {}
        commands.append({"id": row["id"], "command": row["command"], "args": args, "status": row["status"], "created_at": row["created_at"]})
    conn.commit(); conn.close()
    return {"tv_id": tv_id, "server_time": now, "commands": commands}


@app.post("/api/player/{tv_id}/commands/{command_id}/ack")
def api_player_command_ack(request: Request, tv_id: str, command_id: int, data: dict):
    tv_id = tv_id.strip().upper()
    conn = get_db(); require_device_auth(request, conn, tv_id)
    status = str(data.get("status") or "running").strip().lower()
    if status not in {"running", "done", "failed"}:
        status = "running"
    progress = int(max(0, min(100, int(data.get("progress") or 0))))
    message = str(data.get("message") or "")[:1000]
    result_json = json.dumps(data.get("result") or {}, ensure_ascii=False)
    completed_at = time.time() if status in {"done", "failed"} else None
    conn.execute(
        """
        UPDATE player_commands
        SET status = ?, progress = ?, message = ?, result_json = ?, completed_at = COALESCE(?, completed_at)
        WHERE id = ? AND tv_id = ?
        """,
        (status, progress, message, result_json, completed_at, command_id, tv_id),
    )
    if status in {"done", "failed"}:
        conn.execute(
            "UPDATE player_update_jobs SET status = ?, updated_at = ? WHERE command_id = ?",
            ("completed" if status == "done" else "failed", time.time(), command_id),
        )
    conn.commit(); conn.close()
    return {"status": "ok", "server_time": time.time()}



@app.get("/bootstrap/install.sh")
def bootstrap_install_script():
    path = BASE_DIR / "bootstrap" / "install_player.sh"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Instalador não encontrado.")
    return Response(
        content=path.read_text(encoding="utf-8"),
        media_type="text/x-shellscript",
        headers={"Content-Disposition": 'inline; filename="install_player.sh"'},
    )


@app.get("/api/player/{tv_id}/release/{filename}")
def api_player_release_download(request: Request, tv_id: str, filename: str):
    tv_id = tv_id.strip().upper()
    conn = get_db(); require_device_auth(request, conn, tv_id); conn.close()
    safe = Path(filename).name
    path = RELEASE_DIR / safe
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Release não encontrada.")
    return Response(content=path.read_bytes(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{safe}"'})

@app.get("/releases/files/{filename}")
def download_release(request: Request, filename: str):
    # Pode ser acessado pelo player com token; mantemos a validação do token nas APIs de comando.
    require_role(request, "admin")
    safe = Path(filename).name
    path = RELEASE_DIR / safe
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Release não encontrada.")
    return Response(content=path.read_bytes(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{safe}"'})


# =========================================================
# PLAYER
# =========================================================

@app.get("/api/web-player/{tv_id}")
def web_player_content(request: Request, tv_id: str):
    normalized = tv_id.strip().upper()
    require_tv_access(request, normalized)
    request.state.web_player_tv_id = normalized
    return player_content(request, normalized)


@app.get("/api/web-player/{tv_id}/cache-manifest")
def web_player_cache_manifest(request: Request, tv_id: str):
    normalized = tv_id.strip().upper()
    require_tv_access(request, normalized)
    request.state.web_player_tv_id = normalized
    return player_cache_manifest(request, normalized)

@app.get(
    "/player/{tv_id}",
    response_class=HTMLResponse
)
def player(
    request: Request,
    tv_id: str
):
    require_tv_access(request, tv_id)

    return templates.TemplateResponse(
        request=request,
        name="player.html",
        context={
            "tv_id": tv_id
        }
    )

# =========================================================
# GALERIA DE CONTEÚDOS
# =========================================================

@app.get(
    "/gallery",
    response_class=HTMLResponse
)
def gallery_page(
    request: Request,
    playlist_id: int | None = None,
    archived: int = 0
):

    user = require_user(request)
    conn = get_db()

    view_archived = bool(archived)

    archive_filter = (
        "archived_at IS NOT NULL"
        if view_archived
        else "archived_at IS NULL"
    )

    rows = conn.execute(
        f"""
        SELECT
            id,
            name,
            filename,
            content_type,
            original_filename,
            created_at,
            archived_at
        FROM contents
        WHERE {archive_filter}
        ORDER BY
            COALESCE(
                archived_at,
                created_at
            ) DESC,
            id DESC
        """
    ).fetchall()

    rows = [r for r in rows if can_access_content(conn, user, r["id"])]
    viewable_playlists = [r for r in conn.execute("SELECT id, name FROM playlists ORDER BY name") if can_access_playlist(conn, user, r["id"])]
    playlists = [r for r in viewable_playlists if can_access_playlist(conn, user, r["id"], edit=True)]

    contents = []

    for row in rows:
        legacy_format = row["content_type"] not in {"image", "video"}
        if legacy_format:
            try:
                legacy_size = safe_media_path(row["filename"]).stat().st_size
            except (OSError, HTTPException):
                legacy_size = 0
            metadata = {
                "size_bytes": legacy_size,
                "validation_status": "warning",
                "validation_message": "Formato legado não suportado; o arquivo não será reproduzido.",
            }
        else:
            metadata = ensure_content_metadata(
                conn,
                row
            )

        usage_names, protected = content_usage(conn, row["id"])
        if user["role"] != "admin":
            visible_playlist_names = {p["name"] for p in viewable_playlists}
            usage_names = [name for name in usage_names if name in visible_playlist_names]

        contents.append({
            "id":
                row["id"],

            "name":
                row["name"],

            "filename":
                row["filename"],

            "content_type":
                row["content_type"],

            "legacy_format":
                legacy_format,

            "original_filename":
                row["original_filename"],

            "created_at":
                row["created_at"],

            "created_at_text":
                format_monitor_datetime(
                    row["created_at"]
                ),

            "archived_at":
                row["archived_at"],

            "archived_at_text":
                format_monitor_datetime(
                    row["archived_at"]
                )
                if row["archived_at"]
                else "",

            "url":
                media_url(
                    row["filename"]
                ),

            "size_bytes":
                metadata.get(
                    "size_bytes"
                ) or 0,

            "size_text":
                human_bytes(
                    metadata.get(
                        "size_bytes"
                    )
                ),

            "width":
                metadata.get("width"),

            "height":
                metadata.get("height"),

            "duration_sec":
                metadata.get(
                    "duration_sec"
                ),

            "duration_text":
                human_duration(
                    metadata.get(
                        "duration_sec"
                    )
                ),

            "codec":
                metadata.get("codec"),

            "validation_status":
                metadata.get(
                    "validation_status"
                )
                or "warning",

            "validation_message":
                metadata.get(
                    "validation_message"
                )
                or (
                    "Validação "
                    "não disponível."
                ),

            "usage_names":
                usage_names,

            "usage_count":
                len(usage_names),

            "protected":
                protected,
            "can_edit": can_access_content(conn, user, row["id"], edit=True),
        })

    conn.commit()
    conn.close()

    return templates.TemplateResponse(
        request=request,
        name="gallery.html",
        context={
            "app_version":
                APP_VERSION,

            "contents":
                contents,

            "playlists":
                playlists,

            "selected_playlist_id":
                playlist_id,

            "view_archived":
                view_archived,
        }
    )

@app.get(
    "/playlists",
    response_class=HTMLResponse
)
def playlists_page(request: Request):

    user = require_user(request)
    conn = get_db()

    playlists_rows = [r for r in conn.execute("SELECT * FROM playlists ORDER BY name")
                      if can_access_playlist(conn, user, r["id"])]

    allowed = allowed_tv_ids(conn, user)
    tvs = [r for r in conn.execute("SELECT * FROM tvs ORDER BY tv_id") if r["tv_id"] in allowed]

    playlists = []
    published_playlist_ids = []

    for playlist in playlists_rows:

        item_rows = conn.execute("""
            SELECT
                playlist_items.*,
                contents.name AS content_name,
                contents.content_type,
                contents.original_filename,
                contents.filename
            FROM playlist_items
            INNER JOIN contents
                ON playlist_items.content_id = contents.id
            WHERE playlist_items.playlist_id = ?
            AND contents.archived_at IS NULL
            ORDER BY playlist_items.position
        """, (playlist["id"],)).fetchall()

        items = []
        for item in item_rows:
            items.append({
                "id": item["id"],
                "playlist_id": item["playlist_id"],
                "content_id": item["content_id"],
                "position": item["position"],
                "duration_sec": item["duration_sec"],
                "content_name": item["content_name"],
                "content_type": item["content_type"],
                "original_filename": item["original_filename"],
                "filename": item["filename"],
                "url": media_url(item["filename"]),
            })

        draft_snapshot = build_playlist_snapshot(
            conn,
            playlist["id"]
        )

        publication = conn.execute(
            """
            SELECT version, published_at, snapshot_json
            FROM playlist_publications
            WHERE playlist_id = ?
            """,
            (playlist["id"],)
        ).fetchone()

        publication_status = "unpublished"
        published_version = None
        published_at = None
        published_at_text = ""

        if publication:
            published_playlist_ids.append(playlist["id"])
            published_version = publication["version"]
            published_at = publication["published_at"]
            published_at_text = format_monitor_datetime(
                published_at
            )

            try:
                published_snapshot = json.loads(
                    publication["snapshot_json"] or "{}"
                )
            except json.JSONDecodeError:
                published_snapshot = {}

            if snapshot_signature(draft_snapshot) == snapshot_signature(published_snapshot):
                publication_status = "published"
            else:
                publication_status = "dirty"

        history_rows = conn.execute(
            """
            SELECT version, published_at, source
            FROM playlist_publication_history
            WHERE playlist_id = ?
            ORDER BY version DESC
            LIMIT 30
            """,
            (playlist["id"],)
        ).fetchall()

        history = [
            {
                "version": row["version"],
                "published_at": row["published_at"],
                "published_at_text": format_monitor_datetime(
                    row["published_at"]
                ),
                "source": row["source"],
            }
            for row in history_rows
        ]

        playlists.append({
            "id": playlist["id"],
            "name": playlist["name"],
            "items": items,
            "publication_status": publication_status,
            "published_version": published_version,
            "published_at": published_at,
            "published_at_text": published_at_text,
            "history": history,
            "can_edit": can_access_playlist(conn, user, playlist["id"], edit=True),
        })

    assignments = [r for r in conn.execute("SELECT tv_id, playlist_id FROM tv_playlists")
                   if r["tv_id"] in allowed]

    playlist_by_tv = {
        row["tv_id"]: row["playlist_id"]
        for row in assignments
    }

    conn.close()

    return templates.TemplateResponse(
        request=request,
        name="playlists.html",
        context={
            "app_version": APP_VERSION,
            "playlists": playlists,
            "tvs": tvs,
            "playlist_by_tv": playlist_by_tv,
            "published_playlist_ids": set(
                published_playlist_ids
            ),
        }
    )


@app.post("/admin/playlist")
def create_playlist(request: Request, 
    name: str = Form(...)
):

    user = require_role(request, "editor")
    name = name.strip()

    if not name:

        raise HTTPException(
            status_code=400,
            detail="Informe o nome da playlist."
        )

    conn = get_db()

    conn.execute(
        "INSERT INTO playlists (name, created_at, owner_user_id) VALUES (?, ?, ?)",
        (name, time.time(), user["id"]),
    )

    conn.commit()
    conn.close()

    return RedirectResponse(
        "/playlists?" + urlencode({"message": "Playlist criada.", "kind": "success"}),
        status_code=303
    )


@app.post(
    "/admin/playlist/{playlist_id}/items"
)
def add_playlist_items(request: Request, 
    playlist_id: int,
    content_ids: list[int] = Form(...)
):

    require_playlist_access(request, playlist_id, "editor")
    conn = get_db()

    playlist = conn.execute(
        "SELECT id FROM playlists WHERE id = ?",
        (playlist_id,)
    ).fetchone()

    if not playlist:
        conn.close()
        raise HTTPException(
            status_code=404,
            detail="Playlist não encontrada."
        )

    clean_ids = []
    seen = set()

    for content_id in content_ids:
        if content_id in seen:
            continue
        seen.add(content_id)

        content = conn.execute(
            """
            SELECT id, content_type
            FROM contents
            WHERE id = ?
            AND archived_at IS NULL
            """,
            (content_id,)
        ).fetchone()

        if not content:
            continue

        if content["content_type"] not in {"image", "video"}:
            continue
        require_content_access(request, content_id)

        clean_ids.append(content_id)

    if not clean_ids:
        conn.close()
        raise HTTPException(
            status_code=400,
            detail="Selecione pelo menos uma imagem ou vídeo válido."
        )

    position_row = conn.execute(
        """
        SELECT COALESCE(MAX(position), 0) AS last_position
        FROM playlist_items
        WHERE playlist_id = ?
        """,
        (playlist_id,)
    ).fetchone()

    position = int(position_row["last_position"] or 0)

    for content_id in clean_ids:
        position += 1
        conn.execute(
            """
            INSERT INTO playlist_items
            (playlist_id, content_id, position, duration_sec)
            VALUES (?, ?, ?, 10)
            """,
            (playlist_id, content_id, position)
        )

    conn.commit()
    conn.close()

    return RedirectResponse(
        f"/playlists#playlist-{playlist_id}",
        status_code=303
    )


@app.post("/api/playlist/{playlist_id}/upload")
async def upload_files_to_playlist(
    playlist_id: int,
    request: Request,
    files: list[UploadFile] = File(...)
):
    user = require_playlist_access(request, playlist_id, "editor")
    """Envia mídias para a biblioteca e adiciona ao final do rascunho."""
    if not files:
        raise HTTPException(
            status_code=400,
            detail="Selecione pelo menos um arquivo."
        )
    if len(files) > MAX_FILES_PER_UPLOAD:
        raise HTTPException(
            status_code=413,
            detail=f"O lote excede o limite de {MAX_FILES_PER_UPLOAD} arquivos.",
        )

    conn = get_db()
    playlist = conn.execute(
        "SELECT id, name FROM playlists WHERE id = ?",
        (playlist_id,)
    ).fetchone()

    if not playlist:
        conn.close()
        raise HTTPException(
            status_code=404,
            detail="Playlist não encontrada."
        )

    position_row = conn.execute(
        """
        SELECT COALESCE(MAX(position), 0) AS last_position
        FROM playlist_items
        WHERE playlist_id = ?
        """,
        (playlist_id,)
    ).fetchone()
    position = int(position_row["last_position"] or 0)

    uploaded = []
    errors = []
    upload_budget = {"remaining": MAX_REQUEST_BYTES}

    for index, file in enumerate(files):
        prepared = None
        savepoint = f"playlist_upload_{index}"
        savepoint_started = False

        try:
            prepared = await prepare_uploaded_media(file, upload_budget=upload_budget)
            conn.execute(f"SAVEPOINT {savepoint}")
            savepoint_started = True

            content_id = insert_prepared_content(
                conn,
                prepared
            )
            conn.execute("UPDATE contents SET owner_user_id=? WHERE id=?", (user["id"], content_id))
            position += 1
            item_id = conn.execute(
                """
                INSERT INTO playlist_items
                (playlist_id, content_id, position, duration_sec)
                VALUES (?, ?, ?, 10)
                """,
                (playlist_id, content_id, position)
            ).lastrowid

            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            savepoint_started = False

            uploaded.append({
                "item_id": int(item_id),
                "playlist_id": playlist_id,
                "content_id": content_id,
                "position": position,
                "duration_sec": 10,
                "content_name": prepared["name"],
                "content_type": prepared["content_type"],
                "original_filename": prepared["original_filename"],
                "filename": prepared["filename"],
                "url": media_url(prepared["filename"]),
                "validation_status": prepared["metadata"].get("validation_status"),
                "validation_message": prepared["metadata"].get("validation_message"),
            })

        except HTTPException as exc:
            if savepoint_started:
                conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            if prepared and prepared.get("source_path"):
                prepared["source_path"].unlink(missing_ok=True)
            errors.append({
                "filename": file.filename or "arquivo",
                "detail": str(exc.detail),
            })
        except Exception as exc:
            if savepoint_started:
                conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            if prepared and prepared.get("source_path"):
                prepared["source_path"].unlink(missing_ok=True)
            errors.append({
                "filename": file.filename or "arquivo",
                "detail": f"Erro inesperado: {exc}",
            })

    if uploaded:
        audit_event(
            conn,
            request,
            "playlist.upload",
            "playlist",
            str(playlist_id),
            (
                f"{len(uploaded)} arquivo(s) enviado(s) diretamente "
                f"para a playlist {playlist['name']}."
            )
        )
        conn.commit()
    else:
        conn.rollback()

    conn.close()

    if not uploaded:
        raise HTTPException(
            status_code=400,
            detail=(
                errors[0]["detail"]
                if errors
                else "Nenhum arquivo pôde ser enviado."
            )
        )

    return {
        "status": "ok",
        "playlist_id": playlist_id,
        "uploaded": len(uploaded),
        "items": uploaded,
        "errors": errors,
    }


@app.post(
    "/admin/playlist/{playlist_id}/item"
)
def add_playlist_item(request: Request, 
    playlist_id: int,
    content_id: int = Form(...),
    duration_sec: int = Form(10)
):

    require_playlist_access(request, playlist_id, "editor")
    require_content_access(request, content_id)
    duration_sec = max(
        1,
        min(3600, duration_sec)
    )

    conn = get_db()

    playlist = conn.execute(
        """
        SELECT id
        FROM playlists
        WHERE id = ?
        """,
        (playlist_id,)
    ).fetchone()

    content = conn.execute(
        """
        SELECT id, content_type
        FROM contents
        WHERE id = ?
        AND content_type IN ('image', 'video')
        AND archived_at IS NULL
        """,
        (content_id,)
    ).fetchone()

    if not playlist or not content:

        conn.close()

        raise HTTPException(
            status_code=404,
            detail="Playlist ou conteúdo não encontrado."
        )

    position_row = conn.execute(
        """
        SELECT
            COALESCE(
                MAX(position),
                0
            ) + 1 AS next_position
        FROM playlist_items
        WHERE playlist_id = ?
        """,
        (playlist_id,)
    ).fetchone()

    position = position_row["next_position"]

    conn.execute(
        """
        INSERT INTO playlist_items
        (
            playlist_id,
            content_id,
            position,
            duration_sec
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            playlist_id,
            content_id,
            position,
            duration_sec
        )
    )

    conn.commit()
    conn.close()

    return RedirectResponse(
        "/playlists",
        status_code=303
    )


@app.post(
    "/admin/playlist/item/{item_id}/delete"
)
def delete_playlist_item(request: Request, 
    item_id: int
):

    require_item_playlist_access(request, item_id)
    conn = get_db()

    item = conn.execute(
        """
        SELECT playlist_id, position
        FROM playlist_items
        WHERE id = ?
        """,
        (item_id,)
    ).fetchone()

    if not item:

        conn.close()

        return RedirectResponse(
            "/playlists",
            status_code=303
        )

    playlist_id = item["playlist_id"]

    conn.execute(
        """
        DELETE FROM playlist_items
        WHERE id = ?
        """,
        (item_id,)
    )

    conn.execute(
        """
        UPDATE playlist_items
        SET position = position - 1
        WHERE playlist_id = ?
        AND position > ?
        """,
        (
            playlist_id,
            item["position"]
        )
    )

    conn.commit()
    conn.close()

    return RedirectResponse(
        "/playlists",
        status_code=303
    )


@app.post(
    "/admin/playlist/item/{item_id}/move-up"
)
def move_playlist_item_up(request: Request, 
    item_id: int
):

    require_item_playlist_access(request, item_id)
    conn = get_db()

    item = conn.execute(
        """
        SELECT *
        FROM playlist_items
        WHERE id = ?
        """,
        (item_id,)
    ).fetchone()

    if not item:

        conn.close()

        return RedirectResponse(
            "/playlists",
            status_code=303
        )

    previous = conn.execute(
        """
        SELECT *
        FROM playlist_items
        WHERE playlist_id = ?
        AND position < ?
        ORDER BY position DESC
        LIMIT 1
        """,
        (
            item["playlist_id"],
            item["position"]
        )
    ).fetchone()

    if previous:

        conn.execute(
            """
            UPDATE playlist_items
            SET position = ?
            WHERE id = ?
            """,
            (
                previous["position"],
                item["id"]
            )
        )

        conn.execute(
            """
            UPDATE playlist_items
            SET position = ?
            WHERE id = ?
            """,
            (
                item["position"],
                previous["id"]
            )
        )

        conn.commit()

    conn.close()

    return RedirectResponse(
        "/playlists",
        status_code=303
    )


@app.post(
    "/admin/playlist/item/{item_id}/move-down"
)
def move_playlist_item_down(request: Request, 
    item_id: int
):

    require_item_playlist_access(request, item_id)
    conn = get_db()

    item = conn.execute(
        """
        SELECT *
        FROM playlist_items
        WHERE id = ?
        """,
        (item_id,)
    ).fetchone()

    if not item:

        conn.close()

        return RedirectResponse(
            "/playlists",
            status_code=303
        )

    next_item = conn.execute(
        """
        SELECT *
        FROM playlist_items
        WHERE playlist_id = ?
        AND position > ?
        ORDER BY position ASC
        LIMIT 1
        """,
        (
            item["playlist_id"],
            item["position"]
        )
    ).fetchone()

    if next_item:

        conn.execute(
            """
            UPDATE playlist_items
            SET position = ?
            WHERE id = ?
            """,
            (
                next_item["position"],
                item["id"]
            )
        )

        conn.execute(
            """
            UPDATE playlist_items
            SET position = ?
            WHERE id = ?
            """,
            (
                item["position"],
                next_item["id"]
            )
        )

        conn.commit()

    conn.close()

    return RedirectResponse(
        "/playlists",
        status_code=303
    )


@app.post(
    "/admin/playlist/item/{item_id}/duration"
)
def update_playlist_duration(request: Request, 
    item_id: int,
    duration_sec: int = Form(...)
):

    require_item_playlist_access(request, item_id)
    duration_sec = max(
        1,
        min(3600, duration_sec)
    )

    conn = get_db()

    conn.execute(
        """
        UPDATE playlist_items
        SET duration_sec = ?
        WHERE id = ?
        """,
        (
            duration_sec,
            item_id
        )
    )

    conn.commit()
    conn.close()

    return RedirectResponse(
        "/playlists?" + urlencode({"message": "Duração atualizada no rascunho.", "kind": "success"}),
        status_code=303
    )


@app.post("/api/playlist/item/{item_id}/duration")
async def update_playlist_duration_api(
    item_id: int,
    request: Request
):
    require_item_playlist_access(request, item_id)
    data = await request.json()

    try:
        duration_sec = int(data.get("duration_sec"))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail="Duração inválida."
        )

    duration_sec = max(1, min(3600, duration_sec))

    conn = get_db()
    item = conn.execute(
        """
        SELECT playlist_items.id, playlist_items.playlist_id,
               contents.content_type
        FROM playlist_items
        INNER JOIN contents
            ON contents.id = playlist_items.content_id
        WHERE playlist_items.id = ?
        """,
        (item_id,)
    ).fetchone()

    if not item:
        conn.close()
        raise HTTPException(
            status_code=404,
            detail="Item não encontrado."
        )

    if item["content_type"] == "video":
        conn.close()
        raise HTTPException(
            status_code=400,
            detail="Vídeos usam a duração real do arquivo."
        )

    conn.execute(
        """
        UPDATE playlist_items
        SET duration_sec = ?
        WHERE id = ?
        """,
        (duration_sec, item_id)
    )
    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "item_id": item_id,
        "playlist_id": item["playlist_id"],
        "duration_sec": duration_sec,
    }


@app.get(
    "/playlist/{playlist_id}/preview",
    response_class=HTMLResponse
)
def preview_playlist(
    request: Request,
    playlist_id: int
):

    require_playlist_access(request, playlist_id)
    conn = get_db()

    playlist_row = conn.execute(
        "SELECT id, name FROM playlists WHERE id = ?",
        (playlist_id,)
    ).fetchone()

    if not playlist_row:
        conn.close()
        raise HTTPException(
            status_code=404,
            detail="Playlist não encontrada."
        )

    rows = conn.execute("""
        SELECT
            playlist_items.id,
            playlist_items.position,
            playlist_items.duration_sec,
            contents.id AS content_id,
            contents.name AS content_name,
            contents.content_type,
            contents.filename
        FROM playlist_items
        INNER JOIN contents
            ON playlist_items.content_id = contents.id
        WHERE playlist_items.playlist_id = ?
        AND contents.content_type IN ('image', 'video')
        AND contents.archived_at IS NULL
        ORDER BY playlist_items.position
    """, (
        playlist_id,
    )).fetchall()

    conn.close()

    items = []

    for row in rows:
        items.append({
            "id": row["id"],
            "position": row["position"],
            "duration_sec": row["duration_sec"],
            "content": {
                "id": row["content_id"],
                "name": row["content_name"],
                "content_type": row["content_type"],
                "url": media_url(row["filename"]),
            }
        })

    return templates.TemplateResponse(
        request=request,
        name="playlist_preview.html",
        context={
            "playlist": {
                "id": playlist_row["id"],
                "name": playlist_row["name"],
            },
            "playlist_json": json.dumps(
                items,
                ensure_ascii=False
            ),
        }
    )


@app.get("/api/playlist/{playlist_id}/publish-summary")
def playlist_publish_summary(request: Request, playlist_id: int):
    require_playlist_access(request, playlist_id)
    conn = get_db()
    draft = build_playlist_snapshot(conn, playlist_id)
    if not draft:
        conn.close(); raise HTTPException(status_code=404, detail="Playlist não encontrada.")
    current = conn.execute("SELECT version, snapshot_json FROM playlist_publications WHERE playlist_id = ?", (playlist_id,)).fetchone()
    if current:
        try: old = json.loads(current["snapshot_json"] or "{}")
        except json.JSONDecodeError: old = {}
        current_version = current["version"]
    else:
        old = {"items": []}; current_version = None
    diff = playlist_diff_summary(old, draft)
    conn.close()
    return {"playlist_id": playlist_id, "current_version": current_version,
            "next_version": (int(current_version) + 1) if current_version else 1, **diff}


@app.post("/admin/playlist/{playlist_id}/publish")
def publish_playlist_draft(request: Request, playlist_id: int):
    require_playlist_access(request, playlist_id, "operator")
    conn = get_db()
    snapshot = build_playlist_snapshot(conn, playlist_id)
    if not snapshot:
        conn.close(); raise HTTPException(status_code=404, detail="Playlist não encontrada.")
    if not snapshot.get("items"):
        conn.close(); raise HTTPException(status_code=400, detail="Adicione pelo menos um conteúdo antes de publicar.")
    current = conn.execute("SELECT version, snapshot_json FROM playlist_publications WHERE playlist_id = ?", (playlist_id,)).fetchone()
    if current:
        try: current_snapshot = json.loads(current["snapshot_json"] or "{}")
        except json.JSONDecodeError: current_snapshot = {}
        if snapshot_signature(snapshot) == snapshot_signature(current_snapshot):
            conn.close()
            return RedirectResponse(f"/playlists?{urlencode({'message':'O rascunho já é igual à publicação atual.','kind':'info'})}#playlist-{playlist_id}", status_code=303)
        version = int(current["version"]) + 1
        published_at = time.time()
        conn.execute("UPDATE playlist_publications SET version=?, published_at=?, snapshot_json=? WHERE playlist_id=?",
                     (version, published_at, json.dumps(snapshot, ensure_ascii=False), playlist_id))
    else:
        version = 1; published_at = time.time()
        conn.execute("INSERT INTO playlist_publications (playlist_id, version, published_at, snapshot_json) VALUES (?, ?, ?, ?)",
                     (playlist_id, version, published_at, json.dumps(snapshot, ensure_ascii=False)))
    conn.execute(
        """
        INSERT OR REPLACE INTO playlist_publication_history
        (playlist_id, version, published_at, snapshot_json, source)
        VALUES (?, ?, ?, ?, 'publish')
        """, (playlist_id, version, published_at, json.dumps(snapshot, ensure_ascii=False))
    )
    conn.commit(); conn.close()
    return RedirectResponse(f"/playlists?{urlencode({'message':f'Playlist publicada como v{version}.','kind':'success'})}#playlist-{playlist_id}", status_code=303)


@app.post("/admin/playlist/{playlist_id}/restore/{version}")
def restore_playlist_version(request: Request, playlist_id: int, version: int):
    require_playlist_access(request, playlist_id, "editor")
    conn = get_db()
    row = conn.execute(
        "SELECT snapshot_json FROM playlist_publication_history WHERE playlist_id=? AND version=?",
        (playlist_id, version)
    ).fetchone()
    if not row:
        conn.close(); raise HTTPException(status_code=404, detail="Versão histórica não encontrada.")
    try: snapshot = json.loads(row["snapshot_json"] or "{}")
    except json.JSONDecodeError: snapshot = {}
    conn.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (playlist_id,))
    restored = 0
    for position, item in enumerate(snapshot.get("items", []), start=1):
        if not isinstance(item, dict): continue
        content_id = item.get("content_id")
        content = conn.execute("SELECT id FROM contents WHERE id = ?", (content_id,)).fetchone()
        if not content: continue
        conn.execute(
            "INSERT INTO playlist_items (playlist_id, content_id, position, duration_sec) VALUES (?, ?, ?, ?)",
            (playlist_id, content_id, position, max(1, min(3600, int(item.get("duration_sec") or 10))))
        )
        restored += 1
    conn.commit(); conn.close()
    msg = f"Versão v{version} restaurada no rascunho ({restored} itens). Revise antes de publicar."
    return RedirectResponse(f"/playlists?{urlencode({'message':msg,'kind':'info'})}#playlist-{playlist_id}", status_code=303)


@app.post(
    "/admin/tv/{tv_id}/playlist"
)
def assign_playlist_to_tv(request: Request, 
    tv_id: str,
    playlist_id: int
    = Form(...)
):

    require_tv_access(request, tv_id, "operator")
    require_playlist_access(request, playlist_id, "operator")
    conn = get_db()

    tv = conn.execute(
        """
        SELECT tv_id
        FROM tvs
        WHERE tv_id = ?
        """,
        (tv_id,)
    ).fetchone()

    playlist = conn.execute(
        """
        SELECT playlists.id
        FROM playlists
        INNER JOIN playlist_publications
            ON playlist_publications.playlist_id = playlists.id
        WHERE playlists.id = ?
        """,
        (playlist_id,)
    ).fetchone()

    if not tv or not playlist:

        conn.close()

        raise HTTPException(
            status_code=404,
            detail="TV não encontrada ou playlist ainda não publicada."
        )

    conn.execute(
        """
        INSERT INTO tv_playlists
        (
            tv_id,
            playlist_id
        )
        VALUES (?, ?)

        ON CONFLICT(tv_id)
        DO UPDATE SET
            playlist_id =
                excluded.playlist_id
        """,
        (
            tv_id,
            playlist_id
        )
    )

    conn.commit()
    conn.close()

    return RedirectResponse(
        "/playlists?" + urlencode({"message": "Playlist atribuída à TV.", "kind": "success"}),
        status_code=303
    )


# =========================================================
# REMOÇÃO EM LOTE DE ITENS DA PLAYLIST
# =========================================================

@app.post(
    "/api/playlist/{playlist_id}/items/delete-bulk"
)
async def delete_playlist_items_bulk(
    playlist_id: int,
    request: Request
):

    require_playlist_access(request, playlist_id, "editor")
    data = await request.json()

    raw_ids = data.get(
        "item_ids",
        []
    )

    if not isinstance(raw_ids, list):
        raise HTTPException(
            status_code=400,
            detail="Lista de itens inválida."
        )

    try:
        item_ids = list({
            int(item_id)
            for item_id in raw_ids
        })
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail="IDs de itens inválidos."
        )

    if not item_ids:
        raise HTTPException(
            status_code=400,
            detail="Selecione pelo menos um item."
        )

    conn = get_db()

    playlist = conn.execute(
        """
        SELECT id
        FROM playlists
        WHERE id = ?
        """,
        (playlist_id,)
    ).fetchone()

    if not playlist:
        conn.close()
        raise HTTPException(
            status_code=404,
            detail="Playlist não encontrada."
        )

    placeholders = ",".join(
        "?"
        for _ in item_ids
    )

    valid_rows = conn.execute(
        f"""
        SELECT id
        FROM playlist_items
        WHERE playlist_id = ?
        AND id IN ({placeholders})
        """,
        (
            playlist_id,
            *item_ids,
        )
    ).fetchall()

    valid_ids = [
        row["id"]
        for row in valid_rows
    ]

    if not valid_ids:
        conn.close()
        raise HTTPException(
            status_code=404,
            detail=(
                "Nenhum dos itens selecionados "
                "pertence a esta playlist."
            )
        )

    valid_placeholders = ",".join(
        "?"
        for _ in valid_ids
    )

    conn.execute(
        f"""
        DELETE FROM playlist_items
        WHERE playlist_id = ?
        AND id IN ({valid_placeholders})
        """,
        (
            playlist_id,
            *valid_ids,
        )
    )

    remaining = conn.execute(
        """
        SELECT id
        FROM playlist_items
        WHERE playlist_id = ?
        ORDER BY position, id
        """,
        (playlist_id,)
    ).fetchall()

    for position, row in enumerate(
        remaining,
        start=1
    ):
        conn.execute(
            """
            UPDATE playlist_items
            SET position = ?
            WHERE id = ?
            """,
            (
                position,
                row["id"],
            )
        )

    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "playlist_id": playlist_id,
        "removed": len(valid_ids),
        "remaining": len(remaining),
    }



# =========================================================
# REORDENAR PLAYLIST
# =========================================================

@app.post("/api/playlist/{playlist_id}/reorder")
async def reorder_playlist(
    playlist_id: int,
    request: Request
):

    require_playlist_access(request, playlist_id, "editor")
    data = await request.json()

    item_ids = data.get("item_ids", [])

    if not isinstance(item_ids, list):
        raise HTTPException(
            status_code=400,
            detail="Lista de itens inválida."
        )

    try:
        item_ids = [
            int(item_id)
            for item_id in item_ids
        ]
    except (TypeError, ValueError):

        raise HTTPException(
            status_code=400,
            detail="IDs de itens inválidos."
        )

    conn = get_db()

    playlist = conn.execute(
        """
        SELECT id
        FROM playlists
        WHERE id = ?
        """,
        (playlist_id,)
    ).fetchone()

    if not playlist:

        conn.close()

        raise HTTPException(
            status_code=404,
            detail="Playlist não encontrada."
        )

    db_items = conn.execute(
        """
        SELECT id
        FROM playlist_items
        WHERE playlist_id = ?
        """,
        (playlist_id,)
    ).fetchall()

    db_item_ids = {
        row["id"]
        for row in db_items
    }

    # Não aceitar itens que não pertencem à playlist
    if set(item_ids) != db_item_ids:

        conn.close()

        raise HTTPException(
            status_code=400,
            detail="Os itens enviados não correspondem à playlist."
        )

    for position, item_id in enumerate(
        item_ids,
        start=1
    ):

        conn.execute(
            """
            UPDATE playlist_items
            SET position = ?
            WHERE id = ?
            AND playlist_id = ?
            """,
            (
                position,
                item_id,
                playlist_id
            )
        )

    conn.commit()
    conn.close()

    return {
        "status": "ok"
    }

# =========================================================
# EXCLUIR PLAYLIST
# =========================================================

@app.post("/admin/playlist/{playlist_id}/delete")
def delete_playlist(request: Request, 
    playlist_id: int
):

    require_playlist_access(request, playlist_id, "editor")
    conn = get_db()

    playlist = conn.execute(
        """
        SELECT id
        FROM playlists
        WHERE id = ?
        """,
        (playlist_id,)
    ).fetchone()

    if not playlist:

        conn.close()

        raise HTTPException(
            status_code=404,
            detail="Playlist não encontrada."
        )

    # Remove a playlist das TVs
    conn.execute(
        """
        DELETE FROM tv_playlists
        WHERE playlist_id = ?
        """,
        (playlist_id,)
    )

    # Remove agendamentos que dependem da playlist
    conn.execute(
        """
        DELETE FROM schedules
        WHERE playlist_id = ?
        """,
        (playlist_id,)
    )

    # Remove o snapshot publicado
    conn.execute(
        """
        DELETE FROM playlist_publications
        WHERE playlist_id = ?
        """,
        (playlist_id,)
    )

    # Remove os itens da playlist
    conn.execute(
        """
        DELETE FROM playlist_items
        WHERE playlist_id = ?
        """,
        (playlist_id,)
    )

    # Remove a playlist
    conn.execute(
        """
        DELETE FROM playlists
        WHERE id = ?
        """,
        (playlist_id,)
    )

    conn.commit()
    conn.close()

    return RedirectResponse(
        "/playlists",
        status_code=303
    )

# =========================================================
# AGENDAMENTOS
# =========================================================

@app.get(
    "/schedules",
    response_class=HTMLResponse
)
def schedules_page(request: Request):
    user = require_user(request)
    conn = get_db()
    rows = conn.execute(
        """
        SELECT schedules.*, tvs.name AS tv_name, playlists.name AS playlist_name
        FROM schedules
        INNER JOIN tvs ON schedules.tv_id = tvs.tv_id
        INNER JOIN playlists ON schedules.playlist_id = playlists.id
        ORDER BY schedules.day_of_week, schedules.start_time, schedules.id
        """
    ).fetchall()
    allowed = allowed_tv_ids(conn, user)
    rows = [r for r in rows if r["tv_id"] in allowed]
    tvs = [r for r in conn.execute("SELECT * FROM tvs ORDER BY tv_id") if r["tv_id"] in allowed]
    playlists = conn.execute(
        """
        SELECT playlists.* FROM playlists
        INNER JOIN playlist_publications ON playlist_publications.playlist_id = playlists.id
        ORDER BY playlists.name
        """
    ).fetchall()
    playlists = [r for r in playlists if can_access_playlist(conn, user, r["id"], edit=True)]
    conn.close()
    names = ["Segunda-feira","Terça-feira","Quarta-feira","Quinta-feira","Sexta-feira","Sábado","Domingo"]
    schedules = [{"id":r["id"],"tv_id":r["tv_id"],"tv_name":r["tv_name"],"playlist_id":r["playlist_id"],
                  "playlist_name":r["playlist_name"],"day_of_week":r["day_of_week"],"day_name":names[r["day_of_week"]],
                  "start_time":r["start_time"],"end_time":r["end_time"],"enabled":bool(r["enabled"])} for r in rows]
    conflict_ids = schedule_conflict_ids(rows)
    days = [{"index": i, "name": names[i], "schedules": [s for s in schedules if s["day_of_week"] == i]} for i in range(7)]
    return templates.TemplateResponse(request=request, name="schedules.html", context={
        "app_version": APP_VERSION, "schedules": schedules, "tvs": tvs, "playlists": playlists,
        "days": days, "conflict_ids": conflict_ids,
    })


@app.post("/admin/schedule")
def create_schedule(request: Request, 
    tv_id: str = Form(...),
    playlist_id: int = Form(...),
    day_of_week: int = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...)
):
    require_tv_access(request, tv_id, "operator")
    require_playlist_access(request, playlist_id, "operator")
    tv_id = tv_id.strip().upper()
    if day_of_week < 0 or day_of_week > 6:
        raise HTTPException(status_code=400, detail="Dia inválido.")
    try:
        datetime.strptime(start_time, "%H:%M"); datetime.strptime(end_time, "%H:%M")
    except ValueError:
        raise HTTPException(status_code=400, detail="Horário inválido.")
    conn = get_db()
    tv = conn.execute("SELECT tv_id FROM tvs WHERE tv_id = ?", (tv_id,)).fetchone()
    playlist = conn.execute(
        """
        SELECT playlists.id FROM playlists
        INNER JOIN playlist_publications ON playlist_publications.playlist_id = playlists.id
        WHERE playlists.id = ?
        """, (playlist_id,)
    ).fetchone()
    if not tv or not playlist:
        conn.close(); raise HTTPException(status_code=404, detail="TV ou playlist publicada não encontrada.")
    existing = conn.execute("SELECT * FROM schedules WHERE tv_id = ? AND enabled = 1", (tv_id,)).fetchall()
    conflicts = [row for row in existing if schedules_overlap(day_of_week, start_time, end_time, row["day_of_week"], row["start_time"], row["end_time"])]
    if conflicts:
        conn.close()
        msg = f"Conflito: esta janela sobrepõe {len(conflicts)} agendamento(s) ativo(s) da mesma TV."
        return RedirectResponse("/schedules?" + urlencode({"message": msg, "kind": "error"}), status_code=303)
    conn.execute(
        "INSERT INTO schedules (tv_id, playlist_id, day_of_week, start_time, end_time, enabled, created_at) VALUES (?, ?, ?, ?, ?, 1, ?)",
        (tv_id, playlist_id, day_of_week, start_time, end_time, time.time())
    )
    conn.commit(); conn.close()
    return RedirectResponse("/schedules?" + urlencode({"message":"Agendamento criado.","kind":"success"}), status_code=303)


@app.post("/admin/schedule/{schedule_id}/toggle")
def toggle_schedule(request: Request, schedule_id: int):
    require_role(request, "operator")
    require_schedule_access(request, schedule_id)
    conn = get_db(); row = conn.execute("SELECT * FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
    if not row:
        conn.close(); raise HTTPException(status_code=404, detail="Agendamento não encontrado.")
    new_value = 0 if row["enabled"] else 1
    if new_value:
        existing = conn.execute("SELECT * FROM schedules WHERE tv_id=? AND enabled=1 AND id<>?", (row["tv_id"], schedule_id)).fetchall()
        if any(schedules_overlap(row["day_of_week"], row["start_time"], row["end_time"], other["day_of_week"], other["start_time"], other["end_time"]) for other in existing):
            conn.close()
            return RedirectResponse("/schedules?" + urlencode({"message":"Não foi possível ativar: existe conflito com outra regra ativa.","kind":"error"}), status_code=303)
    conn.execute("UPDATE schedules SET enabled = ? WHERE id = ?", (new_value, schedule_id)); conn.commit(); conn.close()
    return RedirectResponse("/schedules?" + urlencode({"message":"Status do agendamento atualizado.","kind":"success"}), status_code=303)


@app.post("/admin/schedule/{schedule_id}/delete")
def delete_schedule(request: Request, schedule_id: int):
    require_role(request, "operator")
    require_schedule_access(request, schedule_id)
    conn = get_db(); conn.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,)); conn.commit(); conn.close()
    return RedirectResponse("/schedules?" + urlencode({"message":"Agendamento excluído.","kind":"success"}), status_code=303)

