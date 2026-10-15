#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

STATE_DIR = Path('/var/lib/digital-signage')
STATE_FILE = STATE_DIR / 'network-watchdog.json'
ENV_FILE = Path(os.getenv('DS_PLAYER_ENV', '/etc/digital-signage/player.env'))
FAILS_BEFORE_RECOVERY = int(os.getenv('DS_NETWORK_RECOVERY_AFTER_FAILURES', '3'))
FAILS_BEFORE_REBOOT = int(os.getenv('DS_NETWORK_REBOOT_AFTER_FAILURES', '15'))
REBOOT_COOLDOWN = int(os.getenv('DS_NETWORK_REBOOT_COOLDOWN_SECONDS', '3600'))


def load_env_file(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        for raw in path.read_text(encoding='utf-8').splitlines():
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            result[key.strip()] = value.strip().strip('"').strip("'")
    except Exception:
        pass
    return result


def load_state() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    os.chmod(tmp, 0o644)
    tmp.replace(STATE_FILE)


def run(cmd: list[str], timeout: int = 15) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or r.stderr or '').strip()
    except Exception as exc:
        return 99, str(exc)


def default_interface() -> str:
    code, out = run(['ip', 'route', 'show', 'default'])
    if code == 0:
        parts = out.split()
        if 'dev' in parts:
            idx = parts.index('dev')
            if idx + 1 < len(parts):
                return parts[idx + 1]
    return ''


def default_gateway() -> str:
    code, out = run(['ip', 'route', 'show', 'default'])
    if code == 0:
        parts = out.split()
        if 'via' in parts:
            idx = parts.index('via')
            if idx + 1 < len(parts):
                return parts[idx + 1]
    return ''


def ping(host: str) -> bool:
    if not host:
        return False
    return run(['ping', '-c', '1', '-W', '2', host], timeout=5)[0] == 0


def http_probe(server_base: str) -> bool:
    if not server_base:
        return False
    try:
        req = urllib.request.Request(server_base.rstrip('/') + '/', method='HEAD')
        with urllib.request.urlopen(req, timeout=4) as resp:
            return 100 <= resp.status < 500
    except Exception:
        # The server may reject HEAD on '/', so TCP/DNS reachability still matters.
        try:
            u = urllib.parse.urlparse(server_base)
            host = u.hostname
            port = u.port or (443 if u.scheme == 'https' else 80)
            with socket.create_connection((host, port), timeout=4):
                return True
        except Exception:
            return False


def active_wifi_connection() -> str:
    if not shutil.which('nmcli'):
        return ''
    code, out = run(['nmcli', '-t', '-f', 'NAME,TYPE,DEVICE', 'connection', 'show', '--active'])
    if code != 0:
        return ''
    for line in out.splitlines():
        parts = line.split(':')
        if len(parts) >= 3 and parts[1] in {'wifi', '802-11-wireless'} and parts[2]:
            return parts[0]
    return ''


def recover_network(iface: str) -> str:
    actions: list[str] = []
    if shutil.which('nmcli'):
        run(['nmcli', 'networking', 'on'])
        conn = active_wifi_connection()
        if conn:
            run(['nmcli', 'connection', 'modify', conn, 'connection.autoconnect', 'yes'])
            run(['nmcli', 'connection', 'modify', conn, 'connection.autoconnect-retries', '0'])
            code, _ = run(['nmcli', 'connection', 'up', conn], timeout=25)
            actions.append(f'nmcli connection up {conn}: {code}')
        elif iface:
            code, _ = run(['nmcli', 'device', 'connect', iface], timeout=25)
            actions.append(f'nmcli device connect {iface}: {code}')

    if iface and shutil.which('wpa_cli'):
        code, _ = run(['wpa_cli', '-i', iface, 'reconnect'], timeout=10)
        actions.append(f'wpa_cli reconnect: {code}')

    for service in ('NetworkManager', 'dhcpcd', 'wpa_supplicant'):
        code, _ = run(['systemctl', 'is-active', '--quiet', service], timeout=5)
        if code == 0:
            rcode, _ = run(['systemctl', 'restart', service], timeout=30)
            actions.append(f'restart {service}: {rcode}')
            break
    return '; '.join(actions) or 'nenhum mecanismo de recuperação disponível'


def main() -> int:
    env = load_env_file(ENV_FILE)
    server_base = os.getenv('DS_SERVER_BASE') or env.get('DS_SERVER_BASE', '')
    iface = default_interface()
    gateway = default_gateway()

    gateway_ok = ping(gateway) if gateway else False
    server_ok = http_probe(server_base)
    online = gateway_ok or server_ok

    state = load_state()
    now = time.time()
    failures = 0 if online else int(state.get('failures') or 0) + 1
    last_action = ''

    if online:
        state.update({
            'status': 'online',
            'failures': 0,
            'interface': iface,
            'gateway': gateway,
            'gateway_ok': gateway_ok,
            'server_ok': server_ok,
            'server_base': server_base,
            'last_ok_at': now,
            'checked_at': now,
            'last_action': state.get('last_action', ''),
        })
        save_state(state)
        return 0

    if failures >= FAILS_BEFORE_RECOVERY:
        last_action = recover_network(iface)
        time.sleep(3)
        gateway_ok = ping(default_gateway()) if default_gateway() else False
        server_ok = http_probe(server_base)
        if gateway_ok or server_ok:
            state.update({
                'status': 'recovered',
                'failures': 0,
                'interface': default_interface(),
                'gateway': default_gateway(),
                'gateway_ok': gateway_ok,
                'server_ok': server_ok,
                'server_base': server_base,
                'last_ok_at': time.time(),
                'checked_at': time.time(),
                'last_action': last_action,
            })
            save_state(state)
            return 0

    last_reboot = float(state.get('last_auto_reboot_at') or 0)
    if FAILS_BEFORE_REBOOT > 0 and failures >= FAILS_BEFORE_REBOOT and now - last_reboot >= REBOOT_COOLDOWN:
        state.update({
            'status': 'rebooting',
            'failures': failures,
            'checked_at': now,
            'last_action': (last_action + '; ' if last_action else '') + 'reboot automático por indisponibilidade prolongada',
            'last_auto_reboot_at': now,
        })
        save_state(state)
        subprocess.Popen(['systemctl', 'reboot'])
        return 0

    state.update({
        'status': 'offline',
        'failures': failures,
        'interface': iface,
        'gateway': gateway,
        'gateway_ok': gateway_ok,
        'server_ok': server_ok,
        'server_base': server_base,
        'checked_at': now,
        'last_action': last_action,
    })
    save_state(state)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
