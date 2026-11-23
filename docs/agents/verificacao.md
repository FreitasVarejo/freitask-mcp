# Verificação

Adaptador da skill `verificar` neste repo. O bloco abaixo é lido pelo script da
skill: globs do que mudou, dois espaços, o comando na raiz do repo.

```portoes
*  uv run ruff check .
*  uv run pytest -q
```

## Como testar à mão

- Tool nova ou mudada: com `HOME` num diretório temporário,
  `uv run mcp dev src/freitask_mcp/local.py` abre o Inspector; conecte, liste as
  tools e rode a que mudou, com o caminho infeliz (id que não existe, projeto
  que não existe, fase fora das permitidas).
