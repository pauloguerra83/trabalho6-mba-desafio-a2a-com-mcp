"""Middleware ASGI que deixa o servidor falar so a revisao 2026-07-28.

O SDK escolhe o transporte pelo header MCP-Protocol-Version
(mcp/server/streamable_http_manager.py): sem o header, ou com uma revisao de
handshake (2024/2025), o request vai para o transporte legado, que nao exige
os campos de _meta e chega a criar reserva sem eles. Aqui esses requests
recebem a resposta que o caminho 2026-07-28 daria, calculada pelo proprio
ladder do SDK: -32602 quando falta protocolVersion ou clientCapabilities,
-32020 quando o header nao bate com o _meta, -32022 para uma revisao que o
servidor nao serve. Todos com HTTP 400.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.shared.inbound import (
    ERROR_CODE_HTTP_STATUS,
    MCP_PROTOCOL_VERSION_HEADER,
    InboundLadderRejection,
    classify_inbound_request,
)
from mcp_types import INVALID_PARAMS, INVALID_REQUEST, PARSE_ERROR
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS
from starlette.types import ASGIApp, Receive, Scope, Send


def _cabecalhos(scope: Scope) -> dict[str, str]:
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}


async def _ler_corpo(receive: Receive) -> bytes:
    partes: list[bytes] = []
    while True:
        mensagem = await receive()
        if mensagem["type"] != "http.request":
            break
        partes.append(mensagem.get("body", b""))
        if not mensagem.get("more_body", False):
            break
    return b"".join(partes)


def _resposta(id_rpc: Any, codigo: int, mensagem: str, dados: Any = None) -> dict[str, Any]:
    erro: dict[str, Any] = {"code": codigo, "message": mensagem}
    if dados is not None:
        erro["data"] = dados
    return {"jsonrpc": "2.0", "id": id_rpc, "error": erro}


def _recusa(corpo: bytes, cabecalhos: dict[str, str]) -> dict[str, Any]:
    """O erro JSON-RPC que o caminho 2026-07-28 daria a este request."""
    try:
        msg = json.loads(corpo)
    except ValueError:
        return _resposta(None, PARSE_ERROR, "Parse error")
    if not isinstance(msg, dict):
        return _resposta(None, INVALID_REQUEST, "Invalid Request")
    rejeicao = classify_inbound_request(msg, headers=cabecalhos)
    if isinstance(rejeicao, InboundLadderRejection):
        return _resposta(msg.get("id"), rejeicao.code, rejeicao.message, rejeicao.data)
    # O ladder so aprova header moderno, e por aqui o header e ausente ou legado.
    return _resposta(msg.get("id"), INVALID_PARAMS, "Unsupported MCP-Protocol-Version header")


class GuardaDeVersao:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return
        cabecalhos = _cabecalhos(scope)
        versao = cabecalhos.get(MCP_PROTOCOL_VERSION_HEADER)
        if versao is not None and versao not in HANDSHAKE_PROTOCOL_VERSIONS:
            # Caminho 2026-07-28 do SDK, que valida _meta e headers por conta propria.
            await self.app(scope, receive, send)
            return

        resposta = _recusa(await _ler_corpo(receive), cabecalhos)
        corpo = json.dumps(resposta).encode()
        await send(
            {
                "type": "http.response.start",
                "status": ERROR_CODE_HTTP_STATUS.get(resposta["error"]["code"], 400),
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(corpo)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": corpo})
