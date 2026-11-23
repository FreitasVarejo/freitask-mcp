"""O servidor remoto: a fábrica que monta o `FastMCP` com OAuth próprio e devolve tudo.

`criar_servidor(config)` valida a configuração (a trava de subida) e devolve o `Servidor`, com o
app ASGI pronto. Relógio, pasta de estado e senha entram pela `Configuracao`, sem variável global,
e o teste alcança o app sem subir processo.

Todo endereço que o servidor escreve (metadados, redirect, o `WWW-Authenticate` do 401) sai de
`Configuracao.url`, nunca de cabeçalho da requisição; o `Host` aceito em `/mcp` é o da URL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from . import login
from .auditoria import Auditoria
from .config import Configuracao, validar
from .freios import Janela
from .provedor import Provedor
from .tools import register_tools


@dataclass
class Servidor:
    config: Configuracao
    mcp: FastMCP
    provedor: Provedor
    auditoria: Auditoria
    freio_de_senha: Janela
    app: Starlette


def _caminho_canonico(caminho: str) -> str:
    """`//mcp` e `/mcp/` são `/mcp`: o Starlette os trataria como rotas outras (ou redirecionaria),
    e a regra de que `/mcp` pede bearer não pode depender da grafia."""
    limpo = re.sub(r"/{2,}", "/", caminho)
    return limpo.rstrip("/") or "/"


class Guarda:
    """O que vem antes das rotas: caminho canônico e o freio do `/register`.

    O SDK só devolve 400 num registro recusado; o 429 do 21º registro da hora precisa estar aqui.
    No modo de cliente fixo não há freio (`registros` é None): o `/register` é 404 sempre.
    """

    def __init__(self, app: ASGIApp, registros: Janela | None):
        self.app = app
        self.registros = registros

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            caminho = _caminho_canonico(scope["path"])
            if caminho != scope["path"]:
                scope = {**scope, "path": caminho, "raw_path": caminho.encode()}
            if (
                self.registros is not None
                and caminho == "/register"
                and scope["method"] == "POST"
                and self.registros.cheia()
            ):
                resposta = JSONResponse(
                    {
                        "error": "too_many_requests",
                        "error_description": "registros demais; espere",
                    },
                    status_code=429,
                    headers={"Retry-After": str(self.registros.espera())},
                )
                return await resposta(scope, receive, send)
        await self.app(scope, receive, send)


def criar_servidor(config: Configuracao) -> Servidor:
    validar(config)
    provedor = Provedor(config)
    auditoria = Auditoria(config.estado / "auditoria.jsonl", config.relogio)
    origem = urlsplit(config.url)
    mcp = FastMCP(
        "freitask",
        auth_server_provider=provedor,
        auth=AuthSettings(
            issuer_url=config.url,
            resource_server_url=config.recurso,
            # Sem escopo: um escopo exigido recusaria no registro o cliente que pede outro, e aqui
            # há um dono e um recurso. Todo token sai carimbado com o recurso do servidor.
            required_scopes=None,
            validate_token_resource=True,
            # Com o cliente fixo o SDK não monta `/register` nem põe `registration_endpoint`.
            client_registration_options=ClientRegistrationOptions(enabled=config.cliente is None),
            revocation_options=RevocationOptions(enabled=True),
        ),
        host=config.host,
        port=config.porta,
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[origem.netloc],
            allowed_origins=[config.url],
        ),
    )
    register_tools(mcp, audited=auditoria)
    freio_de_senha = login.registrar_rotas(mcp, config, provedor)
    app = mcp.streamable_http_app()
    app.add_middleware(Guarda, registros=provedor.registros if config.cliente is None else None)
    return Servidor(config, mcp, provedor, auditoria, freio_de_senha, app)
