"""W3C Trace Context: o trace-id do cliente A2A atravessa ate o servidor MCP; o span-id e novo por request."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

_FORMATO = re.compile(r"^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")


@dataclass(frozen=True)
class Trace:
    trace_id: str
    flags: str = "01"

    def novo_traceparent(self) -> str:
        return f"00-{self.trace_id}-{secrets.token_hex(8)}-{self.flags}"


def ler_traceparent(valor: str | None) -> Trace | None:
    if not valor:
        return None
    achado = _FORMATO.match(valor.strip().lower())
    if achado is None:
        return None
    _, trace_id, span_id, flags = achado.groups()
    if trace_id == "0" * 32 or span_id == "0" * 16:
        return None
    return Trace(trace_id, flags)
