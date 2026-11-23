"""O motor: argumentos em lista, valor de flag colado, códigos de saída e o HOME de teste."""

import os
import stat
from pathlib import Path

import pytest

from freitask_mcp import engine
from freitask_mcp.engine import Conflito, Falha, Recusa, UsoInvalido

from .conftest import HOME_REAL


def falso_freitask(tmp_path: Path, monkeypatch, corpo: str) -> Path:
    """Põe um `freitask` de mentira na frente do PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "freitask"
    script.write_text(f"#!/bin/sh\n{corpo}\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return script


# --- o HOME de teste ------------------------------------------------------------------


def test_o_vigia_derruba_o_teste_que_chama_o_motor_com_o_home_real(monkeypatch, vigia_do_home):
    monkeypatch.setenv("HOME", HOME_REAL)

    with pytest.raises(AssertionError, match="HOME real"):
        engine.rodar(["list", "--json"])

    assert len(vigia_do_home) == 1
    vigia_do_home.clear()  # o vigia já provou o que devia; o teardown não repete a falha


def test_o_vault_da_cli_e_o_do_home_de_teste(casa):
    assert engine.raiz_do_vault() == casa / "ObsidianVault"
    assert str(casa) != HOME_REAL


def test_a_cli_roda_no_home_de_teste_e_nao_toca_o_vault_real(casa):
    engine.rodar(["new-project", "alfa"])

    assert (casa / "ObsidianVault" / "projects" / "alfa" / "tasks").is_dir()


# --- argumentos -----------------------------------------------------------------------


def test_titulo_que_comeca_com_dois_hifens_chega_como_titulo(vault):
    saida = engine.json_de(
        engine.rodar(["new", "--json", "--", "alfa", "titulo-estranho", "--desc x"])
    )

    task = engine.json_de(engine.rodar(["show", "--json", "--", saida["id"]]))
    assert task["title"] == "--desc x"
    assert task["desc"] == ""


def test_valor_de_flag_colado_chega_como_valor(vault):
    engine.rodar(["new", "--json", "--desc=--estado: aguardando", "--", "alfa", "colado", "T"])

    task = engine.json_de(engine.rodar(["show", "--json", "--", "colado"]))
    assert task["desc"] == "--estado: aguardando"


def test_set_com_titulo_colado_que_comeca_com_hifens(vault):
    engine.rodar(["set", "--title=--status=tip", "--", "corrigir-bug"])

    task = engine.json_de(engine.rodar(["show", "--json", "--", "corrigir-bug"]))
    assert task["title"] == "--status=tip"
    assert task["callout"] == "todo"


def test_o_id_que_parece_flag_nao_vira_flag(vault):
    with pytest.raises(Recusa, match="não encontrada"):
        engine.rodar(["show", "--json", "--", "--json"])


def test_kebab_vem_da_cli_e_nunca_comeca_com_hifen(vault):
    assert engine.kebab("--Desc  Ação_Já") == "desc-acao-ja"
    with pytest.raises(Recusa):
        engine.kebab("---")


def test_a_cli_roda_num_diretorio_vazio(vault, tmp_path, monkeypatch):
    """Um arquivo com o nome do id, no cwd do servidor, não pode virar o alvo."""
    isca = tmp_path / "isca"
    isca.mkdir()
    (isca / "corrigir-bug").write_text("não sou uma task\n", encoding="utf-8")
    monkeypatch.chdir(isca)

    task = engine.json_de(engine.rodar(["show", "--json", "--", "corrigir-bug"]))

    assert task["project"] == "alfa"
    with pytest.raises(Recusa):
        engine.rodar(["show", "--json", "--", "isca"])


# --- caminhos -------------------------------------------------------------------------


def test_o_caminho_vira_relativo_ao_vault(vault):
    assert (
        engine.relativo_ao_vault(str(vault / "projects/alfa/tasks/x.md"))
        == "projects/alfa/tasks/x.md"
    )
    with pytest.raises(Falha, match="fora do vault"):
        engine.relativo_ao_vault("/etc/passwd")


# --- códigos de saída -----------------------------------------------------------------


def test_saida_1_e_recusa_com_o_stderr(vault):
    with pytest.raises(Recusa, match="task não encontrada: nao-existe"):
        engine.rodar(["show", "--json", "--", "nao-existe"])


def test_saida_2_e_uso_invalido_com_o_stderr(vault):
    with pytest.raises(UsoInvalido, match="fase desconhecida"):
        engine.rodar(["set", "--status=nope", "--", "corrigir-bug"])


def test_saida_aceita_nao_levanta(tmp_path, monkeypatch):
    falso_freitask(tmp_path, monkeypatch, "echo '{\"ok\": false}'; exit 1")

    resultado = engine.rodar(["doctor", "--json"], aceita=(0, 1))

    assert resultado.codigo == 1
    assert engine.json_de(resultado) == {"ok": False}


def test_saida_3_repete_e_passa_quando_o_conflito_some(tmp_path, monkeypatch):
    contador = tmp_path / "chamadas"
    falso_freitask(
        tmp_path,
        monkeypatch,
        f'echo x >> "{contador}"\n'
        f'if [ "$(wc -l < "{contador}")" -lt 3 ]; then echo "conflito" >&2; exit 3; fi\n'
        "echo gravou",
    )
    monkeypatch.setattr(engine, "ESPERA_ENTRE_TENTATIVAS", 0)

    resultado = engine.rodar(["set", "--", "x"])

    assert resultado.stdout.strip() == "gravou"
    assert len(contador.read_text().splitlines()) == 3


def test_saida_3_que_persiste_vira_conflito_depois_de_tres_chamadas(tmp_path, monkeypatch):
    contador = tmp_path / "chamadas"
    falso_freitask(tmp_path, monkeypatch, f'echo x >> "{contador}"; exit 3')
    monkeypatch.setattr(engine, "ESPERA_ENTRE_TENTATIVAS", 0)

    with pytest.raises(Conflito, match="repita a chamada"):
        engine.rodar(["set", "--", "x"])

    assert len(contador.read_text().splitlines()) == 3


def test_saida_que_a_cli_nao_documenta_vira_falha(tmp_path, monkeypatch):
    falso_freitask(tmp_path, monkeypatch, "echo estranho >&2; exit 42")

    with pytest.raises(Falha, match="saiu com 42: estranho"):
        engine.rodar(["list"])


def test_tempo_esgotado_vira_falha(tmp_path, monkeypatch):
    falso_freitask(tmp_path, monkeypatch, "exec sleep 30")
    monkeypatch.setattr(engine, "TEMPO_LIMITE", 0.3)

    with pytest.raises(Falha, match="não respondeu"):
        engine.rodar(["list"])


def test_binario_ausente_vira_falha(monkeypatch):
    monkeypatch.setenv("PATH", "/nao-existe")

    with pytest.raises(Falha, match="não está no PATH"):
        engine.rodar(["list"])


def test_saida_que_nao_e_json_vira_falha(tmp_path, monkeypatch):
    falso_freitask(tmp_path, monkeypatch, "echo isto não é json")

    with pytest.raises(Falha, match="não é JSON"):
        engine.json_de(engine.rodar(["list", "--json"]))
