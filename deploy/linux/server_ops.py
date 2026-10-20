"""Linux operational helpers. Importing this module never imports the application.

CLI is intentionally restricted to the documented layout. Pure snapshot functions
can also be tested against synthetic data without root or systemd.
"""
import argparse
import ast
from contextlib import closing
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

APP = Path("/opt/digital-signage/app")
VENV = Path("/opt/digital-signage/venv")
DATA = Path("/var/lib/digital-signage")
ENV = Path("/etc/digital-signage/server.env")
BACKUPS = Path("/var/backups/digital-signage")
SERVICE = "digital-signage.service"
EXPECTED = {
    "DS_DB_FILE": (DATA / "digital_signage.db").as_posix(),
    "DS_MEDIA_DIR": (DATA / "media").as_posix(),
    "DS_RELEASE_DIR": (DATA / "releases").as_posix(),
    "DS_UPLOAD_TEMP_DIR": (DATA / "upload-tmp").as_posix(),
}


def run(*args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def output(*args):
    return run(*args, capture_output=True, text=True).stdout.strip()


def read_env(path):
    """Strict, shared subset of systemd EnvironmentFile; never execute shell."""
    raw = path.read_bytes()
    if b"\r" in raw:
        raise ValueError("EnvironmentFile deve usar LF")
    values = {}
    for line in raw.decode("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in values:
            raise ValueError("EnvironmentFile inválido: sintaxe ou chave duplicada")
        value = value.strip()
        if value.startswith(("'", '"')):
            quote = value[0]
            if len(value) < 2 or not value.endswith(quote):
                raise ValueError("Aspas inválidas no EnvironmentFile")
            value = value[1:-1]
        if any(c in value for c in ("\\", "\n", "\r", "\x00", '"', "'")):
            raise ValueError("Use valores simples sem escapes no EnvironmentFile")
        values[key] = value
    return values


def validate_env(path=ENV):
    values = read_env(path)
    if any(values.get(key) != value for key, value in EXPECTED.items()):
        raise ValueError("Configure os três caminhos de dados canônicos")
    if values.get("DS_COOKIE_SECURE") != "0":
        raise ValueError("Este ambiente HTTP exige DS_COOKIE_SECURE=0")
    for key, minimum in (("DS_SESSION_TTL_SECONDS", 60), ("DS_PASSWORD_ITERATIONS", 210000)):
        if int(values.get(key, "0")) < minimum:
            raise ValueError(f"{key} ausente ou abaixo do mínimo")
    if values.get("DS_REQUIRE_DEVICE_TOKEN") not in {"0", "1"}:
        raise ValueError("DS_REQUIRE_DEVICE_TOKEN deve ser 0 ou 1")
    for key in ("DS_ALLOW_LEGACY_PUBLIC_MEDIA", "DS_CLEANUP_DRY_RUN", "DS_CLEANUP_DELETE_ARCHIVED"):
        if values.get(key) not in {"0", "1"}:
            raise ValueError(f"{key} deve ser 0 ou 1")
    for key in (
        "DS_MAX_IMAGE_MB", "DS_MAX_VIDEO_MB", "DS_MAX_RELEASE_MB",
        "DS_MAX_FILES_PER_UPLOAD", "DS_MAX_REQUEST_MB",
        "DS_RETENTION_TEMP_DAYS", "DS_RETENTION_ARCHIVED_DAYS",
    ):
        if int(values.get(key, "0")) < 1:
            raise ValueError(f"{key} deve ser >= 1")
    if values.get("DS_INITIAL_CREDENTIALS_FILE", (DATA / "first_admin_credentials.txt").as_posix()) != (DATA / "first_admin_credentials.txt").as_posix():
        raise ValueError("Caminho de credencial inicial não suportado pelo runbook")
    password = values.get("DS_INITIAL_ADMIN_PASSWORD", "")
    if password and (len(password) < 16 or "substitua" in password.lower()):
        raise ValueError("Senha inicial deve ser privada e ter pelo menos 16 caracteres")
    return values


def safe_tree(root):
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Diretório ausente ou link simbólico recusado")
    for path in root.rglob("*"):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError("Links e arquivos especiais não são aceitos")


def protected_code(root):
    safe_tree(root)
    for path in [root, *root.rglob("*")]:
        info = path.stat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("Código deve pertencer a root e não permitir escrita por grupo/outros")


def runtime_readable(root):
    """Publish root-owned code/venv read-only to the unprivileged service."""
    for path in [root, *root.rglob("*")]:
        if path.is_symlink():
            continue  # venv interpreter links are managed by Python.
        info = path.stat()
        if info.st_uid != 0:
            raise ValueError("Runtime deve pertencer a root")
        os.chmod(path, 0o755 if path.is_dir() or info.st_mode & 0o111 else 0o644)


@contextmanager
def operation_log(action):
    import syslog
    syslog.openlog("digital-signage-operations")
    syslog.syslog(syslog.LOG_INFO, action + ": started")
    try:
        yield
    except BaseException:
        syslog.syslog(syslog.LOG_ERR, action + ": failed; inspect operator output")
        raise
    else:
        syslog.syslog(syslog.LOG_INFO, action + ": completed")


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def integrity(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("SQLite com falha de integridade")


def sqlite_backup(source, destination):
    if destination.exists():
        raise ValueError("Destino SQLite já existe")
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)
    integrity(destination)


def app_version(app):
    for node in ast.parse((app / "app.py").read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "APP_VERSION" for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError("APP_VERSION não encontrada")


def snapshot(data, env, backups, metadata):
    """Caller must quiesce all writers for cross-file consistency."""
    safe_tree(data)
    if env.is_symlink() or not env.is_file() or backups.is_symlink():
        raise ValueError("Configuração/destino inseguro")
    backups.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = "backup-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    stage = backups / ("." + name + ".partial")
    final = backups / name
    stage.mkdir(mode=0o700)
    try:
        sqlite_backup(data / "digital_signage.db", stage / "digital_signage.db")
        for directory in ("media", "releases"):
            shutil.copytree(data / directory, stage / directory)
        shutil.copy2(env, stage / "server.env")
        os.chmod(stage / "server.env", 0o600)
        files = {
            p.relative_to(stage).as_posix(): {"sha256": digest(p), "bytes": p.stat().st_size}
            for p in sorted(stage.rglob("*")) if p.is_file()
        }
        manifest = {
            "format": 1, "created_at": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata, "files": files, "file_count": len(files),
            "total_bytes": sum(v["bytes"] for v in files.values()),
        }
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        verify_snapshot(stage)
        stage.rename(final)
        return final
    except BaseException:
        # Keep incomplete evidence; retention never selects .partial directories.
        raise


def verify_snapshot(root):
    safe_tree(root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != 1 or not isinstance(manifest.get("files"), dict):
        raise ValueError("Manifesto inválido")
    listed = manifest["files"]
    # Nested manifest.json files are data too.
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and p != root / "manifest.json"}
    if set(listed) != actual or not {"digital_signage.db", "server.env"} <= actual:
        raise ValueError("Inventário divergente")
    for name, expected in listed.items():
        parts = name.split("/")
        if any(part in {"", ".", ".."} for part in parts) or "\\" in name:
            raise ValueError("Caminho inválido no manifesto")
        if name not in {"digital_signage.db", "server.env"} and parts[0] not in {"media", "releases"}:
            raise ValueError("Conteúdo não permitido no backup")
        path = root.joinpath(*parts)
        if path.stat().st_size != expected["bytes"] or digest(path) != expected["sha256"]:
            raise ValueError("Checksum divergente")
    if manifest.get("file_count") != len(listed) or manifest.get("total_bytes") != sum(p["bytes"] for p in listed.values()):
        raise ValueError("Totais divergentes")
    for name in ("media", "releases"):
        if not (root / name).is_dir():
            raise ValueError("Diretório obrigatório ausente")
    integrity(root / "digital_signage.db")
    return manifest


def retain(backups, keep, protected=()):
    if keep < 1 or backups.is_symlink():
        raise ValueError("Retenção inválida")
    candidates = sorted(
        (p for p in backups.iterdir() if re.fullmatch(r"backup-\d{8}T\d{6}Z-[a-f0-9]{8}", p.name)),
        reverse=True,
    )
    for path in candidates[keep:]:
        if path in protected:
            continue
        if path.resolve().parent != backups.resolve():
            raise ValueError("Backup fora do diretório permitido")
        verify_snapshot(path)
        shutil.rmtree(path)


def active():
    return subprocess.run(["systemctl", "is-active", "--quiet", SERVICE], check=False).returncode == 0


def wait_health(proxy=False):
    if proxy == "local":
        url = "http://127.0.0.1/health"
    else:
        url = "http://signage.amb.local/health" if proxy else "http://127.0.0.1:8000/health"
    request = urllib.request.Request(url, headers={"Host": "signage.amb.local"} if proxy == "local" else {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(30):
        try:
            with opener.open(request, timeout=2) as response:
                result = json.load(response)
                if response.status == 200 and result.get("status") == "ok":
                    return
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise RuntimeError("Health check indisponível")


@contextmanager
def stopped(restart_on_error=True):
    was_active = active()
    run("systemctl", "stop", SERVICE)
    try:
        yield
    except BaseException:
        if was_active and restart_on_error:
            run("systemctl", "start", SERVICE)
            wait_health()
        raise
    else:
        if was_active:
            run("systemctl", "start", SERVICE)
            wait_health()


def live_snapshot():
    return snapshot(DATA, ENV, BACKUPS, {
        "commit": output("git", "-C", APP, "rev-parse", "HEAD"),
        "version": app_version(APP),
        "dependencies": output(VENV / "bin/python", "-m", "pip", "freeze").splitlines(),
    })


def permissions(data):
    import pwd
    import grp
    uid = pwd.getpwnam("digital-signage").pw_uid
    gid = grp.getgrnam("digital-signage").gr_gid
    safe_tree(data)
    for path in [data, *data.rglob("*")]:
        os.chown(path, uid, gid)
        os.chmod(path, 0o700 if path.is_dir() else 0o600)


def preflight(mode):
    import pwd
    import grp
    failures = []

    def check(label, callback):
        try:
            callback()
            print("OK:", label)
        except Exception:
            failures.append(label)
            print("FALHA:", label)

    def require(condition):
        if not condition:
            raise ValueError("Verificação falhou")

    check("usuário", lambda: pwd.getpwnam("digital-signage"))
    check("grupo", lambda: grp.getgrnam("digital-signage"))
    check("configuração", validate_env)
    check("propriedade/permissões do código", lambda: protected_code(APP))
    check("permissão server.env", lambda: require(not ENV.is_symlink() and ENV.stat().st_uid == 0 and stat.S_IMODE(ENV.stat().st_mode) == 0o600))
    for path in (APP, VENV, DATA, DATA / "media", DATA / "releases", BACKUPS):
        check(f"diretório {path.name}", lambda p=path: require(p.is_dir()))
    for path in (DATA, DATA / "media", DATA / "releases"):
        check(f"acesso do serviço {path.name}", lambda p=path: run(
            "runuser", "-u", "digital-signage", "--", "/usr/bin/test", "-r", p,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        check(f"escrita do serviço {path.name}", lambda p=path: run(
            "runuser", "-u", "digital-signage", "--", "/usr/bin/test", "-w", p,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        check(f"travessia do serviço {path.name}", lambda p=path: run(
            "runuser", "-u", "digital-signage", "--", "/usr/bin/test", "-x", p,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    check("código sem escrita pelo serviço", lambda: require(subprocess.run(
        ["runuser", "-u", "digital-signage", "--", "/usr/bin/test", "-w", str(APP)],
        check=False).returncode != 0))
    check("código legível pelo serviço", lambda: run("runuser", "-u", "digital-signage", "--",
        "/usr/bin/test", "-r", APP / "app.py"))
    check("Python >= 3.11", lambda: run(VENV / "bin/python", "-c", "import sys; assert sys.version_info >= (3,11)"))
    check("dependências Python", lambda: run(VENV / "bin/python", "-c",
        "import fastapi,uvicorn,jinja2,python_multipart; from zoneinfo import ZoneInfo; ZoneInfo('America/Sao_Paulo')"))
    check("pip check", lambda: run(VENV / "bin/python", "-m", "pip", "check"))
    for exe in ("ffprobe", "ffmpeg", "sqlite3", "git", "curl"):
        check(exe, lambda e=exe: require(shutil.which(e) is not None))
    check("espaço livre >= 2 GiB", lambda: require(shutil.disk_usage(DATA).free >= 2 * 1024**3))
    check("DNS", lambda: socket.getaddrinfo("signage.amb.local", 80))
    check("LF scripts", lambda: require(all(b"\r" not in p.read_bytes() for p in (APP / "deploy/linux").glob("*.sh"))))
    if (DATA / "digital_signage.db").exists():
        check("integridade SQLite", lambda: integrity(DATA / "digital_signage.db"))
        check("SQLite gravável pelo serviço", lambda: run("runuser", "-u", "digital-signage", "--",
            "/usr/bin/test", "-w", DATA / "digital_signage.db"))
        check("SQLite legível pelo serviço", lambda: run("runuser", "-u", "digital-signage", "--",
            VENV / "bin/python", "-c",
            "import sqlite3; c=sqlite3.connect('file:/var/lib/digital-signage/digital_signage.db?mode=ro',uri=True); c.execute('SELECT id FROM users LIMIT 1'); c.close()"))
    else:
        check("senha inicial para banco novo", lambda: require(bool(validate_env().get("DS_INITIAL_ADMIN_PASSWORD"))))
    if mode == "offline":
        def free_port():
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 8000))
        check("porta 8000 livre", free_port)
    else:
        check("serviço ativo", lambda: require(active()))
        check("health local", wait_health)
        check("health Nginx", lambda: wait_health(proxy=True))
    if shutil.which("nginx"):
        check("Nginx ativo", lambda: run("systemctl", "is-active", "--quiet", "nginx"))
    if failures:
        raise RuntimeError("Preflight reprovado: " + ", ".join(failures))


def install():
    distro = read_env(Path("/etc/os-release"))
    if distro.get("ID") not in {"debian", "ubuntu"}:
        raise ValueError("Distribuição suportada: Debian ou Ubuntu com Python >= 3.11")
    if sys.version_info < (3, 11):
        raise ValueError("Python >= 3.11 necessário")
    for name in ("python3", "git", "curl", "ffprobe", "ffmpeg", "sqlite3"):
        if not shutil.which(name):
            raise ValueError("Dependência ausente; instalar conforme LINUX_SERVER.md: " + name)
    if not (APP / "app.py").is_file() or not (APP / "requirements.txt").is_file():
        raise ValueError("Disponibilize o checkout em /opt/digital-signage/app")
    protected_code(APP)
    if active():
        raise ValueError("Serviço ativo: use update-server.sh para atualizar")
    import pwd
    import grp
    try:
        grp.getgrnam("digital-signage")
    except KeyError:
        run("groupadd", "--system", "digital-signage")
    try:
        account = pwd.getpwnam("digital-signage")
        if account.pw_shell not in {"/usr/sbin/nologin", "/sbin/nologin", "/bin/false"}:
            raise ValueError("Conta existente possui shell interativo")
    except KeyError:
        run("useradd", "--system", "--gid", "digital-signage", "--home-dir", DATA,
            "--no-create-home", "--shell", "/usr/sbin/nologin", "digital-signage")
    for path in (APP.parent, ENV.parent, DATA, DATA / "media", DATA / "releases", DATA / "upload-tmp", BACKUPS):
        if path.is_symlink():
            raise ValueError("Diretório simbólico recusado")
        path.mkdir(parents=True, exist_ok=True)
    os.chmod(ENV.parent, 0o700)
    os.chmod(BACKUPS, 0o700)
    os.chmod(APP.parent, 0o755)
    os.chown(APP.parent, 0, 0)
    os.chown(ENV.parent, 0, 0)
    os.chown(BACKUPS, 0, 0)
    # Only adjust top-level data directories; never replace existing data.
    account = pwd.getpwnam("digital-signage")
    for path in (DATA, DATA / "media", DATA / "releases", DATA / "upload-tmp"):
        os.chown(path, account.pw_uid, grp.getgrnam("digital-signage").gr_gid)
        os.chmod(path, 0o700)
    if not (VENV / "bin/python").exists():
        run(sys.executable, "-m", "venv", VENV)
    run(VENV / "bin/python", "-m", "pip", "install", "--upgrade", "pip")
    run(VENV / "bin/python", "-m", "pip", "install", "-r", APP / "requirements.txt")
    run(VENV / "bin/python", "-m", "pip", "check")
    runtime_readable(APP)
    runtime_readable(VENV)
    unit_names = (
        SERVICE,
        "digital-signage-backup.service",
        "digital-signage-backup.timer",
        "digital-signage-retention.service",
        "digital-signage-retention.timer",
    )
    for unit_name in unit_names:
        target = Path("/etc/systemd/system") / unit_name
        shutil.copyfile(APP / "deploy/linux" / unit_name, target)
        os.chmod(target, 0o644)
    run("systemctl", "daemon-reload")
    print("Instalação preparada. Serviço NÃO iniciado nem habilitado.")
    print("Crie server.env root:root 0600; execute preflight offline e siga o runbook.")


def recovery_health():
    wait_health()
    wait_health(proxy="local")


def validate_replacement(was_active):
    """Start for validation, then preserve an intentionally stopped state."""
    run("systemctl", "start", SERVICE)
    recovery_health()
    if not was_active:
        run("systemctl", "stop", SERVICE)


def require_service():
    if output("systemctl", "show", SERVICE, "--property=LoadState", "--value") != "loaded":
        raise RuntimeError("Unidade do serviço inexistente ou inválida")


def resume_previous_service(was_active):
    if was_active:
        try:
            validate_replacement(was_active)
        except BaseException:
            run("systemctl", "stop", SERVICE)
            raise RuntimeError("Estado anterior preservado, mas serviço não recuperou a saúde; intervenção necessária") from None
    else:
        run("systemctl", "stop", SERVICE)


@contextmanager
def recovery_window():
    """Before the first swap, failure must resume the untouched installation."""
    require_service()
    state = {"was_active": active(), "swapped": False}
    try:
        run("systemctl", "stop", SERVICE)
        yield state
    except BaseException:
        if not state["swapped"]:
            resume_previous_service(state["was_active"])
        raise


def copy_verified(source, destination):
    source, destination = Path(source), Path(destination)
    shutil.copy2(source, destination)
    if source.stat().st_size != destination.stat().st_size or digest(source) != digest(destination):
        raise ValueError("Cópia diverge da origem")


def preserve_restore_state():
    """Stopped-service forensic copy, not an SQLite-valid operational backup.

    Preserve corrupt DB bytes and sidecars too; explicitly record missing paths.
    The original directories/files are subsequently renamed for exact rollback.
    """
    if BACKUPS.is_symlink() or DATA.is_symlink() or ENV.is_symlink():
        raise ValueError("Links não são aceitos no estado atual")
    BACKUPS.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = "before-restore-" + uuid.uuid4().hex
    stage = BACKUPS / ("." + name + ".partial")
    stage.mkdir(mode=0o700)
    sources = {"data": DATA, "server.env": ENV}
    entries = {}
    for label, source in sources.items():
        if not source.exists():
            entries[label] = {"present": False}
            continue
        if source.is_dir():
            safe_tree(source)
            shutil.copytree(source, stage / label, copy_function=copy_verified)
        elif source.is_file():
            copy_verified(source, stage / label)
        else:
            raise ValueError("Arquivo especial no estado atual")
        for path in [source, *(source.rglob("*") if source.is_dir() else [])]:
            relative = Path(label) / path.relative_to(source)
            info = path.stat()
            item = {"present": True, "mode": stat.S_IMODE(info.st_mode),
                    "uid": info.st_uid, "gid": info.st_gid, "directory": path.is_dir()}
            if path.is_file():
                item.update(bytes=info.st_size, sha256=digest(stage / relative))
            entries[relative.as_posix()] = item
    for name_in_data in ("digital_signage.db", "media", "releases"):
        entries.setdefault("data/" + name_in_data, {"present": False})
    if (stage / "server.env").exists():
        os.chmod(stage / "server.env", 0o600)
    (stage / "preservation.json").write_text(
        json.dumps({"format": "forensic-preservation-1",
                    "created_at": datetime.now(timezone.utc).isoformat(), "entries": entries}, indent=2) + "\n",
        encoding="utf-8",
    )
    final = BACKUPS / stage.name.removeprefix(".").removesuffix(".partial")
    stage.rename(final)
    return final


def restore(source, confirmed):
    if not source.is_absolute() or ".." in source.parts:
        raise ValueError("Informe caminho absoluto sem traversal")
    source = source.absolute()
    if BACKUPS.is_symlink() or source.is_symlink() or source.parent != BACKUPS or source.resolve().parent != BACKUPS.resolve():
        raise ValueError("Importe o backup como filho direto do diretório de backups")
    manifest = verify_snapshot(source)
    validate_env(source / "server.env")
    print(f"Restaurar banco, mídias, releases e configuração: {manifest['file_count']} arquivos, {manifest['total_bytes']} bytes.")
    if not confirmed:
        raise ValueError("Revise o backup e repita com --confirm-restore")
    # Restoration of data across application versions requires a matching checkout.
    if manifest["metadata"].get("commit") != output("git", "-C", APP, "rev-parse", "HEAD"):
        raise ValueError("Checkout deve corresponder ao commit do backup antes do restore")
    require_service()
    if DATA.is_symlink() or ENV.is_symlink() or ENV.parent.is_symlink() or DATA.parent.is_symlink():
        raise ValueError("Destino simbólico recusado")
    stage = DATA.parent / ("digital-signage-restore-" + uuid.uuid4().hex)
    stage.mkdir(mode=0o700)
    copy_verified(source / "digital_signage.db", stage / "digital_signage.db")
    for name in ("media", "releases"):
        shutil.copytree(source / name, stage / name, copy_function=copy_verified)
    integrity(stage / "digital_signage.db")
    permissions(stage)
    ENV.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    stage_env = ENV.with_name("server.env.restore-" + uuid.uuid4().hex)
    copy_verified(source / "server.env", stage_env)
    os.chmod(stage_env, 0o600)
    os.chown(stage_env, 0, 0)
    old_data = DATA.parent / ("digital-signage-before-" + uuid.uuid4().hex)
    old_env = ENV.with_name("server.env.before-" + uuid.uuid4().hex)
    with recovery_window() as state:
        preventive = preserve_restore_state()
        print("Preservação preventiva:", preventive.name)
        data_moved = data_installed = env_moved = env_installed = False
        try:
            state["swapped"] = True
            if DATA.exists():
                DATA.rename(old_data)
                data_moved = True
            stage.rename(DATA)
            data_installed = True
            if ENV.exists():
                ENV.rename(old_env)
                env_moved = True
            stage_env.rename(ENV)
            env_installed = True
            validate_replacement(state["was_active"])
        except BaseException:
            run("systemctl", "stop", SERVICE)
            if data_installed:
                DATA.rename(stage.with_name(stage.name + "-failed"))
            if data_moved:
                old_data.rename(DATA)
            if env_installed:
                ENV.rename(stage_env.with_name(stage_env.name + "-failed"))
            if env_moved:
                old_env.rename(ENV)
            resume_previous_service(state["was_active"])
            raise
    print("Restore concluído. Estado anterior preservado para revisão administrativa.")


def static_checks(app, python):
    run(python, "-c",
        "import ast,pathlib; [ast.parse(p.read_text(encoding='utf-8')) for p in pathlib.Path('.').rglob('*.py') if '.git' not in p.parts]",
        cwd=app)
    run(python, "-c",
        "from jinja2 import Environment; from pathlib import Path; e=Environment(); [e.parse(p.read_text(encoding='utf-8')) for p in Path('templates').glob('*.html')]",
        cwd=app)
    for path in (app / "deploy/linux").glob("*.sh"):
        run("bash", "-n", path)
        if shutil.which("shellcheck"):
            run("shellcheck", path)
    for unit in (app / "deploy/linux").glob("digital-signage*.service"):
        run("systemd-analyze", "verify", unit)
    for unit in (app / "deploy/linux").glob("digital-signage*.timer"):
        run("systemd-analyze", "verify", unit)
    run("git", "-C", app, "diff", "--check")


def update(commit, branch):
    if not re.fullmatch(r"[a-fA-F0-9]{40}", commit or "") or not branch or branch.startswith("-"):
        raise ValueError("Informe --commit SHA completo e --branch")
    if output("git", "-C", APP, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Árvore Git suja; preserve e revise as alterações antes de atualizar")
    current_branch = output("git", "-C", APP, "branch", "--show-current")
    if current_branch and current_branch != branch:
        raise ValueError("Branch atual difere da branch solicitada")
    previous = output("git", "-C", APP, "rev-parse", "HEAD")
    print(f"Estado anterior: branch={current_branch or '(detached)'} commit={previous}")
    remote = output("git", "-C", APP, "remote", "get-url", "origin")
    if not remote.startswith("https://"):
        raise ValueError("Atualização exige origin HTTPS; SSH não é usado")
    require_service()
    with stopped():
        initial_backup = live_snapshot()
    print("Backup antes da preparação:", initial_backup.name)
    tag = uuid.uuid4().hex
    candidate = APP.parent / ("candidate-" + tag)
    env_candidate = APP.parent / ("venv-" + tag)
    old_app = APP.parent / ("app-before-" + tag)
    old_venv = APP.parent / ("venv-before-" + tag)
    # Fetch into an isolated checkout; never reset or clean the running checkout.
    run("git", "clone", "--no-checkout", "--", remote, candidate)
    run("git", "-C", candidate, "fetch", "origin", branch)
    if output("git", "-C", candidate, "rev-parse", "FETCH_HEAD").lower() != commit.lower():
        raise ValueError("Commit remoto difere do SHA esperado")
    run("git", "-C", candidate, "cat-file", "-e", commit + "^{commit}")
    run("git", "-C", candidate, "checkout", "--detach", commit)
    protected_code(candidate)
    run(sys.executable, "-m", "venv", env_candidate)
    python = env_candidate / "bin/python"
    run(python, "-m", "pip", "install", "-r", candidate / "requirements-dev.txt")
    run(python, "-m", "pip", "check")
    static_checks(candidate, python)
    with tempfile.TemporaryDirectory(prefix="signage-update-tests-") as tmp:
        env = dict(os.environ, DS_DB_FILE=tmp + "/test.db", DS_MEDIA_DIR=tmp + "/media",
                   DS_RELEASE_DIR=tmp + "/releases", DS_INITIAL_ADMIN_PASSWORD=uuid.uuid4().hex,
                   DS_INITIAL_CREDENTIALS_FILE=tmp + "/credentials.txt", PYTHONDONTWRITEBYTECODE="1")
        run(python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests", cwd=candidate, env=env)
    (env_candidate / "requirements-resolved.txt").write_text(output(python, "-m", "pip", "freeze") + "\n")
    runtime_readable(candidate)
    runtime_readable(env_candidate)
    # Candidate unit changes require a separate reviewed service installation.
    if (candidate / "deploy/linux/digital-signage.service").read_bytes() != (APP / "deploy/linux/digital-signage.service").read_bytes():
        raise ValueError("Unidade systemd mudou; revisar e instalar separadamente antes da atualização")
    with recovery_window() as state:
        preventive = live_snapshot()
        app_moved = venv_moved = False
        try:
            state["swapped"] = True
            APP.rename(old_app)
            app_moved = True
            candidate.rename(APP)
            VENV.rename(old_venv)
            venv_moved = True
            VENV.symlink_to(env_candidate, target_is_directory=True)
            validate_replacement(state["was_active"])
        except BaseException:
            run("systemctl", "stop", SERVICE)
            if app_moved:
                if APP.exists():
                    APP.rename(candidate)
                old_app.rename(APP)
            if venv_moved:
                if VENV.is_symlink():
                    VENV.unlink()
                old_venv.rename(VENV)
            try:
                resume_previous_service(state["was_active"])
            except Exception as exc:
                run("systemctl", "stop", SERVICE)
                raise RuntimeError(
                    "Rollback de código não recuperou a saúde. Serviço parado; "
                    "preserve dados novos, investigue incompatibilidade e use restore explícito do backup "
                    + preventive.name
                ) from exc
            raise
    print(f"Atualização concluída: {previous} -> {commit}; backup {preventive.name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "preflight", "backup", "restore", "update", "verify"])
    parser.add_argument("--mode", choices=["offline", "online"], default="offline")
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--keep", type=int, default=14)
    parser.add_argument("--confirm-restore", action="store_true")
    parser.add_argument("--commit")
    parser.add_argument("--branch")
    args = parser.parse_args()
    if args.action == "verify":
        if args.backup is None:
            parser.error("--backup obrigatório")
        verify_snapshot(args.backup)
        print("Backup íntegro")
        return
    if os.name != "posix" or os.geteuid() != 0:
        raise ValueError("Execute como root no Linux, somente na janela autorizada")
    os.umask(0o077)
    import fcntl
    # Serialize operational commands, including preflight, against concurrent runs.
    with open("/run/lock/digital-signage-operations.lock", "a", encoding="utf-8") as lock, operation_log(args.action):
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == "install":
            install()
        elif args.action == "preflight":
            preflight(args.mode)
        elif args.action == "restore":
            # Validate the backup's env in restore(); the live env may be lost.
            if args.backup is None:
                parser.error("--backup obrigatório")
            restore(args.backup, args.confirm_restore)
        else:
            validate_env()
            if ENV.is_symlink() or stat.S_IMODE(ENV.stat().st_mode) != 0o600 or ENV.stat().st_uid != 0:
                raise ValueError("server.env deve ser root:root 0600 e não ser link")
            if args.action == "backup":
                if args.keep < 1:
                    raise ValueError("--keep deve ser >= 1")
                with stopped():
                    result = live_snapshot()
                retain(BACKUPS, args.keep, protected=(result,))
                print("Backup concluído:", result.name)
            elif args.action == "update":
                update(args.commit, args.branch)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Never print env values, subprocess output, credentials or HTTP responses.
        print("Operação falhou:", str(error) if not isinstance(error, subprocess.CalledProcessError) else "comando externo falhou", file=sys.stderr)
        sys.exit(1)
