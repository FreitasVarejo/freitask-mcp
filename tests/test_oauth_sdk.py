"""O caminho inteiro com o cliente OAuth do próprio SDK (`mcp.client.auth`), contra o app ASGI.

É a mesma descoberta que o claude.ai faz: metadados do recurso, metadados do servidor de
autorização, registro, authorize, token. O `redirect_handler` do teste faz o papel do navegador:
busca a URL do authorize, envia o POST do login e tira o `code` do `Location`. Nada abre navegador.
"""

import asyncio
import json
import logging
import time

import httpx
import pytest

from freitask_mcp.servidor import criar_servidor

from .apoio_http import CINCO, SENHA
from .apoio_sdk import Armazem, Navegador, sessao_mcp


def texto(resultado) -> str:
    return "".join(getattr(bloco, "text", "") for bloco in resultado.content)


def test_o_caminho_inteiro_descoberta_registro_login_token_e_uma_tool(
    config, vault, caplog, relogio
):
    servidor = criar_servidor(config)
    armazem = Armazem()
    transporte = httpx.ASGITransport(app=servidor.app)
    navegador = Navegador(transporte, SENHA)
    caplog.set_level(logging.DEBUG)

    async def corpo(sessao, auth):
        tools = {t.name for t in (await sessao.list_tools()).tools}
        criada = await sessao.call_tool(
            "freitask_new", {"projeto": "beta", "id": "pelo-sdk", "titulo": "Criada pelo SDK"}
        )
        assert not criada.isError, texto(criada)
        return tools, json.loads(texto(criada))

    async def rodar():
        async with servidor.app.router.lifespan_context(servidor.app):
            return await sessao_mcp(transporte, armazem, navegador, corpo)

    tools, criada = asyncio.run(asyncio.wait_for(rodar(), timeout=60))

    assert tools == CINCO
    assert navegador.passos == ["login"], "um login só"
    assert armazem.cliente is not None and armazem.cliente.client_id, "registrou o cliente"
    assert armazem.tokens.access_token and armazem.tokens.refresh_token
    assert criada["path"] == "projects/beta/tasks/pelo-sdk.md"
    assert "pelo-sdk" in (vault / "CURRENT.md").read_text(encoding="utf-8")
    [linha] = [
        json.loads(x)
        for x in (config.estado / "auditoria.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert linha["client_id"] == armazem.cliente.client_id, "o chamador chegou à tool síncrona"
    # Nada de senha, token nem código no log do servidor (o do cliente do SDK fica de fora).
    do_servidor = "\n".join(
        r.getMessage() for r in caplog.records if not r.name.startswith("mcp.client")
    )
    assert SENHA not in do_servidor
    assert armazem.tokens.access_token not in do_servidor
    assert armazem.tokens.refresh_token not in do_servidor


def test_o_cliente_do_sdk_renova_sozinho_quando_o_acesso_vence(config, vault, relogio):
    servidor = criar_servidor(config)
    armazem = Armazem()
    transporte = httpx.ASGITransport(app=servidor.app)
    navegador = Navegador(transporte, SENHA)

    async def corpo(sessao, auth):
        antes = armazem.tokens.access_token
        assert {t.name for t in (await sessao.list_tools()).tools} == CINCO
        relogio.avancar(3601)  # para o servidor o acesso venceu
        # O cliente conta a validade pelo relógio real: fazê-lo ver o acesso vencido também.
        auth.context.token_expiry_time = time.time() - 1
        assert {t.name for t in (await sessao.list_tools()).tools} == CINCO
        return antes

    async def rodar():
        async with servidor.app.router.lifespan_context(servidor.app):
            return await sessao_mcp(transporte, armazem, navegador, corpo)

    antes = asyncio.run(asyncio.wait_for(rodar(), timeout=60))

    assert armazem.tokens.access_token != antes, "o refresh girou o par"
    assert navegador.passos == ["login"], "renovou sem pedir a senha de novo"


def test_com_a_senha_errada_o_cliente_do_sdk_nao_conecta(config, vault):
    servidor = criar_servidor(config)
    transporte = httpx.ASGITransport(app=servidor.app)
    navegador = Navegador(transporte, "senha-errada-de-mentira")

    async def listar(sessao, auth):
        return await sessao.list_tools()

    async def rodar():
        async with servidor.app.router.lifespan_context(servidor.app):
            await sessao_mcp(transporte, Armazem(), navegador, listar)

    with pytest.raises(BaseException):  # noqa: B017 -- o SDK embrulha o erro do handler
        asyncio.run(asyncio.wait_for(rodar(), timeout=60))

    assert navegador.status_do_login == [401]
    assert navegador.passos == []
    assert servidor.provedor._codigos == {}
