import pytest

from pedido import PedidoInvalido, ler_escolha, ler_pedido
from traceparent import ler_traceparent

PEDIDO = "reservar sala=sala-garagem inicio=2026-11-03T14:00:00-03:00 fim=2026-11-03T15:00:00-03:00 responsavel=Marty"


def test_pedido_em_formato_fixo_vira_argumentos():
    assert ler_pedido(PEDIDO) == {
        "sala": "sala-garagem",
        "inicio": "2026-11-03T14:00:00-03:00",
        "fim": "2026-11-03T15:00:00-03:00",
        "responsavel": "Marty",
    }


@pytest.mark.parametrize("texto", ["", "reserve sala=x", "reservar sala=x inicio=a fim=b", "oi"])
def test_pedido_fora_do_formato_e_recusado(texto):
    with pytest.raises(PedidoInvalido):
        ler_pedido(texto)


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [("escolha=sala-fusca", "sala-fusca"), ("escolha=recusar", "recusar"), ("sala-fusca", None), ("escolha=", None)],
)
def test_escolha(texto, esperado):
    assert ler_escolha(texto) == esperado


def test_traceparent_mantem_trace_id_e_troca_span_id():
    original = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    trace = ler_traceparent(original)
    assert trace is not None
    novo = trace.novo_traceparent()
    assert novo.split("-")[1] == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert novo.split("-")[2] != "00f067aa0ba902b7"
    assert ler_traceparent(novo) is not None


@pytest.mark.parametrize("valor", [None, "", "lixo", "00-" + "0" * 32 + "-00f067aa0ba902b7-01"])
def test_traceparent_invalido_e_ignorado(valor):
    assert ler_traceparent(valor) is None
