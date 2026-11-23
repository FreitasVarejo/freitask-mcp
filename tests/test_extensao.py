"""A extensão do obsidian-web-mcp: as cinco tools no servidor e na auditoria.

Segue `tests/test_audit_extension_ops.py` do upstream: cada caso com auditoria vem com o controle
negativo (a mesma tool sem a declaração, ou com a auditoria desligada).
"""

import asyncio
import json

import pytest
from mcp.server.fastmcp import FastMCP
from obsidian_vault_mcp import audit, config, server
from obsidian_vault_mcp.extensions import Extension
from starlette.testclient import TestClient

from freitask_mcp import extension
from freitask_mcp.extension import FreitaskExtension
from freitask_mcp.tools import OPERACOES

from .conftest import chamar, novo_mcp

CINCO = {"freitask_list", "freitask_show", "freitask_doctor", "freitask_new", "freitask_set"}


@pytest.fixture
def mcp(vault):
    servidor = novo_mcp()
    FreitaskExtension().register_tools(servidor)
    return servidor


@pytest.fixture
def auditoria(vault, tmp_path, monkeypatch):
    """Liga a auditoria do upstream num log fora do vault; devolve a leitura dos registros."""
    log = tmp_path / "audit" / "audit.jsonl"
    monkeypatch.setattr(config, "VAULT_PATH", vault)
    monkeypatch.setattr(config, "VAULT_AUDIT_LOG_PATH", str(log))
    monkeypatch.setattr(config, "VAULT_AUDIT_LOG_INCLUDE_READS", False)

    def registros():
        if not log.exists():
            return []
        return [json.loads(linha) for linha in log.read_text(encoding="utf-8").splitlines()]

    return registros


# --- o registro -----------------------------------------------------------------------


def test_a_extensao_e_uma_extension_do_upstream():
    assert isinstance(FreitaskExtension(), Extension)


def test_a_extensao_registra_as_cinco_tools(mcp):
    nomes = {t.name for t in asyncio.run(mcp.list_tools())}

    assert nomes == CINCO


def test_as_cinco_tools_estao_declaradas_na_auditoria(mcp):
    assert set(OPERACOES) == CINCO
    for operacao, tipo in OPERACOES.items():
        assert audit.operation_kind(operacao) == tipo
    assert {o for o, t in OPERACOES.items() if t == "mutation"} == {"freitask_new", "freitask_set"}


def test_registrar_duas_vezes_nao_quebra(vault):
    ext = FreitaskExtension()
    ext.register_tools(FastMCP("a"))
    ext.register_tools(FastMCP("b"))


def test_o_main_serve_a_extensao(monkeypatch):
    chamadas = []
    monkeypatch.setattr(extension, "serve", lambda extensoes: chamadas.append(list(extensoes)))

    extension.main()

    assert len(chamadas) == 1 and len(chamadas[0]) == 1
    assert isinstance(chamadas[0][0], FreitaskExtension)


def test_as_tools_entram_no_servidor_do_upstream_ao_lado_das_vault(vault):
    """O `serve` chama `register_tools(mcp)` na instância global; a lista tem as duas famílias."""
    FreitaskExtension().register_tools(server.mcp)

    nomes = {t.name for t in asyncio.run(server.mcp.list_tools())}

    assert CINCO <= nomes
    assert {"vault_read", "vault_write"} <= nomes


def test_a_chamada_sem_bearer_e_recusada_no_app_do_upstream(vault, monkeypatch):
    from obsidian_vault_mcp import auth

    monkeypatch.setattr(auth, "VAULT_MCP_TOKEN", "token-de-teste")
    # Sem o lifespan do app, a chamada autenticada cai depois da autenticação (500): basta
    # para separar "recusada pelo bearer" de "passou".
    cliente = TestClient(server.build_app([FreitaskExtension()]), raise_server_exceptions=False)
    corpo = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    cabecalhos = {"Accept": "application/json, text/event-stream"}

    sem = cliente.post("/", json=corpo, headers=cabecalhos)
    errado = cliente.post("/", json=corpo, headers={**cabecalhos, "Authorization": "Bearer outro"})

    certo = cliente.post(
        "/", json=corpo, headers={**cabecalhos, "Authorization": "Bearer token-de-teste"}
    )

    assert sem.status_code == 401
    assert errado.status_code == 401
    assert certo.status_code != 401, (
        "controle positivo: com o bearer certo a chamada passa da autenticação"
    )


# --- a auditoria ----------------------------------------------------------------------


def test_o_freitask_new_deixa_um_registro_com_antes_e_depois(mcp, vault, auditoria):
    resposta = chamar(mcp, "freitask_new", projeto="beta", id="auditada", titulo="Auditada")

    registros = [r for r in auditoria() if r["operation"] == "freitask_new"]
    assert len(registros) == 1, auditoria()
    r = registros[0]
    assert r["target_path"] == resposta["path"] == "projects/beta/tasks/auditada.md"
    assert r["operation_status"] == "success" and r["error"] is None
    assert r["size_before"] is None and r["checksum_before"] is None
    assert r["size_after"] == (vault / resposta["path"]).stat().st_size > 0
    assert r["checksum_after"]


def test_o_freitask_set_registra_o_antes_e_o_depois_do_arquivo_da_task(mcp, vault, auditoria):
    chamar(mcp, "freitask_set", id="corrigir-bug", fase="tip")

    registros = [r for r in auditoria() if r["operation"] == "freitask_set"]
    assert len(registros) == 1, auditoria()
    r = registros[0]
    assert r["target_path"] == "projects/alfa/tasks/corrigir-bug.md"
    assert r["operation_status"] == "success"
    assert r["checksum_before"] and r["checksum_after"]
    assert r["checksum_before"] != r["checksum_after"]


def test_o_freitask_set_recusado_pela_cli_fica_registrado_como_erro(mcp, auditoria):
    from mcp.server.fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="data inválida"):
        chamar(mcp, "freitask_set", id="corrigir-bug", prazo="2026-02-30")

    registros = [r for r in auditoria() if r["operation"] == "freitask_set"]
    assert len(registros) == 1 and registros[0]["operation_status"] == "error", auditoria()
    assert registros[0]["error"] == "tool exception"
    assert registros[0]["checksum_before"], "o antes da task devia estar no registro"


def test_o_freitask_list_nao_deixa_registro(mcp, auditoria):
    chamar(mcp, "freitask_list")
    chamar(mcp, "freitask_show", id="corrigir-bug")
    chamar(mcp, "freitask_doctor")

    assert auditoria() == []


def test_as_leituras_so_aparecem_com_a_auditoria_de_leituras_ligada(mcp, auditoria, monkeypatch):
    monkeypatch.setattr(config, "VAULT_AUDIT_LOG_INCLUDE_READS", True)

    chamar(mcp, "freitask_list")
    chamar(mcp, "freitask_doctor")

    assert [r["operation"] for r in auditoria()] == ["freitask_list", "freitask_doctor"]


def test_controle_negativo_sem_a_declaracao_a_mutacao_nao_deixa_registro(
    mcp, vault, auditoria, monkeypatch
):
    monkeypatch.delitem(audit._registered_operations, "freitask_new")

    resposta = chamar(mcp, "freitask_new", projeto="beta", id="invisivel", titulo="Sem registro")

    assert (vault / resposta["path"]).exists(), "controle negativo: a task não foi criada"
    assert auditoria() == []


def test_controle_negativo_com_a_auditoria_desligada_nao_tira_foto_de_arquivo(
    mcp, vault, monkeypatch
):
    monkeypatch.setattr(config, "VAULT_AUDIT_LOG_PATH", "")
    monkeypatch.setattr(
        audit, "snapshot_path", lambda path: pytest.fail("fotografou um arquivo sem auditoria")
    )

    resposta = chamar(mcp, "freitask_new", projeto="beta", id="sem-log", titulo="Sem log")

    assert (vault / resposta["path"]).exists()
