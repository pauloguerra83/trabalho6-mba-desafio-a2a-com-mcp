# A Ponte: um agente A2A com MCP por dentro

Entrega do desafio do MBA Engenharia de Software com IA (curso de MCP e A2A). O enunciado original está em [enunciado.md](enunciado.md).

São dois processos separados, que conversam por HTTP:

| Processo | Porta | Endpoints | Papel |
|---|---|---|---|
| [servidor-mcp/](servidor-mcp/) | 7301 | `POST /mcp` (Streamable HTTP, revisão `2026-07-28`, stateless) | Expõe as salas: `listar_salas`, `consultar_disponibilidade`, `reservar_sala` e o resource `politica://uso`. Toda regra de negócio fica aqui. |
| [agente/](agente/) | 7300 | `GET /.well-known/agent-card.json`, `POST /a2a` (A2A v1.0, JSON-RPC 2.0) | Servidor A2A por fora (`SendMessage`, `GetTask`) e host MCP por dentro. Traduz protocolo, não domínio, e não usa LLM. |

Stack: Python 3.12+, SDK oficial `mcp==2.3.0` (servidor e cliente), Starlette e uvicorn. As versões estão travadas em [pyproject.toml](pyproject.toml) e, com as dependências transitivas, em [requirements.lock](requirements.lock).

## Como rodar

Pré-requisito: Python 3.12 ou superior. Os comandos abaixo partem de um clone limpo, na raiz do repositório.

No Windows, o venv só tem `python.exe`, e o `python3` do PATH costuma ser o atalho da Microsoft Store. Lá, use `python` (com o venv ativo) ou `py -3.12` no lugar de `python3` em todos os comandos deste README.

**1. Ambiente e dependências** (uma vez só)

```bash
python3 -m venv .venv
source .venv/bin/activate                 # Windows (PowerShell): .venv\Scripts\Activate.ps1
pip install -r requirements.lock
```

**2. Segredo do `requestState`.** É a chave de integridade do estado do MRTR. Precisa ter pelo menos 32 bytes aleatórios e nunca vai para o código nem para o repositório. Gere uma chave sua e exporte no terminal do servidor MCP:

```bash
export REQUEST_STATE_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
# PowerShell: $env:REQUEST_STATE_SECRET = python -c "import secrets; print(secrets.token_hex(32))"
```

Para testar o retry depois de reiniciar o servidor MCP, use a mesma chave nas duas subidas: reinicie o processo no mesmo terminal, sem gerar outra chave.

**3. Subir os dois processos**, cada um em um terminal com o venv ativo. Suba o servidor MCP primeiro:

```bash
# terminal 1: servidor MCP (o log de cada request sai no stderr)
python3 servidor-mcp/servidor.py

# terminal 2: agente
python3 agente/agente.py
```

**4. Validador**, em um terceiro terminal:

```bash
python3 validador/validar.py --agente http://localhost:7300 --mcp http://localhost:7301
```

Reinicie os dois processos antes de cada execução do validador: as reservas criadas numa rodada mudam o resultado da seguinte. Os passos manuais 7 a 9 do fluxo do avaliador também pressupõem processos recém-iniciados.

**Testes unitários** (opcional): `python3 -m pytest`. O `pytest` já vem no `requirements.lock`.

**Variáveis opcionais.** Os padrões são os que o validador espera.

| Variável | Padrão |
|---|---|
| `MCP_HOST` / `MCP_PORT` | `127.0.0.1` / `7301` |
| `AGENTE_HOST` / `AGENTE_PORT` | `127.0.0.1` / `7300` |
| `MCP_URL` | `http://127.0.0.1:7301/mcp` |
| `AGENTE_URL_PUBLICA` | `http://127.0.0.1:7300/a2a` (URL anunciada no Agent Card) |

**Exemplo manual com `curl`:**

```bash
curl -s http://localhost:7300/.well-known/agent-card.json

python3 -c "import json; print(json.dumps(json.load(open('exemplos/wire/08-a2a-send-message.json'))['request']['body']))" \
  | curl -s -X POST http://localhost:7300/a2a -H 'Content-Type: application/json' \
         -H 'traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01' -d @-
# -> TASK_STATE_INPUT_REQUIRED com "alternativas: sala-fusca, sala-mirante"

curl -s -X POST http://localhost:7300/a2a -H 'Content-Type: application/json' -d '{
  "jsonrpc": "2.0", "id": 2, "method": "SendMessage",
  "params": {"message": {"messageId": "m2", "role": "ROLE_USER",
             "parts": [{"text": "escolha=sala-mirante"}], "taskId": "<id da task>"}}}'
# -> TASK_STATE_COMPLETED com o artifact "reserva"
```

**Passos 11 a 13 do avaliador, direto no servidor MCP.** A chave do `inputRequests` é `__main__:escolha_de_sala`, a mesma de `exemplos/wire/03` e `04`, então o retry do exemplo 04 funciona trocando só o `requestState`. Os comandos abaixo guardam a resposta do conflito em `conflito.json` e montam o retry a partir dela, com a chave e a primeira alternativa que o servidor devolveu:

```bash
CABECALHOS=(-H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream'
            -H 'MCP-Protocol-Version: 2026-07-28' -H 'Mcp-Method: tools/call' -H 'Mcp-Name: reservar_sala')

# conflito (wire 03): input_required com a elicitation e o requestState
python3 -c "import json; print(json.dumps(json.load(open('exemplos/wire/03-tools-call-conflito-input-required.json'))['request']['body']))" \
  | curl -s -X POST http://localhost:7301/mcp "${CABECALHOS[@]}" -d @- | tee conflito.json

# retry (wire 04) com o requestState recebido
#   passo 11: rode com ADULTERAR=1 na frente do python3 -> um caractere trocado, -32602
#   passo 12: reinicie o servidor MCP (mesmo REQUEST_STATE_SECRET) e rode sem ADULTERAR -> reserva concluída
python3 - <<'PY' | curl -s -X POST http://localhost:7301/mcp "${CABECALHOS[@]}" -d @-
import json, os
conflito = json.load(open("conflito.json"))["result"]
estado = conflito["requestState"]
if os.environ.get("ADULTERAR"):
    i = len(estado) // 2
    estado = estado[:i] + ("A" if estado[i] != "A" else "B") + estado[i + 1:]
chave, pedido = next(iter(conflito["inputRequests"].items()))
sala = pedido["params"]["requestedSchema"]["properties"]["sala"]
escolha = (sala.get("enum") or [sala.get("const")])[0]
corpo = json.load(open("exemplos/wire/04-tools-call-retry.json"))["request"]["body"]
corpo["params"]["requestState"] = estado
corpo["params"]["inputResponses"] = {chave: {"action": "accept", "content": {"sala": escolha}}}
print(json.dumps(corpo))
PY

# passo 13: o mesmo conflito sem declarar elicitation -> -32021 com HTTP 400
python3 -c "import json; c = json.load(open('exemplos/wire/03-tools-call-conflito-input-required.json'))['request']['body']; c['params']['_meta']['io.modelcontextprotocol/clientCapabilities'] = {}; print(json.dumps(c))" \
  | curl -s -i -X POST http://localhost:7301/mcp "${CABECALHOS[@]}" -d @-
```

## Onde a ponte acontece

1. **Entrada.** O pedido chega em `SendMessage` e o agente abre a Task. Antes do primeiro `tools/call`, ele descobre as tools com `tools/list` e lê a versão da política em `politica://uso`.
2. **Pedido de informação.** No servidor MCP, `reservar_sala` encontra o conflito e não chama o cliente (o transporte não tem canal de volta). Ela termina a resposta com um `InputRequiredResult`: uma elicitation em form mode com o `enum` de alternativas, mais o estado do pedido ([servidor-mcp/servidor.py:155](servidor-mcp/servidor.py#L155) e `_pergunta` em [servidor.py:102](servidor-mcp/servidor.py#L102)). O SDK sela esse estado antes de ele ir para o fio.
3. **O cliente vê o `input_required` cru.** O agente chama a tool pelo cliente do SDK com `allow_input_required=True` e sem nenhum callback de elicitation ([agente/host_mcp.py:215](agente/host_mcp.py#L215)). Assim o SDK não responde a pergunta sozinho: o `input_required` volta cru e vira o desfecho `Pausado` ([host_mcp.py:221-229](agente/host_mcp.py#L221-L229)).
4. **`input_required` → `TASK_STATE_INPUT_REQUIRED`.** Isso acontece em `_pausar` ([agente/agente.py:103](agente/agente.py#L103)). O `requestState`, a chave do `inputRequests`, as alternativas e os argumentos originais ficam guardados na `Pendencia` daquela Task. A Task responde exatamente `alternativas: <ids>`. A `Pendencia` nunca é serializada: `Tarefa.para_wire` só emite os campos públicos da Task ([agente/tarefas.py](agente/tarefas.py)).
5. **O `requestState` volta ao servidor.** Isso acontece em `_retomar` ([agente/agente.py:137](agente/agente.py#L137)). O `SendMessage` de continuação com `escolha=<id>` (ou `escolha=recusar`, que vira `decline`) repete o mesmo `tools/call` com os mesmos argumentos, `inputResponses` com a mesma chave e o `requestState` sem modificação ([agente.py:156-157](agente/agente.py#L156-L157)). O SDK emite um id JSON-RPC novo para esse retry.
6. **O servidor reconstrói o pedido a partir do próprio `requestState`** ([servidor.py:125](servidor-mcp/servidor.py#L125)) e conclui a reserva. A Task termina em `COMPLETED`, com o artifact `reserva`; em `CANCELED`, na recusa; ou em `FAILED`, num `isError`.

O `traceparent` recebido no header A2A é propagado em todos os requests MCP daquela Task. Vai no `_meta`, com o mesmo trace-id e um span-id novo por request ([agente/traceparent.py](agente/traceparent.py)). O stderr do servidor MCP registra `method`, `id`, `traceparent` e a presença do `requestState` em cada request ([servidor-mcp/log_requests.py](servidor-mcp/log_requests.py)). Nesse log aparecem o `tools/list` antes do primeiro `tools/call` e o par `tools/call` inicial / retry com ids diferentes.

## Decisões técnicas

**Proteção do `requestState`.** Uso o utilitário do SDK, `RequestStateSecurity(keys=[REQUEST_STATE_SECRET], ttl=600)`, instalado como `RequestStateBoundary` pelo `MCPServer` ([servidor.py:59](servidor-mcp/servidor.py#L59)). A cifra é AES-256-GCM, com a chave derivada por HKDF-SHA256 do segredo da variável de ambiente; sem a variável, ou com menos de 32 bytes, o processo não sobe. O envelope selado carrega:
- expiração;
- *audience* (o nome do servidor, `central-de-salas`);
- método e tool;
- um digest dos argumentos.

Por isso:
- um caractere trocado falha na autenticação do GCM e é rejeitado com `-32602`;
- um retry com argumentos diferentes dos selados também é rejeitado com `-32602`: o servidor rejeita o estado, que é um dos dois caminhos aceitos pelo enunciado;
- a mensagem de erro é fixa (`Invalid or expired requestState`), e o motivo real vai só para o log.

**Validade.** O `requestState` vale **10 minutos** (`ttl=600`), dentro da faixa de 5 a 30 minutos.

**Estado viaja no `requestState`, não no servidor.** O texto selado contém o pedido original (sala, início, fim, responsável) e as alternativas oferecidas. O servidor não guarda nada entre o `input_required` e o retry. Como a chave vem da env, um retry apresentado depois de reiniciar o servidor MCP continua válido e conclui a reserva.

**Por que o MRTR é conduzido no corpo da tool, e não por resolvers.** Primeiro implementei com os resolvers do SDK (`Resolve` + `Elicit`). Eles recalculam a pergunta em cada rodada e só aceitam a resposta se ela renderizar idêntica. Num teste de restart, as alternativas recalculadas mudaram (as reservas em memória voltam ao estado inicial) e o servidor perguntou de novo em vez de concluir. Com o `InputRequiredResult` montado na tool, as alternativas ficam seladas no estado. No retry, o servidor:
- valida a escolha contra o que foi oferecido;
- confere que a sala segue livre;
- conclui a reserva.

A checagem de capability (`-32021` com `data.requiredCapabilities` e HTTP 400) segue a mesma regra do SDK. Ela vale na primeira rodada e também quando a pergunta é refeita, num retry que chega sem `inputResponses`.

**Chave do `inputRequests`.** A chave é do servidor, e o enunciado não fixa o texto. Uso `__main__:escolha_de_sala`, a mesma de `exemplos/wire/03` e `04` ([servidor.py:41](servidor-mcp/servidor.py#L41)). Assim, um retry montado a partir do exemplo 04 funciona trocando só o `requestState`.

**Estado das Tasks.** As Tasks ficam em memória no processo do agente: um dicionário `id → Tarefa`, com um `asyncio.Lock` por Task ([agente/tarefas.py](agente/tarefas.py)). Cada Task pausada tem a sua própria `Pendencia`, então duas Tasks pausadas ao mesmo tempo nunca trocam de `requestState`. Estados terminais são definitivos: um `SendMessage` numa Task terminal recebe `-32004` (UnsupportedOperation), e uma Task inexistente recebe `-32001`. As reservas também ficam só em memória: são visíveis no mesmo processo e não sobrevivem a um restart, como permite o enunciado.

**Queda do servidor MCP.** O cliente MCP do agente fica vivo entre chamadas. O contexto do SDK abre um task group do anyio, que só pode ser fechado na task em que foi aberto; por isso o cliente vive numa task própria ([agente/host_mcp.py:124](agente/host_mcp.py#L124)). Se o transporte cai (`MCPError -32000 Connection closed`), aquele cliente fica inutilizável: ele é descartado, e o próximo request cria outro ([host_mcp.py:160](agente/host_mcp.py#L160)). Uma continuação que pega o MCP fora não perde a Task: ela volta a `TASK_STATE_INPUT_REQUIRED` com a mesma pendência, e o `SendMessage` recebe `-32603` pedindo para reenviar a escolha ([agente.py:159](agente/agente.py#L159)). Um pedido novo com o MCP fora termina em `FAILED`.

**Lado A2A sem SDK.** O enunciado exige A2A v1.0 em JSON-RPC 2.0, sem SDK específico. O card e as duas operações estão escritos sobre Starlette, seguindo o wire de `exemplos/wire/07` a `10`. São só três rotas e uma máquina de estados, o que dá controle exato sobre as mensagens: a pausa é comparada byte a byte, e nenhum campo interno escapa para a resposta. Uma notificação JSON-RPC (request sem `id`) recebe HTTP 204 sem corpo e não é executada, porque a A2A só define requests. Um erro inesperado vira `-32603` no envelope JSON-RPC, com o traceback no stderr.

**Determinismo.** O agente interpreta texto em formato fixo, por regra ([agente/pedido.py](agente/pedido.py)). O mesmo pedido produz a mesma pausa, e não há dependência de SDK de LLM.

**Limitação do SDK: a declaração de capability.** O enunciado pede que todo request do agente declare `{"elicitation": {"form": {}}}`. O cliente do SDK só anuncia elicitation quando existe um callback, e então anuncia os dois modos (`mcp/client/session.py`, `ClientSession._build_capabilities`):

```python
elicitation = (
    types.ElicitationCapability(form=types.FormElicitationCapability(), url=types.UrlElicitationCapability())
    if self._elicitation_callback is not _default_elicitation_callback
    else None
)
```

O agente não pode ter callback: com ele, o SDK responderia a pergunta sozinho e a Task nunca pausaria. Por isso uma subclasse mínima, `_SessaoFormOnly` em [agente/host_mcp.py:45](agente/host_mcp.py#L45), sobrescreve só esse método, para declarar exatamente form mode. Todo o resto continua sendo o SDK: transporte, headers `MCP-Protocol-Version`/`Mcp-Method`/`Mcp-Name`, `_meta` e ids.

**Limitação do SDK: o transporte legado.** O servidor escolhe o transporte só pelo header `MCP-Protocol-Version` (`mcp/server/streamable_http_manager.py`, `_handle_request`):

```python
pv = next((v.decode("latin-1") for k, v in scope["headers"] if k == header), None)
if pv is not None and pv not in HANDSHAKE_PROTOCOL_VERSIONS:
    await handle_modern_request(...)
    return
# senao: transporte legado (stateless), que nao exige os campos de _meta
```

Sem o header, ou com uma revisão 2024/2025, o request ia para o transporte legado, que aceitava `tools/call` sem `_meta` e chegava a criar reserva. O SDK não tem opção para desligar esse caminho. Por isso um middleware, `GuardaDeVersao` ([servidor-mcp/guarda_versao.py](servidor-mcp/guarda_versao.py)), responde a esses requests com o erro que o caminho 2026-07-28 daria, calculado pelo próprio ladder do SDK (`classify_inbound_request`): `-32602` sem os campos de `_meta`, `-32020` com header divergente e `-32022` para revisão não servida, sempre com HTTP 400. O log de requests fica por fora e registra também o que a guarda recusa.

## Saída do validador

Última execução, com os dois processos recém-iniciados:

```
trace-id desta execucao: ab5058f4ebba7ac9423d74ad94f22948
procure esse valor no stderr do servidor MCP para conferir a propagacao do traceparent.

PASS 01 tools/list traz as tres tools
PASS 02 toda tool tem inputSchema de objeto
PASS 03 listar_salas devolve structuredContent e o mesmo JSON em texto
PASS 04 _meta sem protocolVersion devolve -32602 e HTTP 400
PASS 05 _meta sem clientCapabilities devolve -32602 e HTTP 400
PASS 06 tool inexistente e recusada, por -32602 ou por isError
PASS 07 resources/read de politica://uso devolve a politica
PASS 08 resources/read de URI inexistente devolve -32602
PASS 09 sala inexistente devolve isError com a mensagem exata
PASS 10 fora da janela devolve isError com a mensagem exata
PASS 11 duracao acima de 2h devolve isError com a mensagem exata
PASS 12 intervalo invertido devolve isError com a mensagem exata
PASS 13 conflito devolve input_required com inputRequests e requestState
PASS 14 a elicitation e form mode e oferece as alternativas na ordem certa
PASS 15 conflito sem a capability elicitation devolve -32021 e HTTP 400
PASS 16 retry com inputResponses e requestState conclui a reserva
PASS 17 requestState adulterado e rejeitado com -32602
PASS 18 argumentos adulterados no retry nao tomam efeito
PASS 19 recusa conclui sem reservar e sem isError
PASS 20 conflito sem alternativa possivel devolve isError com a mensagem exata

PASS 21 agent card responde 200 no well-known com JSON
PASS 22 o card declara a interface JSON-RPC com url e versao 1.0
PASS 23 o card declara a skill reservar-sala
PASS 24 SendMessage com sala livre conclui a Task
PASS 25 o artifact chama reserva e traz a versao da politica
PASS 26 GetTask devolve id, contextId e estado corrente
PASS 27 SendMessage com sala ocupada pausa a Task
PASS 28 a Task pausada lista as alternativas na ordem certa
PASS 29 escolha fora do enum mantem a Task pausada
PASS 30 a continuacao conclui a Task na sala escolhida
PASS 31 SendMessage em Task terminal e recusado
PASS 32 a recusa termina a Task em CANCELED
PASS 33 duas Tasks pausadas ao mesmo tempo concluem cada uma com a sua reserva
PASS 34 nenhuma resposta A2A carrega o requestState
PASS 35 sala inexistente termina a Task em FAILED com a mensagem da tool
PASS 36 o agente e deterministico: o mesmo pedido produz a mesma pausa

resumo: 36 passaram, 0 falharam, de 36 verificacoes
```
