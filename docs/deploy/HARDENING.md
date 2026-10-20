# Hardening do Servidor — 20/10/2026

Este registro descreve o estado da branch `redesign-workspace-v4`. A revisão
usou somente código e dados sintéticos; não acessou VM, banco, mídias,
credenciais ou Raspberry operacionais.

## Achados e correções

| Tema | Evidência confirmada | Gravidade | Correção | Risco remanescente |
|---|---|---:|---|---|
| Mídia pública | `app.mount("/media", StaticFiles(...))` ignorava sessão, escopo e arquivamento | Alta | Rota com `FileResponse`, sessão + `can_access_content()` ou Bearer vinculado à Tela; arquivados negados; Range preservado | Desempenho e comportamento do proxy precisam de carga real |
| Manutenção | `player_maintenance()` exigia sessão administrativa e token físico simultaneamente | Alta | Mantido RBAC/escopo de Tela, removida autenticação física e adicionada auditoria com ator/estado | Nenhum conhecido no contrato administrativo |
| CSRF | Operações por cookie não tinham token | Alta | Token derivado da sessão, comparação constante, campo oculto, cabeçalho AJAX, login pré-sessão e logout POST | Requer navegador real para validar todos os fluxos com JavaScript |
| Upload | Mídia sem limites por tipo/lote/total e validação parcial | Alta | Chunks, 413, temporário externo, limites configuráveis, MIME, assinatura, ffprobe e ZIP íntegro | Upload de 1 GiB e buffering exigem ensaio Linux/Nginx |
| PDF/PPT | Conversores sem chamadas ativas ainda traziam PyMuPDF/LibreOffice | Baixa | Código e dependências removidos; novos uploads rejeitados; registros históricos identificados sem reprodução | Arquivos históricos continuam ocupando espaço até política explícita |
| Retenção | Não havia rotina de dados operacionais | Média | Comando independente, lock, auditoria, classificação e timer; dry-run e exclusão de arquivado desativada | Exclusão real só após backup/restore isolado |
| Backup periódico | Backup consistente existia sem timer | Média | Service/timer oneshot diário, journal, atraso aleatório e `Persistent=true` | Cópia externa e restore real dependem da infraestrutura |

Foram descartadas listagem de diretório e entrega direta pelo Nginx: a referência
Nginx encaminha tudo ao Uvicorn. `safe_media_path()` e a rota nova recusam saída
da raiz; testes cobrem traversal. Nomes internos de upload já eram aleatórios e
sem sobrescrita. Backup, restore e update já tinham manifesto, SHA-256, SQLite
consistente, caminhos controlados e rollback; esse núcleo não foi recriado.

## Política de autenticação

Usuários recebem mídia somente com sessão ativa e autorização sobre o conteúdo.
Desativar o usuário invalida o acesso na requisição seguinte. Dispositivos usam
Bearer ou `X-Device-Token`; a credencial resolve uma única Tela e a mídia deve
estar em sua atribuição/publicação/agenda. Rotação invalida o token anterior.
O modo `DS_ALLOW_LEGACY_PUBLIC_MEDIA=1` deve existir apenas durante uma transição
controlada e retorna somente conteúdo associado a alguma Tela.

O Player Web exige sessão e permissão da Tela, usa endpoints `/api/web-player/`
e nunca recebe a credencial física. APIs `/api/player/` e heartbeat continuam no
domínio de autenticação de Dispositivo e não exigem CSRF.

## Upload e Nginx

Defaults: imagem 25 MiB, vídeo MP4 1024 MiB, release ZIP 512 MiB, 20 arquivos e
1536 MiB por requisição. `client_max_body_size 2g` é maior que o limite total da
aplicação e acomoda overhead multipart. Alterar os dois lados em conjunto.
Arquivos são escritos em `DS_UPLOAD_TEMP_DIR` com nome aleatório, validados e
promovidos por `os.replace`; falhas removem o temporário. Imagens aceitas: JPG,
JPEG, PNG, WEBP e GIF. Vídeo aceito: MP4 com fluxo reconhecido pelo ffprobe.

## Ordem de migração

1. Gerar backup consistente, verificar manifesto/checksums e ensaiar restore em
   VM isolada.
2. Inventariar Players sem token. O Player integrado já envia Bearer para mídia;
   atualizar primeiro qualquer versão antiga que não faça isso.
3. Provisionar ou rotacionar credenciais e validar um Raspberry piloto ainda com
   a política antiga.
4. Implantar Servidor/configuração com `DS_REQUIRE_DEVICE_TOKEN=1` e
   `DS_ALLOW_LEGACY_PUBLIC_MEDIA=0`.
5. Validar login, CSRF, mídia/Range, cache offline, heartbeat, upload, `/health`
   local e Nginx.
6. Executar retenção em dry-run e habilitar timers. Manter exclusão definitiva
   desabilitada até revisão formal do relatório e recuperação comprovada.

Não há migração destrutiva de banco. Para rollback, usar `update-server.sh` e sua
troca conjunta de código/venv; preservar banco e dados. Se somente um Player
legado falhar durante a janela, a mitigação temporária é reabrir explicitamente
`DS_ALLOW_LEGACY_PUBLIC_MEDIA=1`, reiniciar o serviço, atualizar/provisionar o
Player e retornar a variável a `0`. Não restaurar banco automaticamente por uma
falha de código, proxy ou autenticação.

## Validação pendente no Linux

Executar com Python 3.12 do Ubuntu 24.04: instalação limpa dos pins, ShellCheck,
`systemd-analyze verify`, `nginx -t`, permissões POSIX, sandbox systemd, uploads
grandes, ffprobe com MP4 real, timers, concorrência SQLite, NAS, restore e
rollback. Validar o cache e seeking em Raspberry de homologação. Nenhum desses
resultados deve ser inferido dos testes Windows.
