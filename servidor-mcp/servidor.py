"""Servidor MCP da Central de Salas: tres tools, um resource e MRTR na reserva.

    REQUEST_STATE_SECRET=... python servidor-mcp/servidor.py

Roda em Streamable HTTP stateless (revisao 2026-07-28), endpoint unico /mcp.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import uvicorn
from mcp.server.elicitation import render_elicitation_schema
from mcp.server.mcpserver import Context, MCPServer, RequestStateSecurity
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from mcp_types import (
    INVALID_PARAMS,
    MISSING_REQUIRED_CLIENT_CAPABILITY,
    ElicitRequest,
    ElicitRequestFormParams,
    InputRequiredResult,
)

from dominio import ERRO_SEM_ALTERNATIVA, CentralDeSalas, ErroDeRegra, Pedido
from guarda_versao import GuardaDeVersao
from log_requests import LogDeRequests
from modelos import ConflitoOut, Disponibilidade, ListaDeSalas, ReservaOut, SalaOut, modelo_escolha

HOST = os.environ.get("MCP_HOST", "127.0.0.1")
PORTA = int(os.environ.get("MCP_PORT", "7301"))
# Entre 5 e 30 minutos, como pede o enunciado.
VALIDADE_DO_ESTADO = 600.0

PERGUNTA = "A sala pedida esta ocupada nesse intervalo. Escolha uma alternativa."
CHAVE_DA_ESCOLHA = "escolha_de_sala"


def _segredo() -> str:
    segredo = os.environ.get("REQUEST_STATE_SECRET", "")
    if len(segredo.encode()) < 32:
        sys.exit(
            "REQUEST_STATE_SECRET ausente ou curto (minimo 32 bytes). Gere com:\n"
            '  python -c "import secrets; print(secrets.token_hex(32))"'
        )
    return segredo


central = CentralDeSalas()

mcp = MCPServer(
    name="central-de-salas",
    version="1.0.0",
    request_state_security=RequestStateSecurity(keys=[_segredo()], ttl=VALIDADE_DO_ESTADO),
)


def _validar(sala: str, inicio: str, fim: str) -> Pedido:
    try:
        return central.validar(sala, inicio, fim)
    except ErroDeRegra as e:
        raise ToolError(str(e)) from None


@mcp.tool(description="Lista todas as salas com capacidade e recursos.")
def listar_salas() -> ListaDeSalas:
    return ListaDeSalas(
        salas=[SalaOut(id=s.id, nome=s.nome, capacidade=s.capacidade, recursos=list(s.recursos)) for s in central.salas.values()]
    )


@mcp.tool(description="Diz se uma sala esta livre no intervalo, e quais reservas conflitam.")
def consultar_disponibilidade(sala: str, inicio: str, fim: str) -> Disponibilidade:
    pedido = _validar(sala, inicio, fim)
    conflitos = central.conflitos(sala, pedido.inicio, pedido.fim)
    return Disponibilidade(
        sala=sala,
        livre=not conflitos,
        conflitos=[ConflitoOut(id=r.id, inicio=r.inicio, fim=r.fim, responsavel=r.responsavel) for r in conflitos],
    )


def _exigir_elicitation_form(ctx: Context) -> None:
    """Sem a capability de elicitation em form mode no _meta deste request, nao ha pergunta: -32021."""
    capabilities = ctx.client_capabilities
    elicitation = capabilities.elicitation if capabilities is not None else None
    # `elicitation: {}` sem modos e a forma anterior aos modos e vale como form.
    if elicitation is not None and (elicitation.form is not None or elicitation.url is None):
        return
    raise MCPError(
        code=MISSING_REQUIRED_CLIENT_CAPABILITY,
        message=f"Client did not declare the form elicitation capability required by {CHAVE_DA_ESCOLHA!r}",
        data={"requiredCapabilities": {"elicitation": {"form": {}}}},
    )


def _pergunta(estado: dict[str, Any]) -> InputRequiredResult:
    """Termina a resposta pedindo a escolha. O SDK sela `requestState` (AES-GCM) antes de ir para o fio."""
    schema = render_elicitation_schema(modelo_escolha(estado["alternativas"]))
    pedido = ElicitRequest(params=ElicitRequestFormParams(message=PERGUNTA, requested_schema=schema))
    return InputRequiredResult(input_requests={CHAVE_DA_ESCOLHA: pedido}, request_state=json.dumps(estado))


def _confirmar(sala: str, inicio: str, fim: str, responsavel: str) -> ReservaOut:
    pedido = _validar(sala, inicio, fim)
    if central.conflitos(sala, pedido.inicio, pedido.fim):
        raise ToolError(f"Sala ocupada no intervalo: {sala}")
    reserva = central.reservar(sala, inicio, fim, responsavel)
    return ReservaOut(
        reserva=reserva.id,
        reservado=True,
        sala=reserva.sala,
        inicio=reserva.inicio,
        fim=reserva.fim,
        responsavel=reserva.responsavel,
        politica=central.politica,
    )


def _retomar(ctx: Context) -> ReservaOut | InputRequiredResult:
    """Retry do MRTR: o pedido e as alternativas vem do requestState, nao da memoria nem dos argumentos.

    O boundary do SDK ja rejeitou com -32602 um estado adulterado, expirado ou
    apresentado com argumentos diferentes dos selados; aqui o texto e o que
    este servidor emitiu.
    """
    try:
        estado = json.loads(ctx.request_state or "")
        pedido, alternativas = estado["pedido"], estado["alternativas"]
    except (ValueError, KeyError, TypeError):
        raise MCPError(code=INVALID_PARAMS, message="Invalid or expired requestState") from None

    resposta = (ctx.input_responses or {}).get(CHAVE_DA_ESCOLHA)
    if resposta is None:
        return _pergunta(estado)
    acao = getattr(resposta, "action", None)
    if acao == "decline":
        return ReservaOut(reservado=False, motivo="recusado")
    if acao == "cancel":
        return ReservaOut(reservado=False, motivo="cancelado")
    escolhida = (getattr(resposta, "content", None) or {}).get("sala")
    if escolhida not in alternativas:
        raise ToolError(f"Escolha fora das alternativas oferecidas: {escolhida}")
    return _confirmar(escolhida, pedido["inicio"], pedido["fim"], pedido["responsavel"])


@mcp.tool(description="Reserva uma sala. Se o intervalo estiver ocupado, pergunta qual alternativa usar.")
def reservar_sala(sala: str, inicio: str, fim: str, responsavel: str, ctx: Context) -> ReservaOut | InputRequiredResult:
    if ctx.request_state is not None:
        return _retomar(ctx)

    pedido = _validar(sala, inicio, fim)
    if not central.conflitos(sala, pedido.inicio, pedido.fim):
        return _confirmar(sala, inicio, fim, responsavel)

    alternativas = central.alternativas(pedido)
    if not alternativas:
        raise ToolError(ERRO_SEM_ALTERNATIVA)
    _exigir_elicitation_form(ctx)
    # Tudo que o retry precisa viaja aqui; o servidor nao guarda nada entre as rodadas.
    estado = {
        "v": 1,
        "pedido": {"sala": sala, "inicio": inicio, "fim": fim, "responsavel": responsavel},
        "alternativas": alternativas,
    }
    return _pergunta(estado)


@mcp.resource("politica://uso", name="politica-de-uso", mime_type="text/markdown")
def politica_de_uso() -> str:
    return central.texto_politica


def main() -> None:
    app = mcp.streamable_http_app(json_response=True, stateless_http=True, host=HOST)
    print(f"[mcp] central-de-salas em http://{HOST}:{PORTA}/mcp", file=sys.stderr, flush=True)
    # O log fica por fora, para registrar tambem o que a guarda de versao recusa.
    uvicorn.run(LogDeRequests(GuardaDeVersao(app)), host=HOST, port=PORTA, log_level="warning")


if __name__ == "__main__":
    main()
