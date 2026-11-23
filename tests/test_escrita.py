"""freitask_new e freitask_set, contra o vault de fixture, com as recusas da task."""

import asyncio
import os
import stat

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from freitask_mcp import engine
from freitask_mcp.tools import register_tools

from .conftest import chamar, novo_mcp


@pytest.fixture
def mcp(vault):
    servidor = novo_mcp()
    register_tools(servidor)
    return servidor


@pytest.fixture
def argv_vistos(monkeypatch):
    """Registra o argv de cada chamada que chega ao motor (o vigia do HOME segue por baixo)."""
    vistos: list[list[str]] = []
    original = engine._spawn

    def espiao(argv):
        vistos.append(argv[1:])
        return original(argv)

    monkeypatch.setattr(engine, "_spawn", espiao)
    return vistos


def task_do_cli(id_: str) -> dict:
    return engine.json_de(engine.rodar(["show", "--json", "--", id_]))


def ler(vault, rel: str) -> str:
    return (vault / rel).read_text(encoding="utf-8")


# --- freitask_new ---------------------------------------------------------------------


def test_new_cria_a_task_e_ela_entra_no_painel(mcp, vault):
    resposta = chamar(
        mcp, "freitask_new", projeto="beta", id="ligar-pro-banco", titulo="Ligar pro banco"
    )

    assert resposta == {
        "id": "ligar-pro-banco",
        "project": "beta",
        "path": "projects/beta/tasks/ligar-pro-banco.md",
    }
    arquivo = ler(vault, resposta["path"])
    assert arquivo.startswith("> [!todo] Ligar pro banco\n> [[projects/beta/tasks/ligar-pro-banco|")
    assert "[[projects/beta/tasks/ligar-pro-banco|ligar-pro-banco]]" in ler(vault, "CURRENT.md")


def test_new_com_descricao(mcp, vault):
    chamar(
        mcp,
        "freitask_new",
        projeto="alfa",
        id="com-desc",
        titulo="Com desc",
        desc="aguardando o Fábio",
    )

    assert task_do_cli("com-desc")["desc"] == "aguardando o Fábio"


def test_new_a_task_aparece_no_freitask_list(mcp):
    chamar(mcp, "freitask_new", projeto="alfa", id="nova-de-fixture", titulo="Nova")

    ids = {t["id"] for t in chamar(mcp, "freitask_list", projeto="alfa")["tasks"]}
    assert "nova-de-fixture" in ids


def test_new_normaliza_projeto_e_id(mcp):
    resposta = chamar(mcp, "freitask_new", projeto=" Alfa ", id="Minha Tarefa Nova_1", titulo="T")

    assert resposta["project"] == "alfa"
    assert resposta["id"] == "minha-tarefa-nova-1"


def test_new_titulo_que_comeca_com_flag_vira_titulo(mcp, argv_vistos):
    resposta = chamar(mcp, "freitask_new", projeto="alfa", id="titulo-flag", titulo="--desc x")

    task = task_do_cli(resposta["id"])
    assert task["title"] == "--desc x"
    assert task["desc"] == ""
    chamada = next(a for a in argv_vistos if a[0] == "new")
    assert chamada == ["new", "--json", "--", "alfa", "titulo-flag", "--desc x"]


def test_new_descricao_que_comeca_com_flag_vira_descricao(mcp, argv_vistos):
    chamar(mcp, "freitask_new", projeto="alfa", id="desc-flag", titulo="T", desc="--title=x --json")

    assert task_do_cli("desc-flag")["desc"] == "--title=x --json"
    chamada = next(a for a in argv_vistos if a[0] == "new")
    assert chamada[2] == "--desc=--title=x --json"


@pytest.mark.parametrize("hostil", ["--json", "--desc", "-h", "--", "--help", "--desc=x"])
def test_new_nada_do_chamador_vira_flag(mcp, argv_vistos, hostil):
    try:
        chamar(mcp, "freitask_new", projeto="alfa", id=hostil, titulo=hostil, desc=hostil)
    except ToolError:
        pass  # o id que não rende kebab é recusado; o que importa é o argv que chegou à CLI

    assert argv_vistos
    for chamada in argv_vistos:
        antes_do_separador = chamada[: chamada.index("--")] if "--" in chamada else chamada
        for argumento in antes_do_separador[1:]:
            if argumento.startswith("-"):
                assert argumento in ("--json", "--archived") or argumento.startswith("--desc="), (
                    chamada
                )


def test_new_recusa_projeto_que_nao_existe_sem_criar_pasta(mcp, vault):
    antes = sorted(p.name for p in (vault / "projects").iterdir())

    with pytest.raises(ToolError, match=r"'gama' não existe.*existem: alfa, beta"):
        chamar(mcp, "freitask_new", projeto="gama", id="x", titulo="T")

    assert sorted(p.name for p in (vault / "projects").iterdir()) == antes


def test_new_projeto_com_cara_de_flag_nao_cria_pasta(mcp, vault):
    antes = sorted(p.name for p in (vault / "projects").iterdir())

    with pytest.raises(ToolError, match="não existe"):
        chamar(mcp, "freitask_new", projeto="--desc", id="x", titulo="T")

    assert sorted(p.name for p in (vault / "projects").iterdir()) == antes


def test_new_recusa_id_ja_usado_no_mesmo_projeto(mcp):
    with pytest.raises(ToolError, match="já existe uma task com o id"):
        chamar(mcp, "freitask_new", projeto="alfa", id="corrigir-bug", titulo="T")


@pytest.mark.parametrize("id_existente", ["corrigir-bug", "tarefa-feita"])
def test_new_recusa_id_que_ja_existe_em_outro_projeto_ou_arquivado(mcp, vault, id_existente):
    """A CLI deixaria criar, e o doctor passaria a acusar `id-duplicado`."""
    with pytest.raises(ToolError, match="já existe.*projects/"):
        chamar(mcp, "freitask_new", projeto="beta", id=id_existente, titulo="T")

    assert not (vault / "projects/beta/tasks/corrigir-bug.md").exists()
    assert chamar(mcp, "freitask_doctor")["ok"] is True


def test_new_recusa_id_que_nao_rende_kebab(mcp):
    with pytest.raises(ToolError, match="id inválido"):
        chamar(mcp, "freitask_new", projeto="alfa", id="???", titulo="T")


@pytest.mark.parametrize(
    "titulo", ["linha1\nlinha2", "com\rretorno", "com\ttab", "com\x00nulo", "sep linha"]
)
def test_new_recusa_titulo_que_nao_cabe_numa_linha(mcp, vault, titulo):
    with pytest.raises(ToolError, match="numa linha"):
        chamar(mcp, "freitask_new", projeto="alfa", id="quebrada", titulo=titulo)


def test_new_recusa_descricao_com_quebra_de_linha(mcp, vault):
    with pytest.raises(ToolError, match="desc precisa caber numa linha"):
        chamar(mcp, "freitask_new", projeto="alfa", id="quebrada", titulo="T", desc="a\nb")


@pytest.mark.parametrize("titulo", ["", "   "])
def test_new_recusa_titulo_vazio(mcp, vault, titulo):
    with pytest.raises(ToolError, match="vazio"):
        chamar(mcp, "freitask_new", projeto="alfa", id="sem-titulo", titulo=titulo)


def test_new_deixa_o_vault_consistente(mcp):
    chamar(mcp, "freitask_new", projeto="alfa", id="consistente", titulo="Não quebra o doctor")

    assert chamar(mcp, "freitask_doctor")["ok"] is True


# --- freitask_set ---------------------------------------------------------------------


@pytest.mark.parametrize("fase", ["todo", "abstract", "tip", "quote"])
def test_set_aceita_as_quatro_fases_do_claude_ai(mcp, fase):
    resposta = chamar(mcp, "freitask_set", id="corrigir-bug", fase=fase)

    assert task_do_cli("corrigir-bug")["callout"] == fase
    assert resposta["id"] == "corrigir-bug"


@pytest.mark.parametrize("fase", ["done", "example", "question", "warning", "check", "nope"])
def test_set_recusa_as_outras_fases(mcp, fase):
    with pytest.raises(ToolError):
        chamar(mcp, "freitask_set", id="corrigir-bug", fase=fase)

    assert task_do_cli("corrigir-bug")["callout"] == "todo"


def test_set_anuncia_so_as_quatro_fases_no_schema(mcp):
    tools = asyncio.run(mcp.list_tools())
    schema = next(t for t in tools if t.name == "freitask_set").inputSchema

    fase = schema["properties"]["fase"]
    enum = fase.get("enum") or next(o["enum"] for o in fase["anyOf"] if "enum" in o)
    assert enum == ["todo", "abstract", "tip", "quote"]


def test_set_devolve_a_task_como_ficou(mcp):
    resposta = chamar(mcp, "freitask_set", id="corrigir-bug", fase="tip", desc="pronta pra codar")

    assert resposta["status"] == "Refinada"
    assert resposta["desc"] == "pronta pra codar"
    assert resposta["path"] == "projects/alfa/tasks/corrigir-bug.md"
    assert resposta["ciclo"] is None


def test_set_titulo_que_comeca_com_flag_vira_titulo(mcp, argv_vistos):
    chamar(mcp, "freitask_set", id="corrigir-bug", titulo="--status=tip")

    task = task_do_cli("corrigir-bug")
    assert task["title"] == "--status=tip"
    assert task["callout"] == "todo"
    chamada = next(a for a in argv_vistos if a[0] == "set")
    assert chamada == ["set", "--title=--status=tip", "--", "corrigir-bug"]


def test_set_muda_prazo_e_volta_em_e_limpa_com_texto_vazio(mcp):
    chamar(mcp, "freitask_set", id="corrigir-bug", prazo="2026-12-31", volta_em="2027-02-01")
    task = task_do_cli("corrigir-bug")
    assert (task["prazo"], task["volta_em"], task["hidden"]) == ("2026-12-31", "2027-02-01", True)

    chamar(mcp, "freitask_set", id="corrigir-bug", prazo="", volta_em="")
    task = task_do_cli("corrigir-bug")
    assert (task["prazo"], task["volta_em"], task["hidden"]) == (None, None, False)


def test_set_limpa_a_descricao_com_texto_vazio(mcp):
    chamar(mcp, "freitask_set", id="corrigir-bug", desc="")

    assert task_do_cli("corrigir-bug")["desc"] == ""


def test_set_bloqueada_por_substitui_a_lista_e_lista_vazia_limpa(mcp):
    chamar(mcp, "freitask_set", id="refinar-tela", bloqueada_por=["corrigir-bug", "Pagar Conta"])
    assert task_do_cli("refinar-tela")["bloqueada_por"] == ["corrigir-bug", "pagar-conta"]

    chamar(mcp, "freitask_set", id="refinar-tela", bloqueada_por=[])
    assert task_do_cli("refinar-tela")["bloqueada_por"] == []


def test_set_recusa_ciclo_de_dependencia_e_nao_grava(mcp):
    with pytest.raises(ToolError, match="ciclo de dependência"):
        chamar(mcp, "freitask_set", id="corrigir-bug", bloqueada_por=["espera-bug"])

    assert task_do_cli("corrigir-bug")["bloqueada_por"] == []


def test_set_recusa_data_que_nao_existe(mcp):
    with pytest.raises(ToolError, match="data inválida"):
        chamar(mcp, "freitask_set", id="corrigir-bug", prazo="2026-02-30")

    assert task_do_cli("corrigir-bug")["prazo"] is None


def test_set_recusa_id_que_nao_existe(mcp):
    with pytest.raises(ToolError, match="task não encontrada: nao-existe"):
        chamar(mcp, "freitask_set", id="nao-existe", fase="tip")


def test_set_sem_nada_a_mudar_e_erro(mcp):
    with pytest.raises(ToolError, match="nada a mudar"):
        chamar(mcp, "freitask_set", id="corrigir-bug")


def test_set_recusa_titulo_vazio_e_quebra_de_linha(mcp):
    with pytest.raises(ToolError, match="vazio"):
        chamar(mcp, "freitask_set", id="corrigir-bug", titulo=" ")
    with pytest.raises(ToolError, match="numa linha"):
        chamar(mcp, "freitask_set", id="corrigir-bug", titulo="a\nb")
    with pytest.raises(ToolError, match="numa linha"):
        chamar(mcp, "freitask_set", id="corrigir-bug", desc="a\nb")

    task = task_do_cli("corrigir-bug")
    assert task["title"] == "Corrigir o bug do login"
    assert task["desc"] == "falta reproduzir"


def test_set_id_que_parece_flag_so_nao_existe(mcp, argv_vistos):
    with pytest.raises(ToolError, match="task não encontrada"):
        chamar(mcp, "freitask_set", id="--status=tip", fase="quote")

    assert all("--status=quote" not in a or "--" in a for a in argv_vistos)


def test_set_so_mexe_na_task_pedida(mcp, vault):
    antes = {
        p: p.read_bytes()
        for p in (vault / "projects").rglob("*.md")
        if "corrigir-bug" not in p.name
    }

    chamar(mcp, "freitask_set", id="corrigir-bug", fase="abstract")

    for p, conteudo in antes.items():
        if p.name != "CURRENT.md":
            assert p.read_bytes() == conteudo, p


# --- conflito de escrita (saída 3) ------------------------------------------------------


def test_conflito_persistente_vira_erro_pedindo_para_repetir(mcp, vault, tmp_path, monkeypatch):
    """Um freitask que responde às leituras e sempre sai 3 na escrita."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    real = engine.shutil.which("freitask")
    script = bin_dir / "freitask"
    script.write_text(
        '#!/bin/sh\ncase "$1" in\n  set|new) echo conflito >&2; exit 3;;\n'
        f'  *) exec "{real}" "$@";;\nesac\n',
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(engine, "ESPERA_ENTRE_TENTATIVAS", 0)

    with pytest.raises(ToolError, match="conflito de escrita.*repita a chamada"):
        chamar(mcp, "freitask_set", id="corrigir-bug", fase="tip")
    with pytest.raises(ToolError, match="conflito de escrita.*repita a chamada"):
        chamar(mcp, "freitask_new", projeto="alfa", id="x-novo", titulo="T")
