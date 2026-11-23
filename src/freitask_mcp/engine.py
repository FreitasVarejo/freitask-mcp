"""O motor: a única função deste pacote que chama processo.

Roda `freitask <sub> ...` com argumentos em lista, sem shell, e traduz o código de saída
(`~/dev/freitask.nvim/docs/freitask.md`, "Códigos de saída"): 1 é recusa, 2 é uso inválido,
3 é conflito de escrita (repete e, se persistir, devolve conflito). O vault é o do `HOME` do
processo: a CLI não tem outra variável.
"""

from __future__ import annotations

import atexit
import json
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from functools import cache
from pathlib import Path

TEMPO_LIMITE = 20.0  # a CLI espera até 5 s pelo lock; o resto é folga
TENTATIVAS = 3  # a primeira chamada mais duas repetições quando sai 3
ESPERA_ENTRE_TENTATIVAS = 0.3


class FreitaskErro(Exception):
    """Falha da CLI, com a mensagem pronta para virar erro de tool."""


class Recusa(FreitaskErro):
    """Saída 1: a CLI recusou (task que não existe, ciclo, fase proibida...)."""


class UsoInvalido(FreitaskErro):
    """Saída 2: argumento ou flag que a CLI não aceita."""


class Conflito(FreitaskErro):
    """Saída 3 depois de todas as tentativas: nada foi gravado."""


class Falha(FreitaskErro):
    """Tempo esgotado, binário ausente, saída fora do contrato."""


@dataclass(frozen=True)
class Resultado:
    codigo: int
    stdout: str
    stderr: str


@cache
def _cwd_vazio() -> str:
    """Diretório vazio para rodar a CLI: ela trata o argumento como caminho quando o arquivo
    existe a partir do cwd, e um id nunca deve resolver para um arquivo qualquer."""
    caminho = tempfile.mkdtemp(prefix="freitask-mcp-cwd-")
    atexit.register(shutil.rmtree, caminho, ignore_errors=True)
    return caminho


def _spawn(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """O único ponto que cria processo. Os testes o vigiam para o `HOME` real nunca passar."""
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        cwd=_cwd_vazio(),
        timeout=TEMPO_LIMITE,
        check=False,
    )


def raiz_do_vault() -> Path:
    """O vault da CLI: sempre `$HOME/ObsidianVault`."""
    return Path.home() / "ObsidianVault"


def relativo_ao_vault(caminho: str) -> str:
    """O caminho absoluto que a CLI devolve, relativo ao vault (é o que `vault_read` entende)."""
    try:
        return Path(caminho).relative_to(raiz_do_vault()).as_posix()
    except ValueError:
        raise Falha(f"o freitask devolveu um caminho fora do vault: {caminho}") from None


def rodar(args: list[str], *, aceita: tuple[int, ...] = (0,)) -> Resultado:
    """Roda `freitask <args>`. Devolve o resultado se a saída está em `aceita`; senão levanta."""
    binario = shutil.which("freitask")
    if binario is None:
        raise Falha("o freitask não está no PATH deste servidor")
    argv = [binario, *args]
    for tentativa in range(1, TENTATIVAS + 1):
        try:
            proc = _spawn(argv)
        except subprocess.TimeoutExpired:
            raise Falha(
                f"o freitask não respondeu em {TEMPO_LIMITE:g} s (`{args[0]}`); "
                "nada garante que gravou, confira antes de repetir"
            ) from None
        if proc.returncode == 3 and tentativa < TENTATIVAS:
            time.sleep(ESPERA_ENTRE_TENTATIVAS)
            continue
        break
    resultado = Resultado(proc.returncode, proc.stdout, proc.stderr.strip())
    if resultado.codigo in aceita:
        return resultado
    if resultado.codigo == 1:
        raise Recusa(resultado.stderr or "o freitask recusou o pedido")
    if resultado.codigo == 2:
        raise UsoInvalido(resultado.stderr or "o freitask recusou os argumentos")
    if resultado.codigo == 3:
        raise Conflito(
            "conflito de escrita: o arquivo mudou durante a gravação (ou o lock seguiu ocupado). "
            "Nada foi gravado; repita a chamada."
        )
    raise Falha(f"o freitask saiu com {resultado.codigo}: {resultado.stderr or 'sem mensagem'}")


def json_de(resultado: Resultado) -> object:
    try:
        return json.loads(resultado.stdout)
    except ValueError:
        raise Falha("o freitask devolveu uma saída que não é JSON") from None


def kebab(texto: str) -> str:
    """O id que o texto rende, pela regra da própria CLI. Nunca começa com `-`."""
    return rodar(["kebab", "--", texto]).stdout.strip()
