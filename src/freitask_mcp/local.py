"""O servidor local, em stdio: as cinco tools do freitask sem a extensão nem a auditoria.

`uv run mcp dev src/freitask_mcp/local.py` abre o Inspector. O vault é o do `HOME`.
"""

from mcp.server.fastmcp import FastMCP

from .tools import register_tools

mcp = FastMCP("freitask")
register_tools(mcp)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
