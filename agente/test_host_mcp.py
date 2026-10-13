import asyncio
from types import SimpleNamespace

import pytest
from mcp.shared.exceptions import MCPError
from mcp_types import CONNECTION_CLOSED, INVALID_PARAMS

import host_mcp
from host_mcp import Falhou, HostMcp, McpIndisponivel


class ClienteFalso:
    """Imita o Client do SDK e registra em que task o contexto foi aberto e fechado."""

    criados: list["ClienteFalso"] = []
    falhas: list[Exception] = []  # levantadas, uma por vez, nas proximas operacoes

    def __init__(self, *args, **kwargs):
        self.entrou = self.saiu = None
        self.session = SimpleNamespace(call_tool=self._call_tool)
        ClienteFalso.criados.append(self)

    async def __aenter__(self):
        await asyncio.sleep(0)  # cede a vez: expoe corrida na criacao do cliente
        self.entrou = asyncio.current_task()
        return self

    async def __aexit__(self, *exc):
        self.saiu = asyncio.current_task()
        return False

    def _talvez_falhar(self):
        if ClienteFalso.falhas:
            raise ClienteFalso.falhas.pop(0)

    async def list_tools(self, meta=None):
        self._talvez_falhar()
        return SimpleNamespace(tools=[SimpleNamespace(name="reservar_sala")])

    async def _call_tool(self, *args, **kwargs):
        self._talvez_falhar()
        raise AssertionError("os testes so chamam call_tool para falhar")


@pytest.fixture(autouse=True)
def cliente_falso(monkeypatch):
    ClienteFalso.criados = []
    ClienteFalso.falhas = []
    monkeypatch.setattr(host_mcp, "_ClienteMcp", ClienteFalso)


def test_queda_do_transporte_descarta_o_cliente_e_o_request_seguinte_reconecta():
    async def cenario():
        host = HostMcp("http://mcp.invalid/mcp")
        assert await host.descobrir(None) == {"reservar_sala"}
        ClienteFalso.falhas.append(MCPError(code=CONNECTION_CLOSED, message="Connection closed"))
        with pytest.raises(McpIndisponivel):
            await host.descobrir(None)
        assert await host.descobrir(None) == {"reservar_sala"}
        await host.fechar()

    asyncio.run(cenario())
    velho, novo = ClienteFalso.criados
    # o anyio exige fechar o contexto na mesma task em que ele foi aberto
    assert velho.saiu is not None and velho.saiu is velho.entrou
    assert novo.saiu is not None and novo.saiu is novo.entrou


def test_requests_simultaneos_criam_um_cliente_so():
    async def cenario():
        host = HostMcp("http://mcp.invalid/mcp")
        await asyncio.gather(*(host.descobrir(None) for _ in range(5)))
        await host.fechar()

    asyncio.run(cenario())
    assert len(ClienteFalso.criados) == 1


def test_erro_de_protocolo_vira_falhou_e_mantem_o_cliente():
    async def cenario():
        host = HostMcp("http://mcp.invalid/mcp")
        ClienteFalso.falhas.append(MCPError(code=INVALID_PARAMS, message="Invalid or expired requestState"))
        desfecho = await host.chamar("reservar_sala", {}, None, request_state="v1.x")
        await host.descobrir(None)
        await host.fechar()
        return desfecho

    assert asyncio.run(cenario()) == Falhou("Invalid or expired requestState")
    assert len(ClienteFalso.criados) == 1


def test_falha_inesperada_tambem_descarta_o_cliente():
    async def cenario():
        host = HostMcp("http://mcp.invalid/mcp")
        ClienteFalso.falhas.append(OSError("conexao recusada"))
        with pytest.raises(OSError):
            await host.descobrir(None)
        await host.descobrir(None)
        await host.fechar()

    asyncio.run(cenario())
    assert len(ClienteFalso.criados) == 2
