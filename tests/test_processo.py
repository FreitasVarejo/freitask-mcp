"""O servidor de verdade: `freitask-mcp-http` num processo, na rede de loopback, com uvicorn.

É o único teste que passa pela fiação do `main` (uvicorn sem `proxy_headers`, sem access log) e
pelo `Host` real. O resto roda contra o app ASGI, sem processo.
"""

import asyncio
import os
import socket
import subprocess
import sys
import time

import httpx
import pytest

from .apoio_http import CINCO, SENHA
from .apoio_sdk import Armazem, Navegador, sessao_mcp


def porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def processo(vault, tmp_path):
    porta = porta_livre()
    url = f"http://127.0.0.1:{porta}"
    estado = tmp_path / "estado"
    estado.mkdir()
    env = {
        **os.environ,
        "FREITASK_MCP_URL": url,
        "FREITASK_MCP_SENHA": SENHA,
        "FREITASK_MCP_ESTADO": str(estado),
        "FREITASK_MCP_PORTA": str(porta),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "freitask_mcp.remoto"],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        limite = time.monotonic() + 30
        while time.monotonic() < limite:
            if proc.poll() is not None:
                pytest.fail(f"o servidor saiu: {proc.stdout.read()}")
            try:
                if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                    break
            except httpx.TransportError:
                time.sleep(0.1)
        else:
            pytest.fail("o servidor não respondeu em 30 s")
        yield url, estado, proc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_o_processo_responde_health_e_recusa_o_mcp_sem_bearer(processo):
    url, _, _ = processo

    assert httpx.get(f"{url}/health").json() == {"status": "ok"}
    sem = httpx.post(
        f"{url}/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Accept": "application/json, text/event-stream"},
    )
    assert sem.status_code == 401
    assert (
        f'resource_metadata="{url}/.well-known/oauth-protected-resource/mcp"'
        in (sem.headers["www-authenticate"])
    )
    metadados = httpx.get(f"{url}/.well-known/oauth-authorization-server").json()
    assert metadados["authorization_endpoint"] == f"{url}/authorize"
    assert metadados["code_challenge_methods_supported"] == ["S256"]


def test_o_processo_com_o_cliente_do_sdk_pela_rede(processo, vault):
    url, estado, proc = processo
    armazem = Armazem()
    navegador = Navegador(None, SENHA, url=url)

    async def corpo(sessao, auth):
        tools = {t.name for t in (await sessao.list_tools()).tools}
        criada = await sessao.call_tool(
            "freitask_new", {"projeto": "beta", "id": "pela-rede", "titulo": "Pela rede"}
        )
        return tools, criada

    tools, criada = asyncio.run(
        asyncio.wait_for(sessao_mcp(None, armazem, navegador, corpo), timeout=60)
    )

    assert tools == CINCO
    assert not criada.isError
    assert "pela-rede" in (vault / "CURRENT.md").read_text(encoding="utf-8")


def test_o_processo_nao_loga_a_url_do_login_nem_segredo(processo):
    url, _, proc = processo
    armazem = Armazem()
    navegador = Navegador(None, SENHA, url=url)

    async def corpo(sessao, auth):
        await sessao.list_tools()

    asyncio.run(asyncio.wait_for(sessao_mcp(None, armazem, navegador, corpo), timeout=60))
    proc.terminate()
    saida = proc.communicate(timeout=10)[0]

    assert "/login" not in saida, "o access log está ligado"
    for segredo in (SENHA, armazem.tokens.access_token, armazem.tokens.refresh_token):
        assert segredo not in saida
