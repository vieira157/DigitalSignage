# Vocabulário do produto

Referência funcional anterior ao redesign, em 14/10/2026. Nomes recomendados não alteram rotas, tabelas, campos JSON ou código. Consulte a [arquitetura](ARQUITETURA_ATUAL.md) e o [inventário](INVENTARIO_FUNCIONAL.md).

| Conceito | Definição confirmada e correspondência técnica |
|---|---|
| Tela | Ponto de exibição administrado pelo painel. Entidade `tvs`, chave operacional `tv_id`. Reúne atribuição de conteúdo, acesso e estado operacional. |
| Dispositivo | Raspberry Pi associado à Tela, identificado nas APIs pelo `tv_id` e, quando provisionado, por token. Não há cadastro independente de hardware confirmado. |
| Player | Software executado no Dispositivo. A entrada efetiva fornecida é `local_player.py`; inclui reprodução local, cache, comunicação e supervisão. |
| Display | TV ou monitor físico conectado ao Dispositivo. Não possui entidade independente no banco nem confirmação física de imagem pelo monitoramento analisado. |
| Mídia | Imagem ou vídeo administrado em `contents`, com arquivo e metadados. O upload ativo aceita JPG/JPEG/PNG/WEBP/GIF e MP4. |
| Biblioteca | Acervo de mídias, atualmente apresentado como Galeria em `/gallery`; inclui ativos, arquivados, metadados e uso. |
| Playlist | Sequência identificada em `playlists`, com itens, ordem e duração no rascunho. Pode ter publicação e vínculos com Telas/agendamentos. |
| Rascunho | Estado editável em `playlist_items`. Alterações usuais não substituem a publicação até publicar; há exceções/riscos documentados de inicialização e arquivamento. |
| Publicação | Snapshot JSON versionado em `playlist_publications`, consumido pela API de reprodução. Publicar é diferente de atribuir a uma Tela. |
| Histórico de publicação | Snapshots anteriores em `playlist_publication_history`. Restaurar coloca itens no rascunho; não publica automaticamente pelo fluxo de restauração. |
| Agendamento | Regra semanal por Tela que seleciona uma playlist publicada durante uma janela e prevalece sobre a atribuição padrão. |
| Sincronização | Consulta ao servidor, download dos recursos necessários e atualização do estado local disponível para reprodução. Não equivale à confirmação física de exibição. |
| Reprodução | Execução da playlist no navegador, com temporização de imagens, vídeos e telemetria de progresso. |
| Cache | Mídias locais no Dispositivo e estado persistido para continuar a última versão válida. Não confundir com backup permanente ou execução offline completa da agenda. |
| Heartbeat | Comunicação periódica do Player ao servidor em `/api/tv/heartbeat`, usada para presença e atualização operacional. |
| Telemetria | Informações reportadas de reprodução, recursos, navegador, rede e estabilidade. Alguns campos podem estar ausentes conforme a versão do Player. |
| Estabilidade | Histórico de eventos e amostras, reinícios e disponibilidade estimada. Não prova que o Display esteja ligado ou exibindo imagem. |
| Release | Pacote versionado do software Player, com checksum e registro em `player_releases`. Não é versão de uma playlist. |
| Provisionamento | Associação/configuração inicial de Dispositivo para uma Tela por código temporário, obtenção de token e instalação de release. |
| Comando remoto | Ação autorizada enfileirada no servidor e executada quando o Player consulta a fila, com confirmações de andamento/resultado. |
| Localidade | Campo textual `tvs.location`. Não há módulo ou entidade independente de Localidades confirmado. |
| Perfil | Papel RBAC: Visualizador (`viewer`), Editor (`editor`), Operador (`operator`) ou Administrador (`admin`). Define nível de ações. |
| Permissão | Autorização efetiva resultante do perfil e das restrições de recurso/Tela, inclusive `user_tv_permissions`, autoria e compartilhamento. |

## Nomes atuais e linguagem de interface

| Nome atual no produto/código | Nome recomendado para a interface | Restrição |
|---|---|---|
| TVs / Players; `tvs`; `/players` | Telas | Não separar TV e Player em dois cadastros operacionais sem nova modelagem autorizada. |
| Galeria; `/gallery`; `contents` | Biblioteca / mídias | É mudança de linguagem; preservar formatos, arquivamento, uso e permissões. |
| Releases; `/releases` | Versões do Player | Distinguir de publicações e versões de playlists. |
| Chromium; funções `chromium_*` | Navegador / Firefox, conforme contexto confirmado | A cópia do Player fornecida executa Firefox; preservar campos técnicos até mudança contratual explícita. |
| Usuários e permissões; `/users` | Usuários e permissões | Não presumir módulo separado de Permissões. |
| `location` | Localidade | Campo da Tela, não cadastro autônomo. |

## Distinções obrigatórias

- Online significa comunicação dentro da janela considerada; não comprova reprodução contínua.
- Publicado significa snapshot disponível; não significa que todos os Dispositivos já o sincronizaram.
- Sincronizado não comprova Display ligado.
- Arquivar mídia preserva o arquivo físico no servidor, mas pode retirá-la de publicações em reprodução.
- Rotacionar token invalida a credencial anterior; não é sinônimo de sincronizar ou atualizar software.
- Restauração de playlist e rollback de instalação do Player são mecanismos diferentes.
- O rótulo atual “Limpar cache” executa remoção de temporários `.part` na cópia analisada.
