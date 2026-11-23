"""`freitask-mcp-http`: sobe o servidor remoto. A referência de configuração é `--help`."""

from __future__ import annotations

import os
import sys

import uvicorn

from .config import AJUDA, ConfigInvalida, do_ambiente
from .servidor import criar_servidor


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if argv in (["--help"], ["-h"]):
        print(AJUDA, end="")
        return
    if argv:
        print("uso: freitask-mcp-http [--help]", file=sys.stderr)
        raise SystemExit(2)
    try:
        servidor = criar_servidor(do_ambiente(os.environ))
    except ConfigInvalida as e:
        print(f"freitask-mcp-http: {e}", file=sys.stderr)
        raise SystemExit(1) from None
    config = servidor.config
    # Sem access log (a URL do login leva o pedido) e sem `proxy_headers`: nenhum endereço sai de
    # cabeçalho da requisição.
    uvicorn.run(
        servidor.app,
        host=config.host,
        port=config.porta,
        access_log=False,
        proxy_headers=False,
        server_header=False,
    )


if __name__ == "__main__":
    main()
