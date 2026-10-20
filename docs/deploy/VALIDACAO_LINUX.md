# Registro de preparação Linux — 20/10/2026

Resultado: **preparado para homologação controlada em Linux**, sem implantação.
Não representa aprovação para produção: as verificações nativas abaixo devem
ser executadas pela infraestrutura em ambiente isolado.

Correção final de recuperação: o restore valida integralmente o backup antes da
parada e preserva o estado atual disponível sem exigir que seu banco ou env
estejam íntegros. Restore e update retomam o estado anterior do serviço quando
a preparação preventiva falha; após uma troca malsucedida, recuperam dados ou
código/venv uma única vez. O serviço originalmente parado é iniciado apenas
para validar a nova versão e volta ao estado parado. Os testes sintéticos de
injeção de falhas desta correção passaram; a execução real permanece reservada
à VM isolada.

## Implementado

- Defaults externos no Linux e instalações Windows novas; variáveis de caminhos
  preservadas; compatibilidade temporária com layout Windows existente.
- Credencial inicial externa, configurável, exclusiva e restrita; senha por
  ambiente não gera arquivo; contas existentes preservadas.
- `/health` público, SQLite somente leitura, sem criação de DB ausente, resposta
  200/503, versão/timestamp e estado dos diretórios sem expor caminhos.
- Upload de releases em blocos, limite e preservação do pacote anterior em
  duplicação de versão.
- Unidade systemd com um worker, loopback, proxy local confiável e journal.
- Referência Nginx HTTP, headers, buffering, limite e timeouts de upload.
- Instalador, preflight offline/online, backup, restore e update/rollback.
  Helpers Python compartilham validação e são testáveis com dados sintéticos.
- Dependências diretas e transitivas delimitadas; testes separados.
- Runbook com instalação, migração, DNS `.local`, Raspberry piloto e retorno Windows.

## Verificações realizadas

| Verificação | Resultado |
|---|---|
| Testes Python de implantação, saúde/versão do Player e permissões | **58 passaram** |
| Reexecução da suíte de implantação após ajuste final do helper | **19 passaram** |
| AST dos Python alterados e testes | Passou, sem importar a aplicação para parsing |
| Parsing Jinja dos templates | **31 templates passaram** |
| `bash -n` | **5 scripts passaram**, Git Bash |
| JavaScript existente de testes de Players | Sintaxe e execução passaram |
| `pip check` do ambiente local | Sem dependências quebradas |
| systemd | Estrutura e argumentos verificados; não executado systemd-analyze |
| Nginx | Estrutura/requisitos verificados; não executado nginx -t |
| Exemplos env | Validados por testes, sem segredo real |
| LF em deploy/linux | Passou |
| Busca de endereços privados nos novos artefatos de implantação | Nenhuma ocorrência |
| `git diff --check` | Passou; aviso de CRLF em documentação preexistente fora deste trabalho |

Testes cobrem: saúde normal e indisponível, ausência/corrupção de banco,
permissão negada simulada, diretórios ausentes, credencial exclusiva, conta
existente, cookie HTTP, proxy confiável e não confiável, IP de auditoria, logout,
upload duplicado de release, mídia estática, backup SQLite com WAL, corrupção
de backup, retenção limitada, backup incompleto, env inválido, preflight com
bloqueadores simulados, monitor único, rollback de restore e rollback de troca
de código/venv com operações externas substituídas.

A suíte usa banco/diretórios temporários definidos antes de importar app.
Não foi executado nenhum dos scripts de instalação/backup/restore/update contra
o ambiente operacional. Testes de helpers usam dados sintéticos; não copiam o
banco, mídias ou releases reais. O arquivo de credenciais operacional existente
não foi lido nem alterado.

Os avisos Python são de descontinuação do `on_event` do FastAPI, mantido para
evitar refatoração de ciclo de vida nesta implantação.

## Pendências obrigatórias na homologação

- WSL não instalado; Docker não disponível como comando; nenhum deles instalado
  por esta tarefa.
- ShellCheck, systemd-analyze e Nginx não disponíveis para validação nativa local.
- Instalação limpa dos wheels no Linux/arquitetura escolhida.
- Permissões POSIX reais, isolamento systemd, parada/reinício e conversões
  LibreOffice/ffprobe com fontes e arquivos representativos.
- DNS corporativo e resolução `.local` no Raspberry, proxy e upload grande.
- Backup, restore e rollback completos em VM isolada, incluindo falhas durante
  troca de diretórios e teste de recuperação com o commit correspondente.
- Inventário/checksums dos dados reais somente na janela de migração autorizada.

O restore automático recupera dados anteriores quando a tentativa falha.
O update recupera código/venv; se a versão anterior também falhar, preserva o
banco e exige diagnóstico antes do restore explícito. Essa decisão evita
descartar dados em uma falha causada apenas por proxy/rede.

## Arquivos desta entrega para revisão

Arquivos novos:

```text
deploy/linux/digital-signage.env.example
deploy/linux/digital-signage.service
deploy/linux/nginx-digital-signage.conf.example
deploy/linux/install-server.sh
deploy/linux/preflight-server.sh
deploy/linux/backup-server.sh
deploy/linux/restore-server.sh
deploy/linux/update-server.sh
deploy/linux/server_ops.py
docs/deploy/LINUX_SERVER.md
docs/deploy/VALIDACAO_LINUX.md
requirements-constraints.txt
requirements-dev.txt
tests/test_linux_deployment.py
```

Arquivos existentes ajustados:

```text
app.py
requirements.txt
tests/test_permissions.py
tests/test_player_version_health.py
```

Essa é a lista exata sugerida para revisão de um futuro commit desta entrega.
`app.py` já continha alterações de outra tarefa; revisar os hunks separadamente.
`tests/test_player_version_health.py` já existia localmente, mas ainda era
untracked: foi adicionado apenas o caminho temporário da credencial ao fixture.
Não incluir alterações preexistentes automaticamente sem revisar seu conteúdo.

Branch mantida: `redesign-workspace-v4`. Nenhum commit, push, reset, restore Git,
clean ou troca de branch foi realizado. Alterações anteriores em templates e
documentação de arquitetura foram preservadas, assim como o teste JavaScript
preexistente. Não houve SSH, sudo, acesso à VM, alteração de DNS/Nginx/firewall,
execução de serviço de produção ou alteração de Raspberry Pi.

O procedimento operacional completo está em [LINUX_SERVER.md](LINUX_SERVER.md).
