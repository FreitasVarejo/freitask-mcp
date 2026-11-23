"""Apoio dos testes com o cliente OAuth do SDK: armazém de tokens, "navegador" e sessão MCP."""

from urllib.parse import parse_qs, urlsplit

import httpx
from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

from .apoio_http import CALLBACK, URL


class Armazem:
    """O `TokenStorage` do cliente: em memória."""

    def __init__(self) -> None:
        self.tokens: OAuthToken | None = None
        self.cliente: OAuthClientInformationFull | None = None

    async def get_tokens(self):
        return self.tokens

    async def set_tokens(self, tokens):
        self.tokens = tokens

    async def get_client_info(self):
        return self.cliente

    async def set_client_info(self, client_info):
        self.cliente = client_info


class Navegador:
    """Quem abre a URL do authorize e digita a senha: devolve o `code` e o `state` ao cliente."""

    def __init__(
        self, transporte: httpx.AsyncBaseTransport | None, senha: str, url: str = URL
    ) -> None:
        self.transporte = transporte
        self.senha = senha
        self.url = url
        self.passos: list[str] = []
        self.status_do_login: list[int] = []
        self._volta: tuple[str, str | None] | None = None

    async def abrir(self, url: str) -> None:
        async with httpx.AsyncClient(transport=self.transporte, base_url=self.url) as http:
            authorize = await http.get(url)
            assert authorize.status_code == 302, authorize.text
            login = authorize.headers["location"]
            assert login.startswith(f"{self.url}/login?t="), login
            tela = await http.get(login)
            assert tela.status_code == 200 and 'type="password"' in tela.text
            t = parse_qs(urlsplit(login).query)["t"][0]
            voltou = await http.post("/login", data={"t": t, "senha": self.senha})
            self.status_do_login.append(voltou.status_code)
            if voltou.status_code != 303:
                raise RuntimeError(f"login recusado: {voltou.status_code}")
            volta = voltou.headers["location"]
            assert volta.startswith(CALLBACK)
            consulta = parse_qs(urlsplit(volta).query)
            self.passos.append("login")
            self._volta = (consulta["code"][0], (consulta.get("state") or [None])[0])

    async def callback(self) -> tuple[str, str | None]:
        assert self._volta is not None
        return self._volta


def provedor_do_cliente(armazem: Armazem, navegador: Navegador) -> OAuthClientProvider:
    return OAuthClientProvider(
        server_url=f"{navegador.url}/mcp",
        client_metadata=OAuthClientMetadata(
            client_name="Cliente do SDK",
            redirect_uris=[CALLBACK],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
        ),
        storage=armazem,
        redirect_handler=navegador.abrir,
        callback_handler=navegador.callback,
    )


async def sessao_mcp(transporte, armazem: Armazem, navegador: Navegador, corpo):
    """Abre a sessão MCP autenticada pelo cliente do SDK e roda `corpo(sessao, auth)`.

    `transporte` é o ASGI do app (sem processo) ou `None` para a rede de verdade.
    """
    navegador.transporte = transporte
    auth = provedor_do_cliente(armazem, navegador)
    url = navegador.url
    async with httpx.AsyncClient(transport=transporte, base_url=url, auth=auth) as http:
        async with streamable_http_client(f"{url}/mcp", http_client=http) as (leitura, escrita, _):
            async with ClientSession(leitura, escrita) as sessao:
                await sessao.initialize()
                return await corpo(sessao, auth)
