from __future__ import annotations

import hashlib
import json
import os
import platform
import socket
import shutil
import signal
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from stability_agent import StabilityAgent
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


# ============================================================
# DIGITAL SIGNAGE PLAYER v3
# Foco: estabilidade no Raspberry Pi físico
# ============================================================

PLAYER_VERSION = "3.2.2-stability-preview"

SERVER_BASE = os.getenv("DS_SERVER_BASE", "").strip().rstrip("/")
TV_ID = os.getenv("DS_TV_ID", "").strip().upper()
DEVICE_TOKEN = os.getenv("DS_DEVICE_TOKEN", "").strip()
PROVISION_CODE = os.getenv("DS_PROVISION_CODE", "").strip()

missing_required_config = [
    name
    for name, value in (
        ("DS_SERVER_BASE", SERVER_BASE),
        ("DS_TV_ID", TV_ID),
    )
    if not value
]

if missing_required_config:
    raise SystemExit(
        "[CONFIG] Configuração obrigatória ausente: "
        + ", ".join(missing_required_config)
    )

LOCAL_HOST = os.getenv("DS_LOCAL_HOST", "127.0.0.1")
LOCAL_PORT = int(os.getenv("DS_LOCAL_PORT", "8765"))

SYNC_INTERVAL = 10
HEARTBEAT_INTERVAL = 2
COMMAND_INTERVAL = 5
REQUEST_TIMEOUT = 20
DOWNLOAD_TIMEOUT = 60
DOWNLOAD_RETRIES = 3
RETRY_BASE_DELAY = 1.5

MIN_FREE_SPACE_MB = 500
CACHE_RETENTION_HOURS = 24
STREAM_CHUNK_SIZE = 1024 * 1024

BROWSER_CHECK_INTERVAL = 5
BROWSER_BOOT_GRACE_SECONDS = 45
BROWSER_TELEMETRY_TIMEOUT = 20
VIDEO_NO_PROGRESS_TIMEOUT = 20
MEDIA_LOADING_TIMEOUT = 30
VIDEO_MIN_PROGRESS_SECONDS = 0.50

# Por estabilidade, o v3 inicia com decodificação de vídeo por software.
# Para testar HW decode futuramente:
#   DS_DISABLE_HW_VIDEO_DECODE=0
DISABLE_HW_VIDEO_DECODE = (
    os.getenv("DS_DISABLE_HW_VIDEO_DECODE", "1").strip().lower()
    not in {"0", "false", "no", "off"}
)

BASE_DIR = Path.home() / "DigitalSignagePlayer"
MEDIA_DIR = BASE_DIR / "media"
STATE_FILE = BASE_DIR / "state.json"
HTML_FILE = BASE_DIR / "player_local.html"
LOG_DIR = BASE_DIR / "logs"
CHROMIUM_LOG = LOG_DIR / "firefox-test.log"
NETWORK_WATCHDOG_STATE = Path("/var/lib/digital-signage/network-watchdog.json")
UPDATER_SCRIPT = BASE_DIR / "player_updater.py"

# Perfil NOVO. Não reutiliza o perfil antigo do Chromium.
BROWSER_PROFILE_DIR = Path.home() / ".mozilla" / "firefox-digital-signage-test"
BROWSER_URL = f"http://127.0.0.1:{LOCAL_PORT}/"

BASE_DIR.mkdir(parents=True, exist_ok=True)
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

stability_agent = StabilityAgent(BASE_DIR)

state_lock = threading.Lock()

current_state: dict | None = None
playback_state: dict = {}

last_sync_ok = False
last_sync_at: float | None = None
last_error = ""
last_sync_fingerprint = ""
sync_in_progress = False

browser_last_start_at: float | None = None
browser_last_restart_reason = ""
browser_restart_count = 0
watchdog_started_at = time.time()

video_watch_key: tuple | None = None
video_watch_last_position: float | None = None
video_watch_last_progress_at: float | None = None
video_watch_last_reason = ""


# ============================================================
# UTILIDADES
# ============================================================

def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def safe_float(value, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def now_ts() -> float:
    return time.time()


def cache_stats() -> dict:
    files = 0
    total_bytes = 0

    for path in MEDIA_DIR.rglob("*"):
        if not path.is_file() or path.name.endswith(".part"):
            continue
        try:
            files += 1
            total_bytes += path.stat().st_size
        except OSError:
            pass

    return {
        "cache_files": files,
        "cache_bytes": total_bytes,
    }


def disk_stats() -> dict:
    usage = shutil.disk_usage(BASE_DIR)
    return {
        "disk_total_bytes": usage.total,
        "disk_used_bytes": usage.used,
        "disk_free_bytes": usage.free,
        "disk_free_mb": round(usage.free / (1024 * 1024), 1),
    }


def ensure_free_space() -> None:
    free_bytes = shutil.disk_usage(BASE_DIR).free
    minimum = MIN_FREE_SPACE_MB * 1024 * 1024

    if free_bytes < minimum:
        raise RuntimeError(
            f"Pouco espaço livre: {free_bytes / (1024 * 1024):.0f} MB; "
            f"mínimo={MIN_FREE_SPACE_MB} MB"
        )


def cleanup_stale_parts() -> None:
    for path in MEDIA_DIR.rglob("*.part"):
        try:
            path.unlink()
        except OSError:
            pass


# ============================================================
# HTTP CLIENTE -> SERVIDOR FASTAPI
# ============================================================

def build_request(
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict | None = None,
) -> urllib.request.Request:
    final_headers = {
        "User-Agent": f"DigitalSignagePlayer/{PLAYER_VERSION}",
    }

    if DEVICE_TOKEN:
        final_headers["Authorization"] = f"Bearer {DEVICE_TOKEN}"

    if headers:
        final_headers.update(headers)

    return urllib.request.Request(
        url,
        data=data,
        headers=final_headers,
        method=method,
    )


def fetch_json(url: str) -> dict:
    request = build_request(url)

    with urllib.request.urlopen(
        request,
        timeout=REQUEST_TIMEOUT,
    ) as response:
        raw = response.read()

    data = json.loads(raw.decode("utf-8"))

    if not isinstance(data, dict):
        raise RuntimeError("Resposta JSON inválida")

    return data


def post_json(url: str, payload: dict) -> dict | None:
    body = json.dumps(
        payload,
        ensure_ascii=False,
    ).encode("utf-8")

    request = build_request(
        url,
        method="POST",
        data=body,
        headers={"Content-Type": "application/json"},
    )

    with urllib.request.urlopen(
        request,
        timeout=REQUEST_TIMEOUT,
    ) as response:
        raw = response.read()

    if not raw:
        return None

    data = json.loads(raw.decode("utf-8"))
    return data if isinstance(data, dict) else None


# ============================================================
# CACHE
# ============================================================

def media_relative_path(server_url: str) -> str | None:
    try:
        parsed = urllib.parse.urlparse(server_url)
    except Exception:
        return None

    path = urllib.parse.unquote(parsed.path or "")

    if not path.startswith("/media/"):
        return None

    relative = path[len("/media/"):].lstrip("/")

    if not relative:
        return None

    candidate = Path(relative)

    if any(part in {"", ".", ".."} for part in candidate.parts):
        return None

    return candidate.as_posix()


def local_path_for_server_url(server_url: str) -> Path | None:
    relative = media_relative_path(server_url)

    if not relative:
        return None

    root = MEDIA_DIR.resolve()
    target = (MEDIA_DIR / relative).resolve()

    if target != root and root not in target.parents:
        return None

    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def local_browser_url(server_relative_url: str) -> str:
    relative = media_relative_path(server_relative_url)

    if not relative:
        return ""

    return "/media/" + urllib.parse.quote(
        relative,
        safe="/._-~",
    )


def absolute_server_url(relative_url: str) -> str:
    return urllib.parse.urljoin(
        SERVER_BASE + "/",
        relative_url,
    )


def validate_cached_resource(server_relative_url: str) -> bool:
    path = local_path_for_server_url(server_relative_url)

    if path is None:
        return False

    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def download_resource(server_relative_url: str) -> bool:
    local_path = local_path_for_server_url(
        server_relative_url
    )

    if local_path is None:
        print(f"[CACHE] URL inválida: {server_relative_url}")
        return False

    if validate_cached_resource(server_relative_url):
        return True

    remote_url = absolute_server_url(
        server_relative_url
    )

    temp_path = Path(str(local_path) + ".part")

    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        try:
            ensure_free_space()

            if temp_path.exists():
                temp_path.unlink()

            request = build_request(remote_url)
            bytes_written = 0

            with urllib.request.urlopen(
                request,
                timeout=DOWNLOAD_TIMEOUT,
            ) as response:
                expected_header = response.headers.get(
                    "Content-Length"
                )

                expected_length = (
                    int(expected_header)
                    if expected_header
                    and expected_header.isdigit()
                    else None
                )

                with temp_path.open("wb") as output:
                    while True:
                        chunk = response.read(
                            STREAM_CHUNK_SIZE
                        )

                        if not chunk:
                            break

                        output.write(chunk)
                        bytes_written += len(chunk)

            if bytes_written <= 0:
                raise RuntimeError("Download vazio")

            if (
                expected_length is not None
                and bytes_written != expected_length
            ):
                raise RuntimeError(
                    f"Download incompleto: "
                    f"esperado={expected_length}, "
                    f"recebido={bytes_written}"
                )

            os.replace(temp_path, local_path)

            print(
                f"[CACHE] OK · {server_relative_url} · "
                f"{bytes_written / (1024 * 1024):.1f} MB"
            )

            return True

        except Exception as exc:
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except OSError:
                pass

            if attempt >= DOWNLOAD_RETRIES:
                print(
                    f"[CACHE] FALHA · {server_relative_url} · "
                    f"{exc}"
                )
                return False

            delay = RETRY_BASE_DELAY * (
                2 ** (attempt - 1)
            )

            print(
                f"[CACHE] Tentativa {attempt} falhou · "
                f"retry={delay:.1f}s · {exc}"
            )

            time.sleep(delay)

    return False


def collect_urls(data: object) -> set[str]:
    urls: set[str] = set()

    if not isinstance(data, dict):
        return urls

    playlist = data.get("playlist")

    if isinstance(playlist, list):
        for item in playlist:
            if not isinstance(item, dict):
                continue

            content = item.get("content")

            if not isinstance(content, dict):
                continue

            url = content.get("url")

            if (
                isinstance(url, str)
                and url.startswith("/media/")
            ):
                urls.add(url)

            pages = content.get("pages")

            if isinstance(pages, list):
                for page in pages:
                    if (
                        isinstance(page, str)
                        and page.startswith("/media/")
                    ):
                        urls.add(page)

    resources = data.get("resources")

    if isinstance(resources, list):
        for resource in resources:
            if (
                isinstance(resource, str)
                and resource.startswith("/media/")
            ):
                urls.add(resource)

    return urls


def rewrite_urls_to_local(data: dict) -> dict:
    cloned = json.loads(
        json.dumps(data, ensure_ascii=False)
    )

    playlist = cloned.get("playlist")

    if not isinstance(playlist, list):
        return cloned

    for item in playlist:
        if not isinstance(item, dict):
            continue

        content = item.get("content")

        if not isinstance(content, dict):
            continue

        url = content.get("url")

        if (
            isinstance(url, str)
            and url.startswith("/media/")
        ):
            content["url"] = local_browser_url(url)

        pages = content.get("pages")

        if isinstance(pages, list):
            content["pages"] = [
                local_browser_url(page)
                if isinstance(page, str)
                and page.startswith("/media/")
                else page
                for page in pages
            ]

    return cloned


def save_state(data: dict) -> None:
    temp = STATE_FILE.with_suffix(".tmp")

    temp.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    os.replace(temp, STATE_FILE)


def load_state() -> dict | None:
    if not STATE_FILE.exists():
        return None

    try:
        data = json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )

        return data if isinstance(data, dict) else None

    except Exception as exc:
        print(f"[STATE] Estado inválido: {exc}")
        return None


def cleanup_orphan_cache(resources: list[str]) -> int:
    expected: set[Path] = set()

    for resource in resources:
        path = local_path_for_server_url(resource)

        if path is not None:
            expected.add(path.resolve())

    cutoff = now_ts() - (
        CACHE_RETENTION_HOURS * 3600
    )

    removed = 0

    for path in MEDIA_DIR.rglob("*"):
        if (
            not path.is_file()
            or path.name.endswith(".part")
        ):
            continue

        try:
            if path.resolve() in expected:
                continue

            if path.stat().st_mtime > cutoff:
                continue

            path.unlink()
            removed += 1

        except OSError:
            pass

    return removed


def compute_sync_fingerprint(
    player_data: dict,
    resources: list[str],
) -> str:
    payload = {
        "revision": player_data.get("revision"),
        "playlist_id": player_data.get("playlist_id"),
        "source": player_data.get("source"),
        "schedule_id": player_data.get("schedule_id"),
        "resources": resources,
    }

    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")

    return hashlib.sha256(raw).hexdigest()[:20]


# ============================================================
# SINCRONIZAÇÃO
# ============================================================

def sync_once() -> bool:
    global current_state
    global last_sync_ok
    global last_sync_at
    global last_error
    global last_sync_fingerprint
    global sync_in_progress

    with state_lock:
        sync_in_progress = True

    try:
        ensure_free_space()

        player_url = (
            f"{SERVER_BASE}/api/player/"
            f"{urllib.parse.quote(TV_ID)}"
        )

        manifest_url = (
            f"{SERVER_BASE}/api/player/"
            f"{urllib.parse.quote(TV_ID)}"
            f"/cache-manifest"
        )

        player_data = fetch_json(player_url)
        manifest_data = fetch_json(manifest_url)

        all_urls = (
            collect_urls(player_data)
            | collect_urls(manifest_data)
        )

        resources = sorted(all_urls)

        missing = [
            resource
            for resource in resources
            if not validate_cached_resource(resource)
        ]

        failed: list[str] = []

        for resource in missing:
            if not download_resource(resource):
                failed.append(resource)

        if failed:
            raise RuntimeError(
                f"{len(failed)} recurso(s) falharam; "
                "mantendo versão anterior"
            )

        local_state = rewrite_urls_to_local(
            player_data
        )

        sync_time = now_ts()

        fingerprint = compute_sync_fingerprint(
            player_data,
            resources,
        )

        local_state.update({
            "tv_id": TV_ID,
            "player_version": PLAYER_VERSION,
            "server_base": SERVER_BASE,
            "cached_resources": len(resources),
            "sync_ok": True,
            "sync_at": sync_time,
            "sync_fingerprint": fingerprint,
        })

        local_state.update(cache_stats())
        local_state.update(disk_stats())

        # A nova playlist só entra em produção depois
        # de TODO o conteúdo estar disponível localmente.
        save_state(local_state)

        with state_lock:
            current_state = local_state
            last_sync_ok = True
            last_sync_at = sync_time
            last_error = ""
            last_sync_fingerprint = fingerprint

        removed = cleanup_orphan_cache(resources)

        print(
            f"[SYNC] OK · revision={player_data.get('revision')} "
            f"· recursos={len(resources)} "
            f"· baixados={len(missing)} "
            f"· removidos={removed}"
        )

        return True

    except Exception as exc:
        with state_lock:
            last_sync_ok = False
            last_error = str(exc)

        print(
            "[SYNC] Offline/erro · "
            f"mantendo última versão válida · {exc}"
        )

        return False

    finally:
        with state_lock:
            sync_in_progress = False


# ============================================================
# CHROMIUM
# ============================================================

def signage_chromium_pids() -> list[int]:
    pids: list[int] = []

    proc_root = Path("/proc")

    if not proc_root.exists():
        return pids

    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue

        try:
            raw = (entry / "cmdline").read_bytes()

            cmdline = raw.replace(
                b"\x00",
                b" ",
            ).decode(
                "utf-8",
                errors="ignore",
            )

        except (OSError, PermissionError):
            continue

        if (
            "firefox" in cmdline.lower()
            and str(BROWSER_PROFILE_DIR) in cmdline
        ):
            pids.append(int(entry.name))

    return sorted(set(pids))


def browser_running() -> bool:
    return bool(signage_chromium_pids())


def chromium_executable() -> str | None:
    return shutil.which("firefox")


def wayland_available() -> bool:
    runtime = Path(
        f"/run/user/{os.getuid()}"
    )

    return (
        bool(os.getenv("WAYLAND_DISPLAY"))
        or os.getenv("XDG_SESSION_TYPE", "").lower()
        == "wayland"
        or (runtime / "wayland-0").exists()
    )


def cleanup_browser_caches() -> None:
    # Mantém cookies/configurações do perfil, mas remove caches gráficos.
    for name in (
        "GPUCache",
        "ShaderCache",
        "GrShaderCache",
        "DawnCache",
    ):
        path = BROWSER_PROFILE_DIR / name

        if path.exists():
            try:
                shutil.rmtree(
                    path,
                    ignore_errors=True,
                )
            except OSError:
                pass


def stop_signage_chromium() -> None:
    pids = signage_chromium_pids()

    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except (
            ProcessLookupError,
            PermissionError,
            OSError,
        ):
            pass

    deadline = time.time() + 3

    while (
        signage_chromium_pids()
        and time.time() < deadline
    ):
        time.sleep(0.2)

    for pid in signage_chromium_pids():
        try:
            os.kill(pid, signal.SIGKILL)
        except (
            ProcessLookupError,
            PermissionError,
            OSError,
        ):
            pass


def reset_video_watch() -> None:
    global video_watch_key
    global video_watch_last_position
    global video_watch_last_progress_at

    video_watch_key = None
    video_watch_last_position = None
    video_watch_last_progress_at = None


def start_chromium(reason: str) -> bool:
    global browser_last_start_at
    global browser_last_restart_reason
    global browser_restart_count

    executable = chromium_executable()

    if not executable:
        browser_last_restart_reason = (
            "Firefox não encontrado"
        )
        return False

    env = os.environ.copy()
    env.setdefault("DISPLAY", ":0")

    runtime_dir = Path(
        f"/run/user/{os.getuid()}"
    )

    if runtime_dir.exists():
        env.setdefault(
            "XDG_RUNTIME_DIR",
            str(runtime_dir),
        )

        bus = runtime_dir / "bus"

        if bus.exists():
            env.setdefault(
                "DBUS_SESSION_BUS_ADDRESS",
                f"unix:path={bus}",
            )

        if (
            (runtime_dir / "wayland-0").exists()
            and not env.get("WAYLAND_DISPLAY")
        ):
            env["WAYLAND_DISPLAY"] = "wayland-0"

    BROWSER_PROFILE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    cleanup_browser_caches()

    # Perfil Firefox exclusivo para este teste A/B.
    # Mantemos decodificação de vídeo por hardware desabilitada.
    prefs_file = BROWSER_PROFILE_DIR / "user.js"

    prefs_file.write_text(
        "\n".join([
            'user_pref("media.hardware-video-decoding.enabled", false);',
            'user_pref("media.ffmpeg.vaapi.enabled", false);',
            'user_pref("browser.shell.checkDefaultBrowser", false);',
            'user_pref("browser.startup.homepage_override.mstone", "ignore");',
            'user_pref("toolkit.telemetry.reportingpolicy.firstRun", false);',
        ]) + "\n",
        encoding="utf-8",
    )

    if wayland_available():
        env["MOZ_ENABLE_WAYLAND"] = "1"

    command = [
        executable,
        "--no-remote",
        "--kiosk",
        "--profile",
        str(BROWSER_PROFILE_DIR),
        BROWSER_URL,
    ]

    try:
        with CHROMIUM_LOG.open(
            "a",
            encoding="utf-8",
            buffering=1,
        ) as log_handle:

            log_handle.write(
                "\n"
                + "=" * 72
                + "\n"
                + f"Chromium start: "
                + time.strftime("%Y-%m-%d %H:%M:%S")
                + "\n"
                + f"reason={reason}\n"
                + f"hw_video_decode="
                + (
                    "off"
                    if DISABLE_HW_VIDEO_DECODE
                    else "on"
                )
                + "\n"
                + "COMMAND: "
                + " ".join(command)
                + "\n"
            )

            subprocess.Popen(
                command,
                cwd=str(BASE_DIR),
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

        browser_last_start_at = now_ts()
        browser_last_restart_reason = reason
        browser_restart_count += 1
        try:
            with state_lock:
                content_name = str(playback_state.get("content_name") or "")
            stability_agent.record_browser_start(reason, content_name)
        except Exception as exc:
            print(f"[STABILITY] não foi possível registrar o navegador: {exc}")

        reset_video_watch()

        print(
            f"[BROWSER] iniciado · motivo={reason} "
            f"· restart={browser_restart_count}"
        )

        return True

    except Exception as exc:
        browser_last_restart_reason = (
            f"Falha ao iniciar: {exc}"
        )

        print(
            f"[BROWSER] falha ao iniciar · {exc}"
        )

        return False


def restart_browser(reason: str) -> None:
    global video_watch_last_reason

    video_watch_last_reason = reason

    print(
        f"[WATCHDOG] reiniciando Firefox · {reason}"
    )

    stop_signage_chromium()

    time.sleep(2)

    start_chromium(
        (f"remote: {reason}" if "comando remoto" in reason.lower() else f"watchdog: {reason}")
    )


def ensure_chromium() -> None:
    """
    Watchdog do navegador e da reprodução.

    Casos tratados:
    - Chromium morreu;
    - página parou de enviar telemetria;
    - mídia ficou presa em loading;
    - vídeo continua 'playing', mas currentTime não avança;
    - vídeo fica pausado inesperadamente.
    """

    global video_watch_key
    global video_watch_last_position
    global video_watch_last_progress_at

    now = now_ts()

    if not browser_running():
        start_chromium("processo ausente")
        return

    grace_origin = (
        browser_last_start_at
        or watchdog_started_at
    )

    if (
        now - grace_origin
        < BROWSER_BOOT_GRACE_SECONDS
    ):
        return

    with state_lock:
        state = (
            json.loads(json.dumps(current_state))
            if current_state
            else {}
        )

        live = (
            json.loads(json.dumps(playback_state))
            if playback_state
            else {}
        )

    playlist = (
        state.get("playlist")
        if isinstance(state, dict)
        else None
    )

    if not isinstance(playlist, list) or not playlist:
        reset_video_watch()
        return

    reported_at = safe_float(
        live.get("reported_at"),
        0,
    ) or 0

    if (
        not reported_at
        or now - reported_at
        > BROWSER_TELEMETRY_TIMEOUT
    ):
        restart_browser(
            "telemetria parada"
        )
        return

    render_state = str(
        live.get("render_state")
        or ""
    ).lower()

    item_started_at = safe_float(
        live.get("item_started_at"),
        None,
    )

    if (
        render_state == "loading"
        and item_started_at
        and now - item_started_at
        > MEDIA_LOADING_TIMEOUT
    ):
        restart_browser(
            "mídia presa em loading"
        )
        return

    content_type = str(
        live.get("content_type")
        or ""
    ).lower()

    if content_type != "video":
        reset_video_watch()
        return

    playback_status = str(
        live.get("playback_state")
        or ""
    ).lower()

    position = safe_float(
        live.get("playback_position"),
        None,
    )

    if position is None:
        return

    key = (
        live.get("browser_session"),
        live.get("item_id"),
        live.get("current_index"),
    )

    if key != video_watch_key:
        video_watch_key = key
        video_watch_last_position = position
        video_watch_last_progress_at = now
        return

    if video_watch_last_position is None:
        video_watch_last_position = position
        video_watch_last_progress_at = now
        return

    # Novo loop/seek para trás.
    if position < video_watch_last_position - 1:
        video_watch_last_position = position
        video_watch_last_progress_at = now
        return

    if (
        position
        >= video_watch_last_position
        + VIDEO_MIN_PROGRESS_SECONDS
    ):
        video_watch_last_position = position
        video_watch_last_progress_at = now
        return

    if video_watch_last_progress_at is None:
        video_watch_last_progress_at = now
        return

    no_progress_for = (
        now - video_watch_last_progress_at
    )

    # Em kiosk, um vídeo visível não deve ficar pausado sozinho.
    if (
        playback_status == "paused"
        and render_state == "visible"
        and no_progress_for >= 12
    ):
        restart_browser(
            f"vídeo pausado sem motivo "
            f"· posição={position:.2f}s"
        )
        return

    if (
        playback_status == "playing"
        and no_progress_for
        >= VIDEO_NO_PROGRESS_TIMEOUT
    ):
        event = str(
            live.get("video_event")
            or ""
        )

        ready = live.get(
            "video_ready_state"
        )

        network = live.get(
            "video_network_state"
        )

        restart_browser(
            f"vídeo sem progresso por "
            f"{no_progress_for:.1f}s "
            f"· posição={position:.2f}s "
            f"· evento={event} "
            f"· ready={ready} "
            f"· network={network}"
        )



# ============================================================
# TELEMETRIA AVANÇADA
# ============================================================

def read_first_line(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()[0].strip()
    except Exception:
        return ""


def os_version_text() -> str:
    try:
        data = Path("/etc/os-release").read_text(encoding="utf-8", errors="ignore")
        values = {}
        for line in data.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                values[k] = v.strip().strip('"')
        return values.get("PRETTY_NAME") or platform.platform()
    except Exception:
        return platform.platform()


def uptime_seconds() -> float | None:
    try:
        return float(read_first_line("/proc/uptime").split()[0])
    except Exception:
        return None


def memory_percent() -> float | None:
    try:
        raw = Path("/proc/meminfo").read_text(encoding="utf-8", errors="ignore")
        data = {}
        for line in raw.splitlines():
            parts = line.replace(":", "").split()
            if len(parts) >= 2:
                data[parts[0]] = float(parts[1])
        total = data.get("MemTotal")
        available = data.get("MemAvailable")
        if total and available is not None:
            return round((total - available) / total * 100, 1)
    except Exception:
        return None
    return None


def cpu_percent() -> float | None:
    try:
        load1 = os.getloadavg()[0]
        cpus = os.cpu_count() or 1
        return round(min(100.0, max(0.0, load1 / cpus * 100.0)), 1)
    except Exception:
        return None


def temperature_c() -> float | None:
    for path in ["/sys/class/thermal/thermal_zone0/temp", "/sys/class/hwmon/hwmon0/temp1_input"]:
        try:
            raw = float(read_first_line(path))
            if raw > 1000:
                raw /= 1000.0
            return round(raw, 1)
        except Exception:
            continue
    return None


def default_network_interface() -> str:
    try:
        for line in Path("/proc/net/route").read_text().splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "00000000":
                return parts[0]
    except Exception:
        pass
    return ""


def wifi_signal() -> str:
    try:
        raw = Path("/proc/net/wireless").read_text(encoding="utf-8", errors="ignore")
        for line in raw.splitlines()[2:]:
            if ":" in line:
                iface, rest = line.split(":", 1)
                vals = rest.split()
                if len(vals) >= 3:
                    return f"{iface.strip()} {vals[2].strip('.')} dBm"
    except Exception:
        pass
    return ""


def network_watchdog_state() -> dict:
    try:
        data = json.loads(NETWORK_WATCHDOG_STATE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {
                "network_watchdog_status": data.get("status", "unknown"),
                "network_watchdog_failures": data.get("failures", 0),
                "network_watchdog_last_action": data.get("last_action", ""),
                "network_watchdog_last_ok_at": data.get("last_ok_at"),
            }
    except Exception:
        pass
    return {
        "network_watchdog_status": "unknown",
        "network_watchdog_failures": 0,
        "network_watchdog_last_action": "",
        "network_watchdog_last_ok_at": None,
    }


def advanced_telemetry() -> dict:
    iface = default_network_interface()
    return {
        "hostname": socket.gethostname(),
        "os_version": os_version_text(),
        "uptime_seconds": uptime_seconds(),
        "cpu_percent": cpu_percent(),
        "memory_percent": memory_percent(),
        "temperature_c": temperature_c(),
        "network_interface": iface,
        "network_type": "wifi" if iface.startswith(("wl", "wlan")) else ("ethernet" if iface else "unknown"),
        "wifi_signal": wifi_signal(),
        "browser_restart_count": browser_restart_count,
        "last_recovery_reason": browser_last_restart_reason,
        "hw_video_decode": not DISABLE_HW_VIDEO_DECODE,
        **network_watchdog_state(),
    }


# ============================================================
# COMANDOS REMOTOS
# ============================================================

def ack_command(command_id: int, status: str, progress: int = 0, message: str = "", result: dict | None = None) -> None:
    try:
        post_json(
            f"{SERVER_BASE}/api/player/{urllib.parse.quote(TV_ID)}/commands/{command_id}/ack",
            {"status": status, "progress": int(progress), "message": message, "result": result or {}},
        )
    except Exception as exc:
        print(f"[COMMAND] Falha ao confirmar comando {command_id}: {exc}")


def fetch_commands() -> list[dict]:
    try:
        data = fetch_json(f"{SERVER_BASE}/api/player/{urllib.parse.quote(TV_ID)}/commands")
        commands = data.get("commands", [])
        return commands if isinstance(commands, list) else []
    except Exception as exc:
        print(f"[COMMAND] não foi possível consultar comandos · {exc}")
        return []


def execute_command(command: dict) -> None:
    command_id = int(command.get("id") or 0)
    name = str(command.get("command") or "")
    args = command.get("args") if isinstance(command.get("args"), dict) else {}
    if not command_id or not name:
        return
    print(f"[COMMAND] executando #{command_id}: {name}")
    ack_command(command_id, "running", 5, "Comando recebido pelo player")
    try:
        if name == "sync_now":
            ok = sync_once()
            ack_command(command_id, "done" if ok else "failed", 100, "Sincronização concluída" if ok else last_error)
        elif name == "restart_browser":
            restart_browser("Comando remoto do painel")
            ack_command(command_id, "done", 100, "Chromium reiniciado")
        elif name == "clear_cache":
            removed_parts = 0
            for path in MEDIA_DIR.rglob("*.part"):
                try:
                    path.unlink(); removed_parts += 1
                except OSError:
                    pass
            ack_command(command_id, "done", 100, f"Cache temporário limpo; {removed_parts} arquivo(s) .part removidos")
        elif name == "diagnose":
            payload = PlayerHandler._health_payload_static()
            payload.update(advanced_telemetry())
            ack_command(command_id, "done", 100, "Diagnóstico coletado", payload)
        elif name == "update_player":
            if not UPDATER_SCRIPT.exists():
                raise RuntimeError("player_updater.py não encontrado")
            url = str(args.get("url") or "")
            version = str(args.get("version") or "")
            sha256 = str(args.get("sha256") or "")
            if not url or not sha256:
                raise RuntimeError("Release sem URL ou SHA-256")
            ack_command(command_id, "running", 15, f"Iniciando atualização {version}")
            subprocess.Popen([
                "python3",
                str(UPDATER_SCRIPT),
                "--server-base", SERVER_BASE,
                "--tv-id", TV_ID,
                "--device-token", DEVICE_TOKEN,
                "--command-id", str(command_id),
                "--url", url,
                "--sha256", sha256,
                "--version", version,
            ], cwd=str(BASE_DIR))
        elif name == "restart_player":
            ack_command(command_id, "done", 100, "Player será reiniciado")
            time.sleep(0.8)
            os._exit(0)
        elif name == "reboot":
            ack_command(command_id, "done", 100, "Raspberry será reiniciado")
            subprocess.Popen(["sudo", "/sbin/reboot"])
        else:
            ack_command(command_id, "failed", 100, f"Comando desconhecido: {name}")
    except Exception as exc:
        ack_command(command_id, "failed", 100, str(exc))


def command_worker_tick() -> None:
    for command in fetch_commands():
        execute_command(command)

# ============================================================
# HEARTBEAT
# ============================================================

def send_heartbeat() -> None:
    with state_lock:
        state = (
            json.loads(json.dumps(current_state))
            if current_state
            else {}
        )

        live = (
            json.loads(json.dumps(playback_state))
            if playback_state
            else {}
        )

        syncing = sync_in_progress
        sync_ok = last_sync_ok
        sync_error = last_error
        sync_at = last_sync_at

    live_age = (
        now_ts()
        - (safe_float(
            live.get("reported_at"),
            0,
        ) or 0)
    )

    live_fresh = (
        bool(live)
        and live_age <= 20
    )

    content = {}

    if live_fresh:
        content = {
            "id": live.get("content_id"),
            "name": live.get(
                "content_name",
                "",
            ),
        }

    payload = {
        "tv_id": TV_ID,
        "current_playlist_id": (
            state.get("playlist_id")
            if isinstance(state, dict)
            else None
        ),
        "current_playlist_version": (
            state.get("published_version")
            if isinstance(state, dict)
            else None
        ),
        "current_content_id": (
            content.get("id")
            if isinstance(content, dict)
            else None
        ),
        "current_content_name": (
            content.get("name", "")
            if isinstance(content, dict)
            else ""
        ),
        "current_content_type": (
            live.get("content_type", "")
            if live_fresh
            else ""
        ),
        "current_item_id": (
            live.get("item_id")
            if live_fresh
            else None
        ),
        "current_item_position": (
            live.get("item_position")
            if live_fresh
            else None
        ),
        "playback_position": (
            live.get("playback_position")
            if live_fresh
            else None
        ),
        "playback_duration": (
            live.get("playback_duration")
            if live_fresh
            else None
        ),
        "playback_state": (
            live.get(
                "playback_state",
                "unknown",
            )
            if live_fresh
            else "unknown"
        ),
        "playback_updated_at": (
            live.get("reported_at")
            if live_fresh
            else None
        ),
        "browser_running": browser_running(),
        "player_version": PLAYER_VERSION,
        "sync_status": (
            "syncing"
            if syncing
            else (
                "ok"
                if sync_ok
                else "offline"
            )
        ),
        "last_sync_at": sync_at,
        "last_error": (
            sync_error[:1000]
            if sync_error
            else ""
        ),
        **advanced_telemetry(),
        **stability_agent.stats(),
        **cache_stats(),
        **disk_stats(),
    }

    try:
        response = post_json(
            f"{SERVER_BASE}/api/tv/heartbeat",
            payload,
        )
        if isinstance(response, dict) and response.get("status") == "ok":
            stability_agent.acknowledge(response.get("accepted_stability_event_ids", []))

    except Exception as exc:
        print(
            f"[HEARTBEAT] servidor indisponível · {exc}"
        )


# ============================================================
# WORKER
# ============================================================

def background_worker() -> None:
    next_sync = 0.0
    next_heartbeat = 0.0
    next_browser_check = 0.0
    next_command_check = 0.0

    while True:
        now = now_ts()

        if now >= next_browser_check:
            ensure_chromium()
            next_browser_check = (
                now_ts()
                + BROWSER_CHECK_INTERVAL
            )

        if now >= next_sync:
            sync_once()
            next_sync = (
                now_ts()
                + SYNC_INTERVAL
            )

        if now >= next_heartbeat:
            send_heartbeat()
            next_heartbeat = (
                now_ts()
                + HEARTBEAT_INTERVAL
            )

        if now >= next_command_check:
            command_worker_tick()
            next_command_check = (
                now_ts()
                + COMMAND_INTERVAL
            )

        time.sleep(0.5)


# ============================================================
# SERVIDOR HTTP LOCAL
# ============================================================

def guess_content_type(suffix: str) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".mp4": "video/mp4",
        ".json": "application/json",
        ".html": "text/html; charset=utf-8",
    }.get(
        suffix.lower(),
        "application/octet-stream",
    )


class PlayerHandler(BaseHTTPRequestHandler):

    @staticmethod
    def _health_payload_static() -> dict:
        with state_lock:
            state = (json.loads(json.dumps(current_state)) if current_state else {})
            live = (json.loads(json.dumps(playback_state)) if playback_state else {})
        payload = {
            "status": "ok",
            "tv_id": TV_ID,
            "player_version": PLAYER_VERSION,
            "sync_ok": last_sync_ok,
            "sync_in_progress": sync_in_progress,
            "last_sync_at": last_sync_at,
            "last_error": last_error,
            "sync_fingerprint": last_sync_fingerprint,
            "playback": live,
            "browser_running": browser_running(),
            "browser_last_start_at": browser_last_start_at,
            "browser_last_restart_reason": browser_last_restart_reason,
            "browser_restart_count": browser_restart_count,
            "stability_boot_id": stability_agent.boot_id,
            "stability_pending_events": len(stability_agent.pending()),
            "server_base": SERVER_BASE,
            "token_configured": bool(DEVICE_TOKEN),
        }
        payload.update(cache_stats())
        payload.update(disk_stats())
        return payload

    server_version = (
        f"DigitalSignageLocalPlayer/"
        f"{PLAYER_VERSION}"
    )

    def _common_headers(
        self,
        content_type: str,
        content_length: int | None = None,
    ) -> None:
        self.send_header(
            "Content-Type",
            content_type,
        )

        self.send_header(
            "Cache-Control",
            "no-store",
        )

        self.send_header(
            "X-Content-Type-Options",
            "nosniff",
        )

        if content_length is not None:
            self.send_header(
                "Content-Length",
                str(content_length),
            )

    def _send_bytes(
        self,
        body: bytes,
        status: int = 200,
        content_type: str = (
            "text/plain; charset=utf-8"
        ),
    ) -> None:
        self.send_response(status)

        self._common_headers(
            content_type,
            len(body),
        )

        self.end_headers()

        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (
                BrokenPipeError,
                ConnectionResetError,
            ):
                pass

    def _resolve_media_path(
        self,
        request_path: str,
    ) -> Path | None:
        relative = urllib.parse.unquote(
            request_path[len("/media/"):]
        ).lstrip("/")

        root = MEDIA_DIR.resolve()
        candidate = (
            MEDIA_DIR / relative
        ).resolve()

        if (
            candidate != root
            and root not in candidate.parents
        ):
            return None

        return candidate

    def _serve_media_file(
        self,
        candidate: Path,
    ) -> None:
        if (
            not candidate.exists()
            or not candidate.is_file()
        ):
            self._send_bytes(
                b"Arquivo nao encontrado",
                404,
            )
            return

        try:
            file_size = candidate.stat().st_size
        except OSError:
            self._send_bytes(
                b"Erro ao acessar arquivo",
                500,
            )
            return

        content_type = guess_content_type(
            candidate.suffix
        )

        range_header = self.headers.get(
            "Range"
        )

        if (
            range_header
            and range_header.startswith("bytes=")
        ):
            try:
                range_spec = (
                    range_header[len("bytes="):]
                    .split(",", 1)[0]
                    .strip()
                )

                start_text, end_text = (
                    range_spec.split("-", 1)
                )

                if start_text:
                    start = int(start_text)
                    end = (
                        int(end_text)
                        if end_text
                        else file_size - 1
                    )
                else:
                    suffix_length = int(end_text)
                    start = max(
                        0,
                        file_size - suffix_length,
                    )
                    end = file_size - 1

                if (
                    start < 0
                    or start >= file_size
                    or end < start
                ):
                    raise ValueError(
                        "Range inválido"
                    )

                end = min(
                    end,
                    file_size - 1,
                )

                length = end - start + 1

                self.send_response(206)
                self.send_header(
                    "Content-Type",
                    content_type,
                )
                self.send_header(
                    "Accept-Ranges",
                    "bytes",
                )
                self.send_header(
                    "Content-Range",
                    f"bytes {start}-{end}/{file_size}",
                )
                self.send_header(
                    "Content-Length",
                    str(length),
                )
                self.send_header(
                    "Cache-Control",
                    "no-store",
                )
                self.end_headers()

                if self.command == "HEAD":
                    return

                with candidate.open("rb") as source:
                    source.seek(start)
                    remaining = length

                    while remaining > 0:
                        chunk = source.read(
                            min(
                                STREAM_CHUNK_SIZE,
                                remaining,
                            )
                        )

                        if not chunk:
                            break

                        self.wfile.write(chunk)
                        remaining -= len(chunk)

                return

            except (
                BrokenPipeError,
                ConnectionResetError,
            ):
                return

            except Exception:
                self.send_response(416)
                self.send_header(
                    "Content-Range",
                    f"bytes */{file_size}",
                )
                self.send_header(
                    "Content-Length",
                    "0",
                )
                self.end_headers()
                return

        self.send_response(200)
        self.send_header(
            "Content-Type",
            content_type,
        )
        self.send_header(
            "Accept-Ranges",
            "bytes",
        )
        self.send_header(
            "Content-Length",
            str(file_size),
        )
        self.send_header(
            "Cache-Control",
            "no-store",
        )
        self.end_headers()

        if self.command == "HEAD":
            return

        try:
            with candidate.open("rb") as source:
                while True:
                    chunk = source.read(
                        STREAM_CHUNK_SIZE
                    )

                    if not chunk:
                        break

                    self.wfile.write(chunk)

        except (
            BrokenPipeError,
            ConnectionResetError,
        ):
            pass

    def _health_payload(self) -> dict:
        with state_lock:
            live = (
                json.loads(
                    json.dumps(playback_state)
                )
                if playback_state
                else {}
            )

            state = (
                json.loads(
                    json.dumps(current_state)
                )
                if current_state
                else {}
            )

        payload = {
            "status": "ok",
            "tv_id": TV_ID,
            "player_version": PLAYER_VERSION,
            "server_base": SERVER_BASE,
            "sync_ok": last_sync_ok,
            "sync_in_progress": sync_in_progress,
            "last_sync_at": last_sync_at,
            "last_error": last_error,
            "sync_fingerprint": (
                last_sync_fingerprint
            ),
            "playlist_id": (
                state.get("playlist_id")
                if isinstance(state, dict)
                else None
            ),
            "playlist_revision": (
                state.get("revision")
                if isinstance(state, dict)
                else None
            ),
            "playback": live,
            "browser_running": browser_running(),
            "browser_pids": (
                signage_chromium_pids()
            ),
            "browser_last_start_at": (
                browser_last_start_at
            ),
            "browser_last_restart_reason": (
                browser_last_restart_reason
            ),
            "browser_restart_count": (
                browser_restart_count
            ),
            "hw_video_decode": (
                "disabled"
                if DISABLE_HW_VIDEO_DECODE
                else "enabled"
            ),
            "video_watchdog": {
                "key": video_watch_key,
                "last_position": (
                    video_watch_last_position
                ),
                "last_progress_at": (
                    video_watch_last_progress_at
                ),
                "last_reason": (
                    video_watch_last_reason
                ),
            },
            "chromium_log": str(CHROMIUM_LOG),
        }

        payload.update(cache_stats())
        payload.update(disk_stats())

        return payload

    def _handle_get_or_head(self) -> None:
        parsed = urllib.parse.urlparse(
            self.path
        )

        path = parsed.path

        if path in {"/", "/index.html"}:
            if not HTML_FILE.exists():
                self._send_bytes(
                    b"player_local.html ausente",
                    500,
                )
                return

            self._send_bytes(
                HTML_FILE.read_bytes(),
                200,
                "text/html; charset=utf-8",
            )
            return

        if path == "/state":
            state = load_state()

            if state is None:
                state = {
                    "tv_id": TV_ID,
                    "playlist": [],
                    "sync_ok": False,
                }

            body = json.dumps(
                state,
                ensure_ascii=False,
            ).encode("utf-8")

            self._send_bytes(
                body,
                200,
                "application/json; charset=utf-8",
            )
            return

        if path == "/health":
            body = json.dumps(
                self._health_payload(),
                ensure_ascii=False,
            ).encode("utf-8")

            self._send_bytes(
                body,
                200,
                "application/json; charset=utf-8",
            )
            return

        if path.startswith("/media/"):
            candidate = self._resolve_media_path(
                path
            )

            if candidate is None:
                self._send_bytes(
                    b"Caminho invalido",
                    400,
                )
                return

            self._serve_media_file(candidate)
            return

        if path == "/favicon.ico":
            self._send_bytes(b"", 204)
            return

        self._send_bytes(
            b"Not Found",
            404,
        )

    def do_GET(self) -> None:
        self._handle_get_or_head()

    def do_HEAD(self) -> None:
        self._handle_get_or_head()

    def do_POST(self) -> None:
        global playback_state

        path = urllib.parse.urlparse(
            self.path
        ).path

        if path != "/playback":
            self._send_bytes(
                b"Not Found",
                404,
            )
            return

        try:
            length = int(
                self.headers.get(
                    "Content-Length"
                )
                or 0
            )

            if (
                length <= 0
                or length > 64 * 1024
            ):
                raise ValueError(
                    "Payload inválido"
                )

            raw = self.rfile.read(length)

            data = json.loads(
                raw.decode("utf-8")
            )

            if not isinstance(data, dict):
                raise ValueError(
                    "JSON inválido"
                )

            clean = {
                "browser_session": str(
                    data.get("browser_session")
                    or ""
                )[:100],
                "content_id": (
                    data.get("content_id")
                ),
                "content_name": str(
                    data.get("content_name")
                    or ""
                )[:500],
                "content_type": str(
                    data.get("content_type")
                    or ""
                )[:50],
                "item_id": data.get("item_id"),
                "item_position": (
                    data.get("item_position")
                ),
                "current_index": (
                    data.get("current_index")
                ),
                "item_started_at": (
                    data.get("item_started_at")
                ),
                "render_state": str(
                    data.get("render_state")
                    or ""
                )[:50],
                "playback_position": (
                    data.get("playback_position")
                ),
                "playback_duration": (
                    data.get("playback_duration")
                ),
                "playback_state": str(
                    data.get("playback_state")
                    or "unknown"
                )[:50],
                "video_ready_state": (
                    data.get("video_ready_state")
                ),
                "video_network_state": (
                    data.get("video_network_state")
                ),
                "video_event": str(
                    data.get("video_event")
                    or ""
                )[:100],
                "video_error": str(
                    data.get("video_error")
                    or ""
                )[:500],
                "video_play_error": str(
                    data.get("video_play_error")
                    or ""
                )[:500],
                "reported_at": now_ts(),
            }

            with state_lock:
                playback_state = clean

            body = json.dumps({
                "status": "ok"
            }).encode("utf-8")

            self._send_bytes(
                body,
                200,
                "application/json; charset=utf-8",
            )

        except Exception as exc:
            body = json.dumps({
                "status": "error",
                "message": str(exc),
            }).encode("utf-8")

            self._send_bytes(
                body,
                400,
                "application/json; charset=utf-8",
            )

    def log_message(
        self,
        fmt: str,
        *args,
    ) -> None:
        # Evita milhares de linhas por causa de Range requests.
        path = urllib.parse.urlparse(
            self.path
        ).path

        if path.startswith("/media/"):
            return

        print(
            f"[LOCAL] {self.address_string()} "
            f"- {fmt % args}"
        )


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    global current_state
    global last_sync_at
    global last_sync_fingerprint

    print("=" * 72)
    print("Digital Signage - Player Local v3")
    print(f"TV: {TV_ID}")
    print(f"Versão: {PLAYER_VERSION}")
    print(f"Servidor: {SERVER_BASE}")
    print(
        f"Player local: "
        f"http://{LOCAL_HOST}:{LOCAL_PORT}"
    )
    print(f"Cache: {MEDIA_DIR}")
    print(
        "HW video decode: "
        + (
            "DESATIVADO"
            if DISABLE_HW_VIDEO_DECODE
            else "ATIVADO"
        )
    )
    print(
        f"Perfil Firefox: "
        f"{BROWSER_PROFILE_DIR}"
    )
    print(
        f"Log Firefox: {CHROMIUM_LOG}"
    )
    print("=" * 72)

    cleanup_stale_parts()

    current_state = load_state()

    if isinstance(current_state, dict):
        sync_at = current_state.get(
            "sync_at"
        )

        if isinstance(
            sync_at,
            (int, float),
        ):
            last_sync_at = float(sync_at)

        fingerprint = current_state.get(
            "sync_fingerprint"
        )

        if isinstance(
            fingerprint,
            str,
        ):
            last_sync_fingerprint = (
                fingerprint
            )

    # Primeiro fazemos o bind da porta local. Assim o Chromium nunca
    # é iniciado antes de 127.0.0.1:8765 estar pronto para responder.
    server = ThreadingHTTPServer(
        (LOCAL_HOST, LOCAL_PORT),
        PlayerHandler,
    )

    # Registrar apenas depois de termos inicializado o servidor local.
    try:
        stability_agent.register_start()
    except Exception as exc:
        print(f"[STABILITY] Falha ao persistir início: {exc}")

    worker = threading.Thread(
        target=background_worker,
        daemon=True,
    )

    worker.start()

    try:
        server.serve_forever()

    except KeyboardInterrupt:
        print("\nEncerrando player...")

    finally:
        stop_signage_chromium()
        server.server_close()


if __name__ == "__main__":
    main()
