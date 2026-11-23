"""A auditoria das escritas do servidor remoto: uma linha por escrita em `auditoria.jsonl`.

`Auditoria` é o `audited(operacao, func, **contexto)` que `tools.register_tools` espera. Só a
operação que `OPERACOES` marca como "mutation" deixa linha; a leitura passa direto. A linha leva
quando, a operação, o resultado, o caminho da task, o `client_id` e o começo do SHA-256 do token
do chamador (o mesmo digest que o estado guarda, nunca o token).
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from mcp.server.auth.middleware.auth_context import get_access_token

from .provedor import digest
from .tools import OPERACOES

log = logging.getLogger(__name__)

TAMANHO_DO_HASH = 12


class Auditoria:
    def __init__(self, caminho: Path, relogio: Callable[[], float]):
        self._caminho = caminho
        self._relogio = relogio

    def __call__(self, operacao: str, func: Callable[[], str], **contexto: object) -> str:
        if OPERACOES.get(operacao) == "read":
            return func()
        try:
            resultado = func()
        except Exception as e:
            self._gravar(operacao, "erro", contexto, type(e).__name__)
            raise
        self._gravar(operacao, "ok", contexto, None)
        return resultado

    def _gravar(
        self, operacao: str, resultado: str, contexto: dict[str, object], erro: str | None
    ) -> None:
        # Vem do SDK: o token do pedido em curso, pelo contextvar que o middleware de bearer põe.
        acesso = get_access_token()
        linha = {
            "quando": datetime.fromtimestamp(self._relogio(), UTC).isoformat(timespec="seconds"),
            "operacao": operacao,
            "resultado": resultado,
            "erro": erro,
            "caminho": contexto.get("path"),
            "client_id": acesso.client_id if acesso else None,
            "token": digest(acesso.token)[:TAMANHO_DO_HASH] if acesso else None,
        }
        try:
            fd = os.open(self._caminho, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as arquivo:
                arquivo.write(json.dumps(linha, ensure_ascii=False) + "\n")
        except OSError as e:
            # A escrita na task já aconteceu: falhar aqui faria o cliente repetir o pedido.
            log.error("não consegui gravar a auditoria de %s: %s", operacao, e.strerror)
