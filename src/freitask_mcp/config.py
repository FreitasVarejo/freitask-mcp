"""A configuração do servidor remoto e a trava de subida.

Tudo vem de variável de ambiente (`freitask-mcp-http --help` é a referência). A fábrica do
servidor chama `validar` antes de montar qualquer coisa: não há como subir sem a trava.
"""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from . import engine

PORTA_PADRAO = 8421
HOST_PADRAO = "127.0.0.1"
REDIRECTS_PADRAO = (
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
)
TAMANHO_MINIMO_DA_SENHA = 16
HOSTS_SEM_TLS = ("127.0.0.1", "localhost", "::1")

AJUDA = """\
freitask-mcp-http: o servidor MCP do freitask em HTTP, com login próprio (OAuth).

Sobe o Streamable HTTP em /mcp (sem estado, resposta em JSON), as rotas do OAuth, a tela de
login em /login e GET /health. Toda configuração é variável de ambiente:

  FREITASK_MCP_URL        o endereço público, https://... (em teste, http://127.0.0.1:<porta>);
                          é o emissor e, com /mcp, o recurso
  FREITASK_MCP_SENHA      a senha da tela de login, com 16 caracteres ou mais
  FREITASK_MCP_ESTADO     a pasta do estado (clientes, tokens, auditoria), fora do vault
  FREITASK_MCP_HOST       onde escuta (padrão 127.0.0.1)
  FREITASK_MCP_PORTA      a porta (padrão 8421)
  FREITASK_MCP_REDIRECTS  os callbacks aceitos no registro, separados por vírgula
                          (padrão: os do claude.ai e do claude.com)

O vault é o do HOME: $HOME/ObsidianVault. O servidor só sobe com a URL e a senha válidas, a pasta
do estado gravável e fora do vault, $HOME/ObsidianVault/projects no lugar e o freitask
respondendo; faltando um, sai com erro e uma linha que diz qual.
"""


class ConfigInvalida(Exception):
    """Uma causa de o servidor não subir, numa linha pronta para ir ao stderr."""


@dataclass(frozen=True)
class Configuracao:
    url: str
    senha: str
    estado: Path
    host: str = HOST_PADRAO
    porta: int = PORTA_PADRAO
    redirects: tuple[str, ...] = REDIRECTS_PADRAO
    # O relógio de tudo que expira. O SDK confere a validade também pelo relógio real, então um
    # relógio de teste precisa partir de `time.time()` e só avançar dali.
    relogio: Callable[[], float] = time.time

    @property
    def recurso(self) -> str:
        return f"{self.url}/mcp"

    @property
    def vault(self) -> Path:
        return engine.raiz_do_vault()


def _endereco(bruto: str) -> str:
    """A URL sem barra final, ou ConfigInvalida."""
    partes = urlsplit(bruto.strip())
    try:
        host = partes.hostname
        _ = partes.port
    except ValueError:
        raise ConfigInvalida("FREITASK_MCP_URL não é uma URL válida") from None
    if partes.scheme not in ("http", "https") or not host:
        raise ConfigInvalida("FREITASK_MCP_URL precisa ser https://<endereço>")
    if partes.scheme == "http" and host not in HOSTS_SEM_TLS:
        raise ConfigInvalida("FREITASK_MCP_URL só pode ser http em 127.0.0.1 ou localhost")
    if partes.username is not None or partes.password is not None:
        raise ConfigInvalida("FREITASK_MCP_URL não pode ter usuário nem senha")
    if partes.path not in ("", "/") or partes.query or partes.fragment:
        raise ConfigInvalida("FREITASK_MCP_URL é só o endereço, sem caminho, query nem fragmento")
    return f"{partes.scheme}://{partes.netloc}"


def _callbacks(bruto: str) -> tuple[str, ...]:
    itens = tuple(i.strip() for i in bruto.split(",") if i.strip())
    if not itens:
        raise ConfigInvalida("FREITASK_MCP_REDIRECTS está vazia")
    for item in itens:
        partes = urlsplit(item)
        if partes.scheme not in ("http", "https") or not partes.netloc or partes.fragment:
            raise ConfigInvalida(f"FREITASK_MCP_REDIRECTS tem um callback inválido: {item!r}")
    return itens


def do_ambiente(env: Mapping[str, str] | None = None) -> Configuracao:
    """Lê as variáveis `FREITASK_MCP_*`. Só o formato; o resto é com `validar`."""
    env = os.environ if env is None else env

    def obrigatoria(nome: str) -> str:
        valor = env.get(nome, "")
        if not valor.strip():
            raise ConfigInvalida(f"{nome} não está definida")
        return valor

    url = _endereco(obrigatoria("FREITASK_MCP_URL"))
    senha = obrigatoria("FREITASK_MCP_SENHA")
    estado = Path(obrigatoria("FREITASK_MCP_ESTADO")).expanduser()
    try:
        porta = int(env.get("FREITASK_MCP_PORTA", "").strip() or PORTA_PADRAO)
    except ValueError:
        raise ConfigInvalida("FREITASK_MCP_PORTA não é um número") from None
    if not 1 <= porta <= 65535:
        raise ConfigInvalida("FREITASK_MCP_PORTA fora de 1 a 65535")
    redirects = (
        _callbacks(env["FREITASK_MCP_REDIRECTS"])
        if env.get("FREITASK_MCP_REDIRECTS", "").strip()
        else REDIRECTS_PADRAO
    )
    return Configuracao(
        url=url,
        senha=senha,
        estado=estado,
        host=env.get("FREITASK_MCP_HOST", "").strip() or HOST_PADRAO,
        porta=porta,
        redirects=redirects,
    )


def validar(config: Configuracao) -> None:
    """A trava de subida: levanta `ConfigInvalida` na primeira causa que achar.

    A CLI trata vault ausente como vault vazio (`list --json` devolve `[]` com saída 0), então
    sem esta conferência o servidor subiria "saudável" servindo um vault que não existe.
    """
    if _endereco(config.url) != config.url:
        raise ConfigInvalida("FREITASK_MCP_URL tem de vir sem barra final")
    if len(config.senha) < TAMANHO_MINIMO_DA_SENHA:
        raise ConfigInvalida(
            f"FREITASK_MCP_SENHA precisa de {TAMANHO_MINIMO_DA_SENHA} caracteres ou mais"
        )
    _validar_estado(config.estado, config.vault)
    if not (config.vault / "projects").is_dir():
        raise ConfigInvalida(
            "não há $HOME/ObsidianVault/projects: o vault não está montado neste usuário"
        )
    try:
        engine.json_de(engine.rodar(["projects", "--json"]))
    except engine.FreitaskErro as e:
        raise ConfigInvalida(f"o freitask não respondeu: {e}") from None


def _validar_estado(estado: Path, vault: Path) -> None:
    if not estado.is_dir():
        raise ConfigInvalida(f"FREITASK_MCP_ESTADO não é uma pasta que exista: {estado}")
    real = estado.resolve()
    if real == vault.resolve() or real.is_relative_to(vault.resolve()):
        raise ConfigInvalida(
            "FREITASK_MCP_ESTADO está dentro de $HOME/ObsidianVault: o Syncthing levaria os "
            "tokens para as outras máquinas"
        )
    try:
        with tempfile.NamedTemporaryFile(dir=estado, prefix=".sonda-"):
            pass
    except OSError as e:
        raise ConfigInvalida(f"FREITASK_MCP_ESTADO não é gravável: {e.strerror}") from None
