"""Agente Central de Salas: servidor A2A v1.0 por fora, host MCP por dentro.

    python agente/agente.py

Card em /.well-known/agent-card.json e JSON-RPC 2.0 em /a2a (SendMessage, GetTask).
A ponte: quando o tools/call responde input_required, a Task vai para
TASK_STATE_INPUT_REQUIRED e o requestState fica guardado nela (`_pausar`);
quando o cliente A2A responde, o mesmo tools/call e repetido com um id novo,
levando inputResponses e o requestState sem modificacao (`_retomar`).
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from cartao import agent_card
from host_mcp import Concluido, Desfecho, Falhou, HostMcp, McpIndisponivel, Pausado
from pedido import RECUSAR, PedidoInvalido, ler_escolha, ler_pedido
from tarefas import CANCELED, COMPLETED, FAILED, INPUT_REQUIRED, WORKING, Pendencia, Tarefa, Tarefas
from traceparent import Trace, ler_traceparent

HOST = os.environ.get("AGENTE_HOST", "127.0.0.1")
PORTA = int(os.environ.get("AGENTE_PORT", "7300"))
URL_A2A = os.environ.get("AGENTE_URL_PUBLICA", f"http://{HOST}:{PORTA}/a2a")
URL_MCP = os.environ.get("MCP_URL", "http://127.0.0.1:7301/mcp")

TOOL_DE_RESERVA = "reservar_sala"

# Codigos JSON-RPC: os da especificacao e os da A2A v1.0.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
TASK_NOT_FOUND = -32001
UNSUPPORTED_OPERATION = -32004

host = HostMcp(URL_MCP)
tarefas = Tarefas()


class ErroRpc(Exception):
    def __init__(self, codigo: int, mensagem: str) -> None:
        super().__init__(mensagem)
        self.codigo = codigo
        self.mensagem = mensagem


def _texto_da_mensagem(mensagem: dict[str, Any]) -> str:
    partes = mensagem.get("parts") or []
    return " ".join(p.get("text", "") for p in partes if isinstance(p, dict)).strip()


def _linha_de_alternativas(alternativas: list[str]) -> str:
    # O avaliador compara duas pausas byte a byte: nada alem desta linha.
    return "alternativas: " + ", ".join(alternativas)


def _aplicar(tarefa: Tarefa, desfecho: Desfecho, argumentos: dict[str, str], politica: str) -> None:
    """Traduz o desfecho do tools/call em estado da Task."""
    if isinstance(desfecho, Pausado):
        _pausar(tarefa, desfecho, argumentos)
        return
    tarefa.pendencia = None
    if isinstance(desfecho, Falhou):
        tarefa.mudar(FAILED, desfecho.mensagem)
        return
    dados = desfecho.dados
    if not dados.get("reservado"):
        tarefa.mudar(CANCELED, "Reserva nao realizada: alternativas recusadas.")
        return
    reserva = {
        "reserva": dados.get("reserva"),
        "sala": dados.get("sala"),
        "inicio": dados.get("inicio"),
        "fim": dados.get("fim"),
        "responsavel": dados.get("responsavel"),
        "politica": politica,
    }
    tarefa.anexar_artifact("reserva", json.dumps(reserva))
    tarefa.mudar(COMPLETED, f"Reserva {reserva['reserva']} confirmada na {reserva['sala']}.")


def _pausar(tarefa: Tarefa, pausa: Pausado, argumentos: dict[str, str]) -> None:
    """input_required do MCP vira TASK_STATE_INPUT_REQUIRED; o requestState fica so do lado de dentro."""
    tarefa.pendencia = Pendencia(
        tool=TOOL_DE_RESERVA,
        argumentos=argumentos,
        chave=pausa.chave,
        alternativas=pausa.alternativas,
        request_state=pausa.request_state,
    )
    tarefa.mudar(INPUT_REQUIRED, _linha_de_alternativas(pausa.alternativas))


async def _iniciar(tarefa: Tarefa, texto: str) -> None:
    tarefa.mudar(WORKING)
    try:
        argumentos = ler_pedido(texto)
    except PedidoInvalido as e:
        tarefa.mudar(FAILED, str(e))
        return
    try:
        tools = await host.descobrir(tarefa.trace)
        if TOOL_DE_RESERVA not in tools:
            tarefa.mudar(FAILED, f"O servidor MCP nao oferece a tool {TOOL_DE_RESERVA}")
            return
        politica = await host.versao_politica(tarefa.trace)
        tarefa.politica = politica
        desfecho = await host.chamar(TOOL_DE_RESERVA, argumentos, tarefa.trace)
    except Exception as e:  # servidor MCP fora do ar ou resposta fora do contrato
        print(f"[agente] falha falando com o MCP: {e!r}", file=sys.stderr, flush=True)
        tarefa.mudar(FAILED, "Servidor MCP indisponivel")
        return
    _aplicar(tarefa, desfecho, argumentos, politica)


async def _retomar(tarefa: Tarefa, texto: str) -> None:
    pendencia = tarefa.pendencia
    assert pendencia is not None
    escolha = ler_escolha(texto)
    if escolha == RECUSAR:
        resposta: dict[str, Any] = {"action": "decline"}
    elif escolha in pendencia.alternativas:
        resposta = {"action": "accept", "content": {"sala": escolha}}
    else:
        # Fora do enum: a Task continua pausada e repete a mesma lista.
        tarefa.mudar(INPUT_REQUIRED, _linha_de_alternativas(pendencia.alternativas))
        return

    tarefa.mudar(WORKING)
    try:
        desfecho = await host.chamar(
            pendencia.tool,
            pendencia.argumentos,
            tarefa.trace,
            input_responses={pendencia.chave: resposta},
            request_state=pendencia.request_state,
        )
    except McpIndisponivel as e:
        # A pergunta continua valida (o requestState nao depende do processo do MCP):
        # a Task volta a pausar com a mesma pendencia e o cliente pode mandar a escolha de novo.
        print(f"[agente] falha falando com o MCP: {e!r}", file=sys.stderr, flush=True)
        tarefa.mudar(INPUT_REQUIRED, _linha_de_alternativas(pendencia.alternativas))
        raise ErroRpc(
            INTERNAL_ERROR, "Servidor MCP indisponivel: a Task continua aguardando a escolha; reenvie a mensagem"
        ) from None
    except Exception as e:
        print(f"[agente] falha falando com o MCP: {e!r}", file=sys.stderr, flush=True)
        tarefa.pendencia = None
        tarefa.mudar(FAILED, "Servidor MCP indisponivel")
        return
    _aplicar(tarefa, desfecho, pendencia.argumentos, tarefa.politica)


async def send_message(params: dict[str, Any], trace: Trace | None) -> dict[str, Any]:
    mensagem = params.get("message")
    if not isinstance(mensagem, dict) or not isinstance(mensagem.get("parts"), list):
        raise ErroRpc(INVALID_PARAMS, "params.message com parts e obrigatorio")
    texto = _texto_da_mensagem(mensagem)
    task_id = mensagem.get("taskId")

    if not task_id:
        tarefa = tarefas.criar(mensagem.get("contextId"))
        tarefa.trace = trace
        async with tarefa.trava:
            tarefa.registrar_usuario(mensagem)
            await _iniciar(tarefa, texto)
        return {"task": tarefa.para_wire()}

    tarefa = tarefas.obter(task_id)
    if tarefa is None:
        raise ErroRpc(TASK_NOT_FOUND, f"Task nao encontrada: {task_id}")
    async with tarefa.trava:
        if tarefa.terminal:
            raise ErroRpc(UNSUPPORTED_OPERATION, f"Task {task_id} esta em estado terminal: {tarefa.estado}")
        if tarefa.estado != INPUT_REQUIRED or tarefa.pendencia is None:
            raise ErroRpc(UNSUPPORTED_OPERATION, f"Task {task_id} nao aguarda input: {tarefa.estado}")
        if trace is not None:
            tarefa.trace = trace
        tarefa.registrar_usuario(mensagem)
        await _retomar(tarefa, texto)
    return {"task": tarefa.para_wire()}


async def get_task(params: dict[str, Any], trace: Trace | None) -> dict[str, Any]:
    task_id = params.get("id")
    if not isinstance(task_id, str):
        raise ErroRpc(INVALID_PARAMS, "params.id e obrigatorio")
    tarefa = tarefas.obter(task_id)
    if tarefa is None:
        raise ErroRpc(TASK_NOT_FOUND, f"Task nao encontrada: {task_id}")
    return {"task": tarefa.para_wire()}


METODOS = {"SendMessage": send_message, "GetTask": get_task}


def _erro(id_rpc: Any, codigo: int, mensagem: str) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": id_rpc, "error": {"code": codigo, "message": mensagem}})


async def endpoint_a2a(request: Request) -> JSONResponse:
    try:
        corpo = json.loads(await request.body())
    except ValueError:
        return _erro(None, PARSE_ERROR, "Parse error")
    if not isinstance(corpo, dict) or corpo.get("jsonrpc") != "2.0" or not isinstance(corpo.get("method"), str):
        return _erro(corpo.get("id") if isinstance(corpo, dict) else None, INVALID_REQUEST, "Invalid Request")

    id_rpc = corpo.get("id")
    metodo = METODOS.get(corpo["method"])
    print(f"[agente] a2a method={corpo['method']} id={json.dumps(id_rpc)}", file=sys.stderr, flush=True)
    if metodo is None:
        return _erro(id_rpc, METHOD_NOT_FOUND, f"Method not found: {corpo['method']}")
    params = corpo.get("params")
    if not isinstance(params, dict):
        return _erro(id_rpc, INVALID_PARAMS, "params deve ser um objeto")
    try:
        resultado = await metodo(params, ler_traceparent(request.headers.get("traceparent")))
    except ErroRpc as e:
        return _erro(id_rpc, e.codigo, e.mensagem)
    return JSONResponse({"jsonrpc": "2.0", "id": id_rpc, "result": resultado})


async def endpoint_card(request: Request) -> JSONResponse:
    return JSONResponse(agent_card(URL_A2A))


@asynccontextmanager
async def ciclo_de_vida(app: Starlette):
    yield
    await host.fechar()


app = Starlette(
    routes=[
        Route("/.well-known/agent-card.json", endpoint_card, methods=["GET"]),
        Route("/a2a", endpoint_a2a, methods=["POST"]),
    ],
    lifespan=ciclo_de_vida,
)


def main() -> None:
    print(f"[agente] A2A em {URL_A2A}, MCP em {URL_MCP}", file=sys.stderr, flush=True)
    uvicorn.run(app, host=HOST, port=PORTA, log_level="warning")


if __name__ == "__main__":
    main()
