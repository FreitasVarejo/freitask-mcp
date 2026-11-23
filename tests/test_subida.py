"""A trava de subida: cada recusa por uma causa, com a linha que diz qual, e o ambiente."""

import os
import subprocess
import sys
from dataclasses import replace

import pytest

from freitask_mcp.config import ConfigInvalida, do_ambiente, validar
from freitask_mcp.servidor import criar_servidor

from .apoio_http import SENHA, URL


def recusa(config, trecho: str) -> None:
    """A fábrica se recusa a montar o servidor, e a linha diz a causa."""
    with pytest.raises(ConfigInvalida, match=trecho) as e:
        criar_servidor(config)
    assert "\n" not in str(e.value), "uma linha só"


def test_a_configuracao_valida_sobe(config):
    validar(config)
    assert criar_servidor(config).app is not None


# --- a URL ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://exemplo.invalid",
        "ftp://exemplo.invalid",
        "exemplo.invalid",
        "https://",
        "https://exemplo.invalid/mcp",
        "https://exemplo.invalid/?x=1",
        "https://usuario:senha@exemplo.invalid",
        "https://exemplo.invalid/",
        "https://exemplo.invalid:porta",
    ],
)
def test_url_invalida_nao_sobe(config, url):
    recusa(replace(config, url=url), "FREITASK_MCP_URL")


def test_https_e_http_em_loopback_valem(config):
    validar(replace(config, url="https://exemplo.invalid"))
    validar(replace(config, url="http://localhost:9000"))
    validar(replace(config, url="http://127.0.0.1:9000"))


# --- a senha --------------------------------------------------------------------------


def test_senha_curta_nao_sobe(config):
    recusa(replace(config, senha="x" * 15), "FREITASK_MCP_SENHA")
    validar(replace(config, senha="x" * 16))


# --- a pasta do estado ----------------------------------------------------------------


def test_estado_que_nao_existe_nao_sobe(config, tmp_path):
    recusa(replace(config, estado=tmp_path / "nao-existe"), "FREITASK_MCP_ESTADO")


def test_estado_que_e_um_arquivo_nao_sobe(config, tmp_path):
    arquivo = tmp_path / "arquivo"
    arquivo.write_text("")
    recusa(replace(config, estado=arquivo), "FREITASK_MCP_ESTADO")


@pytest.mark.skipif(os.geteuid() == 0, reason="root grava em qualquer pasta")
def test_estado_nao_gravavel_nao_sobe(config):
    config.estado.chmod(0o500)
    try:
        recusa(config, "não é gravável")
    finally:
        config.estado.chmod(0o700)


def test_estado_dentro_do_vault_nao_sobe(config, vault):
    dentro = vault / "estado"
    dentro.mkdir()
    recusa(replace(config, estado=dentro), "dentro de .*ObsidianVault")
    recusa(replace(config, estado=vault), "dentro de .*ObsidianVault")


def test_estado_dentro_do_vault_por_symlink_nao_sobe(config, vault, tmp_path):
    atalho = tmp_path / "atalho"
    atalho.symlink_to(vault)

    recusa(replace(config, estado=atalho), "dentro de .*ObsidianVault")


def test_estado_corrompido_nao_sobe(config):
    (config.estado / "estado.json").write_text("{isto não é json")

    recusa(config, "não é legível")


# --- o vault e a CLI ------------------------------------------------------------------


def test_sem_a_pasta_projects_do_vault_nao_sobe(config, vault):
    (vault / "projects").rename(vault / "projetos-movidos")

    recusa(config, "projects")


def test_sem_o_vault_nao_sobe(casa, tmp_path):
    estado = tmp_path / "estado"
    estado.mkdir()
    from freitask_mcp.config import Configuracao

    recusa(Configuracao(url=URL, senha=SENHA, estado=estado), "projects")


def test_sem_o_freitask_no_path_nao_sobe(config, tmp_path, monkeypatch):
    vazio = tmp_path / "sem-binarios"
    vazio.mkdir()
    monkeypatch.setenv("PATH", str(vazio))

    recusa(config, "o freitask não respondeu")


def test_freitask_que_responde_lixo_nao_sobe(config, tmp_path, monkeypatch):
    falso = tmp_path / "falso"
    falso.mkdir()
    binario = falso / "freitask"
    binario.write_text("#!/bin/sh\necho isto-nao-e-json\n")
    binario.chmod(0o755)
    monkeypatch.setenv("PATH", str(falso))

    recusa(config, "o freitask não respondeu")


def test_freitask_que_sai_com_erro_nao_sobe(config, tmp_path, monkeypatch):
    falso = tmp_path / "falso"
    falso.mkdir()
    binario = falso / "freitask"
    binario.write_text("#!/bin/sh\necho quebrou >&2\nexit 7\n")
    binario.chmod(0o755)
    monkeypatch.setenv("PATH", str(falso))

    recusa(config, "o freitask não respondeu")


# --- o ambiente -----------------------------------------------------------------------

AMBIENTE = {
    "FREITASK_MCP_URL": "https://exemplo.invalid",
    "FREITASK_MCP_SENHA": SENHA,
    "FREITASK_MCP_ESTADO": "/tmp/estado-de-mentira",
}


def test_o_ambiente_minimo_usa_os_padroes():
    config = do_ambiente(AMBIENTE)

    assert config.url == "https://exemplo.invalid"
    assert (config.host, config.porta) == ("127.0.0.1", 8421)
    assert do_ambiente({**AMBIENTE, "FREITASK_MCP_PORTA": ""}).porta == 8421
    assert config.redirects == (
        "https://claude.ai/api/mcp/auth_callback",
        "https://claude.com/api/mcp/auth_callback",
    )


def test_o_ambiente_completo():
    config = do_ambiente(
        {
            **AMBIENTE,
            "FREITASK_MCP_URL": "https://exemplo.invalid/",
            "FREITASK_MCP_HOST": "0.0.0.0",
            "FREITASK_MCP_PORTA": "9001",
            "FREITASK_MCP_REDIRECTS": "https://a.invalid/cb, https://b.invalid/cb",
        }
    )

    assert config.url == "https://exemplo.invalid", "sem a barra final"
    assert (config.host, config.porta) == ("0.0.0.0", 9001)
    assert config.redirects == ("https://a.invalid/cb", "https://b.invalid/cb")


@pytest.mark.parametrize("nome", sorted(AMBIENTE))
def test_variavel_obrigatoria_ausente_ou_vazia_nao_sobe(nome):
    for ambiente in ({k: v for k, v in AMBIENTE.items() if k != nome}, {**AMBIENTE, nome: "  "}):
        with pytest.raises(ConfigInvalida, match=nome):
            do_ambiente(ambiente)


@pytest.mark.parametrize("porta", ["abc", "0", "70000", "-1"])
def test_porta_invalida_nao_sobe(porta):
    with pytest.raises(ConfigInvalida, match="FREITASK_MCP_PORTA"):
        do_ambiente({**AMBIENTE, "FREITASK_MCP_PORTA": porta})


def test_callback_invalido_nao_sobe():
    with pytest.raises(ConfigInvalida, match="FREITASK_MCP_REDIRECTS"):
        do_ambiente({**AMBIENTE, "FREITASK_MCP_REDIRECTS": "isto-nao-e-url"})


# --- o processo -----------------------------------------------------------------------


def rodar_remoto(*args, env_extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("FREITASK_MCP_")}
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "freitask_mcp.remoto", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        stdin=subprocess.DEVNULL,
    )


def test_o_processo_sai_com_erro_e_uma_linha_que_diz_qual(vault, tmp_path):
    estado = tmp_path / "estado"
    estado.mkdir()
    ambiente = {**AMBIENTE, "FREITASK_MCP_ESTADO": str(estado)}

    sem_senha = rodar_remoto(env_extra={**ambiente, "FREITASK_MCP_SENHA": "curta"})
    sem_vault = rodar_remoto(env_extra={**ambiente, "HOME": str(tmp_path / "outro-home")})

    for resultado, causa in ((sem_senha, "FREITASK_MCP_SENHA"), (sem_vault, "projects")):
        assert resultado.returncode == 1, resultado.stderr
        assert resultado.stdout == ""
        linhas = resultado.stderr.strip().splitlines()
        assert len(linhas) == 1 and causa in linhas[0], resultado.stderr


def test_o_help_lista_as_variaveis_e_nao_sobe():
    resultado = rodar_remoto("--help", env_extra={})

    assert resultado.returncode == 0
    for nome in (
        "FREITASK_MCP_URL",
        "FREITASK_MCP_SENHA",
        "FREITASK_MCP_ESTADO",
        "FREITASK_MCP_HOST",
        "FREITASK_MCP_PORTA",
        "FREITASK_MCP_REDIRECTS",
    ):
        assert nome in resultado.stdout
