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

**Testes unitários** (opcional): `pip install pytest==9.1.1 && python3 -m pytest`

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

## Onde a ponte acontece

1. **Entrada.** O pedido chega em `SendMessage` e o agente abre a Task. Antes do primeiro `tools/call`, ele descobre as tools com `tools/list` e lê a versão da política em `politica://uso`.
2. **Pedido de informação.** No servidor MCP, `reservar_sala` encontra o conflito e não chama o cliente (o transporte não tem canal de volta). Ela termina a resposta com um `InputRequiredResult`: uma elicitation em form mode com o `enum` de alternativas, mais o estado do pedido ([servidor-mcp/servidor.py:150](servidor-mcp/servidor.py#L150) e `_pergunta` em [servidor.py:99](servidor-mcp/servidor.py#L99)). O SDK sela esse estado antes de ele ir para o fio.
3. **O cliente vê o `input_required` cru.** O agente chama a tool pelo cliente do SDK com `allow_input_required=True` e sem nenhum callback de elicitation ([agente/host_mcp.py:147](agente/host_mcp.py#L147)). Assim o SDK não responde a pergunta sozinho: o `input_required` volta cru e vira o desfecho `Pausado` ([host_mcp.py:152-160](agente/host_mcp.py#L152-L160)).
4. **`input_required` → `TASK_STATE_INPUT_REQUIRED`.** Isso acontece em `_pausar` ([agente/agente.py:93](agente/agente.py#L93)). O `requestState`, a chave do `inputRequests`, as alternativas e os argumentos originais ficam guardados na `Pendencia` daquela Task. A Task responde exatamente `alternativas: <ids>`. A `Pendencia` nunca é serializada: `Tarefa.para_wire` só emite os campos públicos da Task ([agente/tarefas.py](agente/tarefas.py)).
5. **O `requestState` volta ao servidor.** Isso acontece em `_retomar` ([agente/agente.py:127](agente/agente.py#L127)). O `SendMessage` de continuação com `escolha=<id>` (ou `escolha=recusar`, que vira `decline`) repete o mesmo `tools/call` com os mesmos argumentos, `inputResponses` com a mesma chave e o `requestState` sem modificação ([agente.py:146-147](agente/agente.py#L146-L147)). O SDK emite um id JSON-RPC novo para esse retry.
6. **O servidor reconstrói o pedido a partir do próprio `requestState`** ([servidor.py:122](servidor-mcp/servidor.py#L122)) e conclui a reserva. A Task termina em `COMPLETED`, com o artifact `reserva`; em `CANCELED`, na recusa; ou em `FAILED`, num `isError`.

O `traceparent` recebido no header A2A é propagado em todos os requests MCP daquela Task. Vai no `_meta`, com o mesmo trace-id e um span-id novo por request ([agente/traceparent.py](agente/traceparent.py)). O stderr do servidor MCP registra `method`, `id`, `traceparent` e a presença do `requestState` em cada request ([servidor-mcp/log_requests.py](servidor-mcp/log_requests.py)). Nesse log aparecem o `tools/list` antes do primeiro `tools/call` e o par `tools/call` inicial / retry com ids diferentes.

## Decisões técnicas

**Proteção do `requestState`.** Uso o utilitário do SDK, `RequestStateSecurity(keys=[REQUEST_STATE_SECRET], ttl=600)`, instalado como `RequestStateBoundary` pelo `MCPServer` ([servidor.py:56](servidor-mcp/servidor.py#L56)). A cifra é AES-256-GCM, com a chave derivada por HKDF-SHA256 do segredo da variável de ambiente; sem a variável, ou com menos de 32 bytes, o processo não sobe. O envelope selado carrega:
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

A checagem de capability (`-32021` com `data.requiredCapabilities` e HTTP 400) segue a mesma regra do SDK.

**Estado das Tasks.** As Tasks ficam em memória no processo do agente: um dicionário `id → Tarefa`, com um `asyncio.Lock` por Task ([agente/tarefas.py](agente/tarefas.py)). Cada Task pausada tem a sua própria `Pendencia`, então duas Tasks pausadas ao mesmo tempo nunca trocam de `requestState`. Estados terminais são definitivos: um `SendMessage` numa Task terminal recebe `-32004` (UnsupportedOperation), e uma Task inexistente recebe `-32001`. As reservas também ficam só em memória: são visíveis no mesmo processo e não sobrevivem a um restart, como permite o enunciado.

**Lado A2A sem SDK.** O enunciado exige A2A v1.0 em JSON-RPC 2.0, sem SDK específico. O card e as duas operações estão escritos sobre Starlette, seguindo o wire de `exemplos/wire/07` a `10`. São só três rotas e uma máquina de estados, o que dá controle exato sobre as mensagens: a pausa é comparada byte a byte, e nenhum campo interno escapa para a resposta.

**Determinismo.** O agente interpreta texto em formato fixo, por regra ([agente/pedido.py](agente/pedido.py)). O mesmo pedido produz a mesma pausa, e não há dependência de SDK de LLM.

**Limitação do SDK: a declaração de capability.** O enunciado pede que todo request do agente declare `{"elicitation": {"form": {}}}`. O cliente do SDK só anuncia elicitation quando existe um callback, e então anuncia os dois modos (`mcp/client/session.py`, `ClientSession._build_capabilities`):

```python
elicitation = (
    types.ElicitationCapability(form=types.FormElicitationCapability(), url=types.UrlElicitationCapability())
    if self._elicitation_callback is not _default_elicitation_callback
    else None
)
```

O agente não pode ter callback: com ele, o SDK responderia a pergunta sozinho e a Task nunca pausaria. Por isso uma subclasse mínima, `_SessaoFormOnly` em [agente/host_mcp.py:34](agente/host_mcp.py#L34), sobrescreve só esse método, para declarar exatamente form mode. Todo o resto continua sendo o SDK: transporte, headers `MCP-Protocol-Version`/`Mcp-Method`/`Mcp-Name`, `_meta` e ids.

## Saída do validador

Última execução, com os dois processos recém-iniciados:

```
trace-id desta execucao: f49bcd0085b0ed87ef3043856467c197
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
