# Inventário individual da origem do Player

Origem: pasta original externa do projeto Player, analisada antes da integração ao monorepo. Cada caminho abaixo é relativo a essa origem. A/B/C são os únicos arquivos copiados, byte a byte, para o mesmo caminho sob `player/`. Arquivos D/E/F/G ficaram na origem e não entraram no Git.

Categorias: **A** código-fonte obrigatório; **B** infraestrutura obrigatória; **C** documentação/configuração segura; **D** histórico/variante; **E** gerado em execução; **F** sensível; **G** cache/mídia/log/pacote/backup.

| Caminho original (relativo à origem) | Categoria | Copiar / destino | Justificativa, dependências e referências |
| --- | --- | --- | --- |
| `__pycache__/local_player_firefox_hw.cpython-311.pyc` | E | Não: — | Bytecode Python gerado ao importar/executar; regenerável. |
| `__pycache__/local_player_firefox.cpython-311.pyc` | E | Não: — | Bytecode Python gerado ao importar/executar; regenerável. |
| `__pycache__/local_player.cpython-311.pyc` | E | Não: — | Bytecode Python gerado ao importar/executar; regenerável. |
| `__pycache__/stability_agent.cpython-311.pyc` | E | Não: — | Bytecode Python gerado ao importar/executar; regenerável. |
| `backups/20261005-164549/local_player.py` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-164549/network_watchdog.py` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-164549/player_local.html` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-164549/player_updater.py` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-164549/scripts/apply_services.sh` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-164549/services/digital-signage-network-watchdog.service` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-164549/services/digital-signage-network-watchdog.timer` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-164549/services/digital-signage-player.service` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-164549/services/player.env.example` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-164556/local_player.py` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-164556/network_watchdog.py` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-164556/player_local.html` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-164556/player_updater.py` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-164556/scripts/apply_services.sh` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-164556/services/digital-signage-network-watchdog.service` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-164556/services/digital-signage-network-watchdog.timer` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-164556/services/digital-signage-player.service` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-164556/services/player.env.example` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-164557/local_player.py` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-164557/network_watchdog.py` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-164557/player_local.html` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-164557/player_updater.py` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-164557/scripts/apply_services.sh` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-164557/services/digital-signage-network-watchdog.service` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-164557/services/digital-signage-network-watchdog.timer` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-164557/services/digital-signage-player.service` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-164557/services/player.env.example` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-170148/local_player.py` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-170148/network_watchdog.py` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-170148/player_local.html` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-170148/player_updater.py` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-170148/scripts/apply_services.sh` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-170148/services/digital-signage-network-watchdog.service` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-170148/services/digital-signage-network-watchdog.timer` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-170148/services/digital-signage-player.service` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `backups/20261005-170148/services/player.env.example` | G | Não: — | Snapshot de rollback; gerado por player_updater.py. Não é fonte da release atual. |
| `diagnose.sh` | D | Não: — | Diagnóstico histórico; consulta processo/log Chromium, enquanto a entrada atual usa Firefox. |
| `DigitalSignagePlayerAtual13-10.zip` | G | Não: — | Pacote completo gerado; não é fonte versionável. |
| `install_player.sh` | B | Sim: `player/install_player.sh` | Provisiona, valida release e instala o serviço em ~/DigitalSignagePlayer. |
| `last_update.json` | E | Não: — | Registro de atualização/backup gravado por player_updater.py. |
| `local_player_firefox_hw.py` | D | Não: — | Variante de teste Firefox; sem referência no serviço, instalador ou updater atual. |
| `local_player_firefox.py` | D | Não: — | Variante de teste Firefox; sem referência no serviço, instalador ou updater atual. |
| `local_player_firefox.py.BKP_TEST_20261007_162858` | D | Não: — | Cópia histórica de experimento; não referenciada por serviço/instalador/updater atuais. |
| `local_player.py` | A | Sim: `player/local_player.py` | Entrada de digital-signage-player.service; importa stability_agent e requer player_local.html. |
| `local_player.py.BKP_CHROMIUM_20261007_162858` | D | Não: — | Cópia histórica de experimento; não referenciada por serviço/instalador/updater atuais. |
| `local_player.py.BKP_PRE_WATCHDOG_TEST_20261006` | D | Não: — | Cópia histórica de experimento; não referenciada por serviço/instalador/updater atuais. |
| `logs/chromium-v3.log` | G | Não: — | Log/resultado de diagnóstico local; referências em local_player.py ou diagnose.sh; pode conter dados operacionais. |
| `logs/firefox-hw-test.log` | G | Não: — | Log/resultado de diagnóstico local; referências em local_player.py ou diagnose.sh; pode conter dados operacionais. |
| `logs/firefox-test.log` | G | Não: — | Log/resultado de diagnóstico local; referências em local_player.py ou diagnose.sh; pode conter dados operacionais. |
| `logs/player-firefox-hw-test.log` | G | Não: — | Log/resultado de diagnóstico local; referências em local_player.py ou diagnose.sh; pode conter dados operacionais. |
| `logs/player-firefox-test.log` | G | Não: — | Log/resultado de diagnóstico local; referências em local_player.py ou diagnose.sh; pode conter dados operacionais. |
| `logs/video18-decode-check.txt` | G | Não: — | Log/resultado de diagnóstico local; referências em local_player.py ou diagnose.sh; pode conter dados operacionais. |
| `logs/watchdog-90s-test.txt` | G | Não: — | Log/resultado de diagnóstico local; referências em local_player.py ou diagnose.sh; pode conter dados operacionais. |
| `logs/watchdog-90s-test.txt^C` | G | Não: — | Log/resultado de diagnóstico local; referências em local_player.py ou diagnose.sh; pode conter dados operacionais. |
| `manifest.json` | C | Sim: `player/manifest.json` | Manifesto original sem segredo; versão 3.2.1-stable difere da entrada atual. |
| `media/uploads/3e40b7927ada4821be9ee55ea0133003.png` | G | Não: — | Mídia sincronizada; /media e cache em local_player.py; dado corporativo. |
| `media/uploads/7c9ac071cfc847e0aa58b9c8b18960c7.png` | G | Não: — | Mídia sincronizada; /media e cache em local_player.py; dado corporativo. |
| `media/uploads/9952d42e1acc48948398768828420b4a.png` | G | Não: — | Mídia sincronizada; /media e cache em local_player.py; dado corporativo. |
| `media/uploads/bc23b71c541d4c55ade05ed39867e0f2.png` | G | Não: — | Mídia sincronizada; /media e cache em local_player.py; dado corporativo. |
| `network_watchdog.py` | A | Sim: `player/network_watchdog.py` | Executado pelo serviço systemd do watchdog; usa env e /var/lib/digital-signage. |
| `packages/extract-1791229556/diagnose.sh` | G | Não: — | ZIP/extração de release; gerado/consumido por player_updater.py e install_player.sh. |
| `packages/extract-1791229556/install_player.sh` | G | Não: — | ZIP/extração de release; gerado/consumido por player_updater.py e install_player.sh. |
| `packages/extract-1791229556/local_player.py` | G | Não: — | ZIP/extração de release; gerado/consumido por player_updater.py e install_player.sh. |
| `packages/extract-1791229556/manifest.json` | G | Não: — | ZIP/extração de release; gerado/consumido por player_updater.py e install_player.sh. |
| `packages/extract-1791229556/network_watchdog.py` | G | Não: — | ZIP/extração de release; gerado/consumido por player_updater.py e install_player.sh. |
| `packages/extract-1791229556/player_local.html` | G | Não: — | ZIP/extração de release; gerado/consumido por player_updater.py e install_player.sh. |
| `packages/extract-1791229556/player_updater.py` | G | Não: — | ZIP/extração de release; gerado/consumido por player_updater.py e install_player.sh. |
| `packages/extract-1791229556/README_PLAYER_v3_2.md` | G | Não: — | ZIP/extração de release; gerado/consumido por player_updater.py e install_player.sh. |
| `packages/extract-1791229556/scripts/apply_services.sh` | G | Não: — | ZIP/extração de release; gerado/consumido por player_updater.py e install_player.sh. |
| `packages/extract-1791229556/services/digital-signage-network-watchdog.service` | G | Não: — | ZIP/extração de release; gerado/consumido por player_updater.py e install_player.sh. |
| `packages/extract-1791229556/services/digital-signage-network-watchdog.timer` | G | Não: — | ZIP/extração de release; gerado/consumido por player_updater.py e install_player.sh. |
| `packages/extract-1791229556/services/digital-signage-player.service` | G | Não: — | ZIP/extração de release; gerado/consumido por player_updater.py e install_player.sh. |
| `packages/extract-1791229556/services/player.env.example` | G | Não: — | ZIP/extração de release; gerado/consumido por player_updater.py e install_player.sh. |
| `packages/release-3.2.0-stable.zip` | G | Não: — | ZIP/extração de release; gerado/consumido por player_updater.py e install_player.sh. |
| `packages/release-3.2.1-stable.zip` | G | Não: — | ZIP/extração de release; gerado/consumido por player_updater.py e install_player.sh. |
| `player_local.html` | A | Sim: `player/player_local.html` | HTML servido por local_player.py; consulta /state e envia /playback. |
| `player_updater.py` | A | Sim: `player/player_updater.py` | Invocado por local_player.py em update_player; usa services/ e scripts/. |
| `player.env` | F | Não: — | Configuração real com token do Dispositivo; serviço e scripts a carregam. |
| `README_PLAYER_v3_2.md` | D | Não: — | Documento da release 3.2.1/Chromium; não descreve integralmente a entrada atual. |
| `scripts/apply_services.sh` | B | Sim: `player/scripts/apply_services.sh` | Chamado por instalador/updater; instala serviços e timer no Linux. |
| `services/digital-signage-network-watchdog.service` | B | Sim: `player/services/digital-signage-network-watchdog.service` | Unidade systemd copiada por apply_services.sh; aponta à estrutura antiga. |
| `services/digital-signage-network-watchdog.timer` | B | Sim: `player/services/digital-signage-network-watchdog.timer` | Unidade systemd copiada por apply_services.sh; aponta à estrutura antiga. |
| `services/digital-signage-player.service` | B | Sim: `player/services/digital-signage-player.service` | Unidade systemd copiada por apply_services.sh; aponta à estrutura antiga. |
| `services/player.env.example` | C | Sim: `player/services/player.env.example` | Exemplo sem credencial real; modelo para player.env. |
| `stability_agent.py` | A | Sim: `player/stability_agent.py` | Import obrigatório de local_player.py; persiste eventos de estabilidade. |
| `stability_events.json` | F | Não: — | Sessão/boot e fila de eventos persistidos por stability_agent.py. |
| `state.json` | F | Não: — | Estado persistido com playlist, URLs e identificadores; lido por local_player.py. |
