"""freitask_list, freitask_show e freitask_doctor, contra o vault de fixture."""

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from freitask_mcp import engine
from freitask_mcp.tools import CAMPOS_DA_LISTA, register_tools

from .conftest import chamar, novo_mcp


@pytest.fixture
def mcp(vault):
    servidor = novo_mcp()
    register_tools(servidor)
    return servidor


def ids(resposta) -> set[str]:
    return {t["id"] for t in resposta["tasks"]}


# --- freitask_list --------------------------------------------------------------------


def test_list_devolve_so_os_campos_enxutos_e_o_caminho_relativo(mcp):
    resposta = chamar(mcp, "freitask_list")

    task = next(t for t in resposta["tasks"] if t["id"] == "corrigir-bug")
    assert set(task) == {*CAMPOS_DA_LISTA, "path"}
    assert task["path"] == "projects/alfa/tasks/corrigir-bug.md"
    assert task["project"] == "alfa"
    assert task["status"] == "Não iniciada"
    assert task["desc"] == "falta reproduzir"
    assert task["archived"] is None


def test_list_esconde_o_que_o_painel_esconde_e_conta_o_que_omitiu(mcp):
    resposta = chamar(mcp, "freitask_list")

    assert ids(resposta) == {
        "corrigir-bug",
        "refinar-tela",
        "espera-bug",
        "pagar-conta",
    }
    assert resposta["total"] == 4
    assert resposta["escondidas_omitidas"] == 2  # ideia-velha (Algum dia) e adiada


def test_list_com_escondidas_true_mostra_tudo_o_que_esta_ativo(mcp):
    resposta = chamar(mcp, "freitask_list", escondidas=True)

    assert {"ideia-velha", "adiada"} <= ids(resposta)
    assert resposta["escondidas_omitidas"] == 0
    assert "tarefa-feita" not in ids(resposta)


def test_list_por_projeto(mcp):
    resposta = chamar(mcp, "freitask_list", projeto="beta")

    assert ids(resposta) == {"pagar-conta"}


def test_list_normaliza_o_nome_do_projeto(mcp):
    assert ids(chamar(mcp, "freitask_list", projeto="  BETA ")) == {"pagar-conta"}


def test_list_por_fase_pelo_callout_ou_pelo_titulo(mcp):
    assert ids(chamar(mcp, "freitask_list", fase="tip")) == {"refinar-tela"}
    assert ids(chamar(mcp, "freitask_list", fase="Refinada")) == {"refinar-tela"}


def test_list_com_fase_explicita_mostra_as_escondidas_dessa_fase(mcp):
    resposta = chamar(mcp, "freitask_list", fase="quote")

    assert ids(resposta) == {"ideia-velha"}


def test_list_com_fase_e_escondidas_false_as_omite(mcp):
    resposta = chamar(mcp, "freitask_list", fase="quote", escondidas=False)

    assert resposta["tasks"] == []
    assert resposta["escondidas_omitidas"] == 1


def test_list_arquivadas(mcp):
    resposta = chamar(mcp, "freitask_list", arquivadas=True)

    feita = next(t for t in resposta["tasks"] if t["id"] == "tarefa-feita")
    assert feita["archived"] == "done"
    assert feita["path"] == "projects/beta/tasks/archived/done/tarefa-feita.md"


def test_list_recusa_projeto_que_nao_existe_e_diz_quais_existem(mcp):
    with pytest.raises(ToolError, match=r"'gama' não existe; existem: alfa, beta"):
        chamar(mcp, "freitask_list", projeto="gama")


def test_list_recusa_fase_que_nao_existe_e_diz_quais_existem(mcp):
    with pytest.raises(ToolError, match=r"fase desconhecida: 'pronta-pra-ontem'.*tip \(Refinada\)"):
        chamar(mcp, "freitask_list", fase="pronta-pra-ontem")


def test_list_de_vault_vazio_e_lista_vazia(casa):
    servidor = novo_mcp()
    register_tools(servidor)

    resposta = chamar(servidor, "freitask_list")

    assert resposta == {"total": 0, "escondidas_omitidas": 0, "tasks": []}


# --- freitask_show --------------------------------------------------------------------


def test_show_traz_o_que_a_lista_nao_traz(mcp):
    task = chamar(mcp, "freitask_show", id="espera-bug")

    assert task["path"] == "projects/alfa/tasks/espera-bug.md"
    assert task["bloqueada_por"] == ["corrigir-bug"]
    assert task["bloqueada"] is True
    assert {"notes", "historico", "checklist", "ciclo", "dias_para_o_prazo"} <= set(task)


def test_show_do_prazo_conta_os_dias_a_partir_do_dia_fixado(mcp):
    task = chamar(mcp, "freitask_show", id="pagar-conta")

    assert task["prazo"] == "2026-12-24"
    assert task["dias_para_o_prazo"] == 31


def test_show_normaliza_o_id(mcp):
    assert chamar(mcp, "freitask_show", id="Corrigir_Bug")["id"] == "corrigir-bug"


def test_show_de_id_que_nao_existe_e_erro_com_o_stderr(mcp):
    with pytest.raises(ToolError, match="task não encontrada: nao-existe"):
        chamar(mcp, "freitask_show", id="nao-existe")


def test_show_de_id_que_parece_flag_e_so_um_id_que_nao_existe(mcp):
    with pytest.raises(ToolError, match="task não encontrada: json"):
        chamar(mcp, "freitask_show", id="--json")


def test_show_de_id_que_nao_rende_kebab_e_erro(mcp):
    with pytest.raises(ToolError, match="id inválido"):
        chamar(mcp, "freitask_show", id="!!!")


def test_show_de_task_em_ciclo_devolve_a_task_com_o_ciclo(mcp, vault):
    """A CLI sai 1 com o JSON inteiro; a tool não pode transformar isso em erro."""
    for id_, outra in (("corrigir-bug", "espera-bug"), ("espera-bug", "corrigir-bug")):
        arquivo = vault / "projects/alfa/tasks" / f"{id_}.md"
        corpo = arquivo.read_text(encoding="utf-8")
        arquivo.write_text(f"---\nbloqueada-por: [{outra}]\n---\n{corpo}", encoding="utf-8")

    task = chamar(mcp, "freitask_show", id="corrigir-bug")

    assert task["ciclo"] == ["corrigir-bug", "espera-bug", "corrigir-bug"]


# --- freitask_doctor ------------------------------------------------------------------


def test_doctor_de_vault_consistente(mcp):
    assert chamar(mcp, "freitask_doctor") == {"findings": [], "ok": True}


def test_doctor_com_achado_devolve_o_json_e_nao_erro(mcp, vault):
    """`doctor` sai 1 quando há achado pendente: é resultado, não falha da tool."""
    tasks = vault / "projects/alfa/tasks"
    (tasks / "corrigir-bug.sync-conflict-20260101-000000-ABC.md").write_text(
        (tasks / "corrigir-bug.md").read_text(encoding="utf-8"), encoding="utf-8"
    )

    resposta = chamar(mcp, "freitask_doctor")

    assert resposta["ok"] is False
    assert [f["kind"] for f in resposta["findings"]] == ["sync-conflict"]
    assert resposta["findings"][0]["fixed"] is False


def test_doctor_nunca_repara(mcp, vault):
    tasks = vault / "projects/alfa/tasks"
    arquivo = tasks / "corrigir-bug.md"
    corpo = arquivo.read_text(encoding="utf-8").replace("corrigir-bug|corrigir-bug", "x|x")
    arquivo.write_text(corpo, encoding="utf-8")

    chamar(mcp, "freitask_doctor")

    assert arquivo.read_text(encoding="utf-8") == corpo
    assert engine.json_de(engine.rodar(["doctor", "--json"], aceita=(0, 1)))["ok"] is False


def test_as_leituras_nunca_gravam(mcp, vault):
    antes = {p: p.stat().st_mtime_ns for p in vault.rglob("*") if p.is_file()}

    chamar(mcp, "freitask_list", escondidas=True, arquivadas=True)
    chamar(mcp, "freitask_show", id="corrigir-bug")
    chamar(mcp, "freitask_doctor")

    depois = {p: p.stat().st_mtime_ns for p in vault.rglob("*") if p.is_file()}
    assert depois == antes
