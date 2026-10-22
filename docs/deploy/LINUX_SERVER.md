# Servidor Digital Signage — preparação, homologação e operação Linux

Destino: `http://signage.amb.local`, HTTP TCP 80, Nginx já administrado pela
infraestrutura. Não configurar HTTPS, certificados, Certbot ou redirects HTTPS
nesta etapa. Porta 8000 exclusiva de loopback. Nenhum IP do ambiente é versionado.

Os comandos deste documento são para execução futura pelo responsável, na janela
autorizada. A preparação no Windows não executa instalação, serviços, SSH,
migração, acesso à VM ou cópia de dados operacionais.

## Caminho curto após preparar o checkout

Para uma instalação **sem banco anterior**, depois de instalar os pacotes Ubuntu
e colocar o checkout aprovado em `/opt/digital-signage/app`, execute:

```bash
(cd /opt/digital-signage/app && sudo bash deploy/linux/complete-install.sh --start-new)
```

O script aproveita `install-server.sh`, cria `server.env` somente se não existir,
gera uma senha inicial sem colocá-la no histórico do shell, corrige a propriedade
dos backups, preserva o virtual host Nginx já ativo, aplica a referência HTTP
somente se não houver virtual host para o DNS, executa preflight offline e
online e inicia a aplicação. Guarde a senha apresentada; faça login e altere a
senha do administrador em **Usuários**. Depois execute:

```bash
(cd /opt/digital-signage/app && sudo bash deploy/linux/complete-install.sh --finish)
```

Esta etapa exige a senha já alterada, elimina a senha inicial de `server.env`,
verifica a saúde, cria um backup e habilita os timers. Em caso de migração, não
use `--start-new`; siga os passos de cópia/restore e preflight abaixo. A política
UFW depende das sub-redes corporativas e continua uma etapa da infraestrutura.

## Layout e responsabilidades

| Local | Conteúdo / permissão |
|---|---|
| `/opt/digital-signage/app` | Checkout Git revisado, root, serviço sem escrita |
| `/opt/digital-signage/venv` | Python Linux; após update pode apontar para venv versionado |
| `/etc/digital-signage/server.env` | root:root 0600; diretório 0700 |
| `/var/lib/digital-signage/digital_signage.db` | SQLite, usuário digital-signage, 0600 |
| `/var/lib/digital-signage/media` | Mídias ativas e arquivadas |
| `/var/lib/digital-signage/releases` | Pacotes do Player registrados pelo Servidor |
| `/var/lib/digital-signage/upload-tmp` | Temporários promovidos somente após validação |
| `/var/backups/digital-signage` | root:root 0700, backups com segredos |
| `/var/log/digital-signage` | Não necessário: logs principais no journal |

O serviço precisa escrever no diretório do banco, inclusive para journal/WAL, e
nos diretórios de dados. O systemd limita escrita persistente a `/var/lib/digital-signage`;
`PrivateTmp` permite temporários privados. `HOME` do serviço aponta para os dados.
Código, templates e venv são somente leitura.

Servidor: FastAPI, Uvicorn, SQLite, Nginx, `stability_monitor.py`, mídia, releases e
operações deste diretório. Raspberry Pi: `local_player.py`, Firefox,
`player_local.html`, cache, `stability_agent.py`, `network_watchdog.py` e seus
serviços/timers. Não instalar os serviços do Raspberry no Servidor.

## Comportamento da aplicação

Linux usa `/var/lib/digital-signage` por padrão. As três variáveis de caminho têm
precedência. Instalações novas no Windows usam `%LOCALAPPDATA%\DigitalSignage`.
Uma instalação Windows que já tenha `digital_signage.db` junto ao código mantém
o layout antigo temporariamente, para evitar inicializar um banco vazio no lugar
do existente. Não ocorre cópia nem migração automática. Para retirar os dados do
Git também no Windows, mover os dados em janela controlada e configurar as três
variáveis com caminhos absolutos.

A inicialização persistente por importação foi mantida para evitar refatoração
ampla: cria diretórios, esquema, migrações aditivas, TV01, primeiro administrador
em banco novo e sincroniza releases. Nunca importar `app` numa validação sem
definir primeiro caminhos temporários. O startup inicia uma thread de monitoramento.
Importar não inicia essa thread, mas já altera o banco configurado.

**Um worker obrigatório.** SQLite, inicialização, sincronização de releases,
snapshots/eventos e thread interna não foram projetados para vários processos.
Não usar reload, Gunicorn com múltiplos workers ou balanceamento entre processos.

O administrador inicial só é criado em banco novo. Usuários existentes são
preservados. Banco existente sem usuários exige recuperação administrativa
explícita, sem criação silenciosa de uma conta. Preferir senha recebida pelo
ambiente: nesse caso nenhum arquivo de credenciais é criado. Sem senha, a
aplicação gera segredo e cria arquivo externo com modo 0600 e abertura exclusiva;
arquivo existente ou caminho no código faz a inicialização falhar, sem sobrescrever.
Em Windows, restringir também a ACL ao usuário do serviço/administrador.
Após primeiro acesso: trocar senha, remover arquivo inicial e retirar a senha
inicial do `server.env`. Não imprimir a senha ou incluí-la em tickets/logs.

`GET /health` é público, sem sessão ou consulta de autenticação, não lista
arquivos, não expõe caminhos nem usuários. Abre SQLite por URI `mode=ro`,
ativa `query_only` e consulta a tabela users sem retornar seu conteúdo.
Banco ausente não é criado. Diretórios devem existir e ter acesso de leitura,
escrita e travessia. Retorna status, versão, database/media/releases e timestamp;
200 para pronto, 503 para falha essencial. A verificação de permissões não grava
arquivo de teste e não garante disponibilidade de espaço; preflight verifica espaço.
O endpoint não comprova saúde da thread nem reprodução no Player.

O systemd reinicia processos que falham; não consulta HTTP automaticamente.
Monitoramento corporativo deve consultar `/health` pelo hostname, alertando sobre
falhas repetidas. O procedimento de atualização/restore consulta saúde local e proxy.

## Variáveis confirmadas

| Variável | Uso |
|---|---|
| DS_DB_FILE | Caminho absoluto do SQLite |
| DS_MEDIA_DIR | Raiz de mídia; uploads e rendered abaixo dela |
| DS_RELEASE_DIR | Diretório de releases |
| DS_SESSION_TTL_SECONDS | 43200 no exemplo |
| DS_PASSWORD_ITERATIONS | 210000 no exemplo, preserva verificação dos hashes existentes |
| DS_COOKIE_SECURE | 0 para HTTP interno |
| DS_REQUIRE_DEVICE_TOKEN | `1` após provisionar todos os Players |
| DS_ALLOW_LEGACY_PUBLIC_MEDIA | Transição emergencial; manter `0` na homologação segura |
| DS_UPLOAD_TEMP_DIR | Temporários; `/var/lib/digital-signage/upload-tmp` |
| DS_MAX_IMAGE_MB / DS_MAX_VIDEO_MB | Limites por imagem/vídeo; 25/1024 MiB |
| DS_MAX_RELEASE_MB | Limite de release ZIP; 512 MiB |
| DS_MAX_FILES_PER_UPLOAD / DS_MAX_REQUEST_MB | Lote 20; requisição 1536 MiB |
| DS_RETENTION_TEMP_DAYS / DS_RETENTION_ARCHIVED_DAYS | Janelas de 7/90 dias |
| DS_CLEANUP_DRY_RUN / DS_CLEANUP_DELETE_ARCHIVED | Defaults seguros `1`/`0` |
| DS_INITIAL_ADMIN_USER | Nome inicial, somente banco novo |
| DS_INITIAL_ADMIN_PASSWORD | Segredo inicial, somente banco novo |
| DS_INITIAL_CREDENTIALS_FILE | Caminho externo da credencial gerada |
| TZ | Fuso do processo; app e monitor fixam America/Sao_Paulo |
| PYTHONUNBUFFERED | Logs imediatos no journal |
| PYTHONDONTWRITEBYTECODE | Evita cache Python no código |

Não há timezone de aplicação configurável nesta versão. `tzdata` assegura a base
IANA inclusive no Windows. O arquivo real não é um script shell: não usar `source`,
expansões de variáveis, escapes ou comandos. Os helpers aceitam `CHAVE=valor` ou
valores simples entre aspas, sem escapes ou aspas internas; comentam-se linhas
inteiras com `#`. Não adicionar `player.env`.

## Dependências e instalação

Alvo dos scripts: Debian/Ubuntu com Python >= 3.11, systemd e filesystem local
com semântica POSIX. Confirmar arquitetura, versão da distribuição, recursos,
volume de mídia e espaço antes de instalar. Não usar SQLite em compartilhamento
de rede. A disponibilidade dos wheels Linux precisa ser homologada.

Runtime em `requirements.txt`; transitivas em `requirements-constraints.txt`;
testes em `requirements-dev.txt`. Versões vieram do grafo de dependências usado
localmente, sem freeze indiscriminado. Uvicorn usa HTTP padrão, sem extras opcionais
de uvloop/watchfiles/websockets. TestClient da versão instalada de Starlette prefere
`httpx2`; por isso os testes fixam esse pacote, sem adicionar ambos os clientes.
Cada atualização guarda `requirements-resolved.txt` no venv preparado.

Dependências de sistema, a instalar pela equipe em shell root autorizado:

```bash
apt-get update
apt-get install python3 python3-venv git curl sqlite3 ffmpeg
```

`ffprobe`, fornecido pelo pacote ffmpeg, valida vídeos MP4. PDF, PPT e PPTX não
são formatos aceitos pelo Servidor.
Não instalar navegador ou Player no Servidor. Nginx já está provisionado.

1. Disponibilizar checkout revisado em `/opt/digital-signage/app`, com origin
   HTTPS, commit registrado e árvore limpa; excluir dados, segredos e venv Windows.
2. Garantir propriedade root e ausência de escrita pelo usuário do serviço.
3. Executar `bash deploy/linux/install-server.sh` a partir do checkout.
4. Criar `/etc/digital-signage/server.env` usando o exemplo, root:root 0600.
5. Em banco novo, definir senha privada de pelo menos 16 caracteres. Na migração,
   disponibilizar o banco existente antes de iniciar.
6. Executar `bash deploy/linux/preflight-server.sh --mode offline`.
7. Somente após migração/validação aprovada: `systemctl enable --now digital-signage`.
8. Executar `bash deploy/linux/preflight-server.sh --mode online`.

Instalador não inicia/habilita serviço, não cria/sobrescreve env, não copia banco,
não remove mídia/releases e não modifica Nginx. Pode repetir preparação; se o
serviço estiver ativo, recusa para não modificar o venv em uso. Valida pacotes
de sistema em vez de executar apt automaticamente. Instala unidade e daemon-reload.
Scripts são invocados com `bash`, dispensando bit executável no checkout Windows.

Preflight offline: identidade, caminhos, permissões do serviço, env, dependências,
SQLite, espaço livre mínimo de 2 GiB, DNS e porta 8000 livre. Online: exige serviço,
saúde local e proxy, sem exigir porta livre. Nginx ativo é verificado quando instalado.
Não importa app nem cria dados. Apenas cria/usa um lock operacional em `/run/lock`.
Planejar espaço real adicional para backups, buffering do Nginx e uploads;
o mínimo de 2 GiB não dimensiona a instalação.

## Proxy e cookies

Unidade: usuário/grupo digital-signage, WorkingDirectory `/opt/digital-signage/app`,
Python `/opt/digital-signage/venv/bin/python`, `app:app`, host 127.0.0.1, porta 8000,
workers 1, `--proxy-headers --forwarded-allow-ips=127.0.0.1`, restart on-failure,
sem reload, journal, SIGTERM e TimeoutStopSec 60. Após cinco falhas em 120 segundos,
investigar logs; depois da correção, usar `systemctl reset-failed digital-signage`.

Comparar `nginx-digital-signage.conf.example` com o virtual host efetivo usando
`nginx -T` em sessão administrativa protegida; não publicar o dump de outros hosts.
Validar com `nginx -t` antes de qualquer alteração pela infraestrutura.
Não copiar o exemplo automaticamente sobre a configuração real.

Host preservado; X-Real-IP e X-Forwarded-For usam endereço do cliente do Nginx.
Como há um único proxy, o exemplo substitui a cadeia enviada pelo cliente,
evitando aceitar um IP forjado. X-Forwarded-Proto vem de `$scheme`. Se surgir
outro proxy, revisar a cadeia de confiança antes de mudar a configuração.
O app usa `request.client.host` na auditoria; o Uvicorn só interpreta headers
encaminhados por loopback. Cookies mantêm HttpOnly/SameSite=Lax; sem SameSite=None.

Uploads: limite Nginx 2 GiB por requisição (incluindo multipart); buffering em
disco habilitado, timeout de corpo 300s, envio/leitura upstream 600s. Reservar
disco para temporários do Nginx. Releases são gravadas em blocos de 1 MiB,
limitadas a 2 GiB e usam nomes únicos: duplicação de versão não apaga o pacote
anterior. Uploads de mídia usam blocos. Validar imagens e vídeos MP4 reais
de tamanho representativo somente durante homologação autorizada.

## Backup, retenção e restore

O instalador copia as unidades de backup e retenção, mas não habilita timers.
Após preflight e primeira execução manual bem-sucedida:

```bash
systemctl enable --now digital-signage-backup.timer
systemctl enable --now digital-signage-retention.timer
systemctl list-timers 'digital-signage-*'
```

O backup roda às 02:00 e a retenção às 03:30, ambos com atraso aleatório de até
30 minutos e `Persistent=true`. Para outro horário, usar `systemctl edit` na
unidade timer correspondente e redefinir `OnCalendar=` no override.

```bash
bash deploy/linux/backup-server.sh --keep 14
python3 deploy/linux/server_ops.py verify --backup /var/backups/digital-signage/backup-NOME
```

Backup completo para o serviço brevemente, registra se estava ativo e retoma
esse estado ao terminar ou falhar. Essa pausa impede divergência entre DB,
mídias e releases; não iniciar outro processo escritor durante a operação.
SQLite usa API de backup, compatível com WAL, nunca simples cópia de banco ativo.
Integridade, hashes SHA-256, tamanhos e contagens são verificados antes da promoção.
Inclui env restrito, commit, versão, dependências e timestamp UTC.
Não inclui venv, Git, logs, caches ou credencial inicial avulsa.

Destino `.partial` nunca é considerado backup concluído. Falha deixa evidência
para inspeção. Retenção só considera nomes gerados pelo helper e valida cada
backup antes de removê-lo; nunca remove fora da raiz configurada. Hashes detectam
corrupção, não autenticam um backup de origem não confiável. Armazenar cópia
externa protegida conforme política corporativa; este script produz cópia local.
Sincronizar com NAS/storage somente diretórios `backup-*` concluídos, depois do
sucesso da unidade. Credenciais do destino ficam fora do Git e do journal. O
backup só pode ser declarado recuperável após restore ensaiado em VM isolada.

Retenção inicia com dry-run. Antes de exclusão definitiva, executar e verificar
um backup, ensaiar restore isolado e revisar o journal de
`digital-signage-retention.service`. Só então alterar `DS_CLEANUP_DRY_RUN=0` e,
se a política autorizar, `DS_CLEANUP_DELETE_ARCHIVED=1`. Conteúdo referenciado
por rascunho, publicação, histórico, agenda ou Tela permanece protegido, assim
como releases recomendadas, estáveis ou associadas a jobs.

```bash
bash deploy/linux/restore-server.sh --backup /var/backups/digital-signage/backup-NOME
# Após revisar contagem e tamanho exibidos:
bash deploy/linux/restore-server.sh --backup /var/backups/digital-signage/backup-NOME --confirm-restore
```

Restore exige filho direto da raiz de backups, sem links ou arquivos especiais,
manifesto/checksums válidos, SQLite íntegro, configuração canônica e checkout
no commit do backup. Prepara os dados antes da parada, faz backup preventivo,
troca diretórios, aplica propriedade/permissões e valida health local/proxy.
Em falha, preserva os dados da tentativa e recupera o diretório/configuração
anteriores. Estado anterior e env `.before-*` ficam protegidos para revisão;
a limpeza desses artefatos é manual após o aceite, nunca indiscriminada.
Não restaurar sobre produção para testar. Usar os testes com dados sintéticos e
uma VM isolada com os mesmos caminhos para ensaio operacional completo.

## Atualização e rollback

```bash
bash deploy/linux/update-server.sh --branch BRANCH_REVISADA --commit SHA_COMPLETO_40_HEX
```

Recusa árvore suja e branch divergente; não usa reset, restore ou clean Git.
Origin deve ser HTTPS; não usa SSH. SHA solicitado deve ser exatamente o tip da
branch obtida. Cria backup antes de buscar código, retomando o serviço durante
a preparação. Prepara checkout isolado e venv próprio; valida dependências,
Python, templates, scripts, unidade e testes em dados temporários.
Mudança da unidade exige revisão/instalação específica, não troca silenciosa.
Nunca executar código de commit não revisado: instalação e testes são operações
administrativas.

Antes da troca, para o serviço e cria backup consistente atualizado. Mantém
checkout e venv anteriores. O venv novo fica em caminho permanente próprio e
`/opt/digital-signage/venv` aponta para ele, evitando quebrar shebangs por mover venv.
Após troca, testa saúde local e Nginx. Falha recupera código e venv anteriores,
e testa novamente. Não restaura banco só porque houve timeout de proxy:
preserva dados, para o serviço se rollback não recuperar saúde e exige diagnóstico
de incompatibilidade antes do restore explícito. Isso evita descartar gravações
novas por uma falha exclusivamente de rede.

Durante a janela, bloquear operacionalmente novas alterações até o aceite.
Não rodar simultaneamente os scripts: lock exclusivo serializa operações.
Saída identifica resultado, commits e backup; guardar registro da janela.
Início, conclusão e falha também vão ao syslog com tag digital-signage-operations.
Logs do app: `journalctl -u digital-signage -n 200 --no-pager`.
Observar disco, reinícios, `/health`, falhas de upload e eventos dos Players.

## Migração Windows → Linux e corte

1. Confirmar VM, IP fixo com infraestrutura, hostname, TCP 80, SSH restrito e
   ausência de exposição da porta 8000. Não registrar IP em arquivos do projeto.
2. Validar DNS e acesso a partir de cada segmento, inclusive o Raspberry piloto:

   ```powershell
   nslookup signage.amb.local
   Test-NetConnection signage.amb.local -Port 80
   ```

   ```bash
   getent hosts signage.amb.local
   curl -I http://signage.amb.local
   ```

   `.local` pode interagir com mDNS/NSS. `nslookup` isoladamente não prova que o
   Player resolverá o nome; testar também resolução usada pela aplicação e curl.
   Corrigir política de resolução com infraestrutura; não inserir IP em código.
3. Instalar aplicação e configurar env; manter serviço parado.
4. Inventariar os caminhos efetivos do Windows e versão/commit em execução.
   Fazer backup preliminar; registrar usuários, contagens e arquivos sem expor segredos.
5. Definir janela e impedir alterações. Parar aplicação Windows e confirmar que
   não há processo escritor ou upload em andamento.
6. Gerar backup final com `sqlite3.Connection.backup()` e `PRAGMA integrity_check`.
   Exemplo conceitual em Python, sem importar app:

   ```python
   from pathlib import Path
   from contextlib import closing
   import sqlite3
   source = Path("CAMINHO_ABSOLUTO_DO_BANCO").resolve()
   destination = Path("CAMINHO_NOVO_DO_BACKUP").resolve()
   if destination.exists():
       raise RuntimeError("Não sobrescrever backup")
   with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
       with closing(sqlite3.connect(destination)) as dst:
           src.backup(dst)
           assert dst.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
   ```
7. Copiar DB resultante, mídia completa e releases completas pelo canal autorizado;
   não copiar venv Windows. Comparar SHA-256 por caminho relativo, número de
   arquivos e soma de bytes, inclusive diretórios vazios necessários.
8. Inspecionar caminhos armazenados no banco, nomes com diferença somente de
   maiúsculas/minúsculas e dependências de fontes. Não substituir caminhos por
   SQL genérico: validar eventual transformação primeiro em cópia isolada.
9. Aplicar usuário/grupo digital-signage, diretórios 0700 e arquivos 0600.
   Confirmar env root:root 0600 e código root sem escrita pelo serviço.
10. Rodar preflight offline, iniciar via systemd, validar health local, Nginx,
    hostname e preflight online.
11. Validar login/logout, uploads de imagem/vídeo, rejeição de PDF/PPT/PPTX, Biblioteca,
    Playlists, Agendamentos (America/Sao_Paulo), Telas, Releases e Auditoria.
12. Executar backup e ensaio de restore em VM isolada. Conferir conteúdo após restore.
13. Em Raspberry de homologação, alterar apenas `DS_SERVER_BASE` para
    `http://signage.amb.local`; validar DNS, token, heartbeat, sincronização,
    reprodução, comandos e download de releases. Nenhum serviço do Pi é alterado
    implicitamente pela migração do Servidor.
14. Observar por pelo menos um ciclo operacional completo acordado com a equipe,
    incluindo agendamentos, reinício controlado e uploads representativos.
15. Migrar os demais Players em lotes, registrar resultados e manter Windows
    disponível, porém parado, durante a janela de retorno.

Retornar ao Windows se houver divergência de dados, saúde persistentemente
indisponível ou falha essencial de login, sincronização/reprodução/agendamento.
Parar Linux primeiro, preservar seu estado e identificar gravações posteriores
ao corte. Só reativar Windows com estado compatível e aceito; não executar ambos
gravando em paralelo e não descartar alterações novas silenciosamente.
Se o hostname permanecer apontado ao Linux, retorno de roteamento/DNS é ação
coordenada da infraestrutura; não faz parte dos scripts.

## Evidências técnicas e limites da preparação

Regiões relevantes em `app.py` podem ser localizadas pelas funções/constantes:

| Tema | Região / decisão |
|---|---|
| Caminhos Windows e Linux | `Path`, dados externos e temporários em `DS_UPLOAD_TEMP_DIR` |
| Separadores e subprocessos | `Path`; `subprocess` permanece para validação com ffprobe |
| ffprobe/ffmpeg | `probe_media_metadata`/busca `shutil.which("ffprobe")`: ffprobe; sem execução direta de ffmpeg encontrada |
| Escrita no código, diretórios na importação | bloco CONFIGURAÇÃO: novos defaults externos, legado Windows explícito |
| SQLite, admin, TV padrão, migrações, releases | `init_db`, `ensure_initial_admin`, `sync_packaged_releases`; importação persiste |
| Startup e worker | `start_monitor_worker`, `monitor_worker`: único processo |
| Logs | stdout/stderr no journal, sem novo FileHandler persistente |
| Fuso | APP_TIMEZONE e stability_monitor.TZ: America/Sao_Paulo |
| Proxy e auditoria | login/set_cookie e audit_event/request.client.host |
| Upload grande | releases_upload em blocos; upload de mídia já em blocos |
| Permissões / recursos escritos | banco/journal, sessões/auditoria no DB, mídia/rendered, releases e credencial externa |
| Multiworker | inicialização, monitoramento, snapshots e sincronização duplicariam trabalho |

Limites: testes Windows não comprovam ACL POSIX, systemd sandbox, pacotes Linux,
resolver `.local`, Nginx ou restauração sob falha de
energia. Backups por rename oferecem promoção de conclusão, não garantia de
durabilidade contra perda elétrica. É necessário ensaio Linux antes de produção.
Os exemplos antigos diretamente em `deploy/` não são a referência desta implantação;
usar exclusivamente `deploy/linux/`.

Aceite: dados separados; worker único; porta local; cookie HTTP; env restrito;
backup/restore demonstrados; instalação preserva dados; update/rollback ensaiados;
Windows preservado; Player separado; nenhum segredo/IP real versionado; testes
aprovados e evidência das verificações Linux registrada pela infraestrutura.
