"""Quem entra em `/mcp`: bearer válido, e só ele; e o que o servidor escreve não vem de cabeçalho.

Cada recusa vem com o controle positivo (o mesmo pedido, com o bearer certo, passa).
"""

import pytest

from .apoio_http import CINCO, URL, conectar, mcp, no_ar, revogar

METADATA_DO_RECURSO = f"{URL}/.well-known/oauth-protected-resource/mcp"


@pytest.fixture
def no_ar_com_sessao(config):
    with no_ar(config) as (servidor, c):
        yield c, conectar(c)


def confere_401(resposta):
    assert resposta.status_code == 401, resposta.text
    assert f'resource_metadata="{METADATA_DO_RECURSO}"' in resposta.headers["www-authenticate"]


def test_com_o_bearer_certo_o_mcp_lista_so_as_cinco_tools(no_ar_com_sessao):
    c, sessao = no_ar_com_sessao

    resposta = mcp(c, sessao.acesso, "tools/list")

    assert resposta.status_code == 200, resposta.text
    assert {t["name"] for t in resposta.json()["result"]["tools"]} == CINCO


def test_sem_bearer_da_401_com_o_resource_metadata(no_ar_com_sessao):
    c, _ = no_ar_com_sessao

    confere_401(mcp(c, None, "tools/list"))


def test_bearer_errado_da_401(no_ar_com_sessao):
    c, _ = no_ar_com_sessao

    confere_401(mcp(c, "um-token-que-nunca-existiu", "tools/list"))


def test_authorization_que_nao_e_bearer_da_401(no_ar_com_sessao):
    c, sessao = no_ar_com_sessao

    resposta = mcp(c, None, "tools/list", Authorization=f"Basic {sessao.acesso}")

    confere_401(resposta)


def test_bearer_vencido_da_401(config, relogio, no_ar_com_sessao):
    c, sessao = no_ar_com_sessao
    assert mcp(c, sessao.acesso, "tools/list").status_code == 200, "controle positivo"

    relogio.avancar(3601)

    confere_401(mcp(c, sessao.acesso, "tools/list"))


def test_bearer_revogado_da_401(no_ar_com_sessao):
    c, sessao = no_ar_com_sessao
    assert mcp(c, sessao.acesso, "tools/list").status_code == 200, "controle positivo"

    assert revogar(c, sessao, sessao.acesso).status_code == 200

    confere_401(mcp(c, sessao.acesso, "tools/list"))


def test_revogar_o_refresh_mata_tambem_o_acesso_do_par(no_ar_com_sessao):
    c, sessao = no_ar_com_sessao

    assert revogar(c, sessao, sessao.refresco).status_code == 200

    confere_401(mcp(c, sessao.acesso, "tools/list"))


@pytest.mark.parametrize("caminho", ["/mcp/", "//mcp", "///mcp//", "//mcp/"])
def test_as_grafias_do_caminho_tambem_pedem_bearer(no_ar_com_sessao, caminho):
    c, sessao = no_ar_com_sessao

    confere_401(mcp(c, None, "tools/list", caminho=caminho))
    confere_401(mcp(c, "token-de-mentira", "tools/list", caminho=caminho))
    assert mcp(c, sessao.acesso, "tools/list", caminho=caminho).status_code == 200, (
        "controle positivo: com o bearer certo a mesma grafia passa"
    )


def test_so_health_login_e_o_oauth_respondem_sem_bearer(no_ar_com_sessao):
    c, _ = no_ar_com_sessao

    assert c.get("/health").json() == {"status": "ok"}
    assert c.get("/login").status_code == 400
    assert c.get("/.well-known/oauth-authorization-server").status_code == 200
    assert c.get("/.well-known/oauth-protected-resource/mcp").status_code == 200
    assert c.get("/authorize").status_code == 400
    assert c.post("/token").status_code == 401
    assert c.post("/register", json={}).status_code == 400
    # Nenhuma outra rota existe: nada de vault_* nem de painel.
    for caminho in ("/", "/sse", "/messages/", "/admin", "/mcp-extra"):
        assert c.get(caminho).status_code in (404, 405), caminho


def test_host_estranho_em_mcp_e_recusado(no_ar_com_sessao):
    c, sessao = no_ar_com_sessao

    resposta = mcp(c, sessao.acesso, "tools/list", Host="outro.exemplo")

    assert resposta.status_code == 421, resposta.text
    assert mcp(c, sessao.acesso, "tools/list").status_code == 200, "controle positivo"


def test_origin_estranha_em_mcp_e_recusada(no_ar_com_sessao):
    c, sessao = no_ar_com_sessao

    resposta = mcp(c, sessao.acesso, "tools/list", Origin="https://outro.exemplo")

    assert resposta.status_code == 403, resposta.text


def test_cabecalhos_x_forwarded_de_mentira_nao_mudam_nenhum_endereco(no_ar_com_sessao):
    c, sessao = no_ar_com_sessao
    falsos = {"X-Forwarded-Host": "atacante.exemplo", "X-Forwarded-Proto": "https"}
    enderecos = [
        ("/.well-known/oauth-authorization-server", "GET"),
        ("/.well-known/oauth-protected-resource/mcp", "GET"),
    ]

    for caminho, _ in enderecos:
        sem = c.get(caminho)
        com = c.get(caminho, headers=falsos)
        assert com.json() == sem.json(), caminho
        assert "atacante" not in com.text
        assert com.json().get("issuer", com.json().get("resource")).startswith(URL)

    sem401 = mcp(c, None, "tools/list")
    com401 = mcp(c, None, "tools/list", **falsos)
    assert com401.headers["www-authenticate"] == sem401.headers["www-authenticate"]
    assert "atacante" not in com401.headers["www-authenticate"]

    # O redirect do /authorize para o login também sai da URL configurada.
    from .apoio_http import iniciar, pkce

    resposta = iniciar(c, sessao.cliente, pkce()[1])
    assert resposta.headers["location"].startswith(f"{URL}/login?t=")
    com = c.get(
        "/authorize",
        params={
            "client_id": sessao.cliente["client_id"],
            "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
            "response_type": "code",
            "code_challenge": pkce()[1],
            "code_challenge_method": "S256",
        },
        headers=falsos,
        follow_redirects=False,
    )
    assert com.headers["location"].startswith(f"{URL}/login?t="), com.headers["location"]
