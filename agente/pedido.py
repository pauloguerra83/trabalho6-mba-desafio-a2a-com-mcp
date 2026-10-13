"""Interpretacao por regra dos textos em formato fixo. Nenhum LLM: o mesmo texto da sempre o mesmo resultado."""

from __future__ import annotations

import re

CAMPOS_DO_PEDIDO = ("sala", "inicio", "fim", "responsavel")
FORMATO_DO_PEDIDO = "reservar sala=<id> inicio=<iso8601> fim=<iso8601> responsavel=<nome>"
RECUSAR = "recusar"

_PAR = re.compile(r"(\w+)=(\S+)")


class PedidoInvalido(ValueError):
    pass


def ler_pedido(texto: str) -> dict[str, str]:
    """`reservar sala=<id> inicio=<iso8601> fim=<iso8601> responsavel=<nome>` vira os argumentos da tool."""
    partes = texto.strip().split(maxsplit=1)
    if not partes or partes[0] != "reservar":
        raise PedidoInvalido(f"Pedido invalido: use {FORMATO_DO_PEDIDO}")
    corpo = partes[1] if len(partes) > 1 else ""
    pares = dict(_PAR.findall(corpo))
    faltando = [c for c in CAMPOS_DO_PEDIDO if c not in pares]
    if faltando:
        raise PedidoInvalido(f"Pedido invalido: faltou {', '.join(faltando)}; use {FORMATO_DO_PEDIDO}")
    return {c: pares[c] for c in CAMPOS_DO_PEDIDO}


def ler_escolha(texto: str) -> str | None:
    """`escolha=<valor>` devolve o valor; qualquer outro texto devolve None."""
    achado = re.fullmatch(r"\s*escolha=(\S+)\s*", texto)
    return achado.group(1) if achado else None
