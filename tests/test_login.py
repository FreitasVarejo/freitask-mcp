"""A tela de login: nenhum código nasce sem a senha, os cabeçalhos da tela e o freio de senha."""

import logging

import pytest

from .apoio_http import (
    SENHA,
    codigo_de,
    entrar,
    iniciar,
    no_ar,
    pedido_de,
    pkce,
    registrar,
)


@pytest.fixture
def ar(config):
    with no_ar(config) as (servidor, c):
        yield servidor, c


def novo_pedido(c, cliente=None, **extra):
    cliente = cliente or registrar(c, **extra).json()
    return cliente, pedido_de(iniciar(c, cliente, pkce()[1]))


def sem_codigo(servidor, resposta):
    assert resposta.status_code != 303, resposta.text
    assert "code=" not in resposta.headers.get("location", "")
    assert servidor.provedor._codigos == {}, "nasceu um código sem a senha"


# --- nenhum código sem a senha --------------------------------------------------------


def test_o_authorize_manda_para_o_login_e_nao_emite_codigo(ar):
    servidor, c = ar
    cliente = registrar(c).json()

    resposta = iniciar(c, cliente, pkce()[1])

    assert resposta.status_code == 302
    assert "code=" not in resposta.headers["location"]
    assert servidor.provedor._codigos == {}


@pytest.mark.parametrize("senha", ["errada-errada-errada", "", " ", SENHA.upper(), SENHA + "x"])
def test_senha_errada_ou_vazia_nao_gera_codigo(ar, senha):
    servidor, c = ar
    _, t = novo_pedido(c)

    resposta = entrar(c, t, senha)

    assert resposta.status_code == 401
    sem_codigo(servidor, resposta)
    codigo_de(entrar(c, t, SENHA))  # controle positivo: o mesmo pedido, com a senha certa


def test_post_sem_o_campo_da_senha_nao_gera_codigo(ar):
    servidor, c = ar
    _, t = novo_pedido(c)

    resposta = c.post("/login", data={"t": t}, follow_redirects=False)

    assert resposta.status_code == 401
    sem_codigo(servidor, resposta)


@pytest.mark.parametrize("t", ["", "inventado", "A" * 43, "../authorize"])
def test_pedido_inventado_nao_gera_codigo_nem_com_a_senha_certa(ar, t):
    servidor, c = ar

    resposta = entrar(c, t, SENHA)

    assert resposta.status_code == 400
    sem_codigo(servidor, resposta)


def test_post_sem_o_pedido_nao_gera_codigo(ar):
    servidor, c = ar

    resposta = c.post("/login", data={"senha": SENHA}, follow_redirects=False)

    assert resposta.status_code == 400
    sem_codigo(servidor, resposta)


def test_pedido_vencido_nao_gera_codigo_nem_com_a_senha_certa(ar, relogio):
    servidor, c = ar
    _, t = novo_pedido(c)
    relogio.avancar(9 * 60)
    assert c.get("/login", params={"t": t}).status_code == 200, "controle: aos 9 min ainda vale"

    relogio.avancar(2 * 60)

    resposta = entrar(c, t, SENHA)
    assert resposta.status_code == 400
    sem_codigo(servidor, resposta)


def test_pedido_ja_usado_nao_gera_outro_codigo(ar):
    servidor, c = ar
    _, t = novo_pedido(c)
    codigo_de(entrar(c, t, SENHA))
    assert len(servidor.provedor._codigos) == 1

    de_novo = entrar(c, t, SENHA)

    assert de_novo.status_code == 400
    assert len(servidor.provedor._codigos) == 1, "o segundo uso do pedido emitiu outro código"


# --- o login certo --------------------------------------------------------------------


def test_senha_certa_volta_ao_redirect_com_code_e_state(ar):
    _, c = ar
    cliente = registrar(c).json()
    t = pedido_de(iniciar(c, cliente, pkce()[1], state="meu-estado"))

    resposta = entrar(c, t, SENHA)

    assert resposta.headers["location"].startswith("https://claude.ai/api/mcp/auth_callback?code=")
    codigo, estado = codigo_de(resposta)
    assert estado == "meu-estado"
    assert len(codigo) >= 43, "256 bits em base64url"


def test_o_login_mostra_o_nome_do_cliente_escapado(ar):
    _, c = ar
    _, t = novo_pedido(c, client_name="<script>alert(1)</script>")

    pagina = c.get("/login", params={"t": t})

    assert pagina.status_code == 200
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in pagina.text
    assert "<script>" not in pagina.text
    assert 'type="password"' in pagina.text


def test_a_senha_errada_mostra_a_tela_de_novo_com_o_mesmo_pedido(ar):
    _, c = ar
    _, t = novo_pedido(c)

    resposta = entrar(c, t, "errada")

    assert resposta.status_code == 401
    assert "Senha incorreta" in resposta.text and 'type="password"' in resposta.text
    assert t in resposta.text


def test_corpo_grande_demais_e_recusado(ar):
    _, c = ar
    _, t = novo_pedido(c)

    resposta = c.post("/login", data={"t": t, "senha": "x" * 20_000}, follow_redirects=False)

    assert resposta.status_code == 413


CABECALHOS_DA_TELA = {
    "cache-control": "no-store",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "content-security-policy": (
        "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"
    ),
}


def test_a_tela_de_login_leva_os_cabecalhos_de_protecao(ar):
    _, c = ar
    _, t = novo_pedido(c)
    respostas = [
        c.get("/login", params={"t": t}),
        c.get("/login", params={"t": "inventado"}),
        entrar(c, t, "errada"),
        entrar(c, t, SENHA),
    ]

    for resposta in respostas:
        for nome, valor in CABECALHOS_DA_TELA.items():
            assert resposta.headers.get(nome) == valor, (resposta.status_code, nome)
        assert "form-action" not in resposta.headers["content-security-policy"]


# --- o freio de senha -----------------------------------------------------------------


def test_o_11o_erro_de_senha_da_429_e_a_senha_certa_tambem(ar):
    servidor, c = ar
    _, t = novo_pedido(c)

    for _ in range(10):
        assert entrar(c, t, "errada").status_code == 401

    barrada = entrar(c, t, "errada")
    certa_no_freio = entrar(c, t, SENHA)

    assert barrada.status_code == 429
    assert certa_no_freio.status_code == 429, "o freio vale também para a senha certa"
    assert int(certa_no_freio.headers["retry-after"]) > 0
    sem_codigo(servidor, certa_no_freio)


def test_o_freio_vale_para_todos_os_pedidos(ar):
    servidor, c = ar
    _, t1 = novo_pedido(c)
    _, t2 = novo_pedido(c)
    for _ in range(10):
        entrar(c, t1, "errada")

    resposta = entrar(c, t2, SENHA)

    assert resposta.status_code == 429
    sem_codigo(servidor, resposta)


def test_o_freio_solta_quando_a_janela_esvazia(ar, relogio):
    servidor, c = ar
    _, t = novo_pedido(c)
    for _ in range(10):
        entrar(c, t, "errada")
    assert entrar(c, t, SENHA).status_code == 429
    relogio.avancar(14 * 60)
    assert entrar(c, t, SENHA).status_code == 429, "aos 14 min ainda barra"

    relogio.avancar(2 * 60)

    _, t_novo = novo_pedido(c)  # o pedido de antes venceu aos 10 min
    codigo_de(entrar(c, t_novo, SENHA))


def test_pedido_inventado_nao_conta_para_o_freio(ar):
    """Sem um pedido vivo a resposta não depende da senha, então não há o que adivinhar."""
    _, c = ar
    _, t = novo_pedido(c)
    for _ in range(25):
        assert entrar(c, "inventado", "errada").status_code == 400

    codigo_de(entrar(c, t, SENHA))


def test_senha_certa_nao_conta_para_o_freio(ar):
    _, c = ar
    for _ in range(12):
        _, t = novo_pedido(c)
        codigo_de(entrar(c, t, SENHA))


# --- o log do login -------------------------------------------------------------------


def test_o_log_diz_a_senha_errada_e_o_login_aceito_so_com_o_client_id(ar, caplog):
    _, c = ar
    caplog.set_level(logging.INFO, logger="freitask_mcp")
    cliente, t = novo_pedido(c, client_name="Nome\ncom quebra")

    entrar(c, t, "errada-errada-errada")
    codigo, _ = codigo_de(entrar(c, t, SENHA))

    assert f"cliente registrado: {cliente['client_id']}" in caplog.text
    assert f"senha errada no login do cliente {cliente['client_id']}" in caplog.text
    assert f"login aceito para o cliente {cliente['client_id']}" in caplog.text
    for fora in (SENHA, "errada-errada-errada", t, codigo, "com quebra"):
        assert fora not in caplog.text


def test_o_log_avisa_quando_o_freio_de_senha_barra(ar, caplog):
    _, c = ar
    _, t = novo_pedido(c)
    for _ in range(10):
        entrar(c, t, "errada-errada-errada")
    caplog.clear()

    assert entrar(c, t, SENHA).status_code == 429

    assert "o freio de senha está ativo" in caplog.text
    assert SENHA not in caplog.text
