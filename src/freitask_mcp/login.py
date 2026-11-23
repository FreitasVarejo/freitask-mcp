"""A tela de login e o `/health`: as rotas deste servidor que não passam pelo SDK.

`GET /login?t=<pedido>` mostra o nome do cliente e o campo da senha. `POST /login` confere a
senha em tempo constante; certa, o pedido vira um código e o navegador volta ao `redirect_uri`
com `code` e `state`; errada, a tela de novo com 401. Os erros de senha têm um freio global: no
11º dentro da janela vem 429 para todos, inclusive para a senha certa.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from html import escape
from urllib.parse import parse_qs

from mcp.server.auth.provider import construct_redirect_uri
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from .config import Configuracao
from .freios import Janela
from .provedor import Provedor

# O log do login leva só o começo do `client_id`: nome de cliente, senha, pedido e código ficam
# fora, e o ID inteiro também, porque no modo de cliente fixo ele é o que abre a tela de login.
# Sem estas linhas, um 429 no celular não tem como ser explicado depois.
log = logging.getLogger(__name__)
COMECO_DO_CLIENTE = 8


def _curto(client_id: str) -> str:
    return client_id[:COMECO_DO_CLIENTE]

ERROS_DE_SENHA = 10
JANELA_DE_ERROS = 15 * 60
LIMITE_DO_CORPO = 8 * 1024

# Sem `form-action`: o Chromium a aplica também aos redirects que seguem o POST, e ela barraria a
# volta para o claude.ai.
CABECALHOS = {
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"
    ),
}

ESTILO = (
    "body{font:16px system-ui,sans-serif;max-width:24rem;margin:4rem auto;padding:0 1rem}"
    "input{display:block;width:100%;box-sizing:border-box;margin:.5rem 0 1rem;padding:.5rem}"
    "button{padding:.5rem 1rem}.erro{color:#b00020}"
)


def _pagina(corpo: str, status: int = 200, headers: dict[str, str] | None = None) -> HTMLResponse:
    html = (
        '<!doctype html><html lang="pt-BR"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>freitask</title><style>{ESTILO}</style><body>{corpo}</body></html>"
    )
    return HTMLResponse(html, status_code=status, headers={**CABECALHOS, **(headers or {})})


def _formulario(nome_do_cliente: str, t: str, erro: str | None = None) -> str:
    aviso = f'<p class="erro">{escape(erro)}</p>' if erro else ""
    return (
        "<h1>freitask</h1>"
        f"<p><b>{escape(nome_do_cliente)}</b> quer acessar as suas tasks.</p>{aviso}"
        '<form method="post" action="/login">'
        f'<input type="hidden" name="t" value="{escape(t, quote=True)}">'
        '<label>Senha<input type="password" name="senha" autocomplete="current-password" '
        "autofocus></label>"
        '<button type="submit">Entrar</button></form>'
    )


def _pedido_invalido() -> HTMLResponse:
    return _pagina(
        '<h1>freitask</h1><p class="erro">Pedido de login inválido, vencido ou já usado. '
        "Volte ao aplicativo e tente conectar de novo.</p>",
        status=400,
    )


async def _corpo_do_form(request: Request) -> dict[str, str] | None:
    """O formulário urlencoded, com teto de tamanho; None se passar do teto."""
    corpo = bytearray()
    async for pedaco in request.stream():
        corpo += pedaco
        if len(corpo) > LIMITE_DO_CORPO:
            return None
    campos = parse_qs(corpo.decode("utf-8", errors="replace"), keep_blank_values=True)
    return {nome: valores[0] for nome, valores in campos.items()}


def _igual(a: str, b: str) -> bool:
    """Tempo constante, e sem vazar o tamanho: compara os digests."""
    return hmac.compare_digest(
        hashlib.sha256(a.encode()).digest(), hashlib.sha256(b.encode()).digest()
    )


def registrar_rotas(mcp: FastMCP, config: Configuracao, provedor: Provedor) -> Janela:
    """Registra `/login` e `/health` no `mcp`. Devolve o freio da senha, para o teste olhar."""
    erros = Janela(ERROS_DE_SENHA, JANELA_DE_ERROS, config.relogio)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    @mcp.custom_route("/login", methods=["GET"])
    async def tela(request: Request) -> Response:
        achado = provedor.pedido(request.query_params.get("t", ""))
        if achado is None:
            return _pedido_invalido()
        _, cliente = achado
        return _pagina(
            _formulario(cliente.client_name or "Um aplicativo", request.query_params["t"])
        )

    @mcp.custom_route("/login", methods=["POST"])
    async def entrar(request: Request) -> Response:
        campos = await _corpo_do_form(request)
        if campos is None:
            return _pagina("<p>Pedido grande demais.</p>", status=413)
        if erros.cheia():
            log.warning("login recusado: o freio de senha está ativo")
            return _pagina(
                '<h1>freitask</h1><p class="erro">Erros de senha demais. '
                "Espere alguns minutos.</p>",
                status=429,
                headers={"Retry-After": str(erros.espera())},
            )
        t = campos.get("t", "")
        achado = provedor.pedido(t)
        if achado is None:
            return _pedido_invalido()
        _, cliente = achado
        if not _igual(campos.get("senha", ""), config.senha):
            erros.registrar()
            log.warning("senha errada no login do cliente %s", _curto(cliente.client_id))
            return _pagina(
                _formulario(cliente.client_name or "Um aplicativo", t, "Senha incorreta."),
                status=401,
            )
        pedido, codigo = provedor.concluir_login(t)
        log.info("login aceito para o cliente %s", _curto(pedido.client_id))
        volta = construct_redirect_uri(
            str(pedido.params.redirect_uri), code=codigo, state=pedido.params.state
        )
        return RedirectResponse(volta, status_code=303, headers=CABECALHOS)

    return erros
