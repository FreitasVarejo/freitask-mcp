# AGENTS.md — freitask-mcp

O servidor MCP do freitask: as tools `freitask_*`, que embrulham a CLI para o
claude.ai. Repo público: nada de host, usuário, IP ou segredo entra aqui.

- O ticket é a task do freitask, em `~/ObsidianVault/projects/workflow-ia/tasks/`,
  e a branch tem o id dela. O contrato do vault: `~/ObsidianVault/AGENTS.md`.
- O porquê mora no vault: `~/ObsidianVault/projects/workflow-ia/decisoes/`.
- A CLI embrulhada e o contrato dela (JSON, códigos de saída):
  `~/dev/freitask.nvim/docs/freitask.md`. Regra de task mora lá; aqui só há
  tradução entre a tool e o comando.
- Todo teste roda com `HOME` temporário: o vault da CLI é o do `HOME`, e o vault
  real fica fora dos testes.
- Passo que muda o pi01 (build, `up -d`, mover pasta) pede o OK do dono.
- Portões e como testar à mão: `~/dev/freitask-mcp/docs/agents/verificacao.md`.
