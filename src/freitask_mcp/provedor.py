"""O provedor OAuth: guarda de clientes e tokens, sobre o protocolo do SDK.

O SDK (`mcp.server.auth`) serve as rotas `/authorize`, `/token`, `/register`, `/revoke` e os dois
`/.well-known`, e confere cliente, `redirect_uri` registrado, PKCE S256, validade do código e
segredo do cliente. Este módulo é o que o protocolo deixa para o servidor: quem é cliente, o que é
um pedido de login, um código e um token, e o que fica no disco.

Fica no disco (`estado.json`, 0600, troca atômica) só clientes e tokens, e de cada token só o
SHA-256. Pedidos de login e códigos vivem na memória: reiniciar derruba o que está no meio.

Há dois modos. No registro aberto, os clientes vêm do `/register` e vão para o disco. No cliente
fixo (`Configuracao.cliente`), o servidor conhece um cliente só, montado da configuração, e ele
não vai para o disco: o `estado.json` guarda só tokens. Nos dois, token de cliente que o servidor
não conhece não vale, e é isso que faz a troca de modo cortar o que o outro emitiu.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl

from .config import ConfigInvalida, Configuracao
from .freios import Janela

log = logging.getLogger(__name__)

VERSAO_DO_ESTADO = 1
VALIDADE_DO_PEDIDO = 10 * 60
VALIDADE_DO_CODIGO = 5 * 60
VALIDADE_DO_ACESSO = 60 * 60
VALIDADE_DO_REFRESCO = 30 * 24 * 60 * 60
REGISTROS_POR_HORA = 20
CLIENTES_GUARDADOS = 50
PEDIDOS_GUARDADOS = 200


def cliente_fixo(config: Configuracao) -> OAuthClientInformationFull:
    """O único cliente do modo fixo: público (sem segredo, só PKCE), com os callbacks da lista."""
    assert config.cliente is not None
    return OAuthClientInformationFull(
        client_id=config.cliente,
        client_name="Conector do freitask",
        redirect_uris=[AnyUrl(uri) for uri in config.redirects],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
    )


class ErroDeEstado(ConfigInvalida):
    """O arquivo de estado existe e não dá para ler."""


def digest(token: str) -> str:
    """O que fica no disco de um token."""
    return hashlib.sha256(token.encode()).hexdigest()


def _gravar_atomico(caminho: Path, dados: dict) -> None:
    """Grava num temporário 0600 e troca por cima: quem lê vê o arquivo velho ou o novo."""
    temporario = caminho.with_name(caminho.name + ".tmp")
    fd = os.open(temporario, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as arquivo:
            json.dump(dados, arquivo, ensure_ascii=False)
            arquivo.flush()
            os.fsync(arquivo.fileno())
        os.replace(temporario, caminho)
    except BaseException:
        temporario.unlink(missing_ok=True)
        raise


@dataclass
class Pedido:
    """Um `/authorize` à espera da senha: vive 10 min e serve uma vez."""

    client_id: str
    params: AuthorizationParams
    expira: float


class Provedor:
    """Implementa `OAuthAuthorizationServerProvider` do SDK."""

    def __init__(self, config: Configuracao):
        self._config = config
        self._agora = config.relogio
        self._arquivo = config.estado / "estado.json"
        self._redirects = set(config.redirects)
        self._pedidos: dict[str, Pedido] = {}
        self._codigos: dict[str, AuthorizationCode] = {}
        self._clientes: dict[str, OAuthClientInformationFull] = {}
        # digest do token -> {client_id, expira, par}; o par liga o acesso ao refresh dele.
        self._acessos: dict[str, dict] = {}
        self._refrescos: dict[str, dict] = {}
        self.registros = Janela(REGISTROS_POR_HORA, 3600, self._agora)
        self._fixo = cliente_fixo(config) if config.cliente is not None else None
        if self._fixo is not None:
            # Os clientes do registro aberto, se o estado os tem, ficam de fora, e o próximo
            # `_salvar` os tira do arquivo; os tokens deles já não valem (cliente desconhecido).
            self._clientes[self._fixo.client_id] = self._fixo
        self._carregar()

    # --- disco ------------------------------------------------------------------------

    def _carregar(self) -> None:
        if not self._arquivo.exists():
            return
        try:
            dados = json.loads(self._arquivo.read_text(encoding="utf-8"))
            if dados["versao"] != VERSAO_DO_ESTADO:
                raise ValueError(f"versão {dados['versao']}")
            for bruto in dados["clientes"]:
                cliente = OAuthClientInformationFull.model_validate(bruto)
                if self._fixo is None:
                    self._clientes[cliente.client_id] = cliente
            self._acessos = dict(dados["acessos"])
            self._refrescos = dict(dados["refrescos"])
        except (OSError, ValueError, KeyError, TypeError) as e:
            raise ErroDeEstado(f"o estado em {self._arquivo} não é legível: {e}") from None
        for cliente in sorted(self._clientes.values(), key=lambda c: c.client_id_issued_at or 0):
            if cliente.client_id_issued_at:
                self.registros.registrar(cliente.client_id_issued_at)

    def _salvar(self) -> None:
        _gravar_atomico(
            self._arquivo,
            {
                "versao": VERSAO_DO_ESTADO,
                "clientes": [
                    c.model_dump(mode="json", exclude_none=True)
                    for c in self._clientes.values()
                    if c is not self._fixo
                ],
                "acessos": self._acessos,
                "refrescos": self._refrescos,
            },
        )

    # --- clientes ---------------------------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._clientes.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if self._fixo is not None:
            # A rota nem existe neste modo; é a segunda trava, para o caso de o SDK a montar.
            raise RegistrationError("invalid_client_metadata", "o registro está desligado")
        for uri in client_info.redirect_uris or []:
            if str(uri) not in self._redirects:
                raise RegistrationError(
                    "invalid_redirect_uri", "redirect_uri fora da lista de callbacks aceitos"
                )
        if self.registros.cheia():
            raise RegistrationError("invalid_client_metadata", "registros demais; espere")
        while len(self._clientes) >= CLIENTES_GUARDADOS:
            velho = next((c for c in self._clientes.values() if not self._tem_token_vivo(c)), None)
            if velho is None:
                raise RegistrationError(
                    "invalid_client_metadata", "todos os clientes guardados têm token vivo"
                )
            del self._clientes[velho.client_id]
        self._clientes[client_info.client_id] = client_info
        self.registros.registrar()
        self._salvar()
        log.info("cliente registrado: %s", client_info.client_id)

    def _tem_token_vivo(self, cliente: OAuthClientInformationFull) -> bool:
        agora = self._agora()
        return any(
            r["client_id"] == cliente.client_id and r["expira"] > agora
            for r in (*self._acessos.values(), *self._refrescos.values())
        )

    # --- o login ----------------------------------------------------------------------

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Guarda o pedido e manda o navegador para a tela de login."""
        agora = self._agora()
        self._pedidos = {t: p for t, p in self._pedidos.items() if p.expira > agora}
        while len(self._pedidos) >= PEDIDOS_GUARDADOS:
            del self._pedidos[next(iter(self._pedidos))]
        t = secrets.token_urlsafe(32)
        self._pedidos[t] = Pedido(client.client_id, params, agora + VALIDADE_DO_PEDIDO)
        return f"{self._config.url}/login?t={t}"

    def pedido(self, t: str) -> tuple[Pedido, OAuthClientInformationFull] | None:
        """O pedido vivo desse `t` e o cliente dele, sem consumir."""
        pedido = self._pedidos.get(t)
        if pedido is None:
            return None
        cliente = self._clientes.get(pedido.client_id)
        if pedido.expira <= self._agora() or cliente is None:
            del self._pedidos[t]
            return None
        return pedido, cliente

    def concluir_login(self, t: str) -> tuple[Pedido, str] | None:
        """A senha conferiu: consome o pedido e emite o código (256 bits, 5 min, um uso)."""
        achado = self.pedido(t)
        if achado is None:
            return None
        pedido, _ = achado
        del self._pedidos[t]
        agora = self._agora()
        self._codigos = {c: a for c, a in self._codigos.items() if a.expires_at > agora}
        codigo = secrets.token_urlsafe(32)
        p = pedido.params
        self._codigos[codigo] = AuthorizationCode(
            code=codigo,
            scopes=[],
            expires_at=agora + VALIDADE_DO_CODIGO,
            client_id=pedido.client_id,
            code_challenge=p.code_challenge,
            redirect_uri=p.redirect_uri,
            redirect_uri_provided_explicitly=p.redirect_uri_provided_explicitly,
            resource=self._config.recurso,
        )
        return pedido, codigo

    # --- o código e o par de tokens ---------------------------------------------------

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        """O código serve uma vez: lê e consome. De outro cliente, finge que não existe."""
        codigo = self._codigos.get(authorization_code)
        if codigo is None or codigo.client_id != client.client_id:
            return None
        del self._codigos[authorization_code]
        if codigo.expires_at <= self._agora():
            return None
        return codigo

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        return self._emitir(client.client_id)

    def _emitir(self, client_id: str) -> OAuthToken:
        agora = self._agora()
        self._podar_tokens()
        acesso, refresco, par = (
            secrets.token_urlsafe(32),
            secrets.token_urlsafe(32),
            secrets.token_hex(8),
        )
        self._acessos[digest(acesso)] = {
            "client_id": client_id,
            "expira": agora + VALIDADE_DO_ACESSO,
            "par": par,
        }
        self._refrescos[digest(refresco)] = {
            "client_id": client_id,
            "expira": agora + VALIDADE_DO_REFRESCO,
            "par": par,
        }
        self._salvar()
        return OAuthToken(
            access_token=acesso, expires_in=VALIDADE_DO_ACESSO, refresh_token=refresco
        )

    def _vale(self, registro: dict) -> bool:
        """Não venceu e é de um cliente que o servidor conhece."""
        return registro["expira"] > self._agora() and registro["client_id"] in self._clientes

    def _podar_tokens(self) -> None:
        self._acessos = {h: r for h, r in self._acessos.items() if self._vale(r)}
        self._refrescos = {h: r for h, r in self._refrescos.items() if self._vale(r)}

    def _soltar_par(self, par: str) -> None:
        self._acessos = {h: r for h, r in self._acessos.items() if r["par"] != par}
        self._refrescos = {h: r for h, r in self._refrescos.items() if r["par"] != par}

    async def load_access_token(self, token: str) -> AccessToken | None:
        registro = self._acessos.get(digest(token))
        if registro is None or not self._vale(registro):
            return None
        return AccessToken(
            token=token,
            client_id=registro["client_id"],
            scopes=[],
            expires_at=math.floor(registro["expira"]),
            resource=self._config.recurso,
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        registro = self._refrescos.get(digest(refresh_token))
        if (
            registro is None
            or registro["client_id"] != client.client_id
            or not self._vale(registro)
        ):
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=registro["client_id"],
            scopes=[],
            expires_at=math.floor(registro["expira"]),
            resource=self._config.recurso,
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        """Gira o par: o refresh que entrou e o acesso dele param de valer."""
        registro = self._refrescos.get(digest(refresh_token.token))
        if registro is None:
            raise TokenError("invalid_grant", "refresh token não existe")
        self._soltar_par(registro["par"])
        return self._emitir(client.client_id)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        h = digest(token.token)
        registro = self._acessos.get(h) or self._refrescos.get(h)
        if registro is None:
            return
        self._soltar_par(registro["par"])
        self._salvar()
