#!/usr/bin/env bash
set -euo pipefail

SERVER=""
TV_ID=""
CODE=""
WIFI_SSID=""
WIFI_PASS=""
DISABLE_HW="1"
TARGET_PLAYER_USER="${DS_PLAYER_USER:-}"

usage() {
  cat <<'USAGE'
Digital Signage - Instalador/Provisionador

Uso:
  install_player.sh --server http://servidor:8000 --tv-id TV01 --code XXXX-YYYY

Opções adicionais:
  --wifi-ssid NOME       Configura/reforça Wi-Fi via NetworkManager, quando disponível.
  --wifi-pass SENHA      Evite usar em histórico; se omitida, o script pede de forma oculta.
  --enable-hw-video      Habilita decodificação de vídeo por hardware.
  --player-user USUARIO  Instala o player para outro usuário local (ex.: arvedi).
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --server) SERVER="${2:-}"; shift 2 ;;
    --tv-id) TV_ID="${2:-}"; shift 2 ;;
    --code) CODE="${2:-}"; shift 2 ;;
    --wifi-ssid) WIFI_SSID="${2:-}"; shift 2 ;;
    --wifi-pass) WIFI_PASS="${2:-}"; shift 2 ;;
    --enable-hw-video) DISABLE_HW="0"; shift ;;
    --player-user) TARGET_PLAYER_USER="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Opção desconhecida: $1" >&2; usage; exit 2 ;;
  esac
done

SERVER="${SERVER%/}"
TV_ID="$(printf '%s' "$TV_ID" | tr '[:lower:]' '[:upper:]')"

[[ -n "$SERVER" && -n "$TV_ID" && -n "$CODE" ]] || { usage; exit 2; }
command -v python3 >/dev/null || { echo "python3 é obrigatório." >&2; exit 1; }
command -v curl >/dev/null || { echo "curl é obrigatório." >&2; exit 1; }

PLAYER_USER="${TARGET_PLAYER_USER:-${SUDO_USER:-$(id -un)}}"
if ! getent passwd "$PLAYER_USER" >/dev/null 2>&1; then
  echo "Usuário local não existe: $PLAYER_USER" >&2
  exit 1
fi
PLAYER_GROUP="$(id -gn "$PLAYER_USER" 2>/dev/null || echo "$PLAYER_USER")"
USER_HOME="$(getent passwd "$PLAYER_USER" | cut -d: -f6)"
[[ -n "$USER_HOME" ]] || { echo "Home não encontrado para $PLAYER_USER" >&2; exit 1; }
PLAYER_HOME="$USER_HOME/DigitalSignagePlayer"

as_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

log(){ printf '\n[Digital Signage] %s\n' "$*"; }

log "Provisionando $TV_ID em $SERVER para o usuário $PLAYER_USER"
PROVISION_JSON="$TMP/provision.json"
HTTP_CODE=$(curl -sS -o "$PROVISION_JSON" -w '%{http_code}' \
  -X POST "$SERVER/api/player/provision" \
  -H 'Content-Type: application/json' \
  --data "$(python3 - "$TV_ID" "$CODE" <<'PY'
import json,sys
print(json.dumps({'tv_id':sys.argv[1],'code':sys.argv[2]}))
PY
)")

if [[ "$HTTP_CODE" != "200" ]]; then
  echo "Provisionamento falhou (HTTP $HTTP_CODE):" >&2
  cat "$PROVISION_JSON" >&2 || true
  exit 1
fi

readarray -t VALUES < <(python3 - "$PROVISION_JSON" <<'PY'
import json,sys
p=json.load(open(sys.argv[1],encoding='utf-8'))
r=p.get('release') or {}
for v in [p.get('device_token',''),r.get('version',''),r.get('url',''),r.get('sha256','')]:
    print(v)
PY
)
TOKEN="${VALUES[0]:-}"
VERSION="${VALUES[1]:-}"
RELEASE_URL="${VALUES[2]:-}"
SHA256="${VALUES[3]:-}"

[[ -n "$TOKEN" ]] || { echo "Servidor não retornou token." >&2; exit 1; }
[[ -n "$VERSION" && -n "$RELEASE_URL" && -n "$SHA256" ]] || {
  echo "O provisionamento foi aceito, mas não há release estável registrada no servidor." >&2
  echo "Cadastre/promova uma release no painel e gere um novo código de provisionamento." >&2
  exit 1
}

case "$RELEASE_URL" in
  http://*|https://*) DOWNLOAD_URL="$RELEASE_URL" ;;
  /*) DOWNLOAD_URL="$SERVER$RELEASE_URL" ;;
  *) DOWNLOAD_URL="$SERVER/$RELEASE_URL" ;;
esac

log "Baixando Player $VERSION"
PACKAGE="$TMP/player.zip"
curl -fL --retry 3 --retry-delay 2 \
  -H "Authorization: Bearer $TOKEN" \
  "$DOWNLOAD_URL" -o "$PACKAGE"

ACTUAL_SHA=$(python3 - "$PACKAGE" <<'PY'
import hashlib,sys
h=hashlib.sha256()
with open(sys.argv[1],'rb') as f:
    for c in iter(lambda:f.read(1024*1024),b''): h.update(c)
print(h.hexdigest())
PY
)
[[ "${ACTUAL_SHA,,}" == "${SHA256,,}" ]] || { echo "SHA-256 inválido." >&2; exit 1; }

EXTRACT="$TMP/extract"
mkdir -p "$EXTRACT"
python3 -m zipfile -e "$PACKAGE" "$EXTRACT"
PAYLOAD=$(python3 - "$EXTRACT" <<'PY'
from pathlib import Path
import sys
root=Path(sys.argv[1])
if (root/'local_player.py').exists(): print(root); raise SystemExit
for p in root.rglob('local_player.py'):
    print(p.parent); raise SystemExit
raise SystemExit(1)
PY
)

as_root mkdir -p "$PLAYER_HOME"
if [[ -f "$PLAYER_HOME/local_player.py" ]]; then
  BACKUP="$USER_HOME/DigitalSignagePlayer_BKP_$(date +%Y%m%d_%H%M%S)"
  as_root mkdir -p "$BACKUP"
  for n in local_player.py player_local.html player_updater.py network_watchdog.py player.env services scripts; do
    [[ -e "$PLAYER_HOME/$n" ]] && as_root cp -a "$PLAYER_HOME/$n" "$BACKUP/"
  done
  as_root chown -R "$PLAYER_USER:$PLAYER_GROUP" "$BACKUP" || true
  log "Backup de código criado em $BACKUP"
fi
as_root cp -a "$PAYLOAD/." "$PLAYER_HOME/"
as_root chown -R "$PLAYER_USER:$PLAYER_GROUP" "$PLAYER_HOME"
as_root chmod +x "$PLAYER_HOME/player_updater.py" "$PLAYER_HOME/network_watchdog.py" 2>/dev/null || true
as_root find "$PLAYER_HOME/scripts" -type f -name '*.sh' -exec chmod +x {} \; 2>/dev/null || true

ENV_TMP="$TMP/player.env"
cat > "$ENV_TMP" <<ENV
DS_SERVER_BASE=$SERVER
DS_TV_ID=$TV_ID
DS_DEVICE_TOKEN=$TOKEN
DS_LOCAL_HOST=127.0.0.1
DS_LOCAL_PORT=8765
DS_DISABLE_HW_VIDEO_DECODE=$DISABLE_HW
DS_NETWORK_RECOVERY_AFTER_FAILURES=3
DS_NETWORK_REBOOT_AFTER_FAILURES=15
DS_NETWORK_REBOOT_COOLDOWN_SECONDS=3600
ENV
as_root install -o "$PLAYER_USER" -g "$PLAYER_GROUP" -m 0600 "$ENV_TMP" "$PLAYER_HOME/player.env"

log "Configurando reconexão automática de rede"
if command -v nmcli >/dev/null 2>&1; then
  as_root nmcli networking on >/dev/null 2>&1 || true
  if [[ -n "$WIFI_SSID" ]]; then
    if [[ -z "$WIFI_PASS" ]]; then
      read -r -s -p "Senha do Wi-Fi $WIFI_SSID: " WIFI_PASS; echo
    fi
    as_root nmcli device wifi connect "$WIFI_SSID" password "$WIFI_PASS" || true
  fi
  ACTIVE_CONN=$(nmcli -t -f NAME,TYPE connection show --active 2>/dev/null | awk -F: '$2=="802-11-wireless" || $2=="wifi" {print $1; exit}')
  if [[ -n "$ACTIVE_CONN" ]]; then
    as_root nmcli connection modify "$ACTIVE_CONN" connection.autoconnect yes connection.autoconnect-retries 0 || true
  fi
elif command -v wpa_cli >/dev/null 2>&1; then
  IFACE=$(ip route show default 2>/dev/null | awk '/default/ {for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}' | head -1)
  [[ -n "$IFACE" ]] && as_root wpa_cli -i "$IFACE" reconnect >/dev/null 2>&1 || true
fi

log "Instalando serviços e watchdog"
# Permissões estritamente para manutenção automática do player.
SUDOERS_TMP="$TMP/digital-signage-sudoers"
cat > "$SUDOERS_TMP" <<SUDO
$PLAYER_USER ALL=(root) NOPASSWD: /sbin/reboot, /usr/sbin/reboot, /usr/bin/systemctl reboot, $PLAYER_HOME/scripts/apply_services.sh
SUDO
as_root install -m 0440 "$SUDOERS_TMP" /etc/sudoers.d/digital-signage-player
if [[ "$(id -u)" -eq 0 ]]; then
  DS_PLAYER_USER="$PLAYER_USER" DS_PLAYER_HOME="$PLAYER_HOME" "$PLAYER_HOME/scripts/apply_services.sh"
else
  sudo DS_PLAYER_USER="$PLAYER_USER" DS_PLAYER_HOME="$PLAYER_HOME" "$PLAYER_HOME/scripts/apply_services.sh"
fi

sleep 5
log "Validação local"
if curl -fsS http://127.0.0.1:8765/health >/dev/null; then
  curl -s http://127.0.0.1:8765/health | python3 -m json.tool || true
else
  echo "Player instalado, mas /health ainda não respondeu. Consulte:" >&2
  echo "journalctl --user -u digital-signage-player.service -n 100 --no-pager" >&2
  exit 1
fi

log "Instalação concluída. Token foi gravado automaticamente; o código de provisionamento já foi invalidado pelo servidor."
