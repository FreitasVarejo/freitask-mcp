"""As tools do freitask, registráveis em qualquer instância de `FastMCP`.

O servidor local (`local.py`) e a extensão do obsidian-web-mcp (`extension.py`) chamam a mesma
`register_tools`. Aqui só há tradução entre a tool e o comando: regra de task mora na CLI
(`~/dev/freitask.nvim/docs/freitask.md`).

As tools são síncronas de propósito: o `run_audited` do upstream recusa corrotina.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from mcp.server.fastmcp.exceptions import ToolError

from . import engine
from .engine import FreitaskErro, Recusa

# Operação na auditoria do upstream: "read" só registra com VAULT_AUDIT_LOG_INCLUDE_READS.
OPERACOES = {
    "freitask_list": "read",
    "freitask_show": "read",
    "freitask_doctor": "read",
}

# Campos de cada task em `freitask_list`; o resto vem por `freitask_show`.
CAMPOS_DA_LISTA = (
    "id",
    "project",
    "status",
    "title",
    "desc",
    "prioridade",
    "bloqueada",
    "prazo",
    "volta_em",
    "archived",
)

Auditoria = Callable[..., str]


def _sem_auditoria(operacao: str, func: Callable[[], str], **contexto: object) -> str:
    return func()


def _json(valor: object) -> str:
    return json.dumps(valor, ensure_ascii=False, indent=2)


def _erro(e: FreitaskErro) -> ToolError:
    return ToolError(str(e))


def _linha(task: dict) -> dict:
    """A task enxuta: os campos da lista e o caminho relativo ao vault."""
    linha = {campo: task.get(campo) for campo in CAMPOS_DA_LISTA}
    linha["path"] = engine.relativo_ao_vault(task["path"])
    return linha


def _id_de(texto: str, o_que: str) -> str:
    """Passa o texto do chamador pelo kebab da CLI: nada que ele mande vira flag."""
    try:
        valor = engine.kebab(texto)
    except Recusa:
        raise ToolError(f"{o_que} inválido: {texto!r} não rende um id kebab-case") from None
    if not valor:
        raise ToolError(f"{o_que} inválido: {texto!r} não rende um id kebab-case")
    return valor


def _projetos() -> list[str]:
    dados = engine.json_de(engine.rodar(["projects", "--json"]))
    return [p["project"] for p in dados]


def _mostrar(id_: str) -> dict:
    # Task em ciclo de dependência sai 1 com o JSON completo (o ciclo vai em `ciclo`).
    resultado = engine.rodar(["show", "--json", "--", id_], aceita=(0, 1))
    if resultado.codigo == 1 and not resultado.stdout.strip():
        raise Recusa(resultado.stderr or f"task não encontrada: {id_}")
    return engine.json_de(resultado)


def register_tools(mcp, audited: Auditoria = _sem_auditoria) -> None:
    """Registra as tools em `mcp`.

    `audited(operacao, func, **contexto)` envolve as chamadas; a extensão passa o
    `run_audited` do upstream, o servidor local não audita.
    """

    @mcp.tool(
        name="freitask_list",
        description=(
            "Lista as tasks do vault (id, projeto, fase, título, descrição, prioridade, "
            "bloqueada, prazo, volta_em, caminho). Por padrão só as ativas e sem as escondidas "
            "(fase Algum dia ou adiadas); com `fase` explícita as escondidas dessa fase entram. "
            "Para o resto de uma task, use freitask_show; para o corpo, vault_read no caminho."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    def freitask_list(
        projeto: str | None = None,
        fase: str | None = None,
        arquivadas: bool = False,
        escondidas: bool | None = None,
    ) -> str:
        """projeto: id do projeto. fase: callout (todo, abstract, tip, example, question,
        warning, check, quote) ou o título da fase. arquivadas: inclui as arquivadas.
        escondidas: true mostra as escondidas, false as omite; sem valor, as omite a menos que
        `fase` esteja dada."""

        def trabalho() -> str:
            alvo_projeto = None
            if projeto is not None:
                alvo_projeto = _id_de(projeto, "projeto")
                existentes = _projetos()
                if alvo_projeto not in existentes:
                    raise ToolError(
                        f"o projeto {alvo_projeto!r} não existe; existem: {', '.join(existentes)}"
                    )
            alvo_fase = None
            if fase is not None:
                fases = engine.json_de(engine.rodar(["statuses", "--json"]))
                pedida = fase.strip().casefold()
                achada = [
                    f for f in fases if pedida in (f["callout"].casefold(), f["title"].casefold())
                ]
                if not achada:
                    validas = ", ".join(f"{f['callout']} ({f['title']})" for f in fases)
                    raise ToolError(f"fase desconhecida: {fase!r}; existem: {validas}")
                alvo_fase = achada[0]["num"]
            args = ["list", "--json"] + (["--archived"] if arquivadas else [])
            tasks = engine.json_de(engine.rodar(args))
            if alvo_projeto is not None:
                tasks = [t for t in tasks if t["project"] == alvo_projeto]
            if alvo_fase is not None:
                tasks = [t for t in tasks if t["status_num"] == alvo_fase]
            mostrar_escondidas = escondidas if escondidas is not None else fase is not None
            omitidas = 0
            if not mostrar_escondidas:
                omitidas = sum(1 for t in tasks if t["hidden"])
                tasks = [t for t in tasks if not t["hidden"]]
            return _json(
                {
                    "total": len(tasks),
                    "escondidas_omitidas": omitidas,
                    "tasks": [_linha(t) for t in tasks],
                }
            )

        try:
            return audited("freitask_list", trabalho)
        except FreitaskErro as e:
            raise _erro(e) from e

    @mcp.tool(
        name="freitask_show",
        description=(
            "Mostra uma task pelo id: o que o freitask_list traz mais notas, histórico, checklist, "
            "dependências (bloqueada_por, bloqueia, ciclo) e dias para o prazo. O caminho é "
            "relativo ao vault; o corpo da nota se lê com vault_read."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    def freitask_show(id: str) -> str:
        """id: o id da task (nome do arquivo sem .md)."""

        def trabalho() -> str:
            task = _mostrar(_id_de(id, "id"))
            task["path"] = engine.relativo_ao_vault(task["path"])
            return _json(task)

        try:
            return audited("freitask_show", trabalho)
        except FreitaskErro as e:
            raise _erro(e) from e

    @mcp.tool(
        name="freitask_doctor",
        description=(
            "Diagnostica o vault (links pendurados, blocos dessincronizados, conflitos do "
            "Syncthing, dependências...). Só leitura: nunca repara. Devolve {ok, findings}."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    def freitask_doctor() -> str:
        """Sem argumentos."""

        def trabalho() -> str:
            # Saída 1 é "há achado pendente", e o JSON vem inteiro.
            resultado = engine.rodar(["doctor", "--json"], aceita=(0, 1))
            return _json(engine.json_de(resultado))

        try:
            return audited("freitask_doctor", trabalho)
        except FreitaskErro as e:
            raise _erro(e) from e
