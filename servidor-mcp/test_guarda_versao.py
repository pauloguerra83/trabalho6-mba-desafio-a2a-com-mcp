import asyncio
import json

import pytest

from guarda_versao import GuardaDeVersao

META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientCapabilities": {"elicitation": {"form": {}}},
}


def chamar(corpo, cabecalhos, metodo="POST"):
    """Roda o middleware sobre um request: (chegou ao app, status, resposta JSON)."""
    chegou: list[dict] = []
    enviados: list[dict] = []

    async def app(scope, receive, send):
        chegou.append(scope)

    async def receive():
        bruto = corpo if isinstance(corpo, bytes) else json.dumps(corpo).encode()
        return {"type": "http.request", "body": bruto, "more_body": False}

    async def send(mensagem):
        enviados.append(mensagem)

    scope = {
        "type": "http",
        "method": metodo,
        "path": "/mcp",
        "headers": [(k.lower().encode(), v.encode()) for k, v in cabecalhos.items()],
    }
    asyncio.run(GuardaDeVersao(app)(scope, receive, send))
    if chegou:
        return True, None, None
    inicio = next(m for m in enviados if m["type"] == "http.response.start")
    resposta = b"".join(m.get("body", b"") for m in enviados if m["type"] == "http.response.body")
    return False, inicio["status"], json.loads(resposta)


def tools_list(meta=None):
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {} if meta is None else {"_meta": meta}}


def test_revisao_2026_segue_para_o_sdk():
    assert chamar(tools_list(META), {"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": "tools/list"})[0]


def test_get_segue_para_o_sdk():
    assert chamar(b"", {}, metodo="GET")[0]


@pytest.mark.parametrize(
    ("cabecalhos", "meta", "codigo"),
    [
        ({}, None, -32602),
        ({}, META, -32020),
        ({"MCP-Protocol-Version": "2025-11-25"}, None, -32602),
        ({"MCP-Protocol-Version": "2025-11-25", "Mcp-Method": "tools/list"}, META, -32020),
        (
            {"MCP-Protocol-Version": "2025-11-25", "Mcp-Method": "tools/list"},
            {**META, "io.modelcontextprotocol/protocolVersion": "2025-11-25"},
            -32022,
        ),
    ],
)
def test_caminho_legado_e_recusado_com_400(cabecalhos, meta, codigo):
    chegou, status, resposta = chamar(tools_list(meta), cabecalhos)
    assert not chegou
    assert status == 400
    assert resposta["id"] == 1
    assert resposta["error"]["code"] == codigo


def test_corpo_que_nao_e_json_recebe_parse_error():
    chegou, status, resposta = chamar(b"{nao e json", {})
    assert (chegou, status, resposta["error"]["code"]) == (False, 400, -32700)
