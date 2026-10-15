from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

BASE_DIR = Path.home() / "DigitalSignagePlayer"
BACKUP_DIR = BASE_DIR / "backups"
PACKAGES_DIR = BASE_DIR / "packages"
LOG_DIR = BASE_DIR / "logs"
for d in (BACKUP_DIR, PACKAGES_DIR, LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)


def build_url(server_base: str, url: str) -> str:
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return server_base.rstrip("/") + "/" + url.lstrip("/")


def request_json(server_base: str, tv_id: str, token: str, command_id: str, payload: dict) -> None:
    if not command_id:
        return
    url = f"{server_base.rstrip('/')}/api/player/{urllib.parse.quote(tv_id)}/commands/{command_id}/ack"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "DigitalSignageUpdater/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    except Exception as exc:
        print(f"[UPDATER] ack falhou: {exc}")


def ack(args, status: str, progress: int, message: str, result: dict | None = None) -> None:
    request_json(args.server_base, args.tv_id, args.device_token, args.command_id, {
        "status": status,
        "progress": progress,
        "message": message,
        "result": result or {},
    })


def download(args, destination: Path) -> None:
    url = build_url(args.server_base, args.url)
    headers = {"User-Agent": "DigitalSignageUpdater/1.0"}
    if args.device_token:
        headers["Authorization"] = f"Bearer {args.device_token}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=120) as resp, destination.open("wb") as fh:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            fh.write(chunk)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def find_payload_root(extract_dir: Path) -> Path:
    if (extract_dir / "local_player.py").exists():
        return extract_dir
    for child in extract_dir.iterdir():
        if child.is_dir() and (child / "local_player.py").exists():
            return child
    return extract_dir


def copy_if_exists(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)


def restart_service() -> str:
    # O updater é filho do player. Reiniciar o serviço imediatamente mataria
    # o próprio updater antes do ACK final. Agenda o restart em uma unidade
    # transitória separada do systemd do usuário.
    unit = f"digital-signage-player-restart-{int(time.time())}"
    cmd = [
        "systemd-run", "--user",
        f"--unit={unit}",
        "--on-active=3s",
        "/usr/bin/systemctl", "--user", "restart", "digital-signage-player.service",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            return "Atualização concluída; reinício do player agendado em 3 segundos."
    except Exception:
        pass

    # Fallback: o player continuará rodando a versão anterior até o próximo
    # restart, mas os arquivos novos já estarão instalados.
    return "Arquivos atualizados; não foi possível agendar o restart automático."


def main() -> int:
    parser = argparse.ArgumentParser(description="Digital Signage Player Updater")
    parser.add_argument("--server-base", required=True)
    parser.add_argument("--tv-id", required=True)
    parser.add_argument("--device-token", default="")
    parser.add_argument("--command-id", default="")
    parser.add_argument("--url", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--version", default="")
    args = parser.parse_args()

    package = PACKAGES_DIR / f"release-{args.version or int(time.time())}.zip"
    extract_dir = PACKAGES_DIR / f"extract-{int(time.time())}"
    backup_dir = BACKUP_DIR / time.strftime("%Y%m%d-%H%M%S")

    try:
        ack(args, "running", 20, "Baixando pacote")
        download(args, package)
        ack(args, "running", 45, "Validando SHA-256")
        digest = sha256_file(package)
        if digest.lower() != args.sha256.lower():
            raise RuntimeError(f"SHA-256 inválido: {digest}")

        ack(args, "running", 60, "Extraindo pacote")
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        extract_dir.mkdir(parents=True)
        with zipfile.ZipFile(package, "r") as zf:
            zf.extractall(extract_dir)
        payload_root = find_payload_root(extract_dir)

        ack(args, "running", 75, "Criando backup local")
        backup_dir.mkdir(parents=True)
        for name in ["local_player.py", "player_local.html", "player_updater.py", "network_watchdog.py", "scripts", "services"]:
            src = BASE_DIR / name
            if src.exists():
                copy_if_exists(src, backup_dir / name)

        ack(args, "running", 88, "Instalando arquivos")
        for name in ["local_player.py", "player_local.html", "player_updater.py", "network_watchdog.py", "scripts", "services"]:
            copy_if_exists(payload_root / name, BASE_DIR / name)

        apply_helper = BASE_DIR / "scripts" / "apply_services.sh"
        if apply_helper.exists():
            try:
                subprocess.run(["sudo", "-n", str(apply_helper)], timeout=30, check=False)
            except Exception:
                pass
        try:
            subprocess.run(["systemctl", "--user", "daemon-reload"], timeout=10, check=False)
        except Exception:
            pass

        (BASE_DIR / "last_update.json").write_text(json.dumps({
            "version": args.version,
            "sha256": digest,
            "updated_at": time.time(),
            "backup_dir": str(backup_dir),
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        message = restart_service()
        ack(args, "done", 100, message, {"version": args.version, "backup_dir": str(backup_dir)})
        return 0
    except Exception as exc:
        try:
            if backup_dir.exists():
                for name in ["local_player.py", "player_local.html", "player_updater.py", "network_watchdog.py", "scripts", "services"]:
                    copy_if_exists(backup_dir / name, BASE_DIR / name)
        except Exception as rollback_exc:
            ack(args, "failed", 100, f"Falha: {exc}; rollback também falhou: {rollback_exc}")
            return 1
        ack(args, "failed", 100, f"Falha na atualização; rollback executado: {exc}")
        return 1
    finally:
        try:
            if extract_dir.exists():
                shutil.rmtree(extract_dir)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
