# Análise estratégica do estado de versão do Player

Data: 19/10/2026. Código-base: `bc0d1e6`. Análise e proposta; nenhuma regra operacional foi alterada.

**Conclusão**

O modelo atual mistura saúde operacional com conformidade a uma recomendação global. A desigualdade textual entre duas versões produz `warning`, mesmo sem falha operacional. Além disso, `version_outdated` significa apenas “diferente”, e `completed` em um job não confirma que o processo voltou executando a release esperada.

A estratégia recomendada é separar três dimensões: saúde operacional observada, situação da versão/política de suporte e ciclo da atualização. Alertas de incompatibilidade, versão bloqueada e falhas de atualização devem continuar visíveis e acionáveis, com causas próprias. A mera diferença em relação à recomendação não fornece evidência suficiente para classificar a operação como degradada.

**Método e limites**

Foram examinados os caminhos ativos do Servidor, Player, templates, instaladores, serviços, testes e documentação; realizadas buscas globais pelas referências de versão, jobs e saúde; consultado `digital_signage.db` por conexão SQLite `mode=ro`; e inspecionados os quatro ZIPs de `releases/`, incluindo manifesto, constante do executável e SHA-256. Backups e `_update_224/` foram distinguidos do código ativo. Nenhum Player foi acionado e nenhum pacote foi instalado.

Nove cenários foram executados com a função real `evaluate_tv_health`, extraída por AST, sem importar `app.py`. Isso evita seus efeitos de inicialização e migração. Os testes existentes foram lidos, mas a suíte não foi executada nesta análise; não houve alteração de comportamento a validar. A pasta `.pytest_cache` não estava acessível; isso não impediu a leitura de `tests/`.

O banco consultado é o arquivo local padrão, não prova de qual banco um processo implantado usa: `DS_DB_FILE` pode substituí-lo, assim como `DS_RELEASE_DIR` pode substituir a pasta de pacotes ([configuração e get_db](../../app.py#L34), linhas 34–95). Os dados abaixo representam os registros observados, não uma verificação física ou ao vivo das Telas.

**Etapa 1 — mapa das 20 questões**

| # | Conclusão | Arquivo, função e região |
|---|---|---|
| 1 | A recomendação é uma string global em `tv_monitor_settings.recommended_player_version`, linha `id=1`. Não é uma FK para release, nem política individual por Tela. | [app.py](../../app.py#L826), `init_db`, 826–848; `monitor_settings`, 2667–2702. |
| 2 | Pode ser definida pelo formulário de configurações ou pela promoção de release. Inicialização insere `3.0.0-stable`, depois migra valores conhecidos, incluindo vazio, para `3.2.1-stable`; DDL e fallback também usam 3.2.1. Logo, deixar vazio desativa temporariamente a regra, mas uma nova inicialização pode reativá-la. | [app.py](../../app.py#L1213), `init_db`, 1213–1225; `release_promote`, 1527–1538; `players_settings_save`, 3626–3688; [monitor_settings.html](../../templates/monitor_settings.html#L7), bloco content, linha 7. |
| 3 | O processo Python declara `PLAYER_VERSION = "3.2.2-stability-preview"`; não lê a versão de `manifest.json`. Há também o Player web legado com constante própria `1.1.0`. | [local_player.py](../../player/local_player.py#L25), configuração, linha 25; [player.html](../../templates/player.html#L121), script, linha 121; [app.py](../../app.py#L4464), rota `player`. |
| 4 | `send_heartbeat` inclui a constante no campo `player_version` e envia POST a `/api/tv/heartbeat`. A constante também aparece no User-Agent, estado de sync e diagnóstico local. O Player web envia sua própria constante no mesmo campo. | [local_player.py](../../player/local_player.py#L1441), `send_heartbeat`, 1441–1573, especialmente 1545/1568; `build_request`, 192–200; `sync_once`, 690; `_health_payload`, 1956; [player.html](../../templates/player.html#L742), heartbeat JS. |
| 5 | O Servidor grava a versão em `tvs.player_version`; o payload original fica em `tv_runtime_status.payload_json`; a versão usada no monitor também é copiada para `tv_monitor_state.player_version`. Se o heartbeat omite versão ou envia vazio, o Servidor substitui por `APP_VERSION`, versão do próprio Servidor. | [app.py](../../app.py#L3353), `heartbeat`, 3353–3356, 3410–3474; `record_monitor_snapshot`, 2977–2981 e 3159–3199. |
| 6 | A comparação operacional ocorre em `evaluate_tv_health`. A API repete a comparação para calcular `version_outdated`. Provisionamento também busca release por igualdade exata da recomendação, para outro propósito. | [app.py](../../app.py#L2853), `evaluate_tv_health`, 2853–2877; `tvs_status`, 3556–3573; `api_player_provision`, 4338–4351. |
| 7 | É desigualdade de strings após `strip()`, sensível a maiúsculas, sem ordenação numérica, sem análise de canal, suporte ou prerelease. A versão da coluna `tvs` tem precedência sobre a do runtime. Ambos os textos precisam ser não vazios para gerar divergência. | [app.py](../../app.py#L2853), `evaluate_tv_health`, 2853–2877; `tvs_status`, 3556–3573. |
| 8 | A divergência retorna `warning` com os dois textos. Em `/players`, vira badge Atenção, borda, contagem e ordenação por problema, além do símbolo ⚠ na versão. Detalhe traduz warning para Atenção; admin herda o snapshot. | [app.py](../../app.py#L2871), `evaluate_tv_health`; [players.html](../../templates/players.html#L18), `card`, `render`, `loadStatus`, 18–25; [player_detail.html](../../templates/player_detail.html#L11), estado atual; [admin.html](../../templates/admin.html#L107), 107–110. |
| 9 | Prioridade atual: manutenção → offline → `last_error` → navegador parado → erro de reprodução → versão divergente → sync offline → sync antigo → disco baixo → OK. Só retorna a primeira causa. Assim, versão pode esconder a explicação de sync/disco, embora não esconda os erros anteriores. | [app.py](../../app.py#L2780), `evaluate_tv_health`, 2780–2942. |
| 10 | `record_monitor_snapshot` compara o estado anterior, emite eventos e faz UPSERT. É chamado por heartbeat, ciclo do monitor e GET da API de status. O worker roda nominalmente a cada 10 segundos; salvar configurações força um ciclo. Detalhe calcula saúde, mas não grava snapshot. | [app.py](../../app.py#L2945), `record_monitor_snapshot`, 2945–3200; `monitor_cycle`, 3203–3268; `monitor_worker`, 3271–3298; `heartbeat`, 3492–3506; `tvs_status`, 3550–3551; `players_settings_save`, 3688; `player_details_page`, 3764–3765. |
| 11 | `/api/tvs/status` reúne TV, playlist e runtime, respeita Telas autorizadas, calcula saúde e grava snapshot. Retorna `online`, `health`, `health_reason`, versões, `version_outdated` e telemetria. Não reúne comandos nem jobs. O GET tem efeitos persistentes. | [app.py](../../app.py#L3527), `tvs_status`, 3527–3588. |
| 12 | `stats.tvs_problem` é `len(problem_tvs)`, antes do corte da lista visual. O cálculo de online é atual, mas a saúde vem do snapshot existente, sem checar sua idade. Pode haver divergência temporária entre contagem online e saúde. | [app.py](../../app.py#L2144), `admin`, 2144–2173. |
| 13 | `problem_tvs` contém Telas permitidas com saúde `warning`, `error` ou `offline`, na ordem de `tv_id`; a interface recebe só as primeiras oito. Sem snapshot, usa OK se online e offline caso contrário. Não prioriza por gravidade e não recalcula o diagnóstico completo. | [app.py](../../app.py#L2150), `admin`, 2150–2172. |
| 14 | A rota `/players` entrega o template; JS consulta a API a cada 3 segundos. Métricas e cartões usam `health`. “Somente problemas” exclui apenas `ok`, portanto inclui manutenção; isso difere da contagem Atenção/erro e da definição de problemas no admin. | [app.py](../../app.py#L3591), `players_page`, 3591–3603; [players.html](../../templates/players.html#L10), métricas e funções JS, 10–25. |
| 15 | O detalhe recalcula saúde, mostra versão reportada e recomendada lado a lado, telemetria e eventos. Oferece atualização para admin, mas não carrega histórico/estado de jobs para esse detalhe. Labels ainda dizem Chromium, embora o processo atual monitore Firefox. | [app.py](../../app.py#L3740), `player_details_page`, 3740–3834; [player_detail.html](../../templates/player_detail.html#L11), 11–34; [local_player.py](../../player/local_player.py#L744), `signage_chromium_pids`/`chromium_executable`, 744–784. |
| 16 | ZIPs são registrados pelo manifesto quando a inicialização chama `sync_packaged_releases`. Upload recebe versão textual sem confrontá-la com o ZIP. Promover marca a release como stable e troca a recomendação global, sem mudar seu canal nem despromover as anteriores. Não instala automaticamente em Telas existentes; afeta também a escolha no próximo provisionamento. | [app.py](../../app.py#L653), `sync_packaged_releases`, 653–683; `releases_upload`, 1481–1524; `release_promote`, 1527–1538; `api_player_provision`, 4338–4368; [releases.html](../../templates/releases.html#L7), 7–9. |
| 17 | `player_command` monta args com versão, URL, SHA e filename da release; `enqueue_player_command` cria comando pending e auditoria; é criado job queued ligando release, Tela e comando. Não há bloqueio de atualizações concorrentes nem validação de canal/status da release nessa seleção por ID. | [app.py](../../app.py#L620), `enqueue_player_command`, 620–632; schema, 991–1041; `player_command`, 4197–4235. |
| 18 | Poll muda comando pending para dispatched; ACK aceita running/done/failed, progresso/mensagem/resultado. Job só muda em ACK terminal: done → completed; failed → failed. ACK running não muda job queued. Updater informa etapas e emite done após copiar/agendar reinício, antes de confirmar retorno. | [app.py](../../app.py#L4373), `api_player_commands`/`api_player_command_ack`, 4373–4423; [local_player.py](../../player/local_player.py#L1373), `execute_command`, 1373–1435; [player_updater.py](../../player/player_updater.py#L119), `main`, 119–202. |
| 19 | Falha de atualização não é entrada de `evaluate_tv_health` nem da API de status. Pode afetar saúde indiretamente se causar erro, navegador parado ou ausência de heartbeat. Se a operação antiga continuar boa, uma falha de atualização pode ficar fora de `/players` e `/admin`; aparece como comando failed em `/releases`. | [app.py](../../app.py#L2780), `evaluate_tv_health`, 2780–2942; `tvs_status`, 3527–3588; `releases_page`, 1454–1478; [releases.html](../../templates/releases.html#L9), últimos comandos. |
| 20 | Divergência pode aparecer em `health_warning` quando muda a categoria de saúde; primeiro snapshot registra `monitor_started`. Alteração da versão reportada gera `player_version` informativo. Mudança só da razão, mantendo warning, não gera novo health event. Auditoria registra upload, promoção e enfileiramento, não cada divergência nem ACK; salvar recomendação diretamente não chama `audit_event`. | [app.py](../../app.py#L2997), `record_monitor_snapshot`, 2997–3044 e 3120–3135; `enqueue_player_command`, 631; `releases_upload`, 1514; `release_promote`, 1536; `players_settings_save`, 3663–3688; `api_player_command_ack`, 4399–4423. |

**Etapa 2 — fontes e formatos reais**

| Fonte | Valor/formato observado | O que representa e limite |
|---|---|---|
| Executável Python do checkout | `3.2.2-stability-preview` | Versão declarada pelo processo; independente do manifesto. |
| `player/manifest.json` | `3.2.1-stable`, canal/status stable, mínimo do Servidor `2.2.1` | Metadado de uma release anterior; inconsistente com o código atual. |
| Servidor (`APP_VERSION`) | `2.3.1-stability-night` | Outro produto/ciclo. Hoje também é fallback indevido para heartbeat sem versão. |
| Player web (`templates/player.html`) | `1.1.0` | Implementação diferente usando o mesmo campo e endpoint. Exige identificação de tipo de cliente. |
| Recomendação no banco local | `3.2.1-stable` | Alvo administrativo global; não prova de mínimo suportado. |
| Versões reportadas no banco local | Uma Tela `3.2.1-stable`; uma `3.2.2-stability-preview` | Inventário observado, não integridade dos arquivos nem saúde física do Display. |
| Releases cadastradas | `3.2.0-stable` e `3.2.1-stable`, ambas stable; `3.2.3-cache-retention-7d`, candidate/draft | A maior parte numérica não implica aprovação ou recomendação. Preview 3.2.2 não está cadastrada nesse banco. |
| Jobs locais | Cinco jobs completed; quatro apontam para release 3.2.1 e um para 3.2.0 | Alvo por `release_id`; versão também copiada para args do comando. São declarações de conclusão do instalador. |
| Pacotes disponíveis | Quatro ZIPs, detalhados abaixo | Arquivo disponível não equivale a release aprovada, recomendada ou aplicável. |
| Documentação | Reconhece diferença 3.2.2 preview/3.2.1 manifesto e existência de 3.2.3 separado | Não é fonte executável de versão. Ver [player/README.md](../../player/README.md#L15), [PLAYER_ATUAL.md](PLAYER_ATUAL.md#L32), [ARQUITETURA_ATUAL.md](ARQUITETURA_ATUAL.md#L170) e [RISCOS_CONHECIDOS.md](RISCOS_CONHECIDOS.md#L86). |

Inspeção interna dos arquivos em `releases/`:

| ZIP | Manifesto | Constante de `local_player.py` | Conferência com banco |
|---|---|---|---|
| `digital-signage-player-3.1.0-remote-mgmt.zip` | Ausente | `3.1.0-remote-mgmt` | Não cadastrado; importação automática ignora ZIP sem manifesto. |
| `digital-signage-player-3.2.0-stable.zip` | 3.2.0-stable, stable/stable, mínimo 2.2.0 | `3.2.0-stable` | SHA-256 integral confere com registro 1. |
| `digital-signage-player-3.2.1-stable.zip` | 3.2.1-stable, stable/stable, mínimo 2.2.1 | `3.2.1-stable` | SHA-256 integral confere com registro 2. |
| `digital-signage-player-3.2.3-cache-retention-7d.zip` | 3.2.3-cache-retention-7d, candidate/draft, mínimo 2.2.4-playlist-ux | `3.2.3-cache-retention-7d` | SHA-256 integral confere com registro 3. |

Nenhum dos quatro contém `stability_agent.py`. Isso não prova defeito nos quatro pacotes históricos: a necessidade desse módulo depende do código que cada um executa. Porém, empacotar o código atual exige incluí-lo, e o updater atual não o copia. Serviços iniciam `local_player.py` por caminho fixo; não selecionam release pelo manifesto ([digital-signage-player.service](../../player/services/digital-signage-player.service#L7), 7–10).

Há incompatibilidade de vocabulário de canais: upload permite lab/pilot/production/stable, enquanto importação do manifesto aceita `candidate` observado no ZIP 3.2.3. A coluna `tvs.update_channel` existe com default lab, mas não participa da escolha da recomendação, da avaliação de saúde ou do comando de atualização ([app.py](../../app.py#L936), 936, 1491, 668–669, 2853–2877 e 4212–4224).

Os sufixos atuais misturam estabilidade, experimento e funcionalidade: `-stable`, `-stability-preview`, `-remote-mgmt`, `-cache-retention-7d`. Não se deve simplesmente retirar sufixos nem aplicar ordenação lexicográfica para decidir segurança, suporte ou atualização. Tampouco se deve presumir que uma biblioteca de versões reproduzirá a intenção desses nomes: tratar todos os sufixos como prerelease não expressa necessariamente a política histórica do projeto.

Proposta: conservar `raw_version`, separar identidade do artefato, versão numérica estruturada, canal e política. Novas releases devem ter contrato explícito de formato e ordenação; versões históricas devem ser mapeadas pelo catálogo, sem inventar equivalência. Ordem numérica só ajuda a comparar releases da mesma linha e compatíveis; não autoriza downgrade de preview para stable nem promoção automática de candidate.

**Evidência do caso e verificações isoladas**

No snapshot local da Tela preview, `browser_running=true`, `sync_status=ok`, `playback_state=playing`, disco livre de 21014,1 MB e `last_error` vazio. `tvs` e runtime concordam em `3.2.2-stability-preview`. O snapshot registra warning com a razão exata de divergência para `3.2.1-stable`; há eventos `health_warning` com essa razão. Isso reproduz o mecanismo descrito pelo usuário. Não garante que o Display esteja fisicamente exibindo imagem nem que esses sinais permaneçam atuais.

Execução isolada da função, fixando heartbeat/sync recentes, navegador ativo e reprodução normal:

| Cenário | Retorno atual observado |
|---|---|
| Mesma versão recomendada | OK |
| Preview 3.2.2 contra stable 3.2.1 | warning por versão |
| Stable 3.2.0 contra stable 3.2.1 | warning por versão |
| Candidate 3.2.3 contra stable 3.2.1 | warning por versão |
| Versão ausente em TV e runtime | OK; a função isolada não exige versão |
| Divergência + disco baixo | warning cuja razão só menciona versão |
| Divergência + sync offline | warning cuja razão só menciona versão |
| Divergência + navegador parado | error por navegador |
| Divergência + heartbeat vencido | offline |

O caminho HTTP de versão ausente difere da função isolada: o heartbeat inventa a versão do Servidor antes da avaliação. Assim, o estado “desconhecido” pode virar uma divergência fabricada ([app.py](../../app.py#L3353), 3353–3356).

**Impactos adicionais que condicionam a solução**

1. **Duração de indisponibilidade inconsistente.** O detalhe procura apenas `health_ok` para encerrar `health_offline` ([app.py](../../app.py#L3784), `player_details_page`, 3784–3790). Uma Tela que retorna em warning por versão pode continuar com duração “em andamento”. Já [stability_monitor.py](../../stability_monitor.py#L111), `offline_intervals`, 111–126, encerra o intervalo em qualquer transição que não seja offline. `summarize`, 129–157, calcula saúde histórica própria por amostras, disponibilidade, reboots e watchdog, sem comparar versões. Separar versão exige preservar essas diferenças e corrigir a semântica de disponibilidade sem reescrever eventos antigos.
2. **Atualização concluída sem ativação.** `restart_service` pode falhar em agendar restart e ainda assim `main` envia done. `last_update.json` registra a versão solicitada, não uma inspeção do executável ativo; não é enviado como confirmação pelo heartbeat. Não há pós-validação de saúde nem rollback por falha após reinício ([player_updater.py](../../player/player_updater.py#L96), 96–116 e 174–196).
3. **Instalação parcial e confirmação fraca.** A lista fixa de cópia/backup/rollback omite manifesto e agente de estabilidade; `copy_if_exists` ignora faltantes. Erros de aplicação de serviços podem ser ignorados. Não há confronto versão solicitada × manifesto × constante do código, nem uso de `min_server_version`/`restart_required` pelo updater. SHA confere os bytes contra o hash fornecido, mas não prova compatibilidade, completude ou procedência aprovada ([player_updater.py](../../player/player_updater.py#L85), 85–93, 135–196).
4. **Falha e cancelamento confundidos.** Novo provisionamento marca comandos pendentes/dispatched/running como failed e jobs queued como failed, com mensagem de cancelamento. Uma classificação futura precisa distinguir cancelado de falha técnica ([app.py](../../app.py#L4304), `api_player_provision`, 4304–4325).
5. **Estados sem timeout ou transição protegida.** Comandos running não são reapresentados pelo poll; não há timeout/reconciliação nesse fluxo. ACK terminal não exige versão observada; ACK posterior pode sobrescrever estado terminal. Jobs queued podem representar execução em curso. Não basta exibir sua coluna status como verdade completa ([app.py](../../app.py#L4373), 4373–4423).
6. **Recomendação tem efeito de instalação.** Alterá-la para fazer o painel ficar verde muda a release do provisionamento; o lookup exato não exige stable/canal. Fallback usa stable por data, não por maior versão. Desativar recomendação ou promover preview como solução visual pode mudar comportamento de implantação ([app.py](../../app.py#L4338), 4338–4368).
7. **Integridade do catálogo.** Upload aceita conteúdo não vazio sem validar ZIP/manifesto e escreve arquivo antes do INSERT. Uma versão duplicada pode sobrescrever o arquivo e removê-lo ao falhar a restrição UNIQUE. A importação pula versões já registradas sem revalidar o arquivo. Portanto, “há registro” não garante pacote disponível e íntegro ([app.py](../../app.py#L1481), `releases_upload`, 1497–1520; `sync_packaged_releases`, 665–667).
8. **Qualidade e confiança da telemetria.** Heartbeat atual não chama `require_device_auth`, ao contrário de comandos/ACK. Além disso, o Servidor marca `last_sync_at` como horário de recepção sempre que o payload diz sync ok, em vez de preservar a idade da última sincronização bem-sucedida reportada. `evaluate_tv_health` não exige telemetria completa/fresca de reprodução. São limites reais ao significado de “saudável”; não se deve transformá-lo em garantia de reprodução ou de integridade do software ([app.py](../../app.py#L3327), `heartbeat`, 3327–3506; [local_player.py](../../player/local_player.py#L1460), freshness, 1460–1471).

**Alternativas avaliadas**

| Alternativa | Benefício | Limite | Decisão proposta |
|---|---|---|---|
| Fazer recomendação igual à preview ou deixá-la vazia | Alivia alerta imediato | Oculta política; afeta provisionamento; vazio é revertido na inicialização | Não usar como correção do modelo |
| Substituir desigualdade por comparação numérica | Diferencia números anteriores/posteriores | Não resolve catálogo, preview, identidade, suporte, canal ou jobs | Insuficiente isoladamente |
| Remover apenas o warning | Reduz falso alerta | Mantém `version_outdated` enganoso e falhas de atualização pouco visíveis | Só aceitável junto da separação mínima de apresentação |
| Separar operação, versão e atualização, com política explícita | Representa causas e permite ação apropriada | Exige evolução de contrato, UI e atualização | Recomendada, em etapas |

**Modelo proposto — saúde operacional**

Manter `health` como dimensão operacional para preservar o contrato conhecido. Os estados continuam OK, Atenção, Erro, Offline e Manutenção, mas apenas sinais operacionais e impactos conhecidos devem determiná-los. Não mudar silenciosamente todos os demais thresholds nesta tarefa.

Retornar também causas estruturadas, como `operational_issues[]`, com código, severidade e evidência, mantendo `health_reason` como resumo. Isso permite mostrar sync/disco simultaneamente. Acrescentar idade e completude da telemetria; OK deve significar “sem alerta operacional detectado”, não “reprodução física comprovada”. Ausência de dados críticos deve ser apresentada como “telemetria insuficiente”, com regra de tolerância definida, sem confundir com versão ausente.

Manutenção continua explícita, mas não deve apagar informações de versão ou resultado da atualização. Interrupções esperadas durante atualização precisam de janela limitada por tentativa e fase; não ativar manutenção indefinida nem ignorar heartbeat vencido indiscriminadamente.

**Modelo proposto — versão e política**

Evitar um único enum que misture identidade, ordenação e risco. Um contrato aditivo pode apresentar:

| Campo proposto | Significado |
|---|---|
| `reported_version`, `observed_at`, `client_kind` | Declaração bruta, atualidade e tipo de Player; nunca substituir pelo APP_VERSION |
| `release_id`/`build_id`, `identity_status` | Correspondência conhecida, desconhecida ou inconsistente com catálogo; confiança explicitada |
| `recommended_release_id`, `relation` | Igual/diferente/indeterminada em relação ao alvo aplicável |
| `channel`, `policy_status` | Canal e condição aprovada, preview permitida, não avaliada, fora de suporte, incompatível ou bloqueada |
| `available_update` | Alvo efetivamente disponível e autorizado pela política, com motivo; null quando não houver evidência |
| `version_issues[]` | Riscos acionáveis relacionados à versão, sem inventar falha operacional |

“Atualização disponível” exige alvo aprovado para o tipo de cliente/canal, compatibilidade e artefato existente/validado, além de relação de atualização permitida. Um pacote draft numericamente maior não basta. Quando o alvo recomendado é uma versão anterior à preview, apresentar “versão diferente da recomendada” e eventual ação explícita de retorno à estável; não chamar automaticamente de atualização ou executar downgrade.

Preview deve permanecer visível. Pode ser informativa em laboratório autorizado e alerta de política em produção. Hoje o sistema não possui evidência suficiente para declarar todas as previews incompatíveis ou seguras. Uma versão não catalogada é “não identificada no catálogo”, mesmo que seu número pareça recente. A string ausente é “não informada”. Uma versão bloqueada ou comprovadamente incompatível precisa de alerta destacado de risco, independentemente de estar reproduzindo no momento.

Inicialmente, usar a recomendação global existente como alvo legado; não ativar política por canal usando cegamente `update_channel`, cujo default lab pode não representar aprovação real. Políticas por grupo/canal requerem migração explícita. Publicação, aprovação e recomendação devem ser decisões distintas, auditadas. A recomendação futura deve apontar para uma release identificada, com opção explícita de desabilitar que sobreviva à reinicialização.

**Modelo proposto — atualização**

Estado de tentativa separado de saúde e versão: `queued` → `dispatched` → `running` → `awaiting_restart`/`verifying` → `verified`; saídas alternativas `failed`, `cancelled`, `timed_out`/`unknown_outcome`, `rolled_back` e `rollback_failed`. Os nomes podem ser ajustados no desenho do contrato, mas as distinções são necessárias.

Para dados legados, `done`/`completed` deve aparecer como “instalação reportada como concluída; ativação não verificada”. Não converter retrospectivamente esses jobs em sucesso verificado. “Em andamento” usa comando + job + timestamps, pois queued atualmente também cobre execução. Progresso é etapa informada pelo updater, não estimativa de tempo restante.

Uma confirmação forte exige tentativa identificada, alvo imutável, versão/build observados após restart, sessão/boot/processo correlacionado e janela de telemetria operacional satisfatória. Igualdade de texto no heartbeat, sozinha, é confirmação limitada. Se o alvo já estava rodando, deve haver tratamento próprio; não aceitar heartbeat antigo como prova de uma nova instalação.

Registrar tempo do último progresso, impedir tentativas concorrentes por Tela, tornar ACKs idempotentes com transições válidas e exigir que o job atualizado pertença à mesma Tela/comando autenticado. O update de jobs atual filtra apenas command_id, sem repetir tv_id nem comprovar que o UPDATE do comando atingiu uma linha; esse detalhe também precisa ser corrigido na evolução do fluxo ([app.py](../../app.py#L4409), 4409–4421).

Separar falha de download, checksum, instalação, ativação e rollback. Falha com a versão anterior operando normalmente mantém saúde operacional OK, mas mostra alerta de atualização. Falha que interrompe o Player combina erro de atualização com erro/offline operacional. Timeout significa falta de confirmação, não prova automática de que a instalação falhou. Um sucesso posterior resolve a pendência atual sem apagar o histórico; uma falha antiga não deve manter a Tela eternamente em alerta de atualização.

**Apresentação recomendada**

| Situação | Operação | Versão/atualização |
|---|---|---|
| Online, reprodução/sync bons, versão igual | OK | Recomendada |
| Operação boa, release anterior ainda suportada, alvo aprovado | OK | Atualização disponível |
| Operação boa, preview não catalogada | OK | Preview; não identificada no catálogo; conformidade não avaliada |
| Operação boa, versão ausente | Sem alerta operacional detectado, conforme evidências disponíveis | Versão não informada |
| Atualização executando, operação boa | OK | Atualizando, fase/progresso/alvo |
| Instalador terminou, sem confirmação de retorno | Conforme telemetria atual | Aguardando verificação; não “sucesso verificado” |
| Atualização falhou, versão anterior continua operando | OK | Falha de atualização destacada, com motivo e horário |
| Atualização falhou e Player não retorna | Offline/erro | Falha de atualização e possível recuperação necessária |
| Versão bloqueada/incompatível por política explícita | Conforme sinais operacionais | Alerta crítico de versão, sempre visível |
| Disco baixo junto de preview | Atenção por disco | Preview em indicação independente |

Em `/players`, mostrar badge operacional principal, indicador de versão e indicador de atualização. Separar contadores e filtros “problemas operacionais”, “atualizações disponíveis” e “ações necessárias”, distinguindo manutenção. Substituir o ⚠ indiscriminado por texto que explique a condição. Renomear o rótulo de navegador para “Navegador” ou usar identificação reportada.

Em `/players/{tv_id}`, mostrar versão em execução/última observação, alvo aplicável, aprovação/compatibilidade, tentativa ativa e última tentativa, fases, erros e evidência de verificação. Não atribuir automaticamente a versão solicitada ao inventário antes do novo heartbeat.

Em `/admin`, `stats.tvs_problem` deve contar problemas operacionais. Para não esconder risco, acrescentar contagens/listas de atualização com falha e riscos de versão; uma lista “ações necessárias” pode unir categorias sem duplicar Telas e sempre explicar o domínio. Separar os números impede que uma atualização opcional infle o indicador de operação degradada.

Em `/releases`, distinguir “cadastrada”, “aprovada”, “recomendada” e “artefato validado”; exibir qual é a recomendação efetiva, em vez de inferir isso do badge stable. A existência de várias releases stable é normal e hoje não informa qual delas é o alvo global. Expor disponibilidade/compatibilidade reais antes de oferecer instalação.

**Evolução e migração propostas**

1. Criar avaliadores comuns e puros para operação, versão e atualização, usados pela API, detalhe, monitor e admin. Administrador não deve continuar consumindo snapshot antigo enquanto outras páginas usam cálculo novo. Preservar autorização por Tela e realizar consultas de releases/jobs em lote.
2. Adicionar campos novos ao contrato. Manter versões brutas para compatibilidade; descontinuar `version_outdated` com transição documentada. Durante compatibilidade, tratá-lo explicitamente como desigualdade legada e retirar seu uso de alerta operacional no frontend; não mudar seu significado silenciosamente para clientes externos.
3. Remover o fallback para APP_VERSION em novos heartbeats, registrando origem/ausência de versão. Não reinterpretar historicamente toda string igual ao Servidor como ausente: usar payload original quando houver evidência; senão marcar origem incerta.
4. Recalcular snapshots sob a nova regra e registrar mudança de política/avaliador. Manter eventos antigos; uma transição warning → OK por revisão de regra não deve ser descrita como conserto do Dispositivo. Introduzir eventos próprios de política de versão e ciclo de atualização, deduplicados por causa/tentativa. Auditar mudanças diretas da recomendação com antes/depois.
5. Ajustar duração de offline e disponibilidade para transições de conectividade, independentemente de warning de versão. Distinguir ausência de comunicação, reprodução e manutenção; não somar tudo como indisponibilidade física.
6. Tornar artefatos verificáveis antes de promover: build reproduz versão única em código e manifesto; incluir dependências e catálogo de arquivos; validar ZIP, identidade, checksum, compatibilidade e presença do pacote; escrita atômica sem sobrescrever versão existente. Resolver a omissão de módulos/manifesto no updater antes de distribuir o código atual.
7. Evoluir updater/protocolo para ativação verificada, timeout, rollback verificável e autenticidade da telemetria. O instalador inicial entregue pelo Servidor é [bootstrap/install_player.sh](../../bootstrap/install_player.sh), por `bootstrap_install_script` ([app.py](../../app.py#L4427), 4427–4435), e também deve entrar na validação; `player/install_player.sh` não é o único caminho.

A primeira entrega de implementação deve incluir separação visual e contratual, consistência entre páginas, tratamento honesto do legado e testes da saúde. A verificação completa de atualização pode entrar depois, desde que a UI intermediária declare “conclusão reportada” e nunca prometa retorno validado. A mudança não requer promover release, executar atualizações ou alterar versões de Dispositivos.

**Critérios de aceitação e testes necessários para uma implementação**

Os testes existentes em [tests/test_permissions.py](../../tests/test_permissions.py#L70), especialmente 70–110 e 143–152, cobrem acesso por Tela/perfil e compatibilidade de autenticação; não testam a matriz de saúde/versão, transições de atualização, validação de pacote ou confirmação após restart. Devem ser preservados.

| Grupo | Casos mínimos |
|---|---|
| Operação | Mesma telemetria produz mesma saúde com versão igual, anterior, preview, maior, vazia ou não catalogada; preservar offline, manutenção, erro de reprodução, navegador, sync e disco; causas simultâneas não se perdem. |
| Identidade e política | Ausência não vira APP_VERSION; Player web não é comparado como pacote Python; versão em execução, manifesto e alvo distintos permanecem distintos; canal não aprovado e versão bloqueada geram risco próprio. |
| Disponibilidade de atualização | Draft/candidate maior não vira atualização automaticamente; recomendação sem arquivo, hash divergente e incompatibilidade impedem “disponível”; downgrade precisa intenção explícita. |
| Atualização | queued versus dispatched/running; ACK perdido/duplicado/tardio; processo morre sem ACK; done sem restart; retorno na versão errada; retorno correto sem saúde; rollback bom/falho; cancelamento por provisionamento; concorrência; isolamento por Tela no ACK. |
| Páginas e eventos | Mesma decisão em API, lista, detalhe e admin; métricas/filtros coerentes; atualização com falha visível mesmo com operação OK; snapshot antigo atualizado; duração offline encerra ao voltar em warning; nenhuma reescrita de histórico. |
| Empacotamento | Código/manifesto/build concordam; ZIP tem módulos obrigatórios; upload duplicado não destrói pacote; mínimo de compatibilidade aplicado; instalação/backup/rollback incluem o mesmo conjunto previsto. |

Validar primeiro com banco e pacotes temporários e relógio controlado. Validação de restart, serviços, Firefox e reprodução requer Dispositivo isolado de homologação. Esta análise não comprova esses comportamentos em hardware.
