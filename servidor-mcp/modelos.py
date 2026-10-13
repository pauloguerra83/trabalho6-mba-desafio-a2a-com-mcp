"""Modelos de entrada e saida das tools, espelhando os outputSchema de exemplos/wire/01."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, create_model


class SalaOut(BaseModel):
    id: str
    nome: str
    capacidade: int
    recursos: list[str]


class ListaDeSalas(BaseModel):
    salas: list[SalaOut]


class ConflitoOut(BaseModel):
    id: str
    inicio: str
    fim: str
    responsavel: str


class Disponibilidade(BaseModel):
    sala: str
    livre: bool
    conflitos: list[ConflitoOut]


class ReservaOut(BaseModel):
    reserva: str | None = None
    reservado: bool = True
    sala: str | None = None
    inicio: str | None = None
    fim: str | None = None
    responsavel: str | None = None
    politica: str | None = None
    motivo: str | None = None


class EscolhaDeSala(BaseModel):
    """Forma base da resposta da elicitation; o enum real e montado por pedido."""

    sala: str = Field(title="Sala", description="Sala alternativa escolhida")


def modelo_escolha(alternativas: list[str]) -> type[EscolhaDeSala]:
    """Schema plano com `sala` restrita as alternativas, na ordem calculada pelo dominio.

    O nome e os campos sao fixos para que a pergunta renderize igual em todas as
    rodadas: o SDK so aceita a resposta se o digest da pergunta nao mudar.
    """
    return create_model(
        "EscolhaDeSala",
        __base__=EscolhaDeSala,
        sala=(Literal[tuple(alternativas)], Field(title="Sala", description="Sala alternativa escolhida")),
    )
