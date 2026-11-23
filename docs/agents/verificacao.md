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

  O ícone do conector: as três rotas respondem sem bearer, só GET, e cada pedido deixa uma linha
  no log do servidor (o caminho e o começo do `User-Agent`). O `initialize` leva os dois `icons`,
  com o endereço de `$U`, e a tela de login traz o desenho (o `t` vem do `Location` do `/authorize`, como no modo de cliente fixo abaixo):

  ```bash
  for r in /favicon.ico /favicon.svg /icon.png; do
    curl -si -A "Mozilla/5.0 teste" $U$r | head -6   # 200, tipo certo, Cache-Control: private
  done
  curl -si -X POST $U/icon.png | head -1             # 405
  # com um access token (veja os testes), o initialize devolve serverInfo.icons:
  #   curl -s -X POST $U/mcp -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  #     -H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,
  #     "method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},
  #     "clientInfo":{"name":"x","version":"0"}}}'
  curl -s "$U/login?t=$T" | grep -o '<link[^>]*>\|<img[^>]*>'  # o link do ícone e o desenho
  ```

  Para a imagem de produção: `uv build --wheel --out-dir "$(mktemp -d)"` e `unzip -l` no `.whl`
  tem de listar os três arquivos em `freitask_mcp/imagens/`.

  O modo de cliente fixo: o mesmo comando, com `FREITASK_MCP_CLIENTE` de mentira (32 caracteres
  ou mais, só letras, dígitos, `-` e `_`). O `/register` passa a ser 404, e o `/authorize` só leva
  à tela de login com o ID certo:

  ```bash
  ID=cliente-fixo-de-mentira-0123456789abcdef
  # suba o servidor como acima, com FREITASK_MCP_CLIENTE=$ID no ambiente
  curl -si -X POST $U/register -H 'Content-Type: application/json' \
    -d '{"redirect_uris":["https://claude.ai/api/mcp/auth_callback"]}'  # 404
  curl -s $U/.well-known/oauth-authorization-server   # sem registration_endpoint
  A="$U/authorize?response_type=code&redirect_uri=https://claude.ai/api/mcp/auth_callback"
  A="$A&code_challenge=ZXhlbXBsby1kZS1kZXNhZmlvLXBrY2UtczI1Ni1kUk8tdGVzdGU&code_challenge_method=S256"
  curl -si "$A&client_id=$ID" | grep -i '^location'    # .../login?t=...
  curl -si "$A&client_id=outro-id" | head -1           # 400, sem Location
  ```

  A recusa de subida: rode o mesmo comando sem uma das variáveis (ou com a senha curta, ou com
  `FREITASK_MCP_ESTADO` dentro de `$HOME/ObsidianVault`, ou com `FREITASK_MCP_CLIENTE` curto ou
  com caractere fora da lista) e o processo sai com código 1 e uma
  linha que diz a causa. `freitask-mcp-http --help` lista as variáveis.
