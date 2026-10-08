"""O agente como host MCP: um cliente do SDK oficial, vivo entre chamadas, falando HTTP com o servidor.

Nenhum callback de elicitation e registrado: a chamada usa
`allow_input_required=True` e o agente enxerga o `input_required` cru, para
transformar a pergunta em pausa da Task em vez de responder sozinho.
"""

from __future__ import annotations

import re
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

from mcp import Client
from mcp.client.session import ClientSession
from mcp.shared.exceptions import MCPError
from mcp_types import (
    CallToolResult,
    ClientCapabilities,
    ElicitationCapability,
    FormElicitationCapability,
    Implementation,
    InputRequiredResult,
)

from traceparent import Trace

PROTOCOLO = "2026-07-28"
URI_POLITICA = "politica://uso"
CAPABILITIES = ClientCapabilities(elicitation=ElicitationCapability(form=FormElicitationCapability()))


class _SessaoFormOnly(ClientSession):
    """Declara exatamente `{"elicitation": {"form": {}}}`.

    O SDK so anuncia elicitation quando ha callback, e nesse caso anuncia form e
    url (ClientSession._build_capabilities). O agente nao tem callback de
    proposito e so trata form mode, entao a declaracao e fixada aqui.
    """

    def _build_capabilities(self, version: str) -> ClientCapabilities:
        return CAPABILITIES


class _ClienteMcp(Client):
    async def _build_session(self, exit_stack: AsyncExitStack) -> ClientSession:
        sessao = await super()._build_session(exit_stack)
        sessao.__class__ = _SessaoFormOnly
        return sessao


@dataclass(frozen=True)
class Concluido:
    dados: dict[str, Any]


@dataclass(frozen=True)
class Falhou:
    mensagem: str


@dataclass(frozen=True)
class Pausado:
    """Pedido de input do servidor. O requestState e opaco: guardado e ecoado, nunca aberto."""

    chave: str
    alternativas: list[str]
    request_state: str | None


Desfecho = Concluido | Falhou | Pausado


def _meta(trace: Trace | None) -> dict[str, Any] | None:
    return {"traceparent": trace.novo_traceparent()} if trace else None


def _texto(resultado: CallToolResult) -> str:
    return " ".join(getattr(c, "text", "") for c in resultado.content).strip()


def _alternativas(params: Any) -> list[str]:
    schema = getattr(params, "requested_schema", None) or {}
    campo = (schema.get("properties") or {}).get("sala") or {}
    if "enum" in campo:
        return [str(v) for v in campo["enum"]]
    if "const" in campo:
        return [str(campo["const"])]
    return []


class HostMcp:
    def __init__(self, url: str) -> None:
        self.url = url
        self._pilha: AsyncExitStack | None = None
        self._cliente: Client | None = None

    async def _conectado(self) -> Client:
        if self._cliente is None:
            pilha = AsyncExitStack()
            cliente = _ClienteMcp(
                self.url,
                mode=PROTOCOLO,
                cache=None,
                client_info=Implementation(name="agente-central-de-salas", version="1.0.0"),
            )
            self._cliente = await pilha.enter_async_context(cliente)
            self._pilha = pilha
        return self._cliente

    async def fechar(self) -> None:
        if self._pilha is not None:
            await self._pilha.aclose()
        self._pilha = self._cliente = None

    async def descobrir(self, trace: Trace | None) -> set[str]:
        """tools/list antes de qualquer tools/call: o agente nao carrega a lista no codigo."""
        cliente = await self._conectado()
        resultado = await cliente.list_tools(meta=_meta(trace))
        return {t.name for t in resultado.tools}

    async def versao_politica(self, trace: Trace | None) -> str:
        cliente = await self._conectado()
        resultado = await cliente.read_resource(URI_POLITICA, meta=_meta(trace))
        texto = next((getattr(c, "text", "") for c in resultado.contents), "")
        achado = re.match(r"^versao:\s*(\S+)", texto.splitlines()[0] if texto else "")
        return achado.group(1) if achado else ""

    async def chamar(
        self,
        tool: str,
        argumentos: dict[str, Any],
        trace: Trace | None,
        input_responses: dict[str, Any] | None = None,
        request_state: str | None = None,
    ) -> Desfecho:
        """Um tools/call. No retry, o SDK emite um id JSON-RPC novo e o requestState vai como veio."""
        cliente = await self._conectado()
        try:
            resultado = await cliente.session.call_tool(
                tool,
                argumentos,
                input_responses=input_responses,
                request_state=request_state,
                meta=_meta(trace),
                allow_input_required=True,
            )
        except MCPError as e:
            return Falhou(e.error.message)

        if isinstance(resultado, InputRequiredResult):
            pedidos = resultado.input_requests or {}
            if len(pedidos) != 1:
                return Falhou("Resposta input_required inesperada do servidor MCP")
            chave, pedido = next(iter(pedidos.items()))
            alternativas = _alternativas(getattr(pedido, "params", None))
            if not alternativas:
                return Falhou("Elicitation sem alternativas no schema")
            return Pausado(chave, alternativas, resultado.request_state)

        if resultado.is_error:
            return Falhou(_texto(resultado))
        return Concluido(resultado.structured_content or {})
