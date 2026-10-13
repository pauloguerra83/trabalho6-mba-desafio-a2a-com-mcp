import pytest

from dominio import (
    ERRO_DURACAO,
    ERRO_INTERVALO,
    ERRO_JANELA,
    CentralDeSalas,
    ErroDeRegra,
)


def h(hora: str, dia: str = "2026-11-03") -> str:
    return f"{dia}T{hora}:00-03:00"


@pytest.fixture
def central() -> CentralDeSalas:
    return CentralDeSalas()


@pytest.mark.parametrize(
    ("sala", "inicio", "fim", "mensagem"),
    [
        ("sala-delorean", h("09:00"), h("10:00"), "Sala inexistente: sala-delorean"),
        ("sala-aquario", h("10:00"), h("09:00"), ERRO_INTERVALO),
        ("sala-aquario", h("10:00"), h("10:00"), ERRO_INTERVALO),
        ("sala-aquario", h("07:00"), h("08:00"), ERRO_JANELA),
        ("sala-aquario", h("19:30"), h("20:30"), ERRO_JANELA),
        ("sala-aquario", h("09:00"), h("12:00"), ERRO_DURACAO),
    ],
)
def test_regras_devolvem_a_mensagem_exata(central, sala, inicio, fim, mensagem):
    with pytest.raises(ErroDeRegra, match=f"^{mensagem}$"):
        central.validar(sala, inicio, fim)


def test_limites_da_janela_e_da_duracao_sao_permitidos(central):
    central.validar("sala-aquario", h("08:00"), h("10:00"))
    central.validar("sala-aquario", h("18:00"), h("20:00"))


def test_alternativas_por_capacidade_e_id(central):
    pedido = central.validar("sala-garagem", h("14:00"), h("15:00"))
    assert central.conflitos("sala-garagem", pedido.inicio, pedido.fim)
    assert central.alternativas(pedido) == ["sala-fusca", "sala-mirante"]


def test_alternativas_limitadas_a_tres_e_so_livres(central):
    pedido = central.validar("sala-aquario", h("09:00"), h("10:00"))
    assert central.alternativas(pedido) == ["sala-porao", "sala-fusca", "sala-garagem"]
    central.reservar("sala-porao", h("09:30"), h("10:30"), "Doc")
    assert central.alternativas(pedido) == ["sala-fusca", "sala-garagem", "sala-mirante"]


def test_maior_sala_ocupada_nao_tem_alternativa(central):
    central.reservar("sala-mirante", h("11:00"), h("12:00"), "Doc")
    pedido = central.validar("sala-mirante", h("11:00"), h("12:00"))
    assert central.alternativas(pedido) == []


def test_reservas_ganham_ids_sequenciais(central):
    assert central.reservar("sala-porao", h("09:00"), h("10:00"), "Doc").id == "res-0003"
    assert central.reservar("sala-porao", h("10:00"), h("11:00"), "Doc").id == "res-0004"


def test_versao_da_politica(central):
    assert central.politica == "2026-11-01"
