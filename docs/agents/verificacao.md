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
