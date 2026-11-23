"""O ícone do conector: as três rotas sem bearer que o servem e o `icons` do `initialize`.

O desenho é o monograma `f_` e vive em `imagens/` como dado do pacote, lido por
`importlib.resources` (o wheel que a imagem instala o leva). Nada aqui lê a requisição além do
caminho e do começo do `User-Agent`, que vai ao log para o dono ver quem pede o ícone.
"""

from __future__ import annotations

import logging
from importlib import resources

from mcp.server.fastmcp import FastMCP
from mcp.types import Icon
from starlette.requests import Request
from starlette.responses import Response

log = logging.getLogger(__name__)

COMECO_DO_AGENTE = 60
# `private` impede a Cloudflare de guardar a resposta; guardada, o log não veria os pedidos.
CABECALHOS = {
    "Cache-Control": "private, max-age=3600",
    "X-Content-Type-Options": "nosniff",
}
CSP_DO_SVG = "default-src 'none'; style-src 'unsafe-inline'"

# caminho público -> (arquivo em `imagens/`, tipo)
ROTAS = {
    "/favicon.ico": ("favicon.ico", "image/x-icon"),
    "/favicon.svg": ("icon.svg", "image/svg+xml"),
    "/icon.png": ("icon.png", "image/png"),
}


def icones(url: str) -> list[Icon]:
    """Os dois itens do `serverInfo.icons`, com o endereço montado da URL configurada."""
    return [
        Icon(src=f"{url}/icon.png", mimeType="image/png", sizes=["128x128"]),
        Icon(src=f"{url}/favicon.svg", mimeType="image/svg+xml", sizes=["any"]),
    ]


def _ler(arquivo: str) -> bytes:
    return (resources.files("freitask_mcp") / "imagens" / arquivo).read_bytes()


def _agente(request: Request) -> str:
    """O começo do `User-Agent` para o log: só ASCII imprimível, o resto vira `?`."""
    bruto = request.headers.get("user-agent", "")[:COMECO_DO_AGENTE]
    return "".join(c if " " <= c <= "~" else "?" for c in bruto)


def registrar_rotas(mcp: FastMCP) -> None:
    """Registra as três rotas no `mcp`, só GET e sem bearer."""
    for caminho, (arquivo, tipo) in ROTAS.items():
        corpo = _ler(arquivo)
        cabecalhos = dict(CABECALHOS)
        if tipo == "image/svg+xml":
            cabecalhos["Content-Security-Policy"] = CSP_DO_SVG

        def fazer(caminho=caminho, tipo=tipo, corpo=corpo, cabecalhos=cabecalhos):
            async def servir(request: Request) -> Response:
                log.info("ícone pedido: %s user-agent=%s", caminho, _agente(request))
                return Response(corpo, media_type=tipo, headers=cabecalhos)

            return servir

        mcp.custom_route(caminho, methods=["GET"])(fazer())
