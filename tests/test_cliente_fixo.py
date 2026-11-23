"""O modo de cliente fixo (`FREITASK_MCP_CLIENTE`): registro fechado e um cliente só, público.

O protocolo continua sendo do SDK; o que se testa aqui é o que o modo muda: a rota `/register` e o
`registration_endpoint` somem, o único cliente conhecido é o do ID configurado, e token de cliente
que o servidor não conhece não vale (nos dois modos, e é isso que faz a troca de modo cortar o que
o outro emitiu).
"""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from mcp.server.auth.provider import RegistrationError
from mcp.shared.auth import OAuthClientInformationFull

from freitask_mcp.servidor import criar_servidor

from .apoio_http import (
    CALLBACK,
    CINCO,
    CLIENTE_FIXO,
    SENHA,
    URL,
    codigo_de,
    conectar,
    conectar_fixo,
    entrar,
    iniciar,
    mcp,
    no_ar,
    pedido_de,
    pkce,
    registrar,
    renovar,
    trocar,
)
from .apoio_sdk import Armazem, Navegador, sessao_mcp

OUTRO_CALLBACK = "https://claude.com/api/mcp/auth_callback"
METADADOS = "/.well-known/oauth-authorization-server"


@pytest.fixture
def fixa(config):
    """A configuração do modo fixo, sobre a de sempre."""
    return replace(config, cliente=CLIENTE_FIXO)


@pytest.fixture
def ar(fixa):
    with no_ar(fixa) as (servidor, c):
        yield servidor, c


def estado_em_disco(config) -> dict:
    return json.loads((config.estado / "estado.json").read_text(encoding="utf-8"))


# --- o registro fechado ---------------------------------------------------------------


def test_o_registro_responde_404_e_os_metadados_saem_sem_registration_endpoint(ar):
    _, c = ar

    resposta = registrar(c)
    metadados = c.get(METADADOS).json()

    assert resposta.status_code == 404
    assert "registration_endpoint" not in metadados
    # O resto segue de pé: é o servidor de autorização, só sem o registro.
    assert metadados["authorization_endpoint"] == f"{URL}/authorize"
    assert metadados["token_endpoint"] == f"{URL}/token"


def test_o_register_e_404_em_qualquer_metodo_e_grafia_mesmo_com_o_freio_cheio(ar):
    servidor, c = ar
    for _ in range(servidor.provedor.registros.limite):
        servidor.provedor.registros.registrar()
    assert servidor.provedor.registros.cheia()

    # No modo aberto, com o freio cheio, o POST daria 429 (`test_o_21o_registro_da_hora_da_429`).
    assert registrar(c).status_code == 404
    assert c.get("/register").status_code == 404
    assert c.post(f"{URL}//register", json={}).status_code == 404


def test_o_provedor_tambem_se_recusa_a_registrar(ar):
    servidor, _ = ar
    cliente = OAuthClientInformationFull(client_id="x" * 40, redirect_uris=[CALLBACK])

    with pytest.raises(RegistrationError):
        asyncio.run(servidor.provedor.register_client(cliente))

    assert asyncio.run(servidor.provedor.get_client("x" * 40)) is None


def test_o_cliente_fixo_e_publico_com_os_redirects_e_os_grants_da_configuracao(ar, fixa):
    servidor, _ = ar

    cliente = asyncio.run(servidor.provedor.get_client(CLIENTE_FIXO))

    assert cliente.token_endpoint_auth_method == "none"
    assert cliente.client_secret is None
    assert [str(u) for u in cliente.redirect_uris] == list(fixa.redirects)
    assert cliente.grant_types == ["authorization_code", "refresh_token"]
    assert cliente.response_types == ["code"]


def test_sem_a_variavel_o_registro_segue_aberto(config):
    with no_ar(config) as (_, c):
        assert registrar(c).status_code == 201
        assert "registration_endpoint" in c.get(METADADOS).json()


# --- quem chega à tela de login -------------------------------------------------------


def test_o_authorize_com_outro_client_id_nao_chega_ao_login(ar):
    servidor, c = ar

    for outro in ("outro-cliente-qualquer", CLIENTE_FIXO + "x", CLIENTE_FIXO.upper()):
        resposta = iniciar(c, {"client_id": outro}, pkce()[1])
        assert resposta.status_code == 400, outro
        assert "location" not in resposta.headers
    assert servidor.provedor._pedidos == {}


def test_o_authorize_do_cliente_fixo_chega_ao_login(ar):
    _, c = ar

    t = pedido_de(iniciar(c, {"client_id": CLIENTE_FIXO}, pkce()[1]))

    tela = c.get("/login", params={"t": t})
    assert tela.status_code == 200 and 'type="password"' in tela.text


def test_o_redirect_uri_fora_da_lista_e_recusado_no_authorize(ar):
    servidor, c = ar

    resposta = iniciar(c, {"client_id": CLIENTE_FIXO}, pkce()[1], redirect="https://x.invalid/cb")

    assert resposta.status_code == 400
    assert "location" not in resposta.headers
    assert servidor.provedor._pedidos == {}


def test_os_redirects_do_cliente_fixo_sao_os_da_configuracao(fixa):
    with no_ar(replace(fixa, redirects=(OUTRO_CALLBACK,))) as (_, c):
        cliente = {"client_id": CLIENTE_FIXO}

        padrao = iniciar(c, cliente, pkce()[1], redirect=CALLBACK)
        da_lista = iniciar(c, cliente, pkce()[1], redirect=OUTRO_CALLBACK)

        assert padrao.status_code == 400
        assert da_lista.status_code == 302


# --- o caminho por HTTP cru, sem segredo ----------------------------------------------


def test_o_codigo_troca_e_o_refresh_renova_sem_segredo(ar):
    _, c = ar
    sessao = conectar_fixo(c)
    assert "client_secret" not in sessao.cliente
    assert mcp(c, sessao.acesso, "tools/list").status_code == 200

    renovado = renovar(c, sessao.cliente, sessao.refresco)
    antigo = renovar(c, sessao.cliente, sessao.refresco)

    assert renovado.status_code == 200, renovado.text
    assert renovado.json()["refresh_token"] != sessao.refresco
    assert mcp(c, renovado.json()["access_token"], "tools/list").status_code == 200
    assert antigo.status_code == 400 and antigo.json()["error"] == "invalid_grant"
    assert mcp(c, sessao.acesso, "tools/list").status_code == 401, "o acesso antigo parou"


def test_sem_o_verificador_certo_o_codigo_nao_troca(ar):
    servidor, c = ar
    cliente = {"client_id": CLIENTE_FIXO}
    _, desafio = pkce()
    codigo, _ = codigo_de(entrar(c, pedido_de(iniciar(c, cliente, desafio)), SENHA))

    resposta = trocar(c, cliente, codigo, pkce()[0])

    assert resposta.status_code == 400 and "access_token" not in resposta.json()
    assert servidor.provedor._acessos == {}


def test_nenhum_codigo_nasce_sem_a_senha_no_modo_fixo(ar):
    servidor, c = ar
    t = pedido_de(iniciar(c, {"client_id": CLIENTE_FIXO}, pkce()[1]))

    errada = entrar(c, t, "senha-errada-de-mentira")

    assert errada.status_code == 401
    assert servidor.provedor._codigos == {}


def test_o_cliente_fixo_nao_vai_para_o_estado_em_disco(fixa):
    with no_ar(fixa) as (_, c):
        sessao = conectar_fixo(c)

    dados = estado_em_disco(fixa)
    assert dados["clientes"] == []
    assert len(dados["acessos"]) == 1 and len(dados["refrescos"]) == 1

    # E os tokens sobrevivem ao reinício, com o cliente remontado da configuração.
    with no_ar(fixa) as (_, c):
        assert mcp(c, sessao.acesso, "tools/list").status_code == 200
        assert renovar(c, sessao.cliente, sessao.refresco).status_code == 200


# --- o caminho inteiro com o cliente do SDK, sem registrar ----------------------------


def test_o_cliente_do_sdk_conecta_com_o_cliente_fixo_sem_registrar(fixa, vault):
    servidor = criar_servidor(fixa)
    armazem = Armazem()
    armazem.cliente = OAuthClientInformationFull(
        client_id=CLIENTE_FIXO,
        client_name="Cliente do SDK",
        redirect_uris=[CALLBACK],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
    )
    transporte = httpx.ASGITransport(app=servidor.app)
    navegador = Navegador(transporte, SENHA)

    async def corpo(sessao, auth):
        tools = {t.name for t in (await sessao.list_tools()).tools}
        criada = await sessao.call_tool(
            "freitask_new", {"projeto": "beta", "id": "pelo-fixo", "titulo": "Criada pelo fixo"}
        )
        assert not criada.isError
        return tools

    async def rodar():
        async with servidor.app.router.lifespan_context(servidor.app):
            return await sessao_mcp(transporte, armazem, navegador, corpo)

    tools = asyncio.run(asyncio.wait_for(rodar(), timeout=60))

    assert tools == CINCO
    assert navegador.passos == ["login"]
    assert armazem.cliente.client_id == CLIENTE_FIXO, "o cliente não foi trocado por um registrado"
    assert armazem.tokens.access_token and armazem.tokens.refresh_token
    assert "pelo-fixo" in (vault / "CURRENT.md").read_text(encoding="utf-8")
    assert estado_em_disco(fixa)["clientes"] == []
    [linha] = [
        json.loads(x)
        for x in (fixa.estado / "auditoria.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert linha["client_id"] == CLIENTE_FIXO


# --- token de cliente que o servidor não conhece não vale -----------------------------


def test_o_estado_do_registro_aberto_reaberto_com_a_variavel_corta_os_tokens(config, fixa):
    with no_ar(config) as (_, c):
        velha = conectar(c)
        assert mcp(c, velha.acesso, "tools/list").status_code == 200

    with no_ar(fixa) as (_, c):
        assert mcp(c, velha.acesso, "tools/list").status_code == 401
        renovada = renovar(c, velha.cliente, velha.refresco)
        assert renovada.status_code in (400, 401), "o refresh antigo não renova"
        assert "access_token" not in renovada.json()
        assert iniciar(c, velha.cliente, pkce()[1]).status_code == 400, "nem o cliente antigo loga"
        nova = conectar_fixo(c)
        assert mcp(c, nova.acesso, "tools/list").status_code == 200

    # O que o modo anterior emitiu sai do disco junto, e o cliente dele também.
    dados = estado_em_disco(fixa)
    assert dados["clientes"] == []
    assert len(dados["acessos"]) == 1 and len(dados["refrescos"]) == 1

    # Voltar ao registro aberto corta, do mesmo jeito, o que o modo fixo emitiu.
    with no_ar(config) as (_, c):
        assert mcp(c, nova.acesso, "tools/list").status_code == 401
        assert renovar(c, nova.cliente, nova.refresco).status_code in (400, 401)


def test_no_registro_aberto_token_de_cliente_que_o_servidor_nao_conhece_nao_vale(config):
    with no_ar(config) as (_, c):
        sessao = conectar(c)
        assert mcp(c, sessao.acesso, "tools/list").status_code == 200

    # O estado perdeu o cliente, mas guardou os tokens dele (editado à mão ou restaurado de um
    # backup velho): sem o cliente, o token não vale.
    arquivo = config.estado / "estado.json"
    dados = json.loads(arquivo.read_text(encoding="utf-8"))
    dados["clientes"] = []
    arquivo.write_text(json.dumps(dados), encoding="utf-8")

    with no_ar(config) as (servidor, c):
        assert mcp(c, sessao.acesso, "tools/list").status_code == 401
        assert renovar(c, sessao.cliente, sessao.refresco).status_code in (400, 401)
        # Direto no provedor, para pegar também o refresh: o SDK já barra o cliente desconhecido
        # antes, e o provedor não pode depender disso.
        antigo = OAuthClientInformationFull(
            client_id=sessao.cliente["client_id"], redirect_uris=[CALLBACK]
        )
        assert asyncio.run(servidor.provedor.load_access_token(sessao.acesso)) is None
        assert asyncio.run(servidor.provedor.load_refresh_token(antigo, sessao.refresco)) is None
