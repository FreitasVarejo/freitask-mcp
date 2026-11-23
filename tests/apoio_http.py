"""Apoio dos testes do servidor remoto: relógio de teste, fluxo OAuth escrito à mão e o `/mcp`.

Tudo contra o app ASGI da fábrica (`criar_servidor`), pelo `TestClient`, sem subir processo. O fluxo
à mão deixa cada passo a vista para o teste mexer nele; o do SDK está em `test_oauth_sdk.py`.
"""

import base64
import hashlib
import json
import secrets
import time
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from starlette.testclient import TestClient

from freitask_mcp.config import Configuracao
from freitask_mcp.servidor import criar_servidor

URL = "http://127.0.0.1:8421"
SENHA = "senha-de-mentira-com-16-ou-mais"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"
CINCO = {"freitask_list", "freitask_show", "freitask_doctor", "freitask_new", "freitask_set"}
CABECALHOS_MCP = {"Accept": "application/json, text/event-stream"}


class RelogioFalso:
    """Parte do relógio real (o SDK confere validade também por ele) e só avança dali."""

    def __init__(self) -> None:
        self.agora = time.time()

    def __call__(self) -> float:
        return self.agora

    def avancar(self, segundos: float) -> None:
        self.agora += segundos


@contextmanager
def no_ar(config: Configuracao):
    """Monta o servidor pela fábrica e o põe a rodar (com o lifespan do app)."""
    servidor = criar_servidor(config)
    with TestClient(servidor.app, base_url=config.url) as cliente:
        yield servidor, cliente


def pkce() -> tuple[str, str]:
    verificador = secrets.token_urlsafe(48)
    desafio = (
        base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verificador, desafio


def registrar(c: TestClient, redirects=(CALLBACK,), **extra):
    corpo = {
        "client_name": "Cliente de teste",
        "redirect_uris": list(redirects),
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_post",
        **extra,
    }
    return c.post("/register", json=corpo)


def iniciar(c: TestClient, cliente: dict, desafio: str, state="estado-1", redirect=CALLBACK):
    """O `GET /authorize`: devolve a resposta (302 para `/login?t=...` se tudo certo)."""
    return c.get(
        "/authorize",
        params={
            "client_id": cliente["client_id"],
            "redirect_uri": redirect,
            "response_type": "code",
            "code_challenge": desafio,
            "code_challenge_method": "S256",
            "state": state,
            "resource": f"{URL}/mcp",
        },
        follow_redirects=False,
    )


def pedido_de(resposta) -> str:
    """O `t` do `/login?t=...` para onde o `/authorize` mandou."""
    assert resposta.status_code == 302, resposta.text
    return parse_qs(urlsplit(resposta.headers["location"]).query)["t"][0]


def entrar(c: TestClient, t: str, senha: str):
    return c.post("/login", data={"t": t, "senha": senha}, follow_redirects=False)


def codigo_de(resposta) -> tuple[str, str | None]:
    """O `code` e o `state` do `Location` do login certo."""
    assert resposta.status_code == 303, resposta.text
    consulta = parse_qs(urlsplit(resposta.headers["location"]).query)
    return consulta["code"][0], (consulta.get("state") or [None])[0]


def trocar(c: TestClient, cliente: dict, codigo: str, verificador: str, redirect=CALLBACK):
    return c.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": codigo,
            "redirect_uri": redirect,
            "client_id": cliente["client_id"],
            "client_secret": cliente["client_secret"],
            "code_verifier": verificador,
        },
    )


def renovar(c: TestClient, cliente: dict, refresco: str):
    return c.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresco,
            "client_id": cliente["client_id"],
            "client_secret": cliente["client_secret"],
        },
    )


@dataclass
class Sessao:
    cliente: dict
    acesso: str
    refresco: str


def conectar(c: TestClient, **extra) -> Sessao:
    """Registro, authorize, login com a senha certa e troca do código: o caminho feliz inteiro."""
    cliente = registrar(c, **extra).json()
    verificador, desafio = pkce()
    t = pedido_de(iniciar(c, cliente, desafio))
    codigo, _ = codigo_de(entrar(c, t, SENHA))
    resposta = trocar(c, cliente, codigo, verificador)
    assert resposta.status_code == 200, resposta.text
    tokens = resposta.json()
    return Sessao(cliente, tokens["access_token"], tokens["refresh_token"])


def revogar(c: TestClient, sessao: Sessao, token: str):
    return c.post(
        "/revoke",
        data={
            "token": token,
            "client_id": sessao.cliente["client_id"],
            "client_secret": sessao.cliente["client_secret"],
        },
    )


def mcp(c: TestClient, token: str | None, metodo: str, params=None, caminho="/mcp", **cabecalhos):
    """Um pedido JSON-RPC ao `caminho`. Sem `token`, sem `Authorization`."""
    h = dict(CABECALHOS_MCP)
    if token is not None:
        h["Authorization"] = f"Bearer {token}"
    h.update(cabecalhos)
    # URL inteira: "//mcp" sozinho, o httpx o leria como "host mcp".
    return c.post(
        f"{URL}{caminho}",
        json={"jsonrpc": "2.0", "id": 1, "method": metodo, "params": params or {}},
        headers=h,
    )


def chamar_tool(c: TestClient, token: str, nome: str, **argumentos) -> dict:
    """Chama a tool por `/mcp` e devolve o JSON que ela respondeu (ou levanta se foi erro)."""
    resposta = mcp(c, token, "tools/call", {"name": nome, "arguments": argumentos})
    assert resposta.status_code == 200, resposta.text
    resultado = resposta.json()["result"]
    texto = "".join(b.get("text", "") for b in resultado["content"])
    assert not resultado.get("isError"), texto
    return json.loads(texto)
