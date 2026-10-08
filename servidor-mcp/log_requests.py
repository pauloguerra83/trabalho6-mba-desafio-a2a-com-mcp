"""Middleware ASGI que registra no stderr cada request JSON-RPC recebido.

Fica por fora do SDK para registrar tambem o que o SDK rejeita antes do
dispatch (por exemplo, _meta sem os campos obrigatorios). O valor do
requestState nunca vai para o log: so a presenca dele, que marca um retry.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send


def _linha(corpo: Any) -> str:
    if not isinstance(corpo, dict):
        return "[mcp] corpo nao e um objeto JSON-RPC"
    params = corpo.get("params") if isinstance(corpo.get("params"), dict) else {}
    meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
    campos = [
        f"method={corpo.get('method')}",
        f"id={json.dumps(corpo.get('id'))}",
    ]
    alvo = params.get("name") or params.get("uri")
    if alvo:
        campos.append(f"name={alvo}")
    campos.append(f"traceparent={meta.get('traceparent', '-')}")
    if "requestState" in params:
        campos.append("requestState=presente")
    return "[mcp] " + " ".join(campos)


class LogDeRequests:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return

        partes: list[bytes] = []
        mais = True
        while mais:
            mensagem = await receive()
            if mensagem["type"] != "http.request":
                break
            partes.append(mensagem.get("body", b""))
            mais = mensagem.get("more_body", False)
        corpo = b"".join(partes)

        try:
            print(_linha(json.loads(corpo)), file=sys.stderr, flush=True)
        except ValueError:
            print("[mcp] corpo que nao e JSON", file=sys.stderr, flush=True)

        entregue = False

        async def reenvio() -> Message:
            nonlocal entregue
            if not entregue:
                entregue = True
                return {"type": "http.request", "body": corpo, "more_body": False}
            return await receive()

        await self.app(scope, reenvio, send)
