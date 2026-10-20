# Arquitetura atual do Player (análise estática)

Fonte examinada: pasta original `DigitalSignagePlayer` fornecida para integração, sem execução. O código copiado está em `player/`. A versão do Servidor é independente da versão do Player. **Tela** designa o recurso administrado; **Dispositivo**, o Raspberry Pi; **Player**, o software nele executado; **Display**, o monitor conectado. O servidor `app.py` pertence ao componente Servidor, não ao Player.

## Componentes e inicialização

O serviço de usuário `digital-signage-player.service` define `WorkingDirectory=%h/DigitalSignagePlayer`, carrega `player.env` opcional (`EnvironmentFile=-...`) e executa `/usr/bin/python3 %h/DigitalSignagePlayer/local_player.py`; roda sob o usuário que habilitou o serviço, sem `User=` explícito. `install_player.sh` provisiona o Dispositivo e `scripts/apply_services.sh` instala o serviço de usuário, além do watchdog de sistema em `/opt/digital-signage-player/network_watchdog.py`. O timer `digital-signage-network-watchdog.timer` começa após 45 s e repete em 60 s, com precisão de 10 s.

Arquivos obrigatórios na instalação real: `local_player.py`, `stability_agent.py` (import local), `player_local.html`, `player_updater.py`, `network_watchdog.py`, os arquivos `services/` e `scripts/apply_services.sh`, mais `player.env` gerado no provisionamento. O Player precisa do Firefox e de uma sessão gráfica; `state.json` e o cache não são obrigatórios na primeira partida, mas tornam a operação offline possível. Os módulos Python copiados usam apenas biblioteca padrão; a sintaxe requer Python 3.10+. As ferramentas Linux estão detalhadas em `player/README.md`.

## Contrato observado com o Servidor

| Método e rota | Uso e formato geral | Autenticação observada |
| --- | --- | --- |
| `POST /api/player/provision` | JSON `{tv_id, code}`; retorna token do Dispositivo e release com versão, URL, SHA-256 | Código de provisionamento de uso único |
| `GET /api/player/{tv_id}` | JSON da playlist publicada/agenda ativa, revisão e URLs de mídia | Bearer do Dispositivo via `require_device_auth` |
| `GET /api/player/{tv_id}/cache-manifest` | JSON de recursos necessários para playlists atribuídas e agendadas | Bearer do Dispositivo |
| `GET /media/...` | Bytes de mídia para cache local, incluindo Range/206 | Bearer do Dispositivo; o Servidor valida vínculo da mídia com a configuração da Tela |
| `POST /api/tv/heartbeat` | JSON com `tv_id`, versão, estado de sync, telemetria, reprodução informada pelo Firefox e eventos pendentes; resposta com `accepted_stability_event_ids` | Player envia Bearer; a rota do Servidor não chama `require_device_auth` — risco existente |
| `GET /api/player/{tv_id}/commands` | JSON `{commands: [...]}`; cada comando tem id, nome e argumentos | Bearer do Dispositivo |
| `POST /api/player/{tv_id}/commands/{command_id}/ack` | JSON `{status, progress, message, result}` | Bearer do Dispositivo |
| `GET /api/player/{tv_id}/release/{filename}` | Pacote de release para instalador/atualizador | Bearer do Dispositivo |

O servidor HTTP local usa por padrão `127.0.0.1:8765`: `GET /` ou `/index.html` entrega `player_local.html`; `GET /state` fornece o último estado local; `GET /media/...` entrega cache; `GET /health` expõe diagnóstico; `POST /playback` recebe os eventos do HTML. Firefox e serviço local compartilham o Dispositivo. O monitoramento do navegador observa processo e telemetria; não prova que o Display físico mostra imagem.

## Sincronização, offline, estabilidade e atualização

`local_player.py` consulta configuração e manifesto em cada sync, reúne as URLs, baixa faltantes em sequência para `.part`, valida tamanho quando há `Content-Length`, promove por `os.replace` e só depois substitui `state.json`. Erro preserva estado e mídia anteriores. A validação do cache existente verifica apenas tamanho maior que zero. Órfãos saem após 24 h pelo `mtime`; recursos referenciados são preservados. O worker sequencial também cuida do Firefox, heartbeat e comandos: um download demorado pode atrasar as outras tarefas apesar dos intervalos configurados (10 s sync, 2 s heartbeat, 5 s comandos, 5 s navegador). O HTML comunica a reprodução ao serviço local; o Firefox é iniciado em kiosk e supervisionado quanto a processo e progresso de vídeo.

O cliente HTTP central já envia `Authorization: Bearer` também ao baixar mídia;
o token não aparece na URL. O Player integrado continua usando `/media/...` e o
mesmo cache offline. Players antigos sem credencial devem ser provisionados
antes de ativar `DS_REQUIRE_DEVICE_TOKEN=1`; `DS_ALLOW_LEGACY_PUBLIC_MEDIA=1`
existe somente para uma transição curta e registrada. O Player Web administrativo
usa sessão e `/api/web-player/{tv_id}`, sem receber o token físico e sem enviar
heartbeat que possa alterar a saúde do Raspberry.

`stability_agent.py` guarda em `stability_events.json` eventos de reboot detectado por `boot_id`, reinício de processo e navegador. Envia até 40 pendentes por heartbeat e remove apenas IDs aceitos na resposta. CPU, RAM e temperatura são lidas de `/proc` e `/sys`; disco por `shutil.disk_usage`; rede por utilitários/arquivos do Linux. O watchdog usa estado em `/var/lib/digital-signage` e pode executar ações privilegiadas de rede e reboot.

`player_updater.py` faz download autenticado, compara SHA-256, extrai ZIP, faz backup, copia lista fixa e aplica serviços. Em exceção, restaura essa lista; após copiar, registra `last_update.json` e tenta agendar reinício. Não há verificação de saúde posterior. `stability_agent.py` está ausente da lista de backup, instalação e rollback. O script inicial `install_player.sh` copia o pacote inteiro, mas seu backup anterior também omite o agente. O instalador consulta `/health` logo após instalar; isso não equivale a validação de uma atualização remota.

## Divergências e riscos

| Gravidade | Achado | Efeito / próximo passo |
| --- | --- | --- |
| Crítico | `POST /api/tv/heartbeat` no Servidor aceita `tv_id` sem chamar `require_device_auth` | Telemetria e estado operacional podem ser forjados; corrigir em tarefa de contrato/segurança com testes isolados. |
| Alto | `player_updater.py` omite `stability_agent.py` em cópia e rollback | Uma release com esse módulo não é instalada/recuperada de modo completo; revisar allowlist e pacote antes de implantar. |
| Alto | Token do Dispositivo é passado em argumento `--device-token` ao subprocesso atualizador | Pode aparecer na lista de processos/diagnósticos; planejar transporte menos exposto. |
| Alto | `scripts/apply_services.sh` é autorizado no sudoers e reside sob diretório do usuário | Risco de alteração do script privilegiado por esse usuário; definir instalação privilegiada imutável e permissão mínima. |
| Alto | `manifest.json` = `3.2.1-stable`; `local_player.py` = `3.2.2-stability-preview` | O pacote gerado da pasta atual pode declarar versão diferente do executável; reconciliar antes de qualquer release. |
| Médio | Worker de sync, heartbeat, comandos e supervisão é sequencial | Download bloqueante pode atrasar presença e execução de comandos. |
| Médio | Atualização não mede saúde depois do restart; rollback só cobre exceções durante atualização | Falhas posteriores podem persistir; exigir verificação de saúde/recuperação em trabalho futuro. |
| Médio | Arquivos em cache só são validados por tamanho positivo, sem hash por mídia | Corrupção não detectada em cache existente. |
| Médio | Código, serviço e instalador dependem de `~/DigitalSignagePlayer`; watchdog usa `/opt` e `/etc` | A pasta `player/` versionada não é instalação executável direta; manter empacotamento compatível até migração planejada. |
| Baixo | Funções/strings internas usam `chromium_*`, embora executem Firefox; `diagnose.sh` lê log/processo Chromium antigo | Diagnóstico pode induzir interpretação incorreta; limpar apenas em tarefa funcional separada. |

As variantes `local_player_firefox.py` (`3.2.1-firefox-test`) e `local_player_firefox_hw.py` (`3.2.1-firefox-hw-test`) não são referenciadas pelo serviço, instalador nem updater ativos; são experimentos históricos, não entradas atuais. Elas, as cópias `.BKP` e os backups Python examinados declaram retenção de 24 h, como a entrada atual; não foi encontrada variante com prazo diferente. O manifesto e o README antigos refletem 3.2.1, enquanto a entrada ativa é prévia 3.2.2. Há endereço interno e identificador de laboratório como **defaults no código**, sem token literal, preservados nesta cópia por proibição de alterar URLs/versões. Não publicar uma release com esses defaults sem revisão de configuração.

## Achados de dados sensíveis na origem

- `player.env`, linha 3: token real do Dispositivo preenchido. Risco de autenticação indevida se publicado; não foi copiado. Guardar apenas na instalação, com permissão restrita, e rotacionar se houver suspeita de exposição.
- `state.json`, linhas 10 e 72–74: playlist/identificador de Tela e endereço do servidor persistidos. Risco de revelar dados operacionais; não foi copiado.
- `stability_events.json`, linha 1: identificador de boot/sessão e eventos pendentes. Risco de divulgar rastros operacionais; não foi copiado.
- `local_player.py`, linhas 27–29: endereço interno e identificador de laboratório como defaults; o token vem de variável de ambiente, sem literal no código. Risco de configuração equivocada ao executar sem `player.env`; preservar agora e eliminar defaults em mudança funcional própria.
- `install_player.sh`, regiões 60–80 e 130–149: recebe token do provisionamento e o grava no `player.env`; também aceita senha Wi-Fi por argumento. O código copiado não contém valor real, mas argumento de senha pode aparecer em histórico/lista de processos. Rever o fluxo em tarefa de segurança posterior.

Não foram detectados marcadores de chave privada nem credenciais embutidas em URLs no conjunto textual da origem. Logs, backups e pacotes não foram copiados; podem reter dados operacionais e devem permanecer fora do Git.

## Limites e próximas etapas

Esta análise foi estática: não verificou boot, rede, Firefox, health, reprodução real, compatibilidade de um pacote específico, permissões Linux efetivas nem restauração após falha. A cópia no Windows preservou LF e shebang, mas o bit executável Unix dos scripts precisa ser confirmado no processo de empacotamento/commit em Linux. A integração ao Git não atualiza a instalação em Raspberry algum. Antes de implantar, reconciliar versão/manifesto, definir empacotamento que inclua o agente, endurecer segredos e privilégio, corrigir autenticação do heartbeat no Servidor e validar atualização/rollback em Dispositivo isolado.

O inventário individual de todos os arquivos da origem e a decisão de cópia aparecem em `player/docs/INVENTARIO_ORIGEM.md`.
