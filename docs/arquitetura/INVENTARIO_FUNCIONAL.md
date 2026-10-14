# Inventário funcional atual

Referência de 14/10/2026, baseada em análise estática. Descreve funcionalidades existentes, sem criar módulos para corresponder aos mockups. [Arquitetura e catálogo de rotas](ARQUITETURA_ATUAL.md) · [Riscos](RISCOS_CONHECIDOS.md) · [Vocabulário](VOCABULARIO_DO_PRODUTO.md).

## Como ler

Os campos abaixo registram objetivo, dados, ações, autorização, endpoints, scripts, atualização, dependências, estados e itens a preservar por página/fluxo. “Não confirmado” significa que a análise anterior não demonstrou o comportamento; não autoriza inventá-lo. Estados de erro descritos no backend não implicam tratamento visual específico em todos os templates.

Perfis: V = Visualizador; E = Editor; O = Operador; A = Administrador. A hierarquia é cumulativa, com restrições de recurso adicionais. Administrador possui acesso a todas as Telas. V/E/O precisam de concessões explícitas. Edição de playlist compartilhada exige acesso a todas as Telas envolvidas, incluindo agendamentos; alteração de mídia exige autoria e vínculos autorizados, salvo A.

O JavaScript comum de `base_admin.html` oferece mensagens/toasts, leitura de mensagem transitória em `sessionStorage` e indicação de processamento em formulários. Isso se aplica às páginas que herdam a base, não aos templates independentes. Não há bundle externo próprio identificado. Uma página sem polling confirmado não deve ser descrita como “tempo real”.

## 1. Login

- **Nome atual, rota e template:** Login; `GET /login`; `login.html`, independente da base.
- **Objetivo e dados exibidos:** autenticar acesso ao painel; formulário de usuário/senha, erro, destino `next` e versão do servidor. Não exibe valores armazenados de senha.
- **Ações e perfis:** enviar credenciais por `POST /login`; logout por `GET /logout`. Login acessível sem sessão; entrada válida exige usuário ativo.
- **Restrições por Tela:** não há seleção de Tela no login; as concessões são aplicadas nos recursos após autenticar.
- **Endpoints, JavaScript e atualização:** formulário HTML; `/login` e `/logout`; sem JavaScript específico ou atualização periódica identificados.
- **Banco e Dispositivo:** `users`, `sessions`, `audit_log`; sem dependência de Dispositivo.
- **Vazio e erro:** campos obrigatórios; credenciais inválidas/inatividade geram mensagem de falha e redirecionamento. Sessão encerrada é comunicada pelo fluxo de logout.
- **Não perder:** validação no servidor, cookie de sessão, retorno a destino interno permitido, mensagens e impedimento de acesso sem sessão.
- **Evidência:** `login_page`, `login_submit`, `logout` em `app.py`.

## 2. Dashboard

- **Nome atual, rota e template:** Dashboard; `GET /admin`; `admin.html` → `base_admin.html`.
- **Objetivo e dados exibidos:** visão operacional: totais de Telas, online, com atenção, conteúdos, playlists e agendamentos; lista de problemas e eventos recentes.
- **Ações e perfis:** V/E/O/A autenticados; atalhos para Telas, Galeria, Playlists e Agendamentos. “Enviar conteúdo” navega para a Galeria; não é upload direto nessa página.
- **Restrições por Tela:** dados preparados conforme acesso do usuário; nomes e eventos de Telas não autorizadas não devem ser acrescentados pelo redesign.
- **Endpoints, JavaScript e atualização:** renderização em `/admin`, links de navegação e scripts comuns; não foi identificado polling próprio do dashboard. Não transplantar a atualização de três segundos de `/players` como se já existisse aqui.
- **Banco e Dispositivo:** Telas, estado/monitoramento, eventos, conteúdos, playlists, agendamentos e permissões; telemetria do Dispositivo é dependência indireta, sem contato direto do navegador do painel com o Raspberry.
- **Vazio e erro:** “Nenhum player exige atenção agora”, “Nenhum evento registrado ainda”; sessão ausente redireciona ao login. Não há estado visual específico de falha de consulta confirmado.
- **Não perder:** contagens reais e autorizadas, distinção online/atenção, eventos e atalhos existentes. Será a primeira página analisada no redesign.
- **Evidência:** `admin`; variáveis Jinja2 `stats`, `problem_tvs` e `recent_events`.

## 3. Galeria / Biblioteca

- **Nome atual, rota e template:** Galeria; `GET /gallery`; `gallery.html` → base.
- **Objetivo e dados exibidos:** administrar acervo ativo/arquivado; miniaturas, nomes, arquivos, tamanhos, tipos, validação técnica e uso em playlists/histórico.
- **Ações e perfis:** V/E/O/A consultam recursos permitidos; E/O/A enviam mídia, renomeiam, arquivam/restauram conforme autorização e adicionam mídia a playlists autorizadas. Seleção e arquivamento em lote existem.
- **Restrições por Tela:** visibilidade por autoria/uso autorizado; edição de mídia exige autoria e vínculos permitidos, salvo A. Destino playlist também passa por autorização própria.
- **Endpoints:** `POST /admin/upload`, `/admin/content/delete`, `/admin/content/{content_id}/restore`, `/admin/content/{content_id}/rename`, `/api/contents/delete-bulk`, `/admin/playlist/{playlist_id}/items`; prévia em `/preview/{content_id}` e arquivos em `/media/...`.
- **JavaScript e atualização:** filtros, seleção, modal, fila de uploads via `XMLHttpRequest`, progresso, `fetch` para lote e mensagens em `sessionStorage`; atualização após ações, sem polling periódico confirmado.
- **Banco e Dispositivo:** `contents`, `content_metadata`, itens, publicações/histórico e vínculos/permissões. Sem necessidade de Dispositivo online para editar; arquivamento afeta próxima sincronização.
- **Vazio e erro:** acervo/lista filtrada podem estar vazios; texto exato não consolidado nesta análise. Upload exibe falha de rede, cancelamento, incompatibilidade e validação parcial. Ausência de ffprobe não equivale a validação técnica completa.
- **Não perder:** ativos/arquivados, preservação física do arquivo, aviso de impacto nos Players, uso histórico, progresso real de upload e falhas parciais. Não acrescentar PDF/PPT como formatos atuais.
- **Evidência:** `gallery_page`, `prepare_uploaded_media`, `probe_media_metadata`, `delete_content_safe`, `can_access_content`.

## 4. Playlists e edição do rascunho

- **Nome atual, rota e template:** Playlists; `GET /playlists`; `playlists.html` → base. A edição está integrada à página; não há editor independente confirmado.
- **Objetivo e dados exibidos:** playlists e seus itens, miniaturas, ordem, duração, estado do rascunho, versão publicada e histórico; distribuição às Telas.
- **Ações e perfis:** V consulta playlists permitidas; E/O/A criam e editam rascunhos, enviam arquivos, adicionam da Galeria, ordenam, removem itens e restauram histórico conforme escopo. Publicação/atribuição requer O/A e é detalhada abaixo.
- **Restrições por Tela:** leitura exige vínculo permitido ou autoria de rascunho sem vínculo; edição de playlist compartilhada exige todas as Telas autorizadas. Mídia adicionada também deve ser acessível.
- **Endpoints:** `POST /admin/playlist`; `/admin/playlist/{playlist_id}/item` e `/items`; `/api/playlist/{playlist_id}/upload`; `/admin/playlist/item/{item_id}/delete`, `/move-up`, `/move-down`, `/duration`; `/api/playlist/item/{item_id}/duration`; `/api/playlist/{playlist_id}/reorder`, `/items/delete-bulk`; `/admin/playlist/{playlist_id}/delete` e `/restore/{version}`.
- **JavaScript e atualização:** busca, seleção múltipla, reordenação/arraste, ordenação, salvamento assíncrono e indicadores de salvar/salvo/erro; upload com progresso e inserção de itens no DOM. Não há polling periódico confirmado.
- **Banco e Dispositivo:** `playlists`, `playlist_items`, `contents`, metadados, publicações/histórico, atribuições, agendamentos e permissões. Rascunho pode ser editado sem Dispositivo online.
- **Vazio e erro:** playlist sem itens é possível; não pode ser publicada vazia. Falhas de ordem/duração/upload têm mensagens e estados de salvamento; a reordenação tenta restaurar ordem anterior quando falha. Upload pode ter resultados parciais.
- **Não perder:** rascunho separado da publicação, duração de imagem, vídeo até o fim, seleção, ordenação, progresso, tratamento de erro e histórico. Preservar identificação de itens usada no DOM e nas APIs.
- **Evidência:** `playlists_page`, funções de criação/edição, `reorder_playlist`, `restore_playlist_version`; scripts de `playlists.html`.

## 5. Revisão, publicação, histórico e atribuição

- **Nome atual, rota e template:** “Revisar e publicar”, histórico de versões e “Distribuição às TVs”; componentes de `/playlists`, em `playlists.html`. Não há página independente de Publicações.
- **Objetivo e dados exibidos:** comparar rascunho/publicação; versão atual/próxima, itens adicionados/removidos, mudanças de duração e ordem; versões históricas e atribuições.
- **Ações e perfis:** O/A publicam e atribuem; E/O/A restauram histórico ao rascunho. Consulta ao resumo exige acesso à playlist. Não presumir que todo botão visível autorize sua operação.
- **Restrições por Tela:** publicação exige autorização de edição da playlist compartilhada; atribuição exige acesso operacional à Tela e à playlist.
- **Endpoints:** `GET /api/playlist/{playlist_id}/publish-summary`; `POST /admin/playlist/{playlist_id}/publish`, `/restore/{version}`, `/admin/tv/{tv_id}/playlist`. `/admin/publish` é legado desativado e retorna 410.
- **JavaScript e atualização:** modal de revisão carregado por `fetch`; confirmação por formulário; retorno/redirecionamento após ação. Não existe acompanhamento global de distribuição por polling confirmado nessa página.
- **Banco e Dispositivo:** rascunho, `playlist_publications`, `playlist_publication_history`, `tv_playlists`, conteúdos e permissões. Dispositivo consome depois por sync; sucesso da publicação não comprova recebimento.
- **Vazio e erro:** rascunho vazio impede publicação; rascunho idêntico informa ausência de mudança; versão histórica inexistente retorna 404; falha no resumo gera mensagem.
- **Não perder:** revisão antes de publicar, versionamento, restauração para rascunho, atribuição somente de publicação e distinção publicado/sincronizado.
- **Evidência:** `playlist_publish_summary`, `publish_playlist_draft`, `restore_playlist_version`, `assign_playlist_to_tv`.

## 6. Pré-visualização de mídia e playlist

- **Nome atual, rotas e templates:** pré-visualização de conteúdo em `GET /preview/{content_id}` → `preview.html`; de playlist em `GET /playlist/{playlist_id}/preview` → `playlist_preview.html`. Templates independentes.
- **Objetivo e dados exibidos:** inspecionar mídia e sequência antes da publicação; conteúdo e itens preparados pelas funções de preview.
- **Ações e perfis:** V/E/O/A com acesso ao recurso podem abrir; trata-se de visualização, não publicação nem comando ao Dispositivo.
- **Restrições por Tela:** `require_content_access` ou `require_playlist_access` antes da renderização.
- **Endpoints, JavaScript e atualização:** GETs de preview e arquivos `/media/...`; apresentação/reprodução no navegador. Detalhes de todos os controles e timers não foram consolidados na análise anterior e exigem inspeção antes do redesign.
- **Banco e Dispositivo:** conteúdos, itens e permissões/vínculos; não depende de Raspberry online. Preview não prova compatibilidade de reprodução no hardware.
- **Vazio e erro:** recurso ausente/acesso negado são tratados no servidor; mensagens e fallback exatos para sequência vazia ou mídia indisponível devem ser conferidos no template antes da alteração.
- **Não perder:** autorização, renderização de imagens/vídeos, sequência/duração e independência da publicação efetiva.
- **Evidência:** `preview`, `preview_playlist` e templates correspondentes.

## 7. Agendamentos

- **Nome atual, rota e template:** Agendamentos; `GET /schedules`; `schedules.html` → base.
- **Objetivo e dados exibidos:** calendário semanal e lista de regras por Tela; playlist, dia, início/fim, habilitação e conflitos.
- **Ações e perfis:** V/E/O/A consultam escopo permitido; O/A criam, ativam/desativam e excluem regras.
- **Restrições por Tela:** criação exige acesso operacional à Tela e playlist; mutações existentes verificam acesso ao agendamento.
- **Endpoints:** `POST /admin/schedule`, `/admin/schedule/{schedule_id}/toggle` e `/delete`; filtro da listagem em `/schedules`.
- **JavaScript e atualização:** modal/formulário de criação; renderização do calendário pelo servidor; sem polling confirmado. Não confundir com scripts de conflito de templates históricos.
- **Banco e Dispositivo:** `schedules`, `tvs`, playlists/publicações e permissões. O servidor escolhe a regra durante consulta do Player; prefetch não garante troca offline por horário.
- **Vazio e erro:** “Sem regras” no calendário e ausência de agendamentos na lista; horário/dia inválido, recurso não encontrado e sobreposição bloqueiam operações.
- **Não perder:** precedência sobre playlist padrão, dia inteiro, passagem da meia-noite, calendário semanal e validação de conflito na criação e reativação.
- **Evidência:** `schedules_page`, `get_active_schedule`, `schedule_parts`, `schedules_overlap`, `create_schedule`, `toggle_schedule`.

## 8. TVs / Players — Telas

- **Nome atual, rota e template:** TVs / Players; `GET /players`; `players.html` → base. Nome recomendado: Telas.
- **Objetivo e dados exibidos:** central de Telas com totais e cartões: nome, localidade, estado/motivo, playlist/conteúdo, progresso, versão, sync, navegador, IP, último contato e métricas disponíveis.
- **Ações e perfis:** V/E/O/A consultam escopo; filtros, ordenação, agrupamento, somente problemas, atualização e acesso aos detalhes. A cadastra Tela e acessa configurações globais.
- **Restrições por Tela:** `/api/tvs/status` filtra pela lista autorizada. Não reconstruir dados de Telas proibidas no cliente.
- **Endpoints:** `GET /api/tvs/status`; `POST /admin/tv`; navegação para detalhes, estabilidade e configurações.
- **JavaScript e atualização:** `loadStatus()` consulta JSON a cada três segundos, renderiza métricas/cartões e reaplica filtros; botão de atualizar. Erros de fetch são mostrados por toast.
- **Banco e Dispositivo:** `tvs`, runtime, monitor, manutenção, playlists e permissões; heartbeat determina atualidade. Consulta de status também pode gravar eventos, portanto não é estritamente somente leitura no servidor.
- **Vazio e erro:** lista sem Telas acessíveis/sem correspondência de filtros deve ser preservada; texto exato não consolidado. Estados `ok`, `warning`, `error`, `offline` e `maintenance`; falha HTTP não deve ser confundida com todas as Telas offline.
- **Não perder:** filtros, atualização, escopo, motivo do estado, progresso, distinção de manutenção e acesso aos detalhes. Não dividir em dois cadastros TV/Player.
- **Evidência:** `players_page`, `tvs_status`, `create_tv`, `evaluate_tv_health` e script de `players.html`.

## 9. Detalhes técnicos e histórico da Tela

- **Nome atual, rota e template:** página com nome da Tela; `GET /players/{tv_id}`; `player_detail.html` → base.
- **Objetivo e dados exibidos:** estado, versão/recomendação, playlist, conteúdo, sync, contato, diagnóstico, telemetria e histórico filtrável/paginado, incluindo duração de indisponibilidade.
- **Ações e perfis:** V/E/O/A consultam e exportam eventos; O/A solicitam manutenção, sync, reinício do navegador/Player, limpeza temporária, diagnóstico e reboot; A atualiza Player, provisiona e rotaciona token.
- **Restrições por Tela:** acesso à Tela e perfil mínimo no backend. Há defeito confirmado no fluxo de manutenção: exige também token do Dispositivo, que o formulário administrativo normal não fornece.
- **Endpoints:** `GET /players/{tv_id}/events.csv`; `POST /players/{tv_id}/maintenance`, `/command`, `/device-token` e `/provision-code`; link para `/players/{tv_id}/stability`.
- **JavaScript e atualização:** formulários, controles e script compartilhado; não foi confirmado polling equivalente ao da central. Não anunciar dados continuamente atualizados sem verificar o código específico.
- **Banco e Dispositivo:** Tela, runtime, eventos, manutenção, releases/comandos e permissões. Ações entram em fila; precisam do Player para execução/ACK.
- **Vazio e erro:** métricas ausentes indicam espera por telemetria; histórico pode estar vazio; erros 403/404, comando inválido e release ausente. Manutenção pode retornar 401 pelo defeito documentado.
- **Não perder:** exportação CSV, filtros/paginação, dados indisponíveis distinguíveis de zero, manutenção e confirmações das ações sensíveis. Não representar “comando enviado” como execução comprovada.
- **Evidência:** `player_details_page`, `player_events_csv`, `player_maintenance`, `player_command`.

## 10. Configurações de monitoramento

- **Nome atual, rota e template:** configurações da central; `GET /players/settings`; `monitor_settings.html` → base. Monitoramento também está integrado às páginas de Telas, não é um módulo autônomo confirmado.
- **Objetivo e dados exibidos:** parâmetros globais de versão recomendada, offline, aviso de sincronização e espaço livre.
- **Ações e perfis:** somente A consulta/altera; `POST /players/settings`.
- **Restrições por Tela:** configuração global; não é concessão individual de acesso.
- **Endpoints, JavaScript e atualização:** GET/POST de settings e comportamento compartilhado da base; sem atualização automática confirmada.
- **Banco e Dispositivo:** `tv_monitor_settings`; avaliação usa heartbeat/runtime e manutenção das Telas.
- **Vazio e erro:** defaults do monitor; acesso não administrador é bloqueado. Mensagens específicas de limites inválidos precisam ser conferidas antes de alterar o formulário.
- **Não perder:** parâmetros reais, exclusividade administrativa e distinção entre presença, sincronização e saúde. Não acrescentar limite de temperatura como funcionalidade existente.
- **Evidência:** `monitor_settings`, `players_settings_page`, `players_settings_save`, `evaluate_tv_health`.

## 11. Estabilidade

- **Nome atual, rotas e template:** Estabilidade dos Players; `GET /players/stability` e `/players/{tv_id}/stability`; `stability.html` → base.
- **Objetivo e dados exibidos:** resumo por Tela e detalhe por período; disponibilidade estimada, reboots, reinícios de Player/navegador, recuperações, offline, CPU/RAM/temperatura e linha do tempo.
- **Ações e perfis:** V/E/O/A consultam período/data e navegam entre resumo e detalhe.
- **Restrições por Tela:** resumo limitado às Telas autorizadas; detalhe exige acesso à Tela.
- **Endpoints, JavaScript e atualização:** GETs com período/data; gráficos preparados pelo servidor; sem polling confirmado. Não presumir biblioteca de gráficos externa.
- **Banco e Dispositivo:** `stability_events`, `stability_samples`, `tv_event_log`, comandos de reboot e permissões. Depende de coleta prospectiva e eventos do `stability_agent.py`.
- **Vazio e erro:** ausência de amostras é explicitamente distinta de normalidade; histórico anterior à coleta não é reconstruído. Acesso proibido/identificador inexistente não produz dados de outra Tela.
- **Não perder:** janela noturna 18h–08h, períodos móveis, data de referência, distinção reboot comandado/sem comando identificado, gráficos e limitações da disponibilidade estimada. Sem prova física do Display.
- **Evidência:** `stability_overview`, `stability_details`, `stability_monitor.window`, `summarize`, `offline_intervals`.

## 12. Usuários e permissões

- **Nome atual, rota e template:** Usuários e permissões; `GET /users`; `users.html` → base. Permissões não possuem página independente confirmada.
- **Objetivo e dados exibidos:** cadastro, perfil, atividade e Telas autorizadas por usuário.
- **Ações e perfis:** A cria usuário, altera nome/perfil/status, redefine senha e concede/revoga Telas; demais perfis não acessam.
- **Restrições por Tela:** A acessa todas; usuários não administradores dependem de seleção explícita. Sem seleção não acessam Telas.
- **Endpoints:** `POST /users`, `/users/{user_id}/update`.
- **JavaScript e atualização:** formulários e script compartilhado; sem polling confirmado.
- **Banco e Dispositivo:** `users`, `user_tv_permissions`, `tvs`, sessões e auditoria; sem dependência de Dispositivo online.
- **Vazio e erro:** nenhuma Tela atribuída é um estado válido de acesso restrito; usuário inválido/duplicado e acesso proibido são tratados pelo fluxo de servidor. Não confundir senha opcional de atualização com remoção de senha.
- **Não perder:** perfil, ativo/inativo, redefinição opcional, concessões explícitas e efetividade no backend. Não presumir fluxo obrigatório de troca de senha apenas pela existência de `must_change_password`.
- **Evidência:** `users_page`, `users_create`, `users_update`, `sync_user_tv_permissions` e funções de autorização.

## 13. Auditoria

- **Nome atual, rota e template:** Auditoria; `GET /audit`; `audit.html` → base.
- **Objetivo e dados exibidos:** registros administrativos com horário, usuário, ação, alvo, detalhe e IP; filtros por ação, usuário e período.
- **Ações e perfis:** A consulta e filtra; não há edição de evento confirmada.
- **Restrições por Tela:** acesso administrativo global; não é o mesmo histórico operacional filtrado por Tela.
- **Endpoints, JavaScript e atualização:** `/audit` com parâmetros; formulário e base; sem polling confirmado.
- **Banco e Dispositivo:** `audit_log`; ações de Dispositivo podem gerar registros via servidor, mas não exige Dispositivo online para consulta.
- **Vazio e erro:** filtro pode resultar em lista vazia; não administradores recebem bloqueio. Não existe cobertura completa de todas as mutações: ver riscos.
- **Não perder:** filtros, identidade, alvo, detalhes e horário; não apresentar como trilha completa de toda alteração.
- **Evidência:** `audit_page`, `audit_event` e chamadas a essa função.

## 14. Releases / Versões do Player

- **Nome atual, rota e template:** Releases do Player; `GET /releases`; `releases.html` → base.
- **Objetivo e dados exibidos:** pacotes, versões, canal/status, checksum, tamanho, notas e comandos recentes.
- **Ações e perfis:** A envia pacote, promove versão recomendada e consulta operações; atualização individual é acionada no detalhe da Tela.
- **Restrições por Tela:** administração global; atualização individual também verifica a Tela. Não inferir rollout automático por canal.
- **Endpoints:** `POST /releases`, `/releases/{release_id}/promote`; `GET /releases/files/{filename}`; atualização por `POST /players/{tv_id}/command`; download de Dispositivo em `/api/player/{tv_id}/release/{filename}`.
- **JavaScript e atualização:** formulários e base; nenhum monitor global de rollout com polling foi confirmado.
- **Banco e Dispositivo:** `player_releases`, `player_commands`, `player_update_jobs`, settings e auditoria; arquivos em `releases/`. Instalação depende de consulta/ACK do Player.
- **Vazio e erro:** falta de pacote/versão, release ausente e versão duplicada; duplicação possui risco de perda do arquivo original. Lista vazia não implica atualização disponível.
- **Não perder:** checksum, distinção recomendado/instalado, canais/status, notas e comandos. Promoção não equivale a instalação; ACK não substitui verificação de saúde posterior.
- **Evidência:** `releases_page`, `releases_upload`, `release_promote`, `player_command`, downloads e `player_updater.py` externo.

## 15. Provisionamento e rotação de token

- **Nome atual, rotas e templates:** “Provisionar Raspberry” e “Rotacionar token”, originados no detalhe da Tela; respostas em `provision_code.html` e `device_token.html` → base.
- **Objetivo e dados exibidos:** gerar instalação por código temporário ou nova credencial de Dispositivo. Valores reais são sensíveis e não pertencem a documentação, exemplos ou commits.
- **Ações e perfis:** A gera código e rotaciona token. O bootstrap consome código; não utiliza sessão administrativa no Dispositivo.
- **Restrições por Tela:** operação vinculada a `tv_id`; código expira em uma hora e é invalidado após uso. Novo provisionamento cancela comandos antigos pendentes.
- **Endpoints:** `POST /players/{tv_id}/provision-code`, `/device-token`, `/api/player/provision`; `GET /bootstrap/install.sh` e download autenticado de release.
- **JavaScript e atualização:** páginas de resultado e instruções; não há polling de instalação confirmado. O shell efetua chamadas HTTP, configuração e validação local.
- **Banco e Dispositivo:** hashes/campos de provisionamento em `tvs`, settings, releases, comandos/jobs e auditoria; instalação depende de Linux, rede, ferramentas e serviços.
- **Vazio e erro:** Tela inexistente, código ausente/inválido/expirado, release não disponível, download/checksum/serviço falhando. Código já consumido não deve ser reutilizado.
- **Não perder:** uso único, expiração, invalidação da credencial anterior, checksum, instruções de instalação e caráter sensível dos resultados.
- **Evidência:** `generate_provision_code`, `rotate_device_token`, `api_player_provision`, `bootstrap/install_player.sh`.

## 16. Reprodução de compatibilidade no servidor

- **Nome atual, rota e template:** Player de exibição; `GET /player/{tv_id}`; `player.html`, independente.
- **Objetivo e dados exibidos:** reprodução web por Tela, consumindo conteúdo e manifesto. Não é a página administrativa de detalhes nem a página local usada pelo serviço fornecido.
- **Ações e perfis:** acesso público de compatibilidade, fora do RBAC do painel; APIs do Player têm autenticação própria, com exceção do defeito de heartbeat. Página pública não garante sucesso de APIs em Tela com token.
- **Restrições por Tela:** parâmetro `tv_id`; não há filtro por concessão de usuário do painel nessa página pública.
- **Endpoints:** `/api/player/{tv_id}`, `/cache-manifest`, `/api/tv/heartbeat` e `/media/...`.
- **JavaScript e atualização:** script incorporado, estado em `localStorage`, sync/heartbeat a cada dez segundos e prefetch do manifesto a cada sessenta segundos.
- **Banco e Dispositivo:** acesso indireto a publicação, agenda e Tela pelas APIs; execução no navegador de exibição. Não é evidência de que esse caminho esteja em uso nos Raspberry atuais.
- **Vazio e erro:** ausência de playlist, mídia indisponível e falha de comunicação são cenários tratados pela reprodução; textos/fallbacks exatos precisam de conferência específica antes de mudança.
- **Não perder:** compatibilidade existente, cadência e formato de APIs, persistência e comportamento de reprodução. Não modernizar esse template por herança do painel.
- **Evidência:** `player` e `templates/player.html`.

## 17. Reprodução local no Dispositivo

- **Nome atual, rota e template:** Player local; `/` no HTTP local padrão `127.0.0.1:8765`; `player_local.html` externo, servido por `local_player.py`.
- **Objetivo e dados exibidos:** reproduzir mídia do cache no Firefox kiosk, reportando estado/progresso.
- **Ações e perfis:** fluxo automatizado do Dispositivo, sem RBAC administrativo local confirmado. Supervisão reinicia navegador conforme falha; comandos vêm do servidor autorizado.
- **Restrições por Tela:** identidade configurada por `DS_TV_ID` e credencial do Dispositivo. A configuração padrão limita HTTP local ao loopback.
- **Endpoints:** locais `/state`, `/health`, `/playback` e mídia; remotos conteúdo, manifesto, heartbeat, comandos e ACK.
- **JavaScript e atualização:** polling do estado e envio periódico de telemetria; troca de sequência conforme assinatura do estado; imagens temporizadas, vídeo por término e tratamento de eventos de erro/stall. Intervalos remotos documentados na arquitetura; não confundir com timers locais do HTML.
- **Banco e Dispositivo:** não usa diretamente o SQLite central; depende de `state.json`, mídia local, Firefox, Linux e serviços. Sem novas consultas bem-sucedidas, mantém última versão válida disponível.
- **Vazio e erro:** “Inicializando player...” e “Aguardando conteúdo...”; falhas de download preservam estado anterior, erros de reprodução/telemetria alimentam diagnóstico e supervisão.
- **Não perder:** continuidade offline do último estado, promoção somente após downloads completos, suporte HTTP Range, temporização, telemetria e recuperação. Não alegar agenda offline completa ou confirmação física do Display.
- **Evidência:** `main`, `sync_once`, `background_worker`, `PlayerHandler` e `player_local.html` da cópia externa.

## Classificação de elementos não equivalentes a funcionalidades ativas

| Categoria | Evidência e interpretação |
|---|---|
| Código histórico sem referência ativa encontrada | Templates de backup, `live.html`, `editor(1).html`, `gallery(1).html`, scripts de patch e `_update_224` não estão no caminho ativo identificado. Isso não autoriza exclusão nem prova ausência de uso externo. |
| Código presente sem chamada ativa confirmada | `render_pdf` e `convert_powerpoint_to_pdf` existem, mas não são chamados pelo fluxo ativo analisado. Não oferecer PDF/PPT como capacidade atual de upload. |
| Estruturas no banco sem uso funcional confirmado | `schedules.priority`, `schedule_mode`, `start_at`, `end_at`. Não comprovaram agendamento por data ou prioridade; `tv_contents` é estrutura legada, enquanto publicação direta retorna 410. |
| Variantes externas | `local_player_firefox.py` e `local_player_firefox_hw.py` existem; o serviço aponta para `local_player.py`. Não declarar variantes descartáveis nem instaladas em produção. |
| Mockups | Busca global, notificações globais, módulos independentes de Localidades/Permissões/Publicações, progresso consolidado de distribuição e alerta térmico ilustrado não foram confirmados como funcionalidades implementadas. |
| Dados ilustrativos | Totais de Telas, localidades, nomes, horários, percentuais e versões dos mockups não são fontes de dados da aplicação. |

## Limite de completude

Este inventário registra o que foi confirmado pela análise anterior. Não houve teste de navegador, execução dos fluxos ou homologação em Raspberry. Onde a análise não consolidou um texto de estado, timer ou controle, isso está explicitado. Antes do redesign de cada página, conferir seu código específico e registrar a validação visual e funcional sem substituir lacunas por suposições.
