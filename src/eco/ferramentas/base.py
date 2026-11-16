"""Tipo comum das ferramentas que o Claude pode chamar."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class Ferramenta:
    nome: str
    descricao: str
    schema: dict[str, Any]
    executar: Callable[[dict[str, Any]], str]
    # Se preenchido, a ação tem efeito externo: recebe os argumentos e devolve a frase
    # que o Eco lê de volta antes de pedir o "sim".
    confirmar: Callable[[dict[str, Any]], str] | None = None

    def definicao(self) -> dict[str, Any]:
        return {"name": self.nome, "description": self.descricao, "input_schema": self.schema}
