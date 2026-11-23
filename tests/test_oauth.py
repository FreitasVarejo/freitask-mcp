"""As regras do OAuth que o pacote escreve: registro, código, par de tokens e o que fica guardado.

As rotas e as conferências de protocolo (PKCE, `redirect_uri`, segredo do cliente) são do SDK; o
que se testa aqui é o que o provedor e a tela decidem, mais um controle de que o SDK está ligado.
"""

import asyncio
import hashlib
import json
import os
import stat

import pytest

from freitask_mcp.provedor import CLIENTES_GUARDADOS

from .apoio_http import (
    CALLBACK,
    SENHA,
    codigo_de,
    conectar,
    entrar,
    iniciar,
    mcp,
    no_ar,
    pedido_de,
    pkce,
    registrar,
    renovar,
    revogar,
    trocar,
)

OUTRO_CALLBACK = "https://claude.com/api/mcp/auth_callback"


@pytest.fixture
def ar(config):
    with no_ar(config) as (servidor, c):
        yield servidor, c


def codigo_novo(c, cliente):
    verificador, desafio = pkce()
    t = pedido_de(iniciar(c, cliente, desafio))
    codigo, _ = codigo_de(entrar(c, t, SENHA))
    return codigo, verificador


# --- registro -------------------------------------------------------------------------


def test_registro_com_redirect_fora_da_lista_da_400(ar):
    _, c = ar

    for fora in (
        "https://atacante.exemplo/auth_callback",
        "http://claude.ai/api/mcp/auth_callback",
        "https://claude.ai/api/mcp/auth_callback/",
        "https://claude.ai.atacante.exemplo/api/mcp/auth_callback",
    ):
        resposta = registrar(c, redirects=[fora])
        assert resposta.status_code == 400, fora
        assert resposta.json()["error"] == "invalid_redirect_uri"


def test_registro_com_um_redirect_dentro_e_outro_fora_da_400(ar):
    _, c = ar

    resposta = registrar(c, redirects=[CALLBACK, "https://atacante.exemplo/cb"])

    assert resposta.status_code == 400


def test_os_dois_callbacks_do_claude_registram(ar):
    _, c = ar

    assert registrar(c, redirects=[CALLBACK]).status_code == 201
    assert registrar(c, redirects=[OUTRO_CALLBACK]).status_code == 201
    assert registrar(c, redirects=[CALLBACK, OUTRO_CALLBACK]).status_code == 201


def test_o_21o_registro_da_hora_da_429(ar, relogio):
    _, c = ar
    for _ in range(20):
        assert registrar(c).status_code == 201

    barrado = registrar(c)

    assert barrado.status_code == 429
    assert int(barrado.headers["retry-after"]) > 0
    relogio.avancar(3601)
    assert registrar(c).status_code == 201, "controle positivo: passada a hora, registra"


def test_registro_recusado_nao_gasta_o_freio(ar):
    _, c = ar
    for _ in range(25):
        assert registrar(c, redirects=["https://atacante.exemplo/cb"]).status_code == 400

    assert registrar(c).status_code == 201


def test_o_freio_de_registro_sobrevive_ao_reinicio(config):
    with no_ar(config) as (_, c):
        for _ in range(20):
            assert registrar(c).status_code == 201

    with no_ar(config) as (_, c):
        assert registrar(c).status_code == 429


def test_cheio_de_clientes_sai_o_mais_velho_sem_token_vivo(ar, relogio):
    servidor, c = ar
    com_token = conectar(c)
    primeiro_sem_token = registrar(c).json()["client_id"]
    for _ in range(CLIENTES_GUARDADOS):
        if servidor.provedor.registros.cheia():
            relogio.avancar(3601)
        assert registrar(c).status_code == 201

    guardados = set(servidor.provedor._clientes)
    assert len(guardados) == CLIENTES_GUARDADOS
    assert com_token.cliente["client_id"] in guardados, "o cliente com token vivo não sai"
    assert primeiro_sem_token not in guardados


def test_cheio_de_clientes_com_token_vivo_recusa_o_registro(ar, relogio):
    servidor, c = ar
    for _ in range(CLIENTES_GUARDADOS):
        if servidor.provedor.registros.cheia():
            relogio.avancar(3601)
        conectar(c)
    relogio.avancar(3601)

    resposta = registrar(c)

    assert resposta.status_code == 400
    assert len(servidor.provedor._clientes) == CLIENTES_GUARDADOS


# --- o código -------------------------------------------------------------------------


def test_o_codigo_de_um_cliente_nao_serve_a_outro(ar):
    _, c = ar
    a, b = registrar(c).json(), registrar(c).json()
    codigo, verificador = codigo_novo(c, a)

    roubado = trocar(c, b, codigo, verificador)

    assert roubado.status_code == 400 and roubado.json()["error"] == "invalid_grant"
    assert trocar(c, a, codigo, verificador).status_code == 200, "controle: o dono do código troca"


def test_o_codigo_nao_serve_duas_vezes(ar):
    _, c = ar
    cliente = registrar(c).json()
    codigo, verificador = codigo_novo(c, cliente)

    primeira = trocar(c, cliente, codigo, verificador)
    segunda = trocar(c, cliente, codigo, verificador)

    assert primeira.status_code == 200
    assert segunda.status_code == 400 and segunda.json()["error"] == "invalid_grant"


def test_code_verifier_errado_nao_passa_e_queima_o_codigo(ar):
    _, c = ar
    cliente = registrar(c).json()
    codigo, verificador = codigo_novo(c, cliente)

    errado = trocar(c, cliente, codigo, pkce()[0])
    certo_depois = trocar(c, cliente, codigo, verificador)

    assert errado.status_code == 400 and errado.json()["error"] == "invalid_grant"
    assert certo_depois.status_code == 400, (
        "o código de um pedido errado não fica para outra tentativa"
    )
    novo, verificador_novo = codigo_novo(c, cliente)
    assert trocar(c, cliente, novo, verificador_novo).status_code == 200, "controle positivo"


def test_o_codigo_vence_em_5_minutos(ar, relogio):
    _, c = ar
    cliente = registrar(c).json()
    codigo, verificador = codigo_novo(c, cliente)
    relogio.avancar(5 * 60 + 1)

    resposta = trocar(c, cliente, codigo, verificador)

    assert resposta.status_code == 400 and resposta.json()["error"] == "invalid_grant"


def test_o_segredo_do_cliente_errado_nao_troca_o_codigo(ar):
    _, c = ar
    cliente = registrar(c).json()
    codigo, verificador = codigo_novo(c, cliente)

    resposta = trocar(c, {**cliente, "client_secret": "errado"}, codigo, verificador)

    assert resposta.status_code == 401
    assert trocar(c, cliente, codigo, verificador).status_code == 200, "controle positivo"


def test_pkce_so_aceita_s256(ar):
    _, c = ar
    cliente = registrar(c).json()

    resposta = c.get(
        "/authorize",
        params={
            "client_id": cliente["client_id"],
            "redirect_uri": CALLBACK,
            "response_type": "code",
            "code_challenge": "x" * 43,
            "code_challenge_method": "plain",
        },
        follow_redirects=False,
    )

    assert "/login" not in resposta.headers.get("location", "")


def test_authorize_com_redirect_nao_registrado_nao_vai_para_o_login(ar):
    _, c = ar
    cliente = registrar(c).json()

    resposta = iniciar(c, cliente, pkce()[1], redirect=OUTRO_CALLBACK)

    assert resposta.status_code == 400


# --- o par de tokens ------------------------------------------------------------------


def test_o_token_sai_com_a_validade_e_sem_escopo(ar):
    _, c = ar
    cliente = registrar(c).json()
    codigo, verificador = codigo_novo(c, cliente)

    tokens = trocar(c, cliente, codigo, verificador).json()

    assert tokens["token_type"] == "Bearer"
    assert tokens["expires_in"] == 3600
    assert tokens.get("scope") in (None, "")
    assert tokens["access_token"] != tokens["refresh_token"]


def test_o_refresh_gira_o_novo_par_funciona_e_o_antigo_para(ar):
    _, c = ar
    sessao = conectar(c)

    resposta = renovar(c, sessao.cliente, sessao.refresco)

    assert resposta.status_code == 200, resposta.text
    novo = resposta.json()
    assert novo["access_token"] != sessao.acesso and novo["refresh_token"] != sessao.refresco
    assert mcp(c, novo["access_token"], "tools/list").status_code == 200
    assert mcp(c, sessao.acesso, "tools/list").status_code == 401, "o acesso antigo parou"
    antigo = renovar(c, sessao.cliente, sessao.refresco)
    assert antigo.status_code == 400 and antigo.json()["error"] == "invalid_grant"
    assert renovar(c, sessao.cliente, novo["refresh_token"]).status_code == 200, (
        "o refresh novo também gira"
    )


def test_o_refresh_de_um_cliente_nao_serve_a_outro(ar):
    _, c = ar
    sessao = conectar(c)
    outro = registrar(c).json()

    resposta = renovar(c, outro, sessao.refresco)

    assert resposta.status_code == 400
    assert renovar(c, sessao.cliente, sessao.refresco).status_code == 200, "controle positivo"


def test_o_refresh_vence_em_30_dias(ar, relogio):
    _, c = ar
    sessao = conectar(c)
    relogio.avancar(29 * 24 * 3600)
    sessao2 = renovar(c, sessao.cliente, sessao.refresco).json()
    relogio.avancar(31 * 24 * 3600)

    resposta = renovar(c, sessao.cliente, sessao2["refresh_token"])

    assert resposta.status_code == 400 and resposta.json()["error"] == "invalid_grant"


def test_todo_token_sai_carimbado_com_o_recurso_do_servidor(ar):
    servidor, c = ar
    cliente = registrar(c).json()
    verificador, desafio = pkce()
    # O cliente pede outro `resource`: o token sai com o do servidor do mesmo jeito.
    resposta = c.get(
        "/authorize",
        params={
            "client_id": cliente["client_id"],
            "redirect_uri": CALLBACK,
            "response_type": "code",
            "code_challenge": desafio,
            "code_challenge_method": "S256",
            "resource": "https://outro.exemplo/mcp",
        },
        follow_redirects=False,
    )
    codigo, _ = codigo_de(entrar(c, pedido_de(resposta), SENHA))
    acesso = trocar(c, cliente, codigo, verificador).json()["access_token"]

    carimbado = asyncio.run(servidor.provedor.load_access_token(acesso))

    assert carimbado.resource == f"{servidor.config.url}/mcp"


def test_token_de_outro_recurso_e_recusado_pelo_bearer(ar, monkeypatch):
    """`validate_token_resource` está ligado: o SDK recusa token que diz ser de outro recurso."""
    servidor, c = ar
    sessao = conectar(c)
    assert mcp(c, sessao.acesso, "tools/list").status_code == 200, "controle positivo"
    original = servidor.provedor.load_access_token

    async def de_outro_recurso(token):
        achado = await original(token)
        return achado.model_copy(update={"resource": "https://outro.exemplo/mcp"})

    monkeypatch.setattr(servidor.provedor, "load_access_token", de_outro_recurso)

    assert mcp(c, sessao.acesso, "tools/list").status_code == 401


def test_cliente_que_pede_escopo_registra_e_conecta(ar):
    _, c = ar

    sessao = conectar(c, scope="claudeai outro")

    assert mcp(c, sessao.acesso, "tools/list").status_code == 200


def test_revogar_token_de_outro_cliente_nao_faz_nada(ar):
    _, c = ar
    sessao = conectar(c)
    outro = conectar(c)

    revogar(c, outro, sessao.acesso)

    assert mcp(c, sessao.acesso, "tools/list").status_code == 200


# --- o que fica guardado --------------------------------------------------------------


def test_depois_de_reiniciar_com_a_mesma_pasta_de_estado_o_token_segue_valendo(config):
    with no_ar(config) as (_, c):
        sessao = conectar(c)

    with no_ar(config) as (_, c):
        assert mcp(c, sessao.acesso, "tools/list").status_code == 200
        renovado = renovar(c, sessao.cliente, sessao.refresco)
        assert renovado.status_code == 200, "o refresh e o cliente também sobreviveram"
        assert iniciar(c, sessao.cliente, pkce()[1]).status_code == 302


def test_token_revogado_continua_revogado_depois_de_reiniciar(config):
    with no_ar(config) as (_, c):
        sessao = conectar(c)
        revogar(c, sessao, sessao.acesso)

    with no_ar(config) as (_, c):
        assert mcp(c, sessao.acesso, "tools/list").status_code == 401
        assert renovar(c, sessao.cliente, sessao.refresco).status_code == 400


def test_pedidos_e_codigos_nao_sobrevivem_ao_reinicio(config):
    with no_ar(config) as (_, c):
        cliente = registrar(c).json()
        t_pendente = pedido_de(iniciar(c, cliente, pkce()[1]))
        codigo, verificador = codigo_novo(c, cliente)

    with no_ar(config) as (_, c):
        assert entrar(c, t_pendente, SENHA).status_code == 400
        assert trocar(c, cliente, codigo, verificador).status_code == 400


def test_o_arquivo_de_estado_nao_tem_o_token_nem_a_senha(config):
    with no_ar(config) as (_, c):
        sessao = conectar(c)
        # Um erro de senha no meio: a tentativa errada também não pode ir para o disco.
        entrar(c, pedido_de(iniciar(c, sessao.cliente, pkce()[1])), "tentativa-de-senha-errada")

    arquivo = config.estado / "estado.json"
    conteudo = arquivo.read_text(encoding="utf-8")
    for segredo in (sessao.acesso, sessao.refresco, SENHA, "tentativa-de-senha-errada"):
        assert segredo not in conteudo
    assert hashlib.sha256(sessao.acesso.encode()).hexdigest() in conteudo, "só o SHA-256"
    assert hashlib.sha256(sessao.refresco.encode()).hexdigest() in conteudo
    assert stat.S_IMODE(arquivo.stat().st_mode) == 0o600
    assert json.loads(conteudo)["versao"] == 1
    assert [p.name for p in config.estado.iterdir() if p.name.endswith(".tmp")] == []


def test_uma_gravacao_que_falha_deixa_o_estado_de_antes_intacto(config, monkeypatch):
    with no_ar(config) as (_, c):
        conectar(c)
        antes = (config.estado / "estado.json").read_bytes()

        def falha(fd):
            raise OSError("disco cheio")

        with monkeypatch.context() as m:
            m.setattr(os, "fsync", falha)
            with pytest.raises(OSError, match="disco cheio"):
                registrar(c)

    assert (config.estado / "estado.json").read_bytes() == antes
    assert [p.name for p in config.estado.iterdir() if p.name.endswith(".tmp")] == []


# --- o cliente público, como o Claude se registra pelo DCR ----------------------------
#
# A doc dos conectores diz que o DCR registra o Claude como cliente público: sem segredo, a troca
# do código só tem o PKCE. É o caminho que o claude.ai percorre de verdade.


def _troca_sem_segredo(c, cliente, codigo, verificador):
    return c.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": codigo,
            "redirect_uri": CALLBACK,
            "client_id": cliente["client_id"],
            "code_verifier": verificador,
        },
    )


def test_cliente_publico_sem_segredo_conecta_e_renova(ar):
    _, c = ar
    cliente = registrar(c, token_endpoint_auth_method="none").json()
    assert cliente.get("client_secret") is None
    verificador, desafio = pkce()
    codigo, _ = codigo_de(entrar(c, pedido_de(iniciar(c, cliente, desafio)), SENHA))

    troca = _troca_sem_segredo(c, cliente, codigo, verificador)

    assert troca.status_code == 200, troca.text
    tokens = troca.json()
    assert mcp(c, tokens["access_token"], "tools/list").status_code == 200

    pedido = {
        "grant_type": "refresh_token",
        "client_id": cliente["client_id"],
        "refresh_token": tokens["refresh_token"],
    }
    renovado = c.post("/token", data=pedido)
    antigo = c.post("/token", data=pedido)

    assert renovado.status_code == 200, renovado.text
    assert renovado.json()["refresh_token"] != tokens["refresh_token"]
    assert mcp(c, renovado.json()["access_token"], "tools/list").status_code == 200
    assert antigo.status_code == 400
    assert antigo.json()["error"] == "invalid_grant"


def test_cliente_publico_nao_troca_o_codigo_sem_o_verificador_certo(ar):
    servidor, c = ar
    cliente = registrar(c, token_endpoint_auth_method="none").json()
    _, desafio = pkce()
    codigo, _ = codigo_de(entrar(c, pedido_de(iniciar(c, cliente, desafio)), SENHA))

    resposta = _troca_sem_segredo(c, cliente, codigo, pkce()[0])

    assert resposta.status_code == 400
    assert "access_token" not in resposta.json()
    assert servidor.provedor._acessos == {}
