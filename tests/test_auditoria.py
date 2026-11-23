"""A auditoria das escritas: uma linha por escrita que chega à CLI, com quem chamou e sem o token.

Tudo pelo `/mcp` com bearer de verdade: o chamador chega à tool síncrona pelo `get_access_token()`
do SDK, e é isso que estes testes provam.
"""

import hashlib
import json
import logging
import stat
from datetime import datetime

import pytest

from freitask_mcp.auditoria import TAMANHO_DO_HASH

from .apoio_http import chamar_tool, conectar, mcp, no_ar


@pytest.fixture
def ar(config):
    with no_ar(config) as (servidor, c):
        yield c, conectar(c), config.estado / "auditoria.jsonl"


def linhas(arquivo):
    if not arquivo.exists():
        return []
    return [json.loads(linha) for linha in arquivo.read_text(encoding="utf-8").splitlines()]


def chamar_bruto(c, sessao, nome, **argumentos):
    resposta = mcp(c, sessao.acesso, "tools/call", {"name": nome, "arguments": argumentos})
    assert resposta.status_code == 200, resposta.text
    return resposta.json()["result"]


def test_o_freitask_new_deixa_a_linha_com_o_client_id_e_sem_o_token(ar, vault, relogio):
    c, sessao, arquivo = ar

    criada = chamar_tool(
        c, sessao.acesso, "freitask_new", projeto="beta", id="auditada", titulo="A"
    )

    [linha] = linhas(arquivo)
    assert linha["operacao"] == "freitask_new"
    assert linha["resultado"] == "ok" and linha["erro"] is None
    assert linha["caminho"] == criada["path"] == "projects/beta/tasks/auditada.md"
    assert linha["client_id"] == sessao.cliente["client_id"]
    assert linha["token"] == hashlib.sha256(sessao.acesso.encode()).hexdigest()[:TAMANHO_DO_HASH]
    assert datetime.fromisoformat(linha["quando"]).timestamp() == pytest.approx(
        int(relogio()), abs=1
    )
    texto = arquivo.read_text(encoding="utf-8")
    assert sessao.acesso not in texto and sessao.refresco not in texto
    assert stat.S_IMODE(arquivo.stat().st_mode) == 0o600
    assert (vault / criada["path"]).exists(), "controle: a task foi criada de fato"


def test_o_freitask_set_deixa_a_linha_do_caminho_da_task(ar):
    c, sessao, arquivo = ar

    chamar_tool(c, sessao.acesso, "freitask_set", id="corrigir-bug", fase="tip")

    [linha] = linhas(arquivo)
    assert linha["operacao"] == "freitask_set"
    assert linha["caminho"] == "projects/alfa/tasks/corrigir-bug.md"
    assert linha["client_id"] == sessao.cliente["client_id"]


def test_as_leituras_nao_deixam_nada(ar):
    c, sessao, arquivo = ar

    chamar_tool(c, sessao.acesso, "freitask_list")
    chamar_tool(c, sessao.acesso, "freitask_show", id="corrigir-bug")
    chamar_tool(c, sessao.acesso, "freitask_doctor")

    assert linhas(arquivo) == []
    assert not arquivo.exists()


def test_cada_cliente_aparece_com_o_proprio_client_id(ar, config):
    c, primeiro, arquivo = ar
    segundo = conectar(c)

    chamar_tool(c, primeiro.acesso, "freitask_new", projeto="alfa", id="um", titulo="Um")
    chamar_tool(c, segundo.acesso, "freitask_new", projeto="alfa", id="dois", titulo="Dois")

    ids = {linha["caminho"]: linha["client_id"] for linha in linhas(arquivo)}
    assert ids == {
        "projects/alfa/tasks/um.md": primeiro.cliente["client_id"],
        "projects/alfa/tasks/dois.md": segundo.cliente["client_id"],
    }


def test_a_escrita_que_a_cli_recusa_fica_como_erro(ar):
    c, sessao, arquivo = ar

    resultado = chamar_bruto(c, sessao, "freitask_set", id="corrigir-bug", prazo="2026-02-30")

    assert resultado["isError"]
    [linha] = linhas(arquivo)
    assert linha["operacao"] == "freitask_set" and linha["resultado"] == "erro"
    assert linha["erro"] == "UsoInvalido"
    assert linha["client_id"] == sessao.cliente["client_id"]


def test_a_recusa_antes_da_cli_nao_deixa_linha(ar):
    """A auditoria é das escritas que chegam à CLI: o que a tool recusa antes não chega."""
    c, sessao, arquivo = ar

    for nome, argumentos in (
        ("freitask_new", {"projeto": "nao-existe", "id": "x", "titulo": "X"}),
        ("freitask_new", {"projeto": "alfa", "id": "corrigir-bug", "titulo": "Repetida"}),
        ("freitask_set", {"id": "corrigir-bug", "fase": "done"}),
        ("freitask_set", {"id": "nao-existe", "fase": "tip"}),
    ):
        assert chamar_bruto(c, sessao, nome, **argumentos)["isError"], (nome, argumentos)

    assert linhas(arquivo) == []


def test_falha_ao_gravar_a_auditoria_nao_derruba_a_escrita(config, caplog, vault):
    (config.estado / "auditoria.jsonl").mkdir()  # gravar nele dá erro
    with no_ar(config) as (_, c):
        sessao = conectar(c)
        with caplog.at_level(logging.ERROR):
            criada = chamar_tool(
                c, sessao.acesso, "freitask_new", projeto="beta", id="sim", titulo="S"
            )

    assert (vault / criada["path"]).exists()
    assert "não consegui gravar a auditoria" in caplog.text
    assert sessao.acesso not in caplog.text
