#!/usr/bin/env bash
# Short, guarded path from a prepared checkout to a new production installation.
set -euo pipefail
umask 077

APP=/opt/digital-signage/app
DATA=/var/lib/digital-signage
ENV_FILE=/etc/digital-signage/server.env
BACKUPS=/var/backups/digital-signage
SITE=/etc/nginx/sites-available/digital-signage
ENABLED_SITE=/etc/nginx/sites-enabled/digital-signage

fail() { printf 'ERRO: %s\n' "$*" >&2; exit 1; }
info() { printf '%s\n' "$*"; }

[[ ${EUID} -eq 0 ]] || fail 'Execute como root: sudo bash deploy/linux/complete-install.sh ...'
[[ ${PWD} == "$APP" ]] || fail "Execute a partir de $APP"
[[ ! -L "$ENV_FILE" && ! -L "$BACKUPS" ]] || fail 'Configuração ou backups apontam para link simbólico.'

case "${1:-}" in
  --start-new)
    [[ $# -eq 1 ]] || fail 'Uso: complete-install.sh --start-new'
    [[ ! -L "$DATA/digital_signage.db" ]] || fail 'Caminho do SQLite é um link simbólico.'
    [[ ! -e "$DATA/digital_signage.db" ]] || fail 'Banco já existe. Use o procedimento de migração/restore; --start-new criaria uma instalação nova.'
    command -v nginx >/dev/null || fail 'Nginx não instalado. Instale os pacotes Ubuntu descritos no runbook.'
    if systemctl is-active --quiet digital-signage.service; then
      fail 'O serviço já está ativo; não reinicie uma instalação em operação com --start-new.'
    fi

    # The existing installer owns user, data directories, virtualenv and units.
    needs_install=0
    [[ -x /opt/digital-signage/venv/bin/python ]] || needs_install=1
    for unit in digital-signage.service digital-signage-backup.service \
      digital-signage-backup.timer digital-signage-retention.service \
      digital-signage-retention.timer; do
      [[ -f "/etc/systemd/system/$unit" ]] || needs_install=1
    done
    if [[ "$needs_install" == 1 ]]; then
      ./deploy/linux/install-server.sh
    fi

    install -d -o root -g root -m 0700 /etc/digital-signage
    if [[ ! -e "$ENV_FILE" ]]; then
      install -o root -g root -m 0600 deploy/linux/digital-signage.env.example "$ENV_FILE"
      info 'Configuração criada a partir do exemplo do projeto.'
    fi
    chown root:root "$ENV_FILE"
    chmod 0600 "$ENV_FILE"

    # Repair the accidental recursive chown from an interrupted installation.
    [[ -d "$BACKUPS" ]] || fail "Diretório de backup ausente: $BACKUPS"
    chown -R root:root "$BACKUPS"
    chmod 0700 "$BACKUPS"

    if ! grep -Eq '^DS_INITIAL_ADMIN_PASSWORD=.{16}' "$ENV_FILE"; then
      if grep -q '^DS_INITIAL_ADMIN_PASSWORD=' "$ENV_FILE"; then
        fail 'DS_INITIAL_ADMIN_PASSWORD já está definida, mas não tem 16 caracteres. Corrija server.env sem duplicar a chave.'
      fi
      [[ -t 1 ]] || fail 'É necessário um terminal para apresentar a senha inicial uma única vez.'
      bootstrap_password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
      printf '\nDS_INITIAL_ADMIN_USER=admin\nDS_INITIAL_ADMIN_PASSWORD=%s\n' "$bootstrap_password" >> "$ENV_FILE"
      printf '\nGuarde a senha inicial do admin em um cofre corporativo: %s\n\n' "$bootstrap_password" > /dev/tty
      unset bootstrap_password
    fi
    PYTHONPATH=deploy/linux python3 -c 'import server_ops; server_ops.validate_env()' \
      || fail 'server.env inválido; revise a mensagem acima sem substituir o arquivo existente.'

    # Preserve any infrastructure-managed virtual host already serving this DNS.
    if ! nginx -T 2>/dev/null | grep -E '^[[:space:]]*server_name[[:space:]]+signage\.amb\.local([[:space:];]|$)' >/dev/null; then
      if [[ ! -e "$SITE" ]]; then
        install -o root -g root -m 0644 deploy/linux/nginx-digital-signage.conf.example "$SITE"
      fi
      if [[ ! -e "$ENABLED_SITE" ]]; then
        ln -s "$SITE" "$ENABLED_SITE"
      fi
    fi
    nginx -t
    systemctl enable --now nginx
    systemctl reload nginx

    ./deploy/linux/preflight-server.sh --mode offline
    systemd-analyze verify /etc/systemd/system/digital-signage.service
    systemctl daemon-reload
    systemctl enable --now digital-signage.service
    ./deploy/linux/preflight-server.sh --mode online
    info 'Servidor iniciado. Faça o primeiro login e altere a senha do admin em Usuários.'
    info 'Depois execute: (cd /opt/digital-signage/app && sudo bash deploy/linux/complete-install.sh --finish)'
    ;;

  --finish)
    [[ $# -eq 1 ]] || fail 'Uso: complete-install.sh --finish'
    [[ -f "$ENV_FILE" && -f "$DATA/digital_signage.db" ]] || fail 'Configuração ou banco ausente.'
    systemctl is-active --quiet digital-signage.service || fail 'Serviço inativo; examine o journal antes de finalizar.'
    changed="$(sqlite3 "$DATA/digital_signage.db" \
      "SELECT CASE WHEN EXISTS(SELECT 1 FROM users WHERE role='admin' AND is_active=1 AND must_change_password=0) THEN 1 ELSE 0 END;")"
    [[ "$changed" == 1 ]] || fail 'Troque primeiro a senha do administrador no painel de Usuários.'

    if grep -q '^DS_INITIAL_ADMIN_PASSWORD=' "$ENV_FILE"; then
      python3 - "$ENV_FILE" <<'PY'
import os
from pathlib import Path
import sys

path = Path(sys.argv[1])
lines = path.read_text(encoding="utf-8").splitlines()
content = "\n".join(line for line in lines if not line.startswith((
    "DS_INITIAL_ADMIN_USER=", "DS_INITIAL_ADMIN_PASSWORD=",
))) + "\n"
temporary = path.with_name(path.name + ".sealed")
fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    handle.write(content)
os.replace(temporary, path)
PY
      chown root:root "$ENV_FILE"
      chmod 0600 "$ENV_FILE"
    fi
    if [[ -f "$DATA/first_admin_credentials.txt" ]]; then
      rm "$DATA/first_admin_credentials.txt"
    fi

    systemctl restart digital-signage.service
    ./deploy/linux/preflight-server.sh --mode online
    ./deploy/linux/backup-server.sh --keep 14
    systemctl enable --now digital-signage-backup.timer digital-signage-retention.timer
    systemctl list-timers --all digital-signage-backup.timer digital-signage-retention.timer
    info 'Credencial inicial removida, backup inicial criado e timers habilitados.'
    ;;

  *) fail 'Uso: complete-install.sh --start-new | --finish' ;;
esac
