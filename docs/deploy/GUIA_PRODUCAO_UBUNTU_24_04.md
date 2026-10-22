# Guia completo de implantação em produção — Ubuntu Server 24.04 LTS

**Aplicação:** Digital Signage  
**Servidor:** Ubuntu Server 24.04 LTS  
**Endereço:** `http://signage.amb.local`  
**Proxy:** Nginx  
**Data da auditoria:** 20/10/2026  
**Base analisada:** branch `redesign-workspace-v4`, commit local `b195a4f`

Este documento descreve o procedimento suportado pelos arquivos presentes no
repositório. Execute primeiro em uma VM de homologação equivalente ao servidor
final. Os valores `REPO_HTTPS_URL`, `COMMIT_SHA`, `ADMIN_CIDR` e `PLAYER_CIDR`
devem ser substituídos pelos valores aprovados da empresa.

## Caminho curto para instalação nova

Após colocar o checkout aprovado em `/opt/digital-signage/app` e instalar os
pacotes Ubuntu da seção 2, a preparação do servidor exige apenas dois comandos.
O primeiro é para **banco novo**, sem migração. Ele reaproveita o instalador,
configura o ambiente se ausente, corrige a propriedade de backups, configura o
virtual host se ainda não existir, executa preflight e inicia o serviço. Não
sobrescreve `server.env` ou um virtual host já presente.

```bash
(cd /opt/digital-signage/app && sudo bash deploy/linux/complete-install.sh --start-new)
```

Guarde a senha inicial apresentada no terminal. Acesse
`http://signage.amb.local`, entre como `admin` e altere sua senha na página
**Usuários**. A segunda chamada exige que pelo menos um administrador ativo
tenha concluído a troca, remove a senha inicial de `server.env`, reinicia,
verifica a saúde, cria o primeiro backup e habilita os timers:

```bash
(cd /opt/digital-signage/app && sudo bash deploy/linux/complete-install.sh --finish)
```

Se o SQLite antigo será migrado, não use `--start-new`: conclua o procedimento
de migração da seção 4.4 antes de iniciar. O firewall permanece sob a política
de rede da empresa, com as sub-redes reais da seção 7. O fluxo detalhado abaixo
continua disponível para diagnóstico e homologação.

## 1. Resultado da análise do projeto

### 1.1 Componentes e estrutura

| Caminho no repositório | Função |
|---|---|
| `app.py` | Aplicação FastAPI, rotas HTTP, autenticação, persistência e migrações incrementais do SQLite |
| `stability_monitor.py` | Monitor interno iniciado junto com a aplicação |
| `templates/` | 18 templates Jinja2 do painel, login, player web e páginas administrativas |
| `static/` | Recursos estáticos locais, incluindo a identidade visual |
| `player/` | Player Raspberry Pi, página local, atualizador, watchdog, manifesto, serviços e documentação |
| `bootstrap/install_player.sh` | Instalador atual preferencial do Player Raspberry Pi |
| `deploy/linux/` | Instalador, preflight, serviço, Nginx, backup, restauração, atualização e retenção |
| `docs/arquitetura/` | Inventário funcional, arquitetura do Player, vocabulário e riscos conhecidos |
| `docs/deploy/` | Runbooks, hardening e registro de validação Linux |
| `tests/` | Testes funcionais e dos contratos de deploy, recuperação, segurança, retenção e Player |

Não use os exemplos antigos diretamente em `deploy/`. O fluxo atual e testado
está em `deploy/linux/`.

### 1.2 Tecnologia e dependências confirmadas

- Python 3.11 ou superior; o Ubuntu 24.04 fornece Python 3.12.
- FastAPI `0.141.1`, Uvicorn `0.54.0`, Jinja2 `3.1.6`,
  `python-multipart 0.0.32` e `tzdata 2026.4`.
- Dependências transitivas fixadas em `requirements-constraints.txt`.
- SQLite via o módulo `sqlite3` da biblioteca padrão. Não existe PostgreSQL,
  MySQL, Redis ou ORM externo neste projeto.
- Nginx como proxy reverso; Uvicorn escuta somente em `127.0.0.1:8000`.
- `ffmpeg`/`ffprobe` para validar os vídeos enviados.
- Git, curl e sqlite3 para instalação, saúde e operações.
- systemd e journal para serviço, logs, backup e retenção.

O servidor deve usar **exatamente um worker Uvicorn**. O SQLite, as rotinas de
inicialização e o monitor interno não suportam multiplicar o processo sem uma
mudança arquitetural.

### 1.3 Configurações e serviços existentes

Arquivos operacionais atuais:

- `deploy/linux/digital-signage.env.example`: configuração do servidor.
- `deploy/linux/digital-signage.service`: serviço principal.
- `deploy/linux/nginx-digital-signage.conf.example`: proxy HTTP de referência.
- `deploy/linux/digital-signage-backup.service` e `.timer`: backup diário.
- `deploy/linux/digital-signage-retention.service` e `.timer`: retenção diária.
- `deploy/linux/install-server.sh`: prepara usuário, diretórios, virtualenv e
  unidades systemd. Não inicia a aplicação nem instala a configuração Nginx.
- `deploy/linux/preflight-server.sh`: valida ambiente offline ou online.
- `deploy/linux/backup-server.sh`: gera backup consistente e retém 14 cópias por
  padrão.
- `deploy/linux/restore-server.sh`: valida e restaura uma cópia com rollback.
- `deploy/linux/update-server.sh`: atualiza por branch e SHA completo, testa,
  troca código/virtualenv, valida saúde e reverte em falha.
- `deploy/linux/server_ops.py`: implementação comum das operações.
- `deploy/linux/retention_server.py`: classifica e, quando autorizado, remove
  temporários e conteúdo arquivado sem referência.

Todas as operações destrutivas de servidor usam o lock
`/run/lock/digital-signage-operations.lock`.

### 1.4 Componentes obrigatórios em execução

1. `digital-signage.service` ativo.
2. Nginx ativo em TCP 80.
3. SQLite íntegro e armazenado em disco local.
4. Diretórios `media`, `releases` e `upload-tmp` graváveis pelo usuário da
   aplicação.
5. DNS interno resolvendo `signage.amb.local` para o servidor.
6. Players com `DS_SERVER_BASE=http://signage.amb.local` e token válido.
7. Backup local validado e cópia externa/off-site.

### 1.5 Inconsistências encontradas

1. **A exigência de uma única raiz conflita com os scripts atuais.** O projeto
   fixa e valida código em `/opt/digital-signage`, estado em
   `/var/lib/digital-signage`, configuração em `/etc/digital-signage` e backups
   em `/var/backups/digital-signage`. `server_ops.py`, o preflight, o systemd e os
   testes rejeitam outros caminhos. Symlinks não são uma solução suportada. Este
   guia usa o layout real. Colocar tudo sob `/opt/digital-signage` requer uma
   alteração própria dos scripts, unidades, sandbox e testes antes da produção.
2. O transporte suportado hoje é HTTP e `DS_COOKIE_SECURE=0`. Senhas de login e
   tokens de Player trafegam sem TLS. Para um ambiente corporativo definitivo,
   planeje certificado da CA interna, porta 443 e revalidação dos scripts para
   aceitar `DS_COOKIE_SECURE=1`.
3. `player/services/player.env.example` ainda exemplifica acesso direto à porta
   8000. Em produção, use `http://signage.amb.local`, sem `:8000`.
4. `player/manifest.json` declara `3.2.1-stable`, enquanto
   `player/local_player.py` declara `3.2.2-stability-preview`. Não publique esse
   diretório como release até reconciliar e testar a versão.
5. `player_updater.py` não inclui `stability_agent.py` na lista completa de
   cópia/rollback e não confirma saúde após reiniciar. Atualizações remotas dos
   Players exigem piloto e validação presencial antes de expansão.
6. `docs/arquitetura/PLAYER_ATUAL.md` afirma que o heartbeat não valida token,
   mas o código atual chama `require_device_auth()` nessa rota e existe teste
   específico para isso. A documentação de arquitetura está desatualizada nesse
   ponto; o guia segue o código atual. A segmentação da rede continua indicada.
7. O backup nativo é local e o serviço para brevemente a aplicação para obter
   consistência entre banco e arquivos. Isso produz uma pequena janela de
   indisponibilidade diária. Alta disponibilidade sem interrupção exigiria uma
   mudança arquitetural.
8. Não há ferramenta externa de migração. A criação e evolução do schema
   ocorrem durante a importação/inicialização de `app.py`; por isso o backup
   anterior à atualização é obrigatório.
9. A cópia de trabalho contém artefatos operacionais ignorados pelo Git, como
   bancos, mídias, credenciais, virtualenv e pacotes. Eles não podem ser copiados
   para o checkout Linux. Implante somente um commit Git limpo e revisado.
10. A validação registrada foi feita em Windows; ShellCheck, `systemd-analyze`,
    `nginx -t` e uma execução real dos scripts precisam ser concluídos na VM
    Ubuntu de homologação.

### 1.6 Verificação executada durante esta auditoria

A suíte completa local executou 123 casos: 121 passaram, um falhou e um teve
erro. O erro de rename do snapshot foi específico do filesystem Windows e o
mesmo caso passou quando repetido isoladamente. A falha persistente é
`test_generated_credentials_exclusive`: o teste chama `ensure_initial_admin()`
com um SQLite em memória depois que o módulo já classificou outro banco como
existente; o código então recusa corretamente criar administrador em banco
existente vazio. A premissa do teste e o novo controle `DB_WAS_NEW` precisam ser
reconciliados antes do aceite. Avisos também registram a futura migração de
`@app.on_event("startup")` para o mecanismo lifespan do FastAPI.

Essa regressão de teste, somada às verificações Linux ainda pendentes, impede
classificar o commit auditado como homologado. Os comandos deste documento são
o runbook de homologação e produção; a promoção deve ocorrer somente após a
suíte aprovada no Ubuntu.

## 2. Pré-requisitos do servidor

### 2.1 Capacidade e sistema

- Ubuntu Server 24.04 LTS atualizado, relógio sincronizado e hostname definido.
- CPU x86-64/ARM64 compatível com Python 3.12; dimensionar conforme quantidade
  de uploads, Players e transcodificação/validação simultânea.
- Memória mínima operacional sugerida: 2 GiB. Meça o ambiente real antes de
  fixar limites systemd.
- Disco local confiável. O preflight exige apenas 2 GiB livres, mas produção
  deve reservar: mídias ativas + releases + temporários da aplicação +
  buffering do Nginx + pelo menos 14 backups + 30% de margem.
- Não colocar o arquivo SQLite em NFS, SMB ou outro compartilhamento de rede.
- IP estático ou reserva DHCP e entrada DNS `signage.amb.local`.

### 2.2 Pacotes Ubuntu

Obrigatórios para o fluxo deste repositório:

```bash
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3 python3-venv git curl sqlite3 ffmpeg nginx ufw ca-certificates
```

O servidor não precisa de Firefox nem dos serviços do Player Raspberry Pi.

### 2.3 Portas e rede

| Origem | Destino | Porta | Uso |
|---|---|---:|---|
| Administradores | servidor | TCP 80 | Painel e upload via Nginx |
| Raspberry Pi | servidor | TCP 80 | provisionamento, playlist, mídia, heartbeat, comandos e releases |
| Administração | servidor | TCP 22 | SSH, restrito à rede administrativa |
| Nginx local | Uvicorn local | TCP 8000 | loopback; nunca liberar no firewall |
| Servidor e Players | DNS/NTP internos | conforme infraestrutura | resolução e relógio correto |

Não há conexão iniciada pelo servidor para o Raspberry Pi. Os Players consultam
o servidor periodicamente.

## 3. Estrutura final suportada

```text
/opt/digital-signage/
├── app/                         # checkout Git imutável em execução
└── venv/                        # ambiente virtual Python

/var/lib/digital-signage/
├── digital_signage.db           # SQLite
├── media/                        # mídias publicadas
├── releases/                     # pacotes de atualização de Player
├── upload-tmp/                   # uploads incompletos/temporários
└── first_admin_credentials.txt   # primeiro acesso; remover após uso

/etc/digital-signage/
└── server.env                    # configuração e segredos, root:root 0600

/var/backups/digital-signage/
└── backup-AAAA.../               # snapshots validados e manifestos

/var/log/nginx/
├── digital-signage-access.log
└── digital-signage-error.log
```

Os logs da aplicação ficam no journal, consultados por
`journalctl -u digital-signage`. Essa separação segue o layout codificado e
mantém código, estado, segredo, backup e logs com permissões adequadas.

## 4. Instalação passo a passo

### 4.1 Preparar DNS, variáveis e sistema

Crie antes a entrada DNS A/AAAA para o IP do servidor. Entre como root:

```bash
sudo -i

export REPO_HTTPS_URL='https://SEU-SERVIDOR-GIT/GRUPO/DigitalSignage.git'
export COMMIT_SHA='COLE_AQUI_O_SHA_COMPLETO_DE_40_CARACTERES'

test "${#COMMIT_SHA}" -eq 40
getent hosts signage.amb.local

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3 python3-venv git curl sqlite3 ffmpeg nginx ufw ca-certificates

timedatectl set-timezone America/Sao_Paulo
timedatectl status
```

Não use o URL público de exemplo se o repositório corporativo estiver em outro
servidor. O SHA deve identificar um commit aprovado, limpo e reproduzível.

### 4.2 Obter o código aprovado

```bash
install -d -o root -g root -m 0755 /opt/digital-signage
git clone "$REPO_HTTPS_URL" /opt/digital-signage/app
git -C /opt/digital-signage/app fetch --prune origin
git -C /opt/digital-signage/app checkout --detach "$COMMIT_SHA"
test "$(git -C /opt/digital-signage/app rev-parse HEAD)" = "$COMMIT_SHA"
test -z "$(git -C /opt/digital-signage/app status --porcelain)"
chown -R root:root /opt/digital-signage/app
chmod -R go-w /opt/digital-signage/app
```

### 4.3 Criar usuário, diretórios, virtualenv e unidades

O instalador existente realiza essas etapas e instala as dependências fixadas:

```bash
cd /opt/digital-signage/app
python3 deploy/linux/server_ops.py install
```

O wrapper equivalente é:

```bash
cd /opt/digital-signage/app
bash deploy/linux/install-server.sh
```

Use apenas um dos dois comandos. Confira:

```bash
id digital-signage
/opt/digital-signage/venv/bin/python --version
/opt/digital-signage/venv/bin/python -m pip check
systemctl cat digital-signage.service
```

### 4.4 Configurar a aplicação

```bash
install -d -o root -g root -m 0700 /etc/digital-signage
install -o root -g root -m 0600 \
  /opt/digital-signage/app/deploy/linux/digital-signage.env.example \
  /etc/digital-signage/server.env
sudoedit /etc/digital-signage/server.env
```

Para uma instalação nova, descomente e preencha temporariamente:

```text
DS_INITIAL_ADMIN_USER=admin
DS_INITIAL_ADMIN_PASSWORD=UMA_SENHA_CORPORATIVA_UNICA_COM_16_OU_MAIS_CARACTERES
```

Mantenha os caminhos canônicos e estes valores:

```text
DS_DB_FILE=/var/lib/digital-signage/digital_signage.db
DS_MEDIA_DIR=/var/lib/digital-signage/media
DS_RELEASE_DIR=/var/lib/digital-signage/releases
DS_UPLOAD_TEMP_DIR=/var/lib/digital-signage/upload-tmp
DS_INITIAL_CREDENTIALS_FILE=/var/lib/digital-signage/first_admin_credentials.txt
DS_SESSION_TTL_SECONDS=43200
DS_PASSWORD_ITERATIONS=210000
DS_COOKIE_SECURE=0
DS_REQUIRE_DEVICE_TOKEN=1
DS_ALLOW_LEGACY_PUBLIC_MEDIA=0
DS_MAX_IMAGE_MB=25
DS_MAX_VIDEO_MB=1024
DS_MAX_RELEASE_MB=512
DS_MAX_FILES_PER_UPLOAD=20
DS_MAX_REQUEST_MB=1536
DS_RETENTION_TEMP_DAYS=7
DS_RETENTION_ARCHIVED_DAYS=90
DS_CLEANUP_DRY_RUN=1
DS_CLEANUP_DELETE_ARCHIVED=0
TZ=America/Sao_Paulo
PYTHONUNBUFFERED=1
PYTHONDONTWRITEBYTECODE=1
```

Proteja os arquivos:

```bash
chown root:root /etc/digital-signage/server.env
chmod 0600 /etc/digital-signage/server.env
chown -R digital-signage:digital-signage /var/lib/digital-signage
chmod 0700 /var/lib/digital-signage
find /var/lib/digital-signage -type d -exec chmod 0700 {} +
```

Se houver migração do servidor antigo, não inicie a aplicação ainda. Copie o
SQLite, `media/` e `releases/` para `/var/lib/digital-signage`, preserve os
nomes, aplique o `chown` acima e valide:

```bash
sqlite3 /var/lib/digital-signage/digital_signage.db 'PRAGMA integrity_check;'
```

O resultado obrigatório é `ok`.

### 4.5 Instalar o Nginx

Crie `/etc/nginx/sites-available/digital-signage` com o conteúdo da seção 6.
Depois execute:

```bash
ln -sfn /etc/nginx/sites-available/digital-signage \
  /etc/nginx/sites-enabled/digital-signage
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable --now nginx
systemctl reload nginx
```

### 4.6 Preflight, inicialização e primeiro acesso

```bash
cd /opt/digital-signage/app
bash deploy/linux/preflight-server.sh --mode offline
systemd-analyze verify /etc/systemd/system/digital-signage.service
systemctl daemon-reload
systemctl enable --now digital-signage.service
bash deploy/linux/preflight-server.sh --mode online
curl -fsS http://127.0.0.1:8000/health
curl -fsS -H 'Host: signage.amb.local' http://127.0.0.1/health
curl -fsS http://signage.amb.local/health
```

Acesse `http://signage.amb.local`, autentique-se, troque a senha se aplicável e
então remova a senha inicial do ambiente:

```bash
sudoedit /etc/digital-signage/server.env
systemctl restart digital-signage.service
rm -f /var/lib/digital-signage/first_admin_credentials.txt
```

Remova as linhas `DS_INITIAL_ADMIN_USER` e `DS_INITIAL_ADMIN_PASSWORD`, execute
o restart e confirme o login novamente.

## 5. Configuração systemd

Salvar em `/etc/systemd/system/digital-signage.service`. O instalador já copia
este arquivo; o conteúdo auditado é:

```ini
[Unit]
Description=Digital Signage Server
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=120
StartLimitBurst=5

[Service]
Type=simple
User=digital-signage
Group=digital-signage
WorkingDirectory=/opt/digital-signage/app
EnvironmentFile=/etc/digital-signage/server.env
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=HOME=/var/lib/digital-signage
ExecStart=/opt/digital-signage/venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=on-failure
RestartSec=5
TimeoutStopSec=60
KillSignal=SIGTERM
KillMode=control-group
UMask=0077
StandardOutput=journal
StandardError=journal
SyslogIdentifier=digital-signage
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/digital-signage
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
CapabilityBoundingSet=

[Install]
WantedBy=multi-user.target
```

Aplicar e verificar:

```bash
systemctl daemon-reload
systemctl enable --now digital-signage.service
systemctl status digital-signage.service --no-pager
journalctl -u digital-signage.service -n 100 --no-pager
```

Em caso de cinco falhas rápidas, corrija a causa e execute:

```bash
systemctl reset-failed digital-signage.service
systemctl start digital-signage.service
```

## 6. Configuração Nginx

Salvar em `/etc/nginx/sites-available/digital-signage`:

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name signage.amb.local;

    server_tokens off;
    client_max_body_size 2g;
    client_body_timeout 300s;

    access_log /var/log/nginx/digital-signage-access.log;
    error_log  /var/log/nginx/digital-signage-error.log warn;

    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header Referrer-Policy "same-origin" always;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection "";

        proxy_request_buffering on;
        proxy_connect_timeout 10s;
        proxy_send_timeout 600s;
        proxy_read_timeout 600s;
    }
}
```

O Nginx aceita até 2 GiB por requisição multipart; a aplicação é mais restrita:
25 MiB por imagem, 1.024 MiB por vídeo, 512 MiB por release, 20 arquivos e
1.536 MiB por requisição. O buffering exige espaço livre em disco também nos
diretórios temporários do Nginx.

Validar após cada mudança:

```bash
nginx -t
systemctl reload nginx
curl -fsS -H 'Host: signage.amb.local' http://127.0.0.1/health
tail -n 100 /var/log/nginx/digital-signage-error.log
```

Não sirva `media/` diretamente pelo Nginx: a aplicação implementa autorização,
token de dispositivo e suporte a Range.

## 7. Firewall e validação de rede

Substitua as redes abaixo. Execute primeiro a regra de SSH para não perder o
acesso remoto:

```bash
export ADMIN_CIDR='10.0.10.0/24'
export PLAYER_CIDR='10.0.20.0/24'

ufw default deny incoming
ufw default allow outgoing
ufw allow from "$ADMIN_CIDR" to any port 22 proto tcp comment 'SSH administracao'
ufw allow from "$ADMIN_CIDR" to any port 80 proto tcp comment 'Painel Digital Signage'
ufw allow from "$PLAYER_CIDR" to any port 80 proto tcp comment 'Players Digital Signage'
ufw enable
ufw status verbose
```

Se administradores e Players compartilham outras sub-redes, adicione regras
equivalentes. Não libere TCP 8000. Valide no servidor:

```bash
ss -lntp | grep -E '(:80|:8000)'
getent hosts signage.amb.local
curl -fsS http://signage.amb.local/health
```

Valide em pelo menos um Raspberry Pi:

```bash
getent hosts signage.amb.local
curl -fsS http://signage.amb.local/health
curl -I http://signage.amb.local/login
systemctl --user status digital-signage-player.service --no-pager
journalctl --user -u digital-signage-player.service -n 100 --no-pager
```

No `player.env`, use:

```text
DS_SERVER_BASE=http://signage.amb.local
```

Mantenha o token somente no dispositivo, com permissão restrita. Teste
provisionamento, download de playlist/mídia, heartbeat e comando antes de migrar
os demais Players. Se Players antigos ainda dependem de mídia pública, migre-os
e provisione tokens antes de manter `DS_REQUIRE_DEVICE_TOKEN=1`.

## 8. Backup e recuperação

### 8.1 Dados críticos e política

O backup existente inclui:

- `digital_signage.db`, por meio da API de backup do SQLite, compatível com WAL;
- `media/`;
- `releases/`;
- `/etc/digital-signage/server.env`;
- manifesto, metadados do commit e hashes SHA-256.

Não inclui código Git, virtualenv, logs nem a credencial inicial. Código e
dependências devem ser reproduzidos pelo commit. O segredo inicial deve ser
eliminado após uso.

Política mínima recomendada:

- backup local diário às 02:00, mantendo 14 cópias;
- cópia somente de diretórios `backup-*` completos para storage corporativo;
- criptografia e controle de acesso no destino, pois `server.env` contém
  segredos;
- teste de restauração trimestral em VM isolada;
- monitorar falhas do timer e capacidade em `/var/backups`.

### 8.2 Executar e habilitar backup

```bash
cd /opt/digital-signage/app
bash deploy/linux/backup-server.sh --keep 14
BACKUP_DIR="$(find /var/backups/digital-signage -mindepth 1 -maxdepth 1 \
  -type d -name 'backup-*' | sort | tail -n 1)"
python3 deploy/linux/server_ops.py verify --backup "$BACKUP_DIR"
systemctl enable --now digital-signage-backup.timer
systemctl list-timers digital-signage-backup.timer --all
journalctl -u digital-signage-backup.service -n 100 --no-pager
```

O script completo já é o script de backup do projeto; não o substitua por uma
cópia simples do banco ativo.

### 8.3 Retenção de dados operacionais

Comece em simulação:

```bash
systemctl enable --now digital-signage-retention.timer
systemctl start digital-signage-retention.service
journalctl -u digital-signage-retention.service -n 100 --no-pager
```

Mantenha `DS_CLEANUP_DRY_RUN=1` e `DS_CLEANUP_DELETE_ARCHIVED=0` até revisar a
classificação, testar restauração e aprovar a política. A retenção não substitui
backup.

### 8.4 Restaurar

O checkout ativo deve estar exatamente no commit registrado no backup. Primeiro
valide e faça a simulação sem confirmação:

```bash
sudo -i
cd /opt/digital-signage/app
export BACKUP_DIR='/var/backups/digital-signage/backup-NOME_EXATO'
python3 deploy/linux/server_ops.py verify --backup "$BACKUP_DIR"
bash deploy/linux/restore-server.sh --backup "$BACKUP_DIR"
```

Revise a contagem, o tamanho e o commit exibidos. Para executar:

```bash
cd /opt/digital-signage/app
bash deploy/linux/restore-server.sh \
  --backup "$BACKUP_DIR" \
  --confirm-restore
bash deploy/linux/preflight-server.sh --mode online
sqlite3 /var/lib/digital-signage/digital_signage.db 'PRAGMA integrity_check;'
curl -fsS http://signage.amb.local/health
```

O restore aceita apenas um filho direto de `/var/backups/digital-signage`, sem
links ou arquivos especiais, valida hashes/schema/configuração, preserva o
estado anterior e tenta rollback se a saúde falhar.

## 9. Atualização segura e rollback

### 9.1 Preparação

Nunca edite o checkout em produção. Antes da janela:

```bash
sudo -i
cd /opt/digital-signage/app
test -z "$(git status --porcelain)"
git remote get-url origin
git fetch --prune origin
export RELEASE_BRANCH='main'
export RELEASE_COMMIT='SHA_COMPLETO_DE_40_CARACTERES'
test "${#RELEASE_COMMIT}" -eq 40
test "$(git rev-parse "origin/$RELEASE_BRANCH")" = "$RELEASE_COMMIT"
bash deploy/linux/backup-server.sh --keep 14
```

Use o nome real da branch aprovada. O script aceita apenas origem HTTPS, SHA
completo e o commit na ponta da branch remota informada.

### 9.2 Executar a atualização

```bash
cd /opt/digital-signage/app
bash deploy/linux/update-server.sh \
  --branch "$RELEASE_BRANCH" \
  --commit "$RELEASE_COMMIT"
```

O fluxo cria backup preventivo, prepara clone e virtualenv isolados, executa a
suíte de testes, rejeita mudança não aplicada da unidade systemd, troca código e
venv, reinicia e valida saúde direta e via Nginx. O banco e os diretórios de
dados permanecem no lugar. Migrações de schema são feitas na inicialização.

### 9.3 Verificação e rollback

```bash
cd /opt/digital-signage/app
bash deploy/linux/preflight-server.sh --mode online
git rev-parse HEAD
systemctl status digital-signage.service --no-pager
journalctl -u digital-signage.service --since '-15 minutes' --no-pager
curl -fsS http://signage.amb.local/health
```

Falhas detectadas durante o fluxo restauram código e virtualenv anteriores. Se
a nova versão já alterou dados de forma incompatível ou a falha apareceu depois,
use o procedimento formal de restauração do backup anterior. Não faça downgrade
manual apenas do `app.py`.

## 10. Checklist de homologação

Registrar data, responsável e evidência para cada item:

- [ ] Commit implantado é o SHA aprovado e o checkout está limpo.
- [ ] `preflight-server.sh --mode offline` concluído sem bloqueios.
- [ ] `systemd-analyze verify` e `nginx -t` concluídos sem erro.
- [ ] Aplicação acessível em `http://signage.amb.local`.
- [ ] `/health` responde `status=ok` localmente e pelo Nginx.
- [ ] Login funciona e senha inicial foi removida do arquivo de ambiente.
- [ ] Perfis e permissões de Tela foram validados com usuário não administrador.
- [ ] Upload de JPG/PNG/WEBP/GIF e MP4 real funciona.
- [ ] Upload inválido, acima do limite e ZIP de release inválido são rejeitados.
- [ ] Reprodução e Range de vídeo funcionam via Nginx.
- [ ] Um Raspberry piloto resolve DNS e usa `http://signage.amb.local`.
- [ ] Provisionamento/token do Raspberry piloto funciona.
- [ ] Playlist, cache offline, heartbeat e comandos do piloto funcionam.
- [ ] Reprodução física no display foi conferida; heartbeat sozinho não prova imagem.
- [ ] Comunicação servidor ↔ Player foi validada sem liberar TCP 8000.
- [ ] `journalctl` e logs Nginx não mostram erros recorrentes.
- [ ] Banco retorna `ok` em `PRAGMA integrity_check`.
- [ ] Backup manual foi criado e `server_ops.py verify` passou.
- [ ] Backup completo foi copiado para storage externo protegido.
- [ ] Restauração foi ensaiada em VM isolada.
- [ ] Timers de backup e retenção estão habilitados e agendados.
- [ ] Retenção permanece em dry-run até aprovação explícita.
- [ ] Reinicialização completa do servidor preserva Nginx, aplicação e timers.
- [ ] Após reboot, login, upload e Raspberry piloto continuam operacionais.
- [ ] Monitor corporativo consulta `/health` e alerta falha de serviço/disco/backup.

Comandos para o teste de reboot:

```bash
systemctl reboot
```

Após reconectar:

```bash
systemctl is-active nginx digital-signage.service
systemctl is-enabled nginx digital-signage.service \
  digital-signage-backup.timer digital-signage-retention.timer
curl -fsS http://signage.amb.local/health
systemctl list-timers --all | grep digital-signage
```

## 11. Análise de riscos e mitigação

| Risco | Impacto | Mitigação obrigatória/recomendada |
|---|---|---|
| Permissões incorretas | Segredo exposto ou serviço sem gravar | Código `root:root` sem escrita de grupo; `server.env` 0600; dados `digital-signage` 0700; executar preflight após mudanças |
| Disco cheio | Upload, SQLite e backup falham | Alertas em `/var/lib`, `/var/backups`, `/var/lib/nginx` e `/tmp`; capacidade para dados, 14 backups e 30% de margem |
| SQLite em volume de rede ou corrupção | Indisponibilidade/perda | Disco local, um worker, backup via API SQLite, `integrity_check`, UPS e restore ensaiado |
| Concorrência SQLite | Lock e lentidão | Um worker; medir carga; migrar para banco cliente-servidor somente em projeto arquitetural próprio |
| Perda de mídia/releases | Telas sem conteúdo/updates | Backup conjunto com banco, hashes, cópia externa e teste de restauração |
| HTTP sem TLS | Roubo de senha/token na rede | Segmentar rede e ACL agora; implementar HTTPS com CA interna e `DS_COOKIE_SECURE=1` como melhoria prioritária validada |
| DNS `.local` | Conflito com mDNS ou resolução desigual | Validar DNS corporativo em todos os VLANs; considerar subdomínio interno convencional se houver conflito |
| Falha de rede | Players deixam de sincronizar | Cache local do Player, DNS/NTP redundantes, switches/links monitorados e testes de operação offline |
| Falha de energia | Corrupção/interrupção | UPS, desligamento controlado, filesystem confiável, backup externo e teste pós-reboot |
| Backup apenas local | Perda conjunta com servidor | Replicar somente backups completos para NAS/storage protegido; monitorar RPO e testar restore |
| Parada durante backup | Breve interrupção diária | Janela de baixa utilização às 02:00; medir duração; para zero downtime, redesenhar persistência/arquitetura |
| Atualização incompatível | Serviço ou schema indisponível | SHA aprovado, backup, testes isolados do update, janela, saúde e rollback/restore formal |
| Alterações manuais em produção | Update recusado e ambiente irreproduzível | Checkout somente leitura e limpo; toda mudança via commit revisado |
| Player com versão divergente | Atualização parcial/falha de boot | Reconciliar manifesto/código, incluir `stability_agent.py`, validar saúde após restart, piloto antes dos lotes |
| Token legado/público | Acesso indevido a mídias/API | `DS_REQUIRE_DEVICE_TOKEN=1`, mídia pública desabilitada, provisionar e rotacionar tokens |
| Heartbeat não prova display | Incidente visual não detectado | Telemetria + validação física/piloto; monitorar reprodução e não apenas processo/heartbeat |
| Retenção mal configurada | Exclusão indevida | Dry-run inicial, backup verificado, restore ensaiado e exclusão de arquivados desativada até aprovação |
| Logs sem retenção/alerta | Diagnóstico tardio | Configurar política corporativa para journal/logrotate e alertas de falha, disco e timer |
| Nó único | Indisponibilidade durante hardware/reboot | Peças/VM de reposição, RTO documentado e recuperação ensaiada; HA real requer redesenho além do código atual |

## 12. Rotina operacional 24x7

Diariamente, o monitoramento deve confirmar serviço, proxy, saúde, espaço e
último backup. Comandos de diagnóstico:

```bash
systemctl is-active digital-signage.service nginx
curl -fsS http://signage.amb.local/health
df -h /opt /var/lib/digital-signage /var/backups/digital-signage
journalctl -u digital-signage.service --since today --priority warning
journalctl -u digital-signage-backup.service --since '-2 days' --no-pager
systemctl list-timers --all | grep digital-signage
```

Mensalmente, revise crescimento, contas, tokens, versões dos Players e updates
de segurança do Ubuntu. Trimestralmente, execute restore em VM isolada. Mudanças
de pacote Ubuntu que reiniciem Python/Nginx e atualizações da aplicação devem
usar janela, backup prévio e o checklist de homologação.

## 13. Critério de aceite para produção

A implantação só está pronta quando o checklist inteiro estiver documentado, o
backup tiver uma cópia fora do servidor, a restauração tiver sido demonstrada,
um Raspberry piloto tiver reproduzido conteúdo real e os riscos de HTTP, nó
único e pequena interrupção do backup tiverem aceite formal ou plano de correção.
O estado atual fornece recuperação e reinício automático, mas não implementa
alta disponibilidade nem transporte criptografado.
