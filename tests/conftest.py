"""Fixtures: todo teste roda com `HOME` temporário.

O vault da CLI é o do `HOME`, e o vault real fica fora dos testes. O `HOME` e o
`XDG_RUNTIME_DIR` só mudam no ambiente do processo do pytest e dos filhos que ele sobe; o `uv`,
que é o pai, não os vê. Se o motor for chamado com o `HOME` real, o teste falha.
"""

import asyncio
import json
import os
from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP

from freitask_mcp import engine

HOME_REAL = os.path.expanduser("~")
HOJE = "2026-11-23"


@pytest.fixture(autouse=True)
def casa(tmp_path, monkeypatch):
    """O `HOME` de teste, com o lock da CLI e o dia fixados."""
    home = tmp_path / "home"
    run = tmp_path / "run"
    home.mkdir()
    run.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(run))
    monkeypatch.setenv("FREITASK_TODAY", HOJE)
    return home


@pytest.fixture(autouse=True)
def vigia_do_home(monkeypatch):
    """Falha o teste se o motor criar processo com o `HOME` real."""
    violacoes: list[str] = []
    spawn_de_verdade = engine._spawn

    def vigiado(argv):
        if os.environ.get("HOME") == HOME_REAL:
            violacoes.append(" ".join(argv))
            raise AssertionError(f"o motor foi chamado com o HOME real: {argv}")
        return spawn_de_verdade(argv)

    monkeypatch.setattr(engine, "_spawn", vigiado)
    yield violacoes
    assert not violacoes, f"o motor foi chamado com o HOME real: {violacoes}"


@pytest.fixture
def vault(casa) -> Path:
    """Vault de fixture, montado pela própria CLI: dois projetos e uma task de cada tipo."""

    def freitask(*args: str) -> None:
        engine.rodar(list(args))

    freitask("new-project", "alfa")
    freitask("new-project", "beta")
    freitask("new", "alfa", "corrigir-bug", "Corrigir o bug do login", "--desc=falta reproduzir")
    freitask("new", "alfa", "refinar-tela", "Refinar a tela inicial")
    freitask("set", "--status=tip", "refinar-tela")
    freitask("new", "alfa", "ideia-velha", "Uma ideia para algum dia")
    freitask("set", "--status=quote", "ideia-velha")
    freitask("new", "alfa", "adiada", "Task adiada")
    freitask("set", "--volta-em=2027-01-10", "adiada")
    freitask("new", "alfa", "espera-bug", "Espera o bug")
    freitask("set", "--bloqueada-por=corrigir-bug", "espera-bug")
    freitask("new", "beta", "pagar-conta", "Pagar a conta de luz")
    freitask("set", "--prazo=2026-12-24", "pagar-conta")
    freitask("new", "beta", "tarefa-feita", "Já foi")
    freitask("archive", "tarefa-feita", "done")
    return casa / "ObsidianVault"


def novo_mcp() -> FastMCP:
    return FastMCP("teste")


def chamar(mcp: FastMCP, nome: str, **argumentos):
    """Chama a tool pelo registro do FastMCP, como um cliente chegaria, e devolve o JSON."""
    resultado = asyncio.run(mcp.call_tool(nome, argumentos))
    if isinstance(resultado, tuple):
        resultado = resultado[0]
    return json.loads("".join(getattr(bloco, "text", "") for bloco in resultado))


@pytest.fixture
def relogio():
    from .apoio_http import RelogioFalso

    return RelogioFalso()


@pytest.fixture
def config(vault, tmp_path, relogio):
    """A configuração do servidor remoto sobre o vault de fixture, com o estado fora dele."""
    from freitask_mcp.config import Configuracao

    from .apoio_http import SENHA, URL

    estado = tmp_path / "estado"
    estado.mkdir()
    return Configuracao(url=URL, senha=SENHA, estado=estado, relogio=relogio)
