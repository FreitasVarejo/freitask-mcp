"""As cinco tools do freitask, registráveis em qualquer instância de `FastMCP`.

O servidor local (`local.py`) e a extensão do obsidian-web-mcp (`extension.py`) chamam a mesma
`register_tools`. Aqui só há tradução entre a tool e o comando: regra de task mora na CLI
(`~/dev/freitask.nvim/docs/freitask.md`).

As tools são síncronas de propósito: o `run_audited` do upstream recusa corrotina.
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Callable
from typing import Literal

from mcp.server.fastmcp.exceptions import ToolError

from . import engine
from .engine import FreitaskErro, Recusa

# Operação na auditoria do upstream: "read" só registra com VAULT_AUDIT_LOG_INCLUDE_READS.
OPERACOES = {
    "freitask_list": "read",
    "freitask_show": "read",
    "freitask_doctor": "read",
    "freitask_new": "mutation",
    "freitask_set": "mutation",
}

# O que o ~/ObsidianVault/AGENTS.md já permite ao claude.ai: afrouxar é mudar aquela regra.
Fase = Literal["todo", "abstract", "tip", "quote"]

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


def _uma_linha(texto: str, campo: str) -> str:
    """Título e descrição moram numa linha do callout: quebra de linha corromperia o bloco."""
    for c in texto:
        if unicodedata.category(c) in ("Cc", "Zl", "Zp"):
            raise ToolError(
                f"{campo} precisa caber numa linha, sem quebra nem caractere de controle"
            )
    return texto.strip()


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
    """Registra as cinco tools em `mcp`.

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

    @mcp.tool(
        name="freitask_new",
        description=(
            "Cria uma task nova (fase Não iniciada) num projeto que já existe, e regenera o "
            "CURRENT.md. O id é normalizado para kebab-case. Recusa projeto que não existe e id "
            "já usado."
        ),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    def freitask_new(projeto: str, id: str, titulo: str, desc: str | None = None) -> str:
        """projeto: id de um projeto existente. id: id da task. titulo: uma linha. desc: o
        estado em uma linha (opcional)."""
        titulo_limpo = _uma_linha(titulo, "titulo")
        if not titulo_limpo:
            raise ToolError("titulo não pode ser vazio")
        desc_limpa = _uma_linha(desc, "desc") if desc else ""
        try:
            alvo_projeto = _id_de(projeto, "projeto")
            existentes = _projetos()
            if alvo_projeto not in existentes:
                raise ToolError(
                    f"o projeto {alvo_projeto!r} não existe (a tool não cria projeto); "
                    f"existem: {', '.join(existentes)}"
                )
            alvo_id = _id_de(id, "id")
            # A CLI só recusa id repetido no mesmo projeto; em outro, o doctor acusaria
            # `id-duplicado`, e o id é global (wikilink e bloqueada-por).
            ocupada = next(
                (
                    t
                    for t in engine.json_de(engine.rodar(["list", "--json", "--archived"]))
                    if t["id"] == alvo_id
                ),
                None,
            )
            if ocupada is not None:
                raise ToolError(
                    f"já existe uma task com o id {alvo_id!r}: "
                    f"{engine.relativo_ao_vault(ocupada['path'])}"
                )
        except FreitaskErro as e:
            raise _erro(e) from e

        def trabalho() -> str:
            args = ["new", "--json"]
            if desc_limpa:
                args.append(f"--desc={desc_limpa}")
            args += ["--", alvo_projeto, alvo_id, titulo_limpo]
            criada = engine.json_de(engine.rodar(args))
            return _json(
                {
                    "id": criada["id"],
                    "project": criada["project"],
                    "path": engine.relativo_ao_vault(criada["path"]),
                }
            )

        try:
            return audited(
                "freitask_new", trabalho, path=f"projects/{alvo_projeto}/tasks/{alvo_id}.md"
            )
        except FreitaskErro as e:
            raise _erro(e) from e

    @mcp.tool(
        name="freitask_set",
        description=(
            "Muda uma task: fase (só todo, abstract, tip ou quote: Não iniciada, Refinando, "
            "Refinada, Algum dia), título, descrição, volta_em, prazo e bloqueada_por. Só o que "
            "for dado muda; texto vazio limpa desc, volta_em, prazo e bloqueada_por. Datas em "
            "AAAA-MM-DD. bloqueada_por substitui a lista inteira. Devolve a task como ficou."
        ),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    def freitask_set(
        id: str,
        fase: Fase | None = None,
        titulo: str | None = None,
        desc: str | None = None,
        volta_em: str | None = None,
        prazo: str | None = None,
        bloqueada_por: list[str] | None = None,
    ) -> str:
        """id: o id da task. fase: todo, abstract, tip ou quote. titulo e desc: uma linha;
        desc "" limpa. volta_em e prazo: AAAA-MM-DD, "" limpa. bloqueada_por: ids das tasks de
        que esta espera; [] limpa."""
        flags: list[str] = []
        if fase is not None:
            flags.append(f"--status={fase}")
        if titulo is not None:
            titulo_limpo = _uma_linha(titulo, "titulo")
            if not titulo_limpo:
                raise ToolError("titulo não pode ser vazio")
            flags.append(f"--title={titulo_limpo}")
        if desc is not None:
            flags.append(f"--desc={_uma_linha(desc, 'desc')}")
        if volta_em is not None:
            flags.append(f"--volta-em={volta_em.strip()}")
        if prazo is not None:
            flags.append(f"--prazo={prazo.strip()}")
        if not flags and bloqueada_por is None:
            raise ToolError(
                "nada a mudar: passe ao menos um de fase, titulo, desc, volta_em, prazo ou "
                "bloqueada_por"
            )
        try:
            if bloqueada_por is not None:
                ids = [_id_de(b, "bloqueada_por") for b in bloqueada_por]
                flags.append(f"--bloqueada-por={','.join(ids)}")
            alvo_id = _id_de(id, "id")
            caminho = engine.relativo_ao_vault(_mostrar(alvo_id)["path"])
        except FreitaskErro as e:
            raise _erro(e) from e

        def trabalho() -> str:
            engine.rodar(["set", *flags, "--", alvo_id])
            task = _mostrar(alvo_id)
            linha = _linha(task)
            linha["ciclo"] = task.get("ciclo")
            return _json(linha)

        try:
            return audited("freitask_set", trabalho, path=caminho)
        except FreitaskErro as e:
            raise _erro(e) from e
