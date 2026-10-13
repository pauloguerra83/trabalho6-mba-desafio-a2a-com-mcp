import importlib
import json
import secrets
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.shared.exceptions import MCPError
from mcp_types import ClientCapabilities, ElicitationCapability, FormElicitationCapability, InputRequiredResult

# O texto que o servidor sela no requestState; o boundary do SDK ja o teria aberto antes do handler.
ESTADO = json.dumps(
    {
        "v": 1,
        "pedido": {"sala": "sala-garagem", "inicio": "2026-11-03T14:00:00-03:00", "fim": "2026-11-03T15:00:00-03:00", "responsavel": "Marty"},
        "alternativas": ["sala-fusca", "sala-mirante"],
    }
)


@pytest.fixture(scope="module")
def servidor():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("REQUEST_STATE_SECRET", secrets.token_hex(32))
        yield importlib.import_module("servidor")


def retry(capabilities, respostas=None):
    return SimpleNamespace(request_state=ESTADO, input_responses=respostas, client_capabilities=capabilities)


def test_retry_sem_resposta_de_cliente_sem_elicitation_recebe_32021(servidor):
    with pytest.raises(MCPError) as erro:
        servidor._retomar(retry(ClientCapabilities()))
    assert erro.value.error.code == -32021


def test_retry_sem_resposta_de_cliente_com_form_recebe_a_pergunta_de_novo(servidor):
    form = ClientCapabilities(elicitation=ElicitationCapability(form=FormElicitationCapability()))
    resultado = servidor._retomar(retry(form))
    assert isinstance(resultado, InputRequiredResult)
    assert list(resultado.input_requests) == [servidor.CHAVE_DA_ESCOLHA]


def test_chave_da_escolha_e_a_mesma_do_retry_de_exemplo(servidor):
    exemplo = Path(__file__).resolve().parents[1] / "exemplos" / "wire" / "04-tools-call-retry.json"
    corpo = json.loads(exemplo.read_text(encoding="utf-8"))["request"]["body"]
    assert list(corpo["params"]["inputResponses"]) == [servidor.CHAVE_DA_ESCOLHA]
