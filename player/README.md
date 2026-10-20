# Player do Digital Signage

Esta pasta versiona o código-fonte do **Player** executado no **Dispositivo** Raspberry Pi. A **Tela** é o recurso operacional cadastrado no servidor; o **Display** é o monitor físico ligado ao Dispositivo. Servidor e Player pertencem ao mesmo repositório, mas têm versões e ciclos de implantação independentes.

## Entrada e arquivos

- `local_player.py`: entrada do serviço de usuário, cliente HTTP do servidor, cache, servidor HTTP local, supervisão do Firefox, heartbeat e comandos.
- `player_local.html`: interface de reprodução servida localmente; busca `/state` e envia estado de reprodução a `/playback`.
- `stability_agent.py`: fila persistente de eventos de início/reinício, incluída no heartbeat.
- `player_updater.py`: baixa uma release, confere SHA-256, faz backup/cópia e solicita reinício do serviço.
- `network_watchdog.py`: sondagem de rede, tentativas de recuperação e eventual reinício do Dispositivo.
- `install_player.sh`: provisionamento inicial e instalação do pacote validado.
- `scripts/apply_services.sh`: instala e habilita os serviços/timer Linux.
- `services/`: serviço de usuário do Player, serviço/timer de sistema do watchdog e `player.env.example`.
- `manifest.json`: manifesto original seguro da release **3.2.1-stable**. Ele não descreve a versão `3.2.2-stability-preview` declarada no `local_player.py` copiado.

Os módulos Python copiados usam somente a biblioteca padrão. No Dispositivo, o fluxo existente exige Python 3 compatível com anotações `dict | None` (3.10+), Firefox, systemd de sistema e de usuário, `curl`, `sudo`, utilitários de rede (`ip`, `ping`; NetworkManager/`nmcli` ou `wpa_cli` quando disponíveis) e sessão gráfica funcional. O instalador também usa ferramentas POSIX comuns. Nenhum `requirements.txt` foi criado porque não há dependência Python de terceiros confirmada.

## Fluxo atual

O instalador envia `tv_id` e código de provisionamento a `POST /api/player/provision`, recebe token e referência de release, baixa o pacote com autenticação Bearer, valida SHA-256, copia os arquivos para `~/DigitalSignagePlayer`, grava `player.env` com permissão 0600 e aplica os serviços. O serviço `digital-signage-player.service` carrega opcionalmente esse arquivo e inicia `local_player.py` como o usuário do Dispositivo. `apply_services.sh` instala o watchdog como serviço de sistema em `/opt/digital-signage-player`, com configuração em `/etc/digital-signage/player.env`.

Na inicialização, o Player limpa arquivos `.part` antigos, carrega `state.json` se existir, abre o HTTP local em `DS_LOCAL_HOST:DS_LOCAL_PORT` (padrão `127.0.0.1:8765`), registra a sessão de estabilidade e inicia o worker. O Firefox em modo kiosk abre essa página local. O HTML carrega `/state`, reproduz mídia via `/media/...` e relata eventos a `/playback`; isso é telemetria de software, não confirmação física do Display.

O worker consulta a configuração publicada e o manifesto de cache, baixa mídias ausentes para `.part`, compara tamanho quando `Content-Length` existe e só promove com `os.replace` após download completo. A playlist local só muda quando todos os recursos foram obtidos. Falha de rede ou servidor mantém o último `state.json` válido e a mídia já presente. Um arquivo em cache é considerado válido apenas se existe e não está vazio; não há hash por mídia. Recursos órfãos são removidos após **24 horas contadas pelo `mtime`** do arquivo, desde que não sejam referenciados no manifesto atual.

O download de mídia usa o Bearer do Dispositivo no cabeçalho, sem token em URL.
Antes de exigir credenciais no Servidor, provisionar todos os Players e validar
download, Range, cache offline e heartbeat em um Raspberry de homologação.

O mesmo worker sequencial supervisiona o Firefox, sincroniza, envia heartbeat e busca comandos. O heartbeat inclui versão, playlist/conteúdo informado pelo navegador, estado de sincronização, CPU, RAM, temperatura, armazenamento, rede, watchdog e até 40 eventos pendentes de estabilidade; o servidor responde com IDs aceitos para retirada da fila. O Player pode receber `sync_now`, `restart_browser`, `clear_cache` (limpa `.part`), `diagnose`, `update_player`, `restart_player` e `reboot`. Esses comandos dependem do contrato e das autorizações do servidor; não devem ser testados em Dispositivos reais durante uma revisão do repositório.

## Atualização e estabilidade

O atualizador baixa a release para `packages/`, valida o SHA-256 fornecido no comando, extrai, salva backup em `backups/`, copia uma lista fixa de arquivos, chama `apply_services.sh`, grava `last_update.json` e agenda reinício do serviço de usuário. Em exceções durante esse fluxo, tenta restaurar a mesma lista do backup. **`stability_agent.py` não participa da lista de cópia nem de rollback atual.** Não há checagem de saúde após reinício nem rollback automático por falha posterior. O instalador inicial também copia o pacote inteiro, mas seu backup anterior não inclui o agente.

`stability_agent.py` distingue novo `boot_id` de reinício de processo quando há histórico anterior, registra inícios do navegador e guarda eventos pendentes em `stability_events.json` até confirmação do servidor. O timer de sistema executa o watchdog de rede aproximadamente a cada minuto; ele testa gateway/servidor, tenta reconexão após limiar configurado e pode reiniciar a máquina após falhas prolongadas e cooldown. Seus contadores ficam em `/var/lib/digital-signage/network-watchdog.json`.

## Configuração, empacotamento e limites

`services/player.env.example` contém apenas valores de exemplo. Uma instalação real deve gerar `player.env` **fora do Git**, com token individual e permissões restritas. Não incluir `player.env`, `state.json`, `stability_events.json`, `last_update.json`, mídias, logs, cache, `packages/`, `backups/` ou perfil do Firefox em commits ou pacotes de código-fonte. Para uma release, usar um processo controlado que inclua explicitamente os arquivos obrigatórios e verifique os serviços, permissões e SHA-256; esta integração não gera pacote nem instala serviços.

**Não execute esta árvore diretamente a partir de `player/` no monorepo esperando equivalência com a instalação atual.** O código e os serviços apontam para `~/DigitalSignagePlayer`, `/opt/digital-signage-player` e `/etc/digital-signage`. O instalador assume a estrutura anterior. A versão do código diverge do manifesto, há nomes internos `chromium_*` embora o executável procurado seja Firefox, o worker pode atrasar heartbeat/comandos durante downloads, e o atualizador omite o agente de estabilidade. Revisar esses pontos, o tratamento do token em argumentos do atualizador e a autenticação do heartbeat no servidor em tarefas separadas, com ambiente isolado. A descrição detalhada do contrato e do inventário está em `docs/arquitetura/PLAYER_ATUAL.md`.
