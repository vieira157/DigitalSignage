# Regras do redesign gradual

Este diretório registra diretrizes; não contém implementação do redesign. A documentação de referência foi preparada em 14/10/2026 na branch `redesign-workspace-v4`.

## Referências obrigatórias

- [Arquitetura atual](../arquitetura/ARQUITETURA_ATUAL.md)
- [Inventário funcional por página](../arquitetura/INVENTARIO_FUNCIONAL.md)
- [Riscos conhecidos](../arquitetura/RISCOS_CONHECIDOS.md)
- [Vocabulário do produto](../arquitetura/VOCABULARIO_DO_PRODUTO.md)

## Regras de execução

1. A aplicação existente é a fonte da verdade funcional: código, rotas, dados, permissões, integrações e comportamentos existentes.
2. Mockups são referências visuais. Seus dados ilustrativos não devem ser transformados em dados reais ou valores fixos da aplicação.
3. Nenhuma funcionalidade deve ser removida apenas por não aparecer no mockup.
4. Endpoints, métodos, contratos JSON, parâmetros de formulários e variáveis Jinja2 devem ser preservados. Inventariar também seletores e eventos usados pelos scripts antes de alterar o DOM.
5. Permissões devem continuar protegidas no backend. Ocultar um botão não substitui autorização.
6. Alterações de segurança devem ser separadas de alterações visuais. Registrar os riscos; não corrigi-los implicitamente como parte do layout.
7. Alterações no banco exigem planejamento específico, migração e estratégia de recuperação. Não usar redesign como motivo para recriar dados ou tabelas.
8. A comunicação com os Dispositivos não deve ser modificada implicitamente. Preservar identificação, credenciais, sincronização, heartbeat, manifesto, comandos e ACKs.
9. O redesign será realizado uma página por vez, mantendo o conceito de Tela como recurso operacional principal.
10. Cada etapa deve possuir validação visual e funcional. A validação deve abranger estados vazios, falhas, perfis, restrições por Tela e ações existentes.
11. O dashboard (`/admin`, `admin.html`) será a primeira página analisada.
12. O template base (`base_admin.html`) será modernizado. Como é compartilhado, suas mudanças exigem verificação das páginas dependentes; não presumir que o impacto se limita ao dashboard.
13. `main` permanece referência estável. Melhorias de documentação e interface devem ocorrer em `redesign-workspace-v4` ou branches derivadas dela.
14. Arquivos operacionais, credenciais, SQLite, mídias, releases, backups e logs devem permanecer fora do rastreamento. Exemplos de ambiente podem ser rastreados sem valores reais.

## Contratos operacionais a preservar

- Edição em rascunho, revisão/publicação e atribuição são etapas distintas.
- Restaurar histórico de playlist não publica a restauração diretamente.
- Arquivar mídia tem efeito sobre o payload de reprodução; não descrever a ação como simples organização visual.
- Agendamentos são semanais e por Tela, com conflito validado no servidor e suporte a passagem de meia-noite.
- Operações remotas são assíncronas: envio à fila não equivale a execução concluída.
- Saúde, sincronização, reprodução e disponibilidade física do Display são conceitos diferentes.
- Recursos ausentes no mockup continuam fazendo parte do escopo funcional existente, como exportação de eventos, manutenção, permissões por Tela e histórico de publicação.

## Limites e validação das próximas etapas

Antes de modificar cada página, identificar seu template, contexto Jinja2, formulários, endpoints e scripts no inventário. Definir o que será validado e comparar antes/depois com dados controlados e perfis diferentes.

A análise anterior foi estática. Não iniciar a aplicação nem importar `app.py` apenas para inspecionar: a importação modifica dados e pode gravar credenciais iniciais. Testes existentes também criam arquivos. Validações executáveis futuras precisam de ambiente isolado e planejamento dos efeitos persistentes.

O texto recebido para esta tarefa termina em “O template base será modernizado”. A diretriz foi registrada no item 12, sem inferir uma continuação, prazo ou implementação adicional.

Nenhuma alteração funcional, visual, de banco ou comunicação é executada pela criação desta documentação. Os riscos permanecem abertos até tarefas específicas autorizadas.
