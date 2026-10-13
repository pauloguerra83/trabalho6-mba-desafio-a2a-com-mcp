"""Regras de sala: politica de uso, conflitos e alternativas. Toda decisao de dominio mora aqui."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

DADOS = Path(__file__).resolve().parents[1] / "dados"

FUSO = timezone(timedelta(hours=-3))
ABERTURA = time(8, 0)
FECHAMENTO = time(20, 0)
DURACAO_MAXIMA = timedelta(hours=2)
MAX_ALTERNATIVAS = 3

ERRO_JANELA = "Fora da janela de uso: a politica permite reservas entre 08:00 e 20:00"
ERRO_DURACAO = "Duracao acima do limite: a politica permite no maximo 2 horas"
ERRO_INTERVALO = "Intervalo invalido: fim deve ser posterior a inicio"
ERRO_SEM_ALTERNATIVA = "Sem alternativas disponiveis no intervalo"


class ErroDeRegra(Exception):
    """Violacao de regra de negocio; a mensagem e a fonte unica de verdade do enunciado."""


@dataclass(frozen=True)
class Sala:
    id: str
    nome: str
    capacidade: int
    recursos: tuple[str, ...]


@dataclass(frozen=True)
class Reserva:
    id: str
    sala: str
    inicio: str
    fim: str
    responsavel: str

    @property
    def intervalo(self) -> tuple[datetime, datetime]:
        return _parse(self.inicio), _parse(self.fim)


@dataclass(frozen=True)
class Pedido:
    sala: Sala
    inicio: datetime
    fim: datetime


def _parse(valor: str) -> datetime:
    try:
        instante = datetime.fromisoformat(valor)
    except (TypeError, ValueError):
        raise ErroDeRegra(f"Data invalida: {valor}") from None
    if instante.tzinfo is None:
        instante = instante.replace(tzinfo=FUSO)
    return instante


def _sobrepoe(a: tuple[datetime, datetime], b: tuple[datetime, datetime]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def versao_da_politica(texto: str) -> str:
    primeira = texto.splitlines()[0] if texto else ""
    achado = re.match(r"^versao:\s*(\S+)", primeira)
    return achado.group(1) if achado else ""


class CentralDeSalas:
    """Salas fixas e reservas em memoria do processo; nada sobrevive a um restart."""

    def __init__(self, dados: Path = DADOS) -> None:
        bruto_salas = json.loads((dados / "salas.json").read_text(encoding="utf-8"))
        self.salas: dict[str, Sala] = {
            s["id"]: Sala(s["id"], s["nome"], s["capacidade"], tuple(s["recursos"])) for s in bruto_salas
        }
        bruto_reservas = json.loads((dados / "reservas.json").read_text(encoding="utf-8"))
        self.reservas: list[Reserva] = [Reserva(**r) for r in bruto_reservas]
        self.texto_politica = (dados / "politica-de-uso.md").read_text(encoding="utf-8")
        self.politica = versao_da_politica(self.texto_politica)
        self._proximo = len(self.reservas) + 1

    def validar(self, sala: str, inicio: str, fim: str) -> Pedido:
        """Aplica as regras na ordem: sala, intervalo, janela, duracao."""
        encontrada = self.salas.get(sala)
        if encontrada is None:
            raise ErroDeRegra(f"Sala inexistente: {sala}")
        ini, fi = _parse(inicio), _parse(fim)
        if fi <= ini:
            raise ErroDeRegra(ERRO_INTERVALO)
        local_ini, local_fim = ini.astimezone(FUSO), fi.astimezone(FUSO)
        if (
            local_ini.date() != local_fim.date()
            or not ABERTURA <= local_ini.time() <= FECHAMENTO
            or not ABERTURA <= local_fim.time() <= FECHAMENTO
        ):
            raise ErroDeRegra(ERRO_JANELA)
        if fi - ini > DURACAO_MAXIMA:
            raise ErroDeRegra(ERRO_DURACAO)
        return Pedido(encontrada, ini, fi)

    def conflitos(self, sala: str, inicio: datetime, fim: datetime) -> list[Reserva]:
        return [r for r in self.reservas if r.sala == sala and _sobrepoe(r.intervalo, (inicio, fim))]

    def alternativas(self, pedido: Pedido) -> list[str]:
        """Salas livres no intervalo, com capacidade >= a pedida, por capacidade e id, no maximo tres."""
        candidatas = [
            s
            for s in self.salas.values()
            if s.id != pedido.sala.id
            and s.capacidade >= pedido.sala.capacidade
            and not self.conflitos(s.id, pedido.inicio, pedido.fim)
        ]
        candidatas.sort(key=lambda s: (s.capacidade, s.id))
        return [s.id for s in candidatas[:MAX_ALTERNATIVAS]]

    def reservar(self, sala: str, inicio: str, fim: str, responsavel: str) -> Reserva:
        reserva = Reserva(f"res-{self._proximo:04d}", sala, inicio, fim, responsavel)
        self._proximo += 1
        self.reservas.append(reserva)
        return reserva
