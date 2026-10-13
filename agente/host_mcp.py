"""O agente como host MCP: um cliente do SDK oficial, vivo entre chamadas, falando HTTP com o servidor.

Nenhum callback de elicitation e registrado: a chamada usa
`allow_input_required=True` e o agente enxerga o `input_required` cru, para
transformar a pergunta em pausa da Task em vez de responder sozinho.

O cliente vive numa task propria (`HostMcp._manter`): o contexto do SDK abre um
task group do anyio, que so pode ser fechado na task em que foi aberto. Se o
transporte cai (servidor MCP fora do ar), esse cliente fica inutilizavel: ele e
descartado e o proximo request cria outro.
"""

from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, TypeVar

from mcp import Client
from mcp.client.session import ClientSession
from mcp.shared.exceptions import MCPError
from mcp_types import (
    CONNECTION_CLOSED,
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

T = TypeVar("T")


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


class McpIndisponivel(Exception):
    """O transporte com o servidor MCP caiu. O cliente foi descartado; o proximo request cria outro."""


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
        self._cliente: Client | None = None
        self._encerrar: asyncio.Event | None = None
        self._donos: set[asyncio.Task[None]] = set()
        self._trava = asyncio.Lock()

    def _novo_cliente(self) -> Client:
        return _ClienteMcp(
            self.url,
            mode=PROTOCOLO,
            cache=None,
            client_info=Implementation(name="agente-central-de-salas", version="1.0.0"),
        )

    async def _manter(self, pronto: asyncio.Future[Client], encerrar: asyncio.Event) -> None:
        """Dona do cliente: abre e fecha o contexto do SDK na mesma task."""
        try:
            async with self._novo_cliente() as cliente:
                pronto.set_result(cliente)
                await encerrar.wait()
        except Exception as e:
            if not pronto.done():
                pronto.set_exception(e)
            else:  # fechar um cliente cujo transporte ja caiu pode falhar; ele sai de cena assim mesmo
                print(f"[agente] cliente MCP encerrado com erro: {e!r}", file=sys.stderr, flush=True)
        finally:
            if not pronto.done():
                pronto.cancel()

    async def _conectado(self) -> Client:
        async with self._trava:
            if self._cliente is None:
                pronto: asyncio.Future[Client] = asyncio.get_running_loop().create_future()
                encerrar = asyncio.Event()
                dono = asyncio.create_task(self._manter(pronto, encerrar))
                self._donos.add(dono)
                dono.add_done_callback(self._donos.discard)
                self._cliente = await pronto
                self._encerrar = encerrar
            return self._cliente

    async def _descartar(self, cliente: Client) -> None:
        async with self._trava:
            if self._cliente is not cliente:
                return  # outro request ja trocou o cliente
            self._cliente = None
            if self._encerrar is not None:
                self._encerrar.set()
            self._encerrar = None

    async def _executar(self, operacao: Callable[[Client], Awaitable[T]]) -> T:
        """Roda uma operacao no cliente atual; se o transporte caiu, descarta o cliente."""
        cliente = await self._conectado()
        try:
            return await operacao(cliente)
        except MCPError as e:
            if e.error.code != CONNECTION_CLOSED:
                raise
            await self._descartar(cliente)
            raise McpIndisponivel(e.error.message) from e
        except Exception:
            await self._descartar(cliente)
            raise

    async def fechar(self) -> None:
        async with self._trava:
            self._cliente = None
            if self._encerrar is not None:
                self._encerrar.set()
            self._encerrar = None
            donos = set(self._donos)
        if donos:
            await asyncio.wait(donos, timeout=5)

    async def descobrir(self, trace: Trace | None) -> set[str]:
        """tools/list antes de qualquer tools/call: o agente nao carrega a lista no codigo."""
        resultado = await self._executar(lambda c: c.list_tools(meta=_meta(trace)))
        return {t.name for t in resultado.tools}

    async def versao_politica(self, trace: Trace | None) -> str:
        resultado = await self._executar(lambda c: c.read_resource(URI_POLITICA, meta=_meta(trace)))
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
        """Um tools/call. No retry, o SDK emite um id JSON-RPC novo e o requestState vai como veio.

        Levanta `McpIndisponivel` quando o servidor MCP nao responde.
        """
        try:
            resultado = await self._executar(
                lambda c: c.session.call_tool(
                    tool,
                    argumentos,
                    input_responses=input_responses,
                    request_state=request_state,
                    meta=_meta(trace),
                    allow_input_required=True,
                )
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
