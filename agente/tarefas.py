"""Tasks A2A em memoria: identidade, estado, historico e produto.

O que liga uma Task pausada ao servidor MCP (`Pendencia`, com o requestState)
fica fora da serializacao: `para_wire` so emite os campos publicos da Task.
"""

from __future__ import annotations

import asyncio
import secrets
import sys
from dataclasses import dataclass, field
from typing import Any

from traceparent import Trace

SUBMITTED = "TASK_STATE_SUBMITTED"
WORKING = "TASK_STATE_WORKING"
INPUT_REQUIRED = "TASK_STATE_INPUT_REQUIRED"
COMPLETED = "TASK_STATE_COMPLETED"
CANCELED = "TASK_STATE_CANCELED"
FAILED = "TASK_STATE_FAILED"
TERMINAIS = frozenset({COMPLETED, CANCELED, FAILED})


def novo_id(prefixo: str) -> str:
    return f"{prefixo}-{secrets.token_hex(6)}"


@dataclass
class Pendencia:
    """O que o agente precisa para repetir o tools/call original na retomada."""

    tool: str
    argumentos: dict[str, str]
    chave: str
    alternativas: list[str]
    request_state: str | None


@dataclass
class Tarefa:
    id: str
    context_id: str
    estado: str = SUBMITTED
    mensagem: dict[str, Any] | None = None
    historico: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    pendencia: Pendencia | None = None
    trace: Trace | None = None
    politica: str = ""
    trava: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def terminal(self) -> bool:
        return self.estado in TERMINAIS

    def mudar(self, estado: str, texto: str | None = None) -> None:
        if self.terminal:
            raise RuntimeError(f"Task {self.id} ja esta em estado terminal {self.estado}")
        self.estado = estado
        if texto is not None:
            self.mensagem = {
                "messageId": novo_id("msg"),
                "role": "ROLE_AGENT",
                "parts": [{"text": texto}],
                "taskId": self.id,
                "contextId": self.context_id,
            }
            self.historico.append(self.mensagem)
        print(f"[agente] task={self.id} estado={estado}", file=sys.stderr, flush=True)

    def registrar_usuario(self, mensagem: dict[str, Any]) -> None:
        self.historico.append(mensagem)

    def anexar_artifact(self, nome: str, texto: str) -> None:
        self.artifacts.append({"artifactId": novo_id("art"), "name": nome, "parts": [{"text": texto}]})

    def para_wire(self) -> dict[str, Any]:
        status: dict[str, Any] = {"state": self.estado}
        if self.mensagem is not None:
            status["message"] = self.mensagem
        return {
            "id": self.id,
            "contextId": self.context_id,
            "status": status,
            "history": list(self.historico),
            "artifacts": list(self.artifacts),
        }


class Tarefas:
    def __init__(self) -> None:
        self._por_id: dict[str, Tarefa] = {}

    def criar(self, context_id: str | None) -> Tarefa:
        tarefa = Tarefa(id=novo_id("task"), context_id=context_id or novo_id("ctx"))
        self._por_id[tarefa.id] = tarefa
        print(f"[agente] task={tarefa.id} estado={tarefa.estado}", file=sys.stderr, flush=True)
        return tarefa

    def obter(self, task_id: str) -> Tarefa | None:
        return self._por_id.get(task_id)
