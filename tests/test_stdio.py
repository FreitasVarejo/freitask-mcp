"""O servidor local de ponta a ponta: um cliente MCP em stdio, num processo filho.

O filho herda o ambiente do teste (HOME e XDG_RUNTIME_DIR temporários); o `uv` não os vê.
"""

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .conftest import HOME_REAL


def sessao_stdio(corpo):
    """Sobe `python -m freitask_mcp.local`, abre a sessão e roda `corpo(sessao)`."""
    assert os.environ["HOME"] != HOME_REAL
    parametros = StdioServerParameters(
        command=sys.executable,
        args=["-m", "freitask_mcp.local"],
        env={**os.environ},
    )

    async def rodar():
        async with stdio_client(parametros) as (leitura, escrita):
            async with ClientSession(leitura, escrita) as sessao:
                await sessao.initialize()
                return await corpo(sessao)

    return asyncio.run(asyncio.wait_for(rodar(), timeout=60))


def texto(resultado) -> str:
    return "".join(getattr(bloco, "text", "") for bloco in resultado.content)


def test_o_cliente_stdio_lista_as_tools(vault):
    async def corpo(sessao):
        return sorted(t.name for t in (await sessao.list_tools()).tools)

    assert sessao_stdio(corpo) == ["freitask_doctor", "freitask_list", "freitask_show"]


def test_o_cliente_stdio_le_o_vault_de_fixture(vault):
    async def corpo(sessao):
        lista = await sessao.call_tool("freitask_list", {"projeto": "beta"})
        doctor = await sessao.call_tool("freitask_doctor", {})
        return lista, doctor

    lista, doctor = sessao_stdio(corpo)

    assert not lista.isError
    assert [t["id"] for t in json.loads(texto(lista))["tasks"]] == ["pagar-conta"]
    assert json.loads(texto(doctor))["ok"] is True


def test_o_erro_da_tool_chega_ao_cliente_como_erro(vault):
    async def corpo(sessao):
        return await sessao.call_tool("freitask_show", {"id": "nao-existe"})

    resultado = sessao_stdio(corpo)

    assert resultado.isError
    assert "task não encontrada: nao-existe" in texto(resultado)
