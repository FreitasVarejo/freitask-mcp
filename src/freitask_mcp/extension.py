"""A extensão do obsidian-web-mcp: as cinco tools do freitask no servidor remoto.

Registra as tools e declara cada uma na auditoria do upstream (mutação ou leitura). Autenticação,
OAuth e bearer vêm do upstream.
"""

from obsidian_vault_mcp.audit import register_audit_operation, run_audited
from obsidian_vault_mcp.extensions import Extension
from obsidian_vault_mcp.server import serve

from .tools import OPERACOES, register_tools


class FreitaskExtension(Extension):
    def register_tools(self, mcp) -> None:
        for operacao, tipo in OPERACOES.items():
            register_audit_operation(operacao, kind=tipo)
        register_tools(mcp, audited=run_audited)


def main() -> None:
    serve([FreitaskExtension()])
