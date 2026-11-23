# Verificação

Adaptador da skill `verificar` neste repo. O bloco abaixo é lido pelo script da
skill: globs do que mudou, dois espaços, o comando na raiz do repo.

```portoes
*  uv run ruff check .
*  uv run pytest -q
```

## Como testar à mão

- Tool nova ou mudada: pelo Inspector em modo CLI, da raiz do repo, depois de
  `uv sync`. O `HOME` temporário entra só no processo do servidor, pelo `-e`:
  no do `uv` ou no do `npx` ele os faz perder o cache.

  ```bash
  S=$(mktemp -d) && mkdir "$S/casa" "$S/run"
  HOME="$S/casa" XDG_RUNTIME_DIR="$S/run" freitask new-project alfa
  /usr/bin/npx -y @modelcontextprotocol/inspector --cli \
    -e HOME="$S/casa" -e XDG_RUNTIME_DIR="$S/run" \
    --method tools/call --tool-name freitask_new \
    --tool-arg projeto=alfa id=teste titulo=Teste \
    -- "$PWD/.venv/bin/freitask-mcp"
  ```

  Rode a tool que mudou e o caminho infeliz dela (id que não existe, projeto
  que não existe, fase fora das permitidas). `--method tools/list` lista as
  tools; o que foi gravado está em `$S/casa/ObsidianVault`.

- Servidor HTTP (`freitask-mcp-http`): sobe em `127.0.0.1` com `HOME` descartável, e o
  `curl` faz o papel do claude.ai. A senha e a porta são de mentira; o estado fica fora do
  vault descartável. O caminho inteiro do login (registro, tela, token, `tools/list`) está nos
  testes (`tests/test_oauth_sdk.py` e `tests/test_processo.py`); à mão basta ver que o servidor
  sobe, recusa e responde.

  ```bash
  S=$(mktemp -d) && mkdir "$S/casa" "$S/run" "$S/estado"
  HOME="$S/casa" XDG_RUNTIME_DIR="$S/run" freitask new-project alfa
  U=http://127.0.0.1:8431
  HOME="$S/casa" XDG_RUNTIME_DIR="$S/run" FREITASK_MCP_URL=$U FREITASK_MCP_PORTA=8431 \
    FREITASK_MCP_SENHA=senha-descartavel-de-teste FREITASK_MCP_ESTADO="$S/estado" \
    .venv/bin/freitask-mcp-http &
  curl -s $U/health                                   # {"status":"ok"}
  curl -si -X POST $U/mcp -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'  # 401 com WWW-Authenticate: resource_metadata=...
  curl -s $U/.well-known/oauth-protected-resource/mcp  # o recurso, com o endereço de $U
  curl -s $U/.well-known/oauth-authorization-server    # os endpoints, todos de $U
  curl -s -X POST $U/register -H 'Content-Type: application/json' \
    -d '{"redirect_uris":["https://x.invalid/cb"]}'    # 400: callback fora da lista
  kill %1; ls -l "$S/estado"                            # estado.json em 0600 (e auditoria.jsonl, se houve escrita)
  ```

  A recusa de subida: rode o mesmo comando sem uma das variáveis (ou com a senha curta, ou com
  `FREITASK_MCP_ESTADO` dentro de `$HOME/ObsidianVault`) e o processo sai com código 1 e uma
  linha que diz a causa. `freitask-mcp-http --help` lista as variáveis.
