"""Agent Card na forma da A2A v1.0 (exemplos/wire/07-a2a-agent-card.json)."""

from __future__ import annotations

from typing import Any

EXEMPLO = (
    "reservar sala=sala-garagem inicio=2026-11-03T14:00:00-03:00 "
    "fim=2026-11-03T15:00:00-03:00 responsavel=Marty"
)


def agent_card(url_a2a: str) -> dict[str, Any]:
    return {
        "name": "Central de Salas",
        "description": "Reserva salas de reuniao da Hill Valley Tech.",
        "provider": {"organization": "Hill Valley Tech", "url": "https://hillvalley.example"},
        "version": "1.0.0",
        "supportedInterfaces": [
            {"url": url_a2a, "protocolBinding": "JSONRPC", "protocolVersion": "1.0"},
        ],
        "capabilities": {"streaming": False, "pushNotifications": False, "extendedAgentCard": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [
            {
                "id": "reservar-sala",
                "name": "Reservar sala",
                "description": "Reserva uma sala em um intervalo. Se houver conflito, pergunta qual alternativa usar.",
                "tags": ["salas", "agenda"],
                "inputModes": ["text/plain"],
                "outputModes": ["text/plain"],
                "examples": [EXEMPLO],
            }
        ],
    }
