import asyncio

import pytest

import agente
from host_mcp import Concluido, McpIndisponivel, Pausado
from tarefas import COMPLETED, INPUT_REQUIRED, WORKING

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
