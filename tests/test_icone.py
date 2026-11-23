"""O ícone do conector: as três rotas sem bearer, o `icons` do `initialize` e o log do pedido."""

import logging
import re
from importlib import resources

import pytest

from .apoio_http import URL, conectar, mcp, no_ar
from .test_stdio import sessao_stdio
from .test_subida import rodar_remoto

PNG = b"\x89PNG\r\n\x1a\n"
ICO = b"\x00\x00\x01\x00"

# caminho -> (tipo, arquivo em `imagens/`, assinatura do começo do arquivo ou None)
ROTAS = {
    "/favicon.ico": ("image/x-icon", "favicon.ico", ICO),
    "/favicon.svg": ("image/svg+xml", "icon.svg", b"<svg"),
    "/icon.png": ("image/png", "icon.png", PNG),
}
CSP_DO_SVG = "default-src 'none'; style-src 'unsafe-inline'"


@pytest.fixture
def ar(config):
    with no_ar(config) as (servidor, c):
        yield servidor, c


def arquivo(nome: str) -> bytes:
    return (resources.files("freitask_mcp") / "imagens" / nome).read_bytes()


def inicializar(c, token):
    return mcp(
        c,
        token,
        "initialize",
        {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "teste", "version": "0"},
        },
    )


# --- os dados do pacote -------------------------------------------------------------


@pytest.mark.parametrize("nome", ["icon.svg", "icon.png", "favicon.ico"])
def test_os_tres_arquivos_existem_no_pacote(nome):
    recurso = resources.files("freitask_mcp") / "imagens" / nome

    assert recurso.is_file()
    assert len(recurso.read_bytes()) > 0


# --- as três rotas ------------------------------------------------------------------


@pytest.mark.parametrize("caminho", ROTAS)
def test_a_rota_responde_sem_bearer_com_o_tipo_os_cabecalhos_e_os_bytes(ar, caminho):
    _, c = ar
    tipo, nome, assinatura = ROTAS[caminho]

    resposta = c.get(caminho)

    assert resposta.status_code == 200
    assert resposta.headers["content-type"] == tipo
    assert resposta.headers["cache-control"] == "private, max-age=3600"
    assert resposta.headers["x-content-type-options"] == "nosniff"
    assert resposta.content == arquivo(nome)
    assert resposta.content.startswith(assinatura)


def test_so_o_svg_leva_o_csp(ar):
    _, c = ar

    for caminho in ROTAS:
        csp = c.get(caminho).headers.get("content-security-policy")
        assert csp == (CSP_DO_SVG if caminho == "/favicon.svg" else None), caminho


@pytest.mark.parametrize("caminho", ROTAS)
@pytest.mark.parametrize("metodo", ["post", "put", "delete"])
def test_outro_metodo_que_nao_o_get_da_405(ar, caminho, metodo):
    _, c = ar

    assert getattr(c, metodo)(caminho).status_code == 405


# --- o initialize -------------------------------------------------------------------


def test_o_initialize_devolve_os_dois_icones_com_o_endereco_da_url_configurada(ar):
    _, c = ar
    sessao = conectar(c)

    resposta = inicializar(c, sessao.acesso)

    assert resposta.status_code == 200, resposta.text
    assert resposta.json()["result"]["serverInfo"]["icons"] == [
        {"src": f"{URL}/icon.png", "mimeType": "image/png", "sizes": ["128x128"]},
        {"src": f"{URL}/favicon.svg", "mimeType": "image/svg+xml", "sizes": ["any"]},
    ]


def test_o_endereco_do_icone_nao_vem_do_cabecalho_do_pedido(ar):
    _, c = ar
    sessao = conectar(c)

    resposta = inicializar(c, sessao.acesso)
    outro = mcp(
        c,
        sessao.acesso,
        "initialize",
        {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "teste", "version": "0"},
        },
        **{"X-Forwarded-Host": "invasor.invalid", "X-Forwarded-Proto": "https"},
    )

    assert outro.json() == resposta.json()
    assert "invasor" not in outro.text


def test_o_servidor_em_stdio_nao_devolve_icones(vault):
    async def corpo(sessao):
        return await sessao.initialize()

    resultado = sessao_stdio(corpo)

    assert not resultado.serverInfo.icons


# --- a tela de login ----------------------------------------------------------------


def test_a_tela_de_login_traz_o_link_do_icone_a_imagem_e_o_img_src(ar):
    from .test_login import novo_pedido

    _, c = ar
    _, t = novo_pedido(c)

    resposta = c.get("/login", params={"t": t})

    assert resposta.status_code == 200
    assert '<link rel="icon" href="/favicon.svg" type="image/svg+xml">' in resposta.text
    assert '<img src="/favicon.svg" width="48" height="48" alt="">' in resposta.text
    assert "img-src 'self'" in resposta.headers["content-security-policy"]


# --- o log --------------------------------------------------------------------------


def linhas_do_icone(caplog):
    return [r for r in caplog.records if r.name == "freitask_mcp.icone"]


def test_o_log_leva_o_caminho_e_o_comeco_do_user_agent(ar, caplog):
    _, c = ar

    with caplog.at_level(logging.INFO, logger="freitask_mcp.icone"):
        c.get("/icon.png", headers={"User-Agent": "Claude-User/1.0"})

    [registro] = linhas_do_icone(caplog)
    assert registro.levelno == logging.INFO
    assert registro.getMessage() == "ícone pedido: /icon.png user-agent=Claude-User/1.0"


def test_o_log_corta_o_user_agent_em_60_caracteres(ar, caplog):
    _, c = ar

    with caplog.at_level(logging.INFO, logger="freitask_mcp.icone"):
        c.get("/favicon.ico", headers={"User-Agent": "a" * 59 + "bcdef" + "z" * 100})

    [registro] = linhas_do_icone(caplog)
    assert registro.getMessage().endswith("user-agent=" + "a" * 59 + "b")


def test_o_log_nao_deixa_passar_quebra_de_linha_nem_caractere_fora_do_ascii(ar, caplog):
    _, c = ar

    with caplog.at_level(logging.INFO, logger="freitask_mcp.icone"):
        c.get("/favicon.svg", headers={"User-Agent": b"ok\r\nFALSO: linha\tcaf\xe9\x7f"})

    [registro] = linhas_do_icone(caplog)
    mensagem = registro.getMessage()
    assert "\n" not in mensagem and "\r" not in mensagem and "\t" not in mensagem
    # o `é` pode chegar em um ou dois bytes, conforme o transporte; o 0x7f é um `?` a mais
    assert re.search(r"user-agent=ok\?\?FALSO: linha\?caf\?{2,3}$", mensagem), mensagem


def test_o_log_sem_user_agent_fica_com_o_campo_vazio(ar, caplog):
    _, c = ar

    with caplog.at_level(logging.INFO, logger="freitask_mcp.icone"):
        c.get("/icon.png", headers={"User-Agent": ""})

    [registro] = linhas_do_icone(caplog)
    assert registro.getMessage().endswith("user-agent=")


def test_o_log_do_pedido_nao_leva_cookie_authorization_nem_query(ar, caplog):
    _, c = ar

    with caplog.at_level(logging.INFO, logger="freitask_mcp.icone"):
        c.get(
            "/icon.png?segredo=querysecreta",
            headers={"Authorization": "Bearer tokensecreto", "Cookie": "s=cookiesecreto"},
        )

    texto = " ".join(r.getMessage() for r in linhas_do_icone(caplog))
    for segredo in ("querysecreta", "tokensecreto", "cookiesecreto"):
        assert segredo not in texto


# --- o texto ------------------------------------------------------------------------


def test_o_help_cita_as_tres_rotas_do_icone():
    resultado = rodar_remoto("--help", env_extra={})

    assert resultado.returncode == 0
    for caminho in ROTAS:
        assert caminho in resultado.stdout
