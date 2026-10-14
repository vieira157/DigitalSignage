# Riscos conhecidos

Registro anterior ao redesign, em 14/10/2026. **Nenhum risco é corrigido por esta documentação.** Alterações técnicas devem ser planejadas separadamente das alterações visuais. Evidências são estáticas; consequências indicam caminhos possíveis, não incidentes comprovados.

## Escala e localização

- **Crítico:** comprometimento amplo imediato ou perda grave inevitável demonstrada no escopo observado.
- **Alto:** falha de autorização, perda de artefato/dado ou interrupção relevante possível por caminho concreto.
- **Médio:** limitação de proteção, integridade, rastreabilidade ou operação que requer condições adicionais.
- **Baixo:** inconsistência de apresentação/documentação com impacto limitado.

Nenhum achado foi classificado como Crítico apenas por hipótese. A prioridade deve ser revista à luz da exposição de rede e da implantação real. Arquivos do servidor são relativos à raiz; arquivos do Player são da cópia externa fornecida. Funções delimitam as regiões afetadas.

## R01 — Heartbeat sem autenticação do Dispositivo — Alto

- **Descrição/evidência:** `heartbeat` grava dados sem chamar `require_device_auth`. O middleware não exige sessão nessa API; o comentário de autenticação no corpo não corresponde a uma verificação executada.
- **Arquivos/regiões:** [app.py](../../app.py), `heartbeat`, `security_middleware`, `require_device_auth`.
- **Consequência possível:** envio de telemetria falsa para `tv_id` conhecido, alteração de presença, saúde e histórico se a rota estiver acessível.
- **Área impactada:** autenticação de Dispositivo, monitoramento, estabilidade.
- **Recomendação geral:** planejar autenticação consistente com a política de compatibilidade dos Players, sem modificar o protocolo como efeito colateral visual.
- **Validação necessária:** integração isolada com token ausente, incorreto e correto, Tela sem token e flag de compatibilidade; confirmar ausência de escrita quando rejeitado; homologar Player real.

## R02 — Token do Dispositivo exigido na manutenção administrativa — Alto

- **Descrição/evidência:** `player_maintenance` valida perfil/Tela e depois exige `require_device_auth`, embora seja formulário do painel autenticado por sessão.
- **Arquivos/regiões:** `app.py`, `player_maintenance`; `templates/player_detail.html`.
- **Consequência possível:** HTTP 401 para manutenção de Tela provisionada, mesmo com usuário autorizado. As Telas do banco local examinado tinham hash de token.
- **Área impactada:** operação e manutenção.
- **Recomendação geral:** definir separação de autenticação administrativa e de Dispositivo em correção própria.
- **Validação necessária:** matriz de perfis e concessões, com Tela provisionada, confirmando que usuário não autorizado continua impedido.

## R03 — Reenvio de versão pode apagar pacote de release — Alto

- **Descrição/evidência:** `releases_upload` escreve o arquivo de destino antes do INSERT; em conflito de unicidade, o tratamento de erro remove esse destino.
- **Arquivos/regiões:** `app.py`, `releases_upload`; diretório operacional `releases/`.
- **Consequência possível:** perda do ZIP original de versão já cadastrada e falha de downloads/instalações posteriores.
- **Área impactada:** distribuição e integridade de releases.
- **Recomendação geral:** planejar validação de duplicidade e promoção atômica do artefato, preservando pacote existente em falha.
- **Validação necessária:** reenvio duplicado em ambiente isolado, comparação do conteúdo original após rejeição, erros de banco e disco.

## R04 — Publicação automática de rascunhos na inicialização — Alto

- **Descrição/evidência:** migração em `init_db` encontra playlists com itens e sem publicação e cria versão publicada 1; roda em toda importação, sem marco específico que limite a dados antigos.
- **Arquivos/regiões:** `app.py`, bloco `legacy_playlists` de `init_db`.
- **Consequência possível:** rascunho novo pode virar publicação após reinício, sem revisão explícita. Não significa necessariamente que já esteja atribuído a uma Tela.
- **Área impactada:** fluxo editorial e publicação.
- **Recomendação geral:** planejar migração versionada e distinguir legado de rascunho novo; não alterar a regra silenciosamente no redesign.
- **Validação necessária:** banco isolado com rascunho novo, publicação existente e dados legados; reinicialização e conferência de versões/atribuições.

## R05 — Sincronização bloqueia outras tarefas do loop — Alto

- **Descrição/evidência:** `background_worker` executa sync, heartbeat, comandos e supervisão sequencialmente. `sync_once` pode fazer downloads com timeout e retries.
- **Arquivos/regiões:** Player externo `local_player.py`, `background_worker`, `sync_once`, `download_resource`.
- **Consequência possível:** atrasos de heartbeat/comandos e de recuperação do navegador, com falso offline durante transferência longa.
- **Área impactada:** comunicação, reprodução e monitoramento.
- **Recomendação geral:** planejar isolamento das tarefas bloqueantes, preservando consistência de estado e cache.
- **Validação necessária:** rede lenta/indisponível, arquivo grande, retries e medição de heartbeat e supervisão em Raspberry de homologação.

## R06 — Atualizador não copia todos os módulos necessários — Alto

- **Descrição/evidência:** a lista de cópia/backup/rollback em `player_updater.py` não inclui `stability_agent.py`, importado por `local_player.py` fornecido.
- **Arquivos/regiões:** Player externo `player_updater.py`, `main`; `local_player.py`, imports.
- **Consequência possível:** release futura dependente do módulo não será instalada integralmente; em Dispositivo sem o arquivo, o Player pode falhar ao iniciar. A existência do arquivo na cópia examinada não elimina o risco de upgrade.
- **Área impactada:** atualização e compatibilidade.
- **Recomendação geral:** definir conteúdo e dependências de pacote, abrangência de instalação e rollback.
- **Validação necessária:** atualização de instalação antiga sem o módulo, instalação limpa, pacote incompleto e rollback.

## R07 — Helper privilegiado em pasta do usuário — Alto

- **Descrição/evidência:** bootstrap permite execução como root sem senha de `scripts/apply_services.sh`, dentro da árvore atribuída ao usuário do Player.
- **Arquivos/regiões:** [bootstrap/install_player.sh](../../bootstrap/install_player.sh), criação de sudoers/chown; Player externo `scripts/apply_services.sh`.
- **Consequência possível:** modificação do helper por quem controla essa conta pode permitir execução privilegiada. A exploração real depende das permissões implantadas, não inspecionadas no Raspberry.
- **Área impactada:** privilégio do sistema operacional e atualização.
- **Recomendação geral:** revisar propriedade, localização e limites dos helpers privilegiados em tarefa de segurança.
- **Validação necessária:** inspeção de permissões/sudoers em homologação, usuário sem privilégios e cadeia de atualização, sem experimentação em produção.

## R08 — Arquivamento afeta publicações existentes — Alto

- **Descrição/evidência:** `delete_content_safe` marca `archived_at`; `snapshot_to_player_playlist` e `player_cache_manifest` excluem IDs arquivados mesmo se constarem em snapshots já publicados.
- **Arquivos/regiões:** `app.py`, funções citadas; `templates/gallery.html`.
- **Consequência possível:** retirada de mídia da reprodução sem nova publicação, apesar de o arquivo físico e histórico continuarem existentes.
- **Área impactada:** biblioteca, publicação e reprodução.
- **Recomendação geral:** preservar aviso explícito e planejar qualquer mudança dessa regra com análise de impacto; não tratar arquivamento como ação meramente visual.
- **Validação necessária:** mídia compartilhada em publicação/agenda, arquivamento, restauração, autorização e sincronização de Player.

## R09 — Divergência de versões e retenção — Médio

- **Descrição/evidência:** código fornecido declara `3.2.2-stability-preview`, manifesto declara `3.2.1-stable`; pacote separado `3.2.3-cache-retention-7d` não equivale ao código local. Este mantém 24 h por `mtime`.
- **Arquivos/regiões:** Player externo `local_player.py`, `manifest.json`, `cleanup_orphan_cache`; pacotes operacionais de release.
- **Consequência possível:** recomendação/diagnóstico confusos, expectativa errada de retenção e instalação de versão diferente da pretendida.
- **Área impactada:** gestão de versões e cache.
- **Recomendação geral:** estabelecer correspondência verificável entre código, manifesto, pacote e versão reportada antes de distribuir.
- **Validação necessária:** inspeção de conteúdo/hash de cada artefato e versão efetivamente executada; cenários de retenção desde desuso versus idade do arquivo.

## R10 — Importação executa operações persistentes — Alto

- **Descrição/evidência:** `app.py` cria diretórios, executa `init_db`, cadastra/migra dados e inicializa schema de estabilidade no nível do módulo. Primeiro administrador pode gerar arquivo de credenciais.
- **Arquivos/regiões:** `app.py`, configuração inicial, `init_db`, `ensure_initial_admin` e chamada de schema de estabilidade.
- **Consequência possível:** inspeção por importação ou ferramenta de desenvolvimento modifica banco/arquivos e pode publicar rascunhos conforme R04.
- **Área impactada:** manutenção, migração, desenvolvimento e testes.
- **Recomendação geral:** separar inspeção estática de execução e planejar inicialização explícita/isolamento em tarefa específica.
- **Validação necessária:** rastrear efeitos de importação em ambiente descartável; verificar caminhos de todos os arquivos gerados, não apenas do banco.

## R11 — Dependências sem versões fixadas — Médio

- **Descrição/evidência:** `requirements.txt` lista dependências sem pinagem; a disponibilidade de timezone e ferramentas externas também influencia a inicialização/validação.
- **Arquivos/regiões:** [requirements.txt](../../requirements.txt), `app.py` imports/`ZoneInfo`/`probe_media_metadata`; exemplos de implantação.
- **Consequência possível:** reinstalações divergentes ou quebra por combinação diferente de dependências; validação parcial sem ffprobe.
- **Área impactada:** implantação e validação de mídia.
- **Recomendação geral:** estabelecer ambiente reproduzível e dependências externas documentadas, sem atualizar bibliotecas junto com layout.
- **Validação necessária:** instalação limpa isolada e compatibilidade com versões escolhidas; upload com/sem ffprobe.

## R12 — Cobertura limitada e testes com efeitos colaterais — Médio

- **Descrição/evidência:** suíte identificada concentra-se em permissões. Não foi executada. Usa banco temporário, mas importa `app.py`; `CREDENTIALS_FILE` continua apontando à raiz real. `httpx` não foi localizado no ambiente inspecionado.
- **Arquivos/regiões:** [tests/test_permissions.py](../../tests/test_permissions.py), configuração/importação; `app.py`, `CREDENTIALS_FILE` e `ensure_initial_admin`.
- **Consequência possível:** testes podem criar/sobrescrever arquivo de credencial inicial; regressões de heartbeat, release, sincronização e reprodução não estão cobertas pela evidência disponível.
- **Área impactada:** qualidade e segurança do ambiente de desenvolvimento.
- **Recomendação geral:** isolar todos os destinos persistentes e expandir validações dos contratos críticos em tarefa própria.
- **Validação necessária:** execução em workspace descartável, verificação de escritas e cenários representativos; não executar diretamente contra produção.

## R13 — Mídias públicas fora do RBAC — Médio

- **Descrição/evidência:** montagem `/media` e prefixo público no middleware. O isolamento de Galeria/Telas não protege o acesso direto ao arquivo.
- **Arquivos/regiões:** `app.py`, `app.mount`, `PUBLIC_PREFIXES`, `security_middleware`.
- **Consequência possível:** acesso por URL conhecida a mídia, inclusive arquivada cujo arquivo foi preservado, conforme exposição de rede.
- **Área impactada:** confidencialidade de conteúdo.
- **Recomendação geral:** avaliar política de acesso com compatibilidade de cache e Players; não bloquear mídias implicitamente durante redesign.
- **Validação necessária:** matriz de acesso direto, sessão e token, reprodução local/legada e cache em ambiente isolado.

## R14 — Auditoria não cobre todas as mutações — Médio

- **Descrição/evidência:** chamadas de `audit_event` abrangem login/logout, usuários, releases, credenciais, comandos e upload de playlist; não cobrem uniformemente publicação e alterações de agendamento.
- **Arquivos/regiões:** `app.py`, `audit_event`, `publish_playlist_draft`, `create_schedule`, `toggle_schedule`, `delete_schedule`.
- **Consequência possível:** impossibilidade de reconstituir autoria/contexto completo de determinada mudança apenas pela tela Auditoria.
- **Área impactada:** rastreabilidade operacional.
- **Recomendação geral:** definir eventos auditáveis e retenção sem prometer cobertura total no novo layout.
- **Validação necessária:** matriz ação/evento, identidade, alvo e falhas; conferir ausência de segredos nos detalhes registrados.

## R15 — Disponibilidade não confirma Display físico — Médio

- **Descrição/evidência:** não há entidade ou medição independente do Display; disponibilidade usa transições do monitor e amostras recebidas.
- **Arquivos/regiões:** `stability_monitor.py`, `offline_intervals`, `summarize`; `app.py`, `evaluate_tv_health`; telemetria do Player.
- **Consequência possível:** operador interpretar online/disponibilidade como prova de imagem exibida, apesar de Display desligado, falha de entrada ou lacuna de coleta.
- **Área impactada:** interpretação operacional e indicadores.
- **Recomendação geral:** manter ressalva explícita e distinguir comunicação, reprodução reportada e Display; qualquer confirmação física exige projeto específico.
- **Validação necessária:** revisão de linguagem e cenários controlados de Display desligado, cabo/entrada e servidor indisponível.

## R16 — Rollback limitado à instalação — Médio

- **Descrição/evidência:** updater restaura backup em exceção, mas agenda restart sem validar posteriormente que a nova versão inicia e reproduz. Pode informar conclusão com arquivos instalados e restart não agendado.
- **Arquivos/regiões:** Player externo `player_updater.py`, `main`, `restart_service`.
- **Consequência possível:** pacote sintaticamente inválido ou incompatível deixar Player indisponível sem rollback automático pós-reinício.
- **Área impactada:** atualização e continuidade.
- **Recomendação geral:** definir critérios de sucesso e recuperação pós-restart antes de prometer atualização resiliente.
- **Validação necessária:** pacote inválido, dependência ausente, falha de systemd, versão reportada e retomada da reprodução.

## R17 — Concorrência, SQLite e monitor por processo — Médio

- **Descrição/evidência:** conexão direta SQLite, schema na importação e thread de monitor com trava local ao processo. Não foi encontrada ativação explícita de `PRAGMA foreign_keys` em `get_db`.
- **Arquivos/regiões:** `app.py`, `get_db`, `init_db`, `start_monitor_worker`; exemplo systemd com um worker.
- **Consequência possível:** monitor duplicado com múltiplos workers, contenção de escrita e integridade dependente das rotinas de aplicação.
- **Área impactada:** persistência e escalabilidade.
- **Recomendação geral:** manter implantação compatível e planejar concorrência/migrações antes de escalar.
- **Validação necessária:** carga, concorrência, integridade referencial e reinícios em ambiente isolado.

## R18 — Credenciais e transporte dependem da implantação — Médio

- **Descrição/evidência:** geração de arquivo de primeiro acesso, token em `player.env` e passagem de token por argumento ao updater; exemplos HTTP. Não foi confirmada proteção TLS/rede efetiva.
- **Arquivos/regiões:** `app.py`, `ensure_initial_admin`; Player externo `execute_command`/`player_updater.py`; bootstrap.
- **Consequência possível:** exposição por arquivo, inspeção de processos ou transporte sem criptografia, conforme permissões e rede reais.
- **Área impactada:** credenciais e implantação.
- **Recomendação geral:** revisar armazenamento, acesso e transporte em tarefa de segurança. O `.gitignore` protege contra inclusão acidental, não remove arquivos locais nem prova proteção em runtime.
- **Validação necessária:** inspeção autorizada de permissões, processo e TLS sem registrar valores sensíveis.

## R19 — Nomes de navegador não correspondem à implementação — Baixo

- **Descrição/evidência:** rótulos/funções mencionam Chromium, enquanto o executável do Player fornecido é Firefox. A configuração de hardware decode também convive com preferências Firefox fixadas no código.
- **Arquivos/regiões:** Player externo `chromium_executable`, `start_chromium`; templates de Telas/detalhes e mensagens do servidor.
- **Consequência possível:** interpretação incorreta de diagnóstico e de configuração aplicada.
- **Área impactada:** nomenclatura e suporte.
- **Recomendação geral:** alinhar linguagem mediante confirmação da versão em uso, sem renomear contratos técnicos implicitamente.
- **Validação necessária:** versão/executável efetivo e revisão das mensagens, campos e preferências aplicadas.

## Tratamento futuro

Cada correção deve ter escopo próprio, evidência, plano de compatibilidade, validação e possibilidade de recuperação. A branch `main` continua referência estável. Redesign não autoriza mudança silenciosa em autorização, schema, publicação, cache ou comunicação com os Dispositivos.
