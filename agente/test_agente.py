import asyncio
import json

import pytest
from starlette.requests import Request

import agente
from host_mcp import Concluido, McpIndisponivel, Pausado
from tarefas import CANCELED, COMPLETED, FAILED, INPUT_REQUIRED, WORKING

ALTERNATIVAS = ["sala-fusca", "sala-mirante"]
PEDIDO = {"sala": "sala-garagem", "inicio": "2026-11-03T14:00:00-03:00", "fim": "2026-11-03T15:00:00-03:00", "responsavel": "Marty"}


def tarefa_pausada():
    tarefa = agente.tarefas.criar(None)
    tarefa.mudar(WORKING)
    tarefa.politica = "2026-11-01"
    agente._pausar(tarefa, Pausado("__main__:escolha_de_sala", ALTERNATIVAS, "v1.estado-selado"), PEDIDO)
    return tarefa


def continuar(tarefa, texto):
    mensagem = {"messageId": "m-1", "role": "ROLE_USER", "parts": [{"text": texto}], "taskId": tarefa.id}
    return asyncio.run(agente.send_message({"message": mensagem}, None))


def texto_do_status(tarefa):
    return tarefa.para_wire()["status"]["message"]["parts"][0]["text"]


def test_continuacao_com_mcp_fora_mantem_a_task_pausada_e_aceita_a_escolha_depois(monkeypatch):
    async def mcp_fora(*args, **kwargs):
        raise McpIndisponivel("Connection closed")

    monkeypatch.setattr(agente.host, "chamar", mcp_fora)
    tarefa = tarefa_pausada()
    with pytest.raises(agente.ErroRpc) as erro:
        continuar(tarefa, "escolha=sala-fusca")
    assert erro.value.codigo == agente.INTERNAL_ERROR
    assert tarefa.estado == INPUT_REQUIRED
    assert tarefa.pendencia is not None and tarefa.pendencia.request_state == "v1.estado-selado"
    assert texto_do_status(tarefa) == "alternativas: sala-fusca, sala-mirante"

    async def mcp_de_volta(*args, **kwargs):
        return Concluido({**PEDIDO, "reserva": "res-0003", "reservado": True, "sala": "sala-fusca"})

    monkeypatch.setattr(agente.host, "chamar", mcp_de_volta)
    continuar(tarefa, "escolha=sala-fusca")
    assert tarefa.estado == COMPLETED
    assert tarefa.artifacts[0]["name"] == "reserva"


def test_recusa_enviada_pelo_agente_termina_canceled(monkeypatch):
    async def mcp(*args, **kwargs):
        assert kwargs["input_responses"] == {"__main__:escolha_de_sala": {"action": "decline"}}
        return Concluido({"reservado": False, "motivo": "recusado"})

    monkeypatch.setattr(agente.host, "chamar", mcp)
    tarefa = tarefa_pausada()
    continuar(tarefa, "escolha=recusar")
    assert tarefa.estado == CANCELED


def test_complete_sem_reserva_e_sem_recusa_termina_failed():
    tarefa = agente.tarefas.criar(None)
    tarefa.mudar(WORKING)
    agente._aplicar(tarefa, Concluido({"reservado": False}), PEDIDO, "2026-11-01")
    assert tarefa.estado == FAILED


def requisitar(corpo):
    async def receive():
        return {"type": "http.request", "body": json.dumps(corpo).encode(), "more_body": False}

    scope = {"type": "http", "method": "POST", "path": "/a2a", "headers": [(b"content-type", b"application/json")]}
    return asyncio.run(agente.endpoint_a2a(Request(scope, receive)))


def test_notificacao_jsonrpc_nao_recebe_resposta_e_nao_executa():
    antes = len(agente.tarefas._por_id)
    mensagem = {"messageId": "m-2", "role": "ROLE_USER", "parts": [{"text": "reservar sala=sala-porao"}]}
    resposta = requisitar({"jsonrpc": "2.0", "method": "SendMessage", "params": {"message": mensagem}})
    assert (resposta.status_code, resposta.body) == (204, b"")
    assert len(agente.tarefas._por_id) == antes


def test_erro_inesperado_vira_erro_jsonrpc_e_nao_http_500():
    mensagem = {"messageId": "m-3", "role": "ROLE_USER", "parts": [{"text": "escolha=recusar"}], "taskId": ["nao-hashable"]}
    resposta = requisitar({"jsonrpc": "2.0", "id": 7, "method": "SendMessage", "params": {"message": mensagem}})
    assert resposta.status_code == 200
    assert json.loads(resposta.body) == {"jsonrpc": "2.0", "id": 7, "error": {"code": -32603, "message": "Internal error"}}


def test_text_que_nao_e_string_e_ignorado():
    assert agente._texto_da_mensagem({"parts": [{"text": 123}, {"text": "escolha=recusar"}]}) == "escolha=recusar"
