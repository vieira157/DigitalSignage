#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLAYER_HOME="${DS_PLAYER_HOME:-$(dirname "$SCRIPT_DIR")}"
PLAYER_USER="${DS_PLAYER_USER:-${SUDO_USER:-$(id -un)}}"
PLAYER_GROUP="$(id -gn "$PLAYER_USER" 2>/dev/null || echo "$PLAYER_USER")"
USER_HOME="$(getent passwd "$PLAYER_USER" | cut -d: -f6)"
[[ -n "$USER_HOME" ]] || USER_HOME="$HOME"

mkdir -p /opt/digital-signage-player /etc/digital-signage /var/lib/digital-signage
install -m 0755 "$PLAYER_HOME/network_watchdog.py" /opt/digital-signage-player/network_watchdog.py
install -m 0644 "$PLAYER_HOME/services/digital-signage-network-watchdog.service" /etc/systemd/system/digital-signage-network-watchdog.service
install -m 0644 "$PLAYER_HOME/services/digital-signage-network-watchdog.timer" /etc/systemd/system/digital-signage-network-watchdog.timer
install -m 0600 "$PLAYER_HOME/player.env" /etc/digital-signage/player.env
mkdir -p "$USER_HOME/.config/systemd/user"
install -o "$PLAYER_USER" -g "$PLAYER_GROUP" -m 0644 "$PLAYER_HOME/services/digital-signage-player.service" "$USER_HOME/.config/systemd/user/digital-signage-player.service"
systemctl daemon-reload
systemctl enable --now digital-signage-network-watchdog.timer
loginctl enable-linger "$PLAYER_USER" >/dev/null 2>&1 || true
PLAYER_UID="$(id -u "$PLAYER_USER")"
USER_RUNTIME="/run/user/$PLAYER_UID"
# Garante que o user manager exista mesmo durante bootstrap/SSH sem sessão gráfica.
systemctl start "user@${PLAYER_UID}.service" >/dev/null 2>&1 || true
for _ in 1 2 3 4 5; do
  [[ -S "$USER_RUNTIME/bus" ]] && break
  sleep 1
done
user_systemctl() {
  runuser -u "$PLAYER_USER" -- env \
    XDG_RUNTIME_DIR="$USER_RUNTIME" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=$USER_RUNTIME/bus" \
    systemctl --user "$@"
}
user_systemctl daemon-reload
user_systemctl enable digital-signage-player.service
user_systemctl restart digital-signage-player.service
