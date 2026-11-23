# freitask-mcp

Um servidor MCP que embrulha a CLI do [freitask](https://github.com/FreitasVarejo/freitask.nvim)
para o claude.ai: ler, criar e mudar tasks do vault com a validação do motor de verdade, em vez de
editar o Markdown à mão. Cada tool roda `freitask <sub>` com argumentos em lista, sem shell, e lê o
`--json`. O vault é o do `HOME` de quem roda: `$HOME/ObsidianVault`.

## As cinco tools

| Tool | Faz |
|---|---|
| `freitask_list` | lista as tasks, filtrando por projeto e fase |
| `freitask_show` | mostra uma task pelo id |
| `freitask_doctor` | diagnostica o vault (só leitura, nunca repara) |
| `freitask_new` | cria uma task num projeto que já existe |
| `freitask_set` | muda fase (só `todo`, `abstract`, `tip`, `quote`), título, descrição, datas e dependência |

Nada além disso: não há `archive`, `move`, `delete` nem `rebuild`.

## Rodar

Precisa de Python 3.12, de [uv](https://docs.astral.sh/uv/) e do `freitask` no `PATH`.

```bash
uv sync
```

**Local, em stdio** (sem login; para o Inspector do MCP e clientes locais):

```bash
uv run freitask-mcp
```

**Remoto, em HTTP** (Streamable HTTP em `/mcp`, com login próprio por OAuth, para o claude.ai):

```bash
uv run freitask-mcp-http --help   # a referência: variáveis de ambiente e o que impede a subida
```

O servidor remoto só sobe com a URL pública e a senha válidas, a pasta do estado gravável e fora
do vault, o vault no lugar e o `freitask` respondendo. Ele guarda clientes e tokens (só o hash dos
tokens) na pasta do estado, e registra cada escrita em `auditoria.jsonl` nela. Quem for exposto na
internet fica atrás de um proxy com TLS; o endereço que o servidor escreve nos metadados vem de
`FREITASK_MCP_URL`, nunca de cabeçalho da requisição.

## Desenvolver

```bash
uv run ruff check .
uv run pytest -q
```

Os testes rodam com `HOME` temporário. O contrato do repo está em `AGENTS.md`, e o roteiro de
testar à mão em `docs/agents/verificacao.md`.
